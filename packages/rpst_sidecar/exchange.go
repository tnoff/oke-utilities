package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

const (
	grantType          = "urn:ietf:params:oauth:grant-type:token-exchange"
	requestedTokenType = "urn:oci:token-type:oci-rpst"
	errorBodyChars     = 200
	// An HTTP error status carries the identity domain's error JSON, which ends in the `ecid` OCI support asks
	// for; 200 chars cut it off (2026-10-25 JWKS read timeout). Only this path gets the longer cap: a 2xx that
	// is not 200 could carry an issued token, and the short cap keeps most of one out of the log.
	httpErrorBodyChars = 2000
	// Ceiling on how much of a response body is read at all.
	maxBodyBytes = 1 << 20
)

// ExchangeError means the exchange failed. Messages never contain the client secret or any token.
type ExchangeError struct{ msg string }

func (e *ExchangeError) Error() string { return e.msg }

func exchangeErrorf(format string, args ...any) error {
	return &ExchangeError{fmt.Sprintf(format, args...)}
}

// newHTTPClient never follows redirects: the request carries the client secret and the SA token.
// A 3xx is reported like any other non-2xx status.
func newHTTPClient(cfg Config) *http.Client {
	return &http.Client{
		Timeout: cfg.RequestTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

// generateKey returns a fresh RSA-2048 key; generated once per process.
func generateKey() (*rsa.PrivateKey, error) {
	return rsa.GenerateKey(rand.Reader, 2048)
}

// privatePEM is the unencrypted PKCS8 PEM, the form the OCI SDK reads.
func privatePEM(key *rsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, err
	}
	return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), nil
}

// publicKeyB64 is standard base64 of the DER SubjectPublicKeyInfo.
func publicKeyB64(key *rsa.PrivateKey) (string, error) {
	der, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(der), nil
}

// jwtClaims returns the payload claims of a JWT, decoded WITHOUT verifying the signature.
//
// Used only for scheduling and logging. Returns an empty map for anything unparseable.
func jwtClaims(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return map[string]any{}
	}
	segment := strings.TrimRight(parts[1], "=")
	segment = strings.NewReplacer("+", "-", "/", "_").Replace(segment)
	raw, err := base64.RawURLEncoding.DecodeString(segment)
	if err != nil {
		return map[string]any{}
	}
	var claims map[string]any
	if err := json.Unmarshal(raw, &claims); err != nil || claims == nil {
		return map[string]any{}
	}
	return claims
}

// readSecretFile returns the file contents with surrounding whitespace removed.
func readSecretFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

// redact returns the first `limit` characters of `text`, with any credential that was echoed back removed.
func redact(text string, secrets []string, limit int) string {
	text = strings.ToValidUTF8(text, "�")
	for _, secret := range secrets {
		if secret != "" {
			text = strings.ReplaceAll(text, secret, "[redacted]")
		}
	}
	runes := []rune(text)
	if len(runes) > limit {
		runes = runes[:limit]
	}
	return string(runes)
}

func inputFileError(err error) error {
	var pathErr *fs.PathError
	if errors.As(err, &pathErr) {
		return exchangeErrorf("cannot read an input file: %v (%s)", pathErr.Err, pathErr.Path)
	}
	return exchangeErrorf("cannot read an input file: %v", err)
}

// exchange trades the current ServiceAccount token for an RPST.
//
// Returns (rpst, serviceAccountToken). Every error is an *ExchangeError.
// Credentials and the SA token are re-read on every call: both rotate underneath us.
func exchange(cfg Config, key *rsa.PrivateKey, client *http.Client) (string, string, error) {
	clientID, err := readSecretFile(filepath.Join(cfg.ClientDir, "client_id"))
	if err != nil {
		return "", "", inputFileError(err)
	}
	clientSecret, err := readSecretFile(filepath.Join(cfg.ClientDir, "client_secret"))
	if err != nil {
		return "", "", inputFileError(err)
	}
	rawToken, err := readSecretFile(cfg.TokenFile)
	if err != nil {
		return "", "", inputFileError(err)
	}
	saToken := strings.Join(strings.Fields(rawToken), "")

	publicKey, err := publicKeyB64(key)
	if err != nil {
		return "", "", exchangeErrorf("cannot encode the public key: %v", err)
	}
	basic := base64.StdEncoding.EncodeToString([]byte(clientID + ":" + clientSecret))
	secrets := []string{clientSecret, saToken, basic}
	form := url.Values{
		"grant_type":           {grantType},
		"requested_token_type": {requestedTokenType},
		"subject_token_type":   {"jwt"},
		"subject_token":        {saToken},
		"public_key":           {publicKey},
		"res_type":             {"k8sworkload"},
		"rpst_exp":             {fmt.Sprint(cfg.ExpMinutes)},
	}
	// The URL is validated as https by Config.validate.
	request, err := http.NewRequest(http.MethodPost, cfg.DomainURL+"/oauth2/v1/token", strings.NewReader(form.Encode()))
	if err != nil {
		return "", "", exchangeErrorf("%s", redact(fmt.Sprintf("bad request: %v", err), secrets, errorBodyChars))
	}
	request.Header.Set("Authorization", "Basic "+basic)
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	response, err := client.Do(request)
	if err != nil {
		// Connection failures and timeouts.
		return "", "", exchangeErrorf("%s", redact(fmt.Sprintf("request failed: %v", err), secrets, errorBodyChars))
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxBodyBytes))
	if err != nil {
		return "", "", exchangeErrorf("%s", redact(fmt.Sprintf("request failed: %v", err), secrets, errorBodyChars))
	}
	text := string(body)
	status := response.StatusCode
	if status < 200 || status > 299 {
		return "", "", exchangeErrorf("HTTP %d: %s", status, redact(text, secrets, httpErrorBodyChars))
	}
	if status != http.StatusOK {
		return "", "", exchangeErrorf("HTTP %d: %s", status, redact(text, secrets, errorBodyChars))
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil || payload == nil {
		return "", "", exchangeErrorf("response is not a JSON object")
	}
	token, ok := payload["token"].(string)
	if !ok || token == "" {
		return "", "", exchangeErrorf("response has no token")
	}
	return token, saToken, nil
}
