package main

import (
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

func runExchange(t *testing.T, replies ...reply) (*fakeDomain, Config, string, string, error) {
	t.Helper()
	fake := newFakeDomain(t, replies...)
	cfg := testConfig(t, fake.server.URL)
	token, sa, err := exchange(cfg, testKey(t), fake.client())
	return fake, cfg, token, sa, err
}

func expectExchangeError(t *testing.T, err error) string {
	t.Helper()
	var exErr *ExchangeError
	if !errors.As(err, &exErr) {
		t.Fatalf("want an ExchangeError, got %v", err)
	}
	return err.Error()
}

func TestRequestShape(t *testing.T) {
	fake, _, token, sa, err := runExchange(t, ok("rpst-token"))
	if err != nil {
		t.Fatal(err)
	}
	if token != "rpst-token" || sa != saToken {
		t.Fatalf("got %q, %q", token, sa)
	}
	request := fake.last(t)
	if request.method != http.MethodPost || request.path != "/oauth2/v1/token" {
		t.Fatalf("%s %s", request.method, request.path)
	}
	if got := request.header.Get("Content-Type"); got != "application/x-www-form-urlencoded" {
		t.Fatalf("content type %q", got)
	}
	basic := base64.StdEncoding.EncodeToString([]byte("the-client-id:the-client-secret"))
	if got := request.header.Get("Authorization"); got != "Basic "+basic {
		t.Fatalf("authorization %q", got)
	}
	var keys []string
	for k := range request.form {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	wantKeys := []string{"grant_type", "public_key", "requested_token_type", "res_type", "rpst_exp", "subject_token", "subject_token_type"}
	if !reflect.DeepEqual(keys, wantKeys) {
		t.Fatalf("form keys %v", keys)
	}
	want := map[string]string{
		"grant_type":           "urn:ietf:params:oauth:grant-type:token-exchange",
		"requested_token_type": "urn:oci:token-type:oci-rpst",
		"subject_token_type":   "jwt",
		"subject_token":        saToken,
		"res_type":             "k8sworkload",
		"rpst_exp":             "180",
	}
	for k, v := range want {
		if got := request.form.Get(k); got != v {
			t.Errorf("%s = %q, want %q", k, got, v)
		}
	}
}

func TestPublicKeyIsBase64DEROfTheKey(t *testing.T) {
	fake, _, _, _, err := runExchange(t, ok("t"))
	if err != nil {
		t.Fatal(err)
	}
	der, err := base64.StdEncoding.Strict().DecodeString(fake.last(t).form.Get("public_key"))
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := x509.ParsePKIXPublicKey(der)
	if err != nil {
		t.Fatal(err)
	}
	public, isRSA := parsed.(*rsa.PublicKey)
	if !isRSA || !public.Equal(&testKey(t).PublicKey) || public.N.BitLen() != 2048 {
		t.Fatal("public_key is not the sidecar's RSA-2048 key")
	}
}

func TestPrivatePEMIsUnencryptedPKCS8(t *testing.T) {
	data, err := privatePEM(testKey(t))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(data), "-----BEGIN PRIVATE KEY-----") {
		t.Fatalf("pem starts %q", string(data[:30]))
	}
	block, _ := pem.Decode(data)
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if !testKey(t).Equal(parsed) {
		t.Fatal("pem holds a different key")
	}
}

func TestRpstExpFollowsConfig(t *testing.T) {
	fake := newFakeDomain(t, ok("t"))
	cfg := testConfig(t, fake.server.URL)
	cfg.ExpMinutes = 45
	if _, _, err := exchange(cfg, testKey(t), fake.client()); err != nil {
		t.Fatal(err)
	}
	if got := fake.last(t).form.Get("rpst_exp"); got != "45" {
		t.Fatalf("rpst_exp %q", got)
	}
}

func TestCredentialsAndTokenRereadAndStripped(t *testing.T) {
	fake := newFakeDomain(t, ok("t"), ok("t"))
	cfg := testConfig(t, fake.server.URL)
	if _, _, err := exchange(cfg, testKey(t), fake.client()); err != nil {
		t.Fatal(err)
	}
	mustWrite(t, filepath.Join(cfg.ClientDir, "client_id"), "  id2 \n\n")
	mustWrite(t, filepath.Join(cfg.ClientDir, "client_secret"), "secret2\r\n")
	mustWrite(t, cfg.TokenFile, " a.b\n.c \n")
	if _, _, err := exchange(cfg, testKey(t), fake.client()); err != nil {
		t.Fatal(err)
	}
	request := fake.last(t)
	if got := request.header.Get("Authorization"); got != "Basic "+base64.StdEncoding.EncodeToString([]byte("id2:secret2")) {
		t.Fatalf("authorization %q", got)
	}
	if got := request.form.Get("subject_token"); got != "a.b.c" {
		t.Fatalf("subject_token %q", got)
	}
}

func TestMissingInputFile(t *testing.T) {
	for _, name := range []string{"client_id", "client_secret", "token"} {
		t.Run(name, func(t *testing.T) {
			fake := newFakeDomain(t)
			cfg := testConfig(t, fake.server.URL)
			path := cfg.TokenFile
			if name != "token" {
				path = filepath.Join(cfg.ClientDir, name)
			}
			os.Remove(path)
			_, _, err := exchange(cfg, testKey(t), fake.client())
			msg := expectExchangeError(t, err)
			if msg != "cannot read an input file: no such file or directory ("+path+")" {
				t.Fatalf("message %q", msg)
			}
			if len(fake.calls()) != 0 {
				t.Fatal("made a request without its inputs")
			}
		})
	}
}

func TestHTTPErrorReportsStatusAndTruncatedBody(t *testing.T) {
	_, _, _, _, err := runExchange(t, reply{status: 400, body: strings.Repeat("x", 3000)})
	if msg := expectExchangeError(t, err); msg != "HTTP 400: "+strings.Repeat("x", httpErrorBodyChars) {
		t.Fatalf("message is %d chars", len(msg))
	}
}

func TestHTTPErrorKeepsTheEcidPast200Characters(t *testing.T) {
	body := `{"error":"unauthorized_client","error_description":"Error during GET signing certificate : ` +
		`https://objectstorage.us-sanjose-1.oraclecloud.com/n/tnoff/b/k8s_oidc_jwks/o/jwks.jsonRead timed out",` +
		`"ecid":"0123456789abcdef-0001"}`
	if len(body) <= errorBodyChars {
		t.Fatal("fixture too short")
	}
	_, _, _, _, err := runExchange(t, reply{status: 401, body: body})
	if msg := expectExchangeError(t, err); msg != "HTTP 401: "+body {
		t.Fatalf("message %q", msg)
	}
}

func TestSuccessStatusBodyKeepsTheShortCap(t *testing.T) {
	_, _, _, _, err := runExchange(t, reply{status: 202, body: strings.Repeat("t", 500)})
	if msg := expectExchangeError(t, err); msg != "HTTP 202: "+strings.Repeat("t", errorBodyChars) {
		t.Fatalf("message is %d chars", len(msg))
	}
}

func TestNon200SuccessStatus(t *testing.T) {
	_, _, _, _, err := runExchange(t, reply{status: 202, body: "accepted"})
	if msg := expectExchangeError(t, err); msg != "HTTP 202: accepted" {
		t.Fatalf("message %q", msg)
	}
}

func TestErrorBodyIsRedacted(t *testing.T) {
	basic := base64.StdEncoding.EncodeToString([]byte("the-client-id:the-client-secret"))
	_, _, _, _, err := runExchange(t, reply{status: 401, body: "bad: the-client-secret and " + saToken + " and " + basic})
	msg := expectExchangeError(t, err)
	for _, secret := range []string{"the-client-secret", saToken, basic} {
		if strings.Contains(msg, secret) {
			t.Fatalf("message leaks %q", secret)
		}
	}
	if !strings.Contains(msg, "[redacted]") {
		t.Fatalf("message %q", msg)
	}
}

// The request carries the client secret and the SA token, so a redirect must not take them anywhere.
func TestRedirectIsNotFollowed(t *testing.T) {
	for _, status := range []int{301, 302, 303, 307, 308} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// A target the real client would happily follow: same server, a path that would succeed.
			fake := newFakeDomain(t)
			fake.replies = []reply{{status: status, body: "moved", location: "/elsewhere"}, ok("must-not-be-fetched")}
			cfg := testConfig(t, fake.server.URL)
			client := newHTTPClient(cfg)
			client.Transport = fake.server.Client().Transport // trust the test server's certificate
			_, _, err := exchange(cfg, testKey(t), client)
			if msg := expectExchangeError(t, err); msg != fmt.Sprintf("HTTP %d: moved", status) {
				t.Fatalf("message %q", msg)
			}
			if calls := fake.calls(); len(calls) != 1 {
				t.Fatalf("%d requests, the redirect was followed", len(calls))
			}
		})
	}
}

func TestHTTPClientUsesTheConfiguredTimeout(t *testing.T) {
	if got := newHTTPClient(Config{RequestTimeout: 7 * time.Second}).Timeout; got != 7*time.Second {
		t.Fatalf("timeout %v", got)
	}
}

func TestNetworkError(t *testing.T) {
	fake := newFakeDomain(t)
	cfg := testConfig(t, fake.server.URL)
	client := fake.client()
	fake.server.Close()
	_, _, err := exchange(cfg, testKey(t), client)
	if msg := expectExchangeError(t, err); !strings.HasPrefix(msg, "request failed: ") {
		t.Fatalf("message %q", msg)
	}
}

func TestTimeout(t *testing.T) {
	release := make(chan struct{})
	fake := newFakeDomain(t)
	fake.server.Config.Handler = http.HandlerFunc(func(http.ResponseWriter, *http.Request) { <-release })
	defer close(release)
	cfg := testConfig(t, fake.server.URL)
	client := fake.client()
	client.Timeout = 50 * time.Millisecond
	_, _, err := exchange(cfg, testKey(t), client)
	if msg := expectExchangeError(t, err); !strings.Contains(msg, "request failed") || !strings.Contains(msg, "Timeout") {
		t.Fatalf("message %q", msg)
	}
}

func TestBadBodiesFail(t *testing.T) {
	for _, body := range []string{"not json", "[1, 2]", "null", "{}", `{"token": ""}`, `{"token": 5}`, `{"other": "x"}`} {
		t.Run(body, func(t *testing.T) {
			_, _, _, _, err := runExchange(t, reply{status: 200, body: body})
			expectExchangeError(t, err)
		})
	}
}

func TestJWTClaims(t *testing.T) {
	got := jwtClaims(makeJWT(map[string]any{"sub": "s", "exp": 5}))
	if !reflect.DeepEqual(got, map[string]any{"sub": "s", "exp": 5.0}) {
		t.Fatalf("claims %v", got)
	}
}

func TestJWTClaimsAcceptsPaddedSegments(t *testing.T) {
	segment := base64.URLEncoding.EncodeToString([]byte(`{"a":1}`))
	if !strings.HasSuffix(segment, "=") {
		t.Fatal("fixture has no padding")
	}
	if got := jwtClaims("h." + segment + ".s"); got["a"] != 1.0 {
		t.Fatalf("claims %v", got)
	}
}

func TestJWTClaimsUnparseable(t *testing.T) {
	for _, token := range []string{"", "nodots", "a.!!!.c", "a.bm90LWpzb24.c", "a.WzFd.c", "a.bnVsbA.c"} {
		if got := jwtClaims(token); len(got) != 0 {
			t.Errorf("%q: %v", token, got)
		}
	}
}

func TestGenerateKeyIsRSA2048(t *testing.T) {
	if testKey(t).N.BitLen() != 2048 {
		t.Fatal("not 2048 bits")
	}
}

func TestRedact(t *testing.T) {
	if got := redact("hello", []string{""}, 200); got != "hello" {
		t.Fatalf("%q", got)
	}
	// Characters, not bytes, and invalid UTF-8 cannot break the cut.
	if got := redact("ééé\xff", nil, 3); got != "ééé" {
		t.Fatalf("%q", got)
	}
}
