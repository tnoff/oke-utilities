package main

import (
	"bytes"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

const testNow = 1_800_000_000

var (
	keyOnce   sync.Once
	sharedKey *rsa.PrivateKey
)

// testKey is one RSA key for the whole test run (generation is slow).
func testKey(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	keyOnce.Do(func() {
		var err error
		if sharedKey, err = generateKey(); err != nil {
			panic(err)
		}
	})
	return sharedKey
}

func b64url(data []byte) string { return base64.RawURLEncoding.EncodeToString(data) }

// makeJWT is an unsigned-looking JWT with the given payload claims.
func makeJWT(claims map[string]any) string {
	header, _ := json.Marshal(map[string]any{"alg": "RS256"})
	payload, _ := json.Marshal(claims)
	return b64url(header) + "." + b64url(payload) + ".c2ln"
}

func rpstFor(jti string, iat, exp float64) string {
	return makeJWT(map[string]any{"jti": jti, "iat": iat, "exp": exp})
}

var saToken = makeJWT(map[string]any{"sub": "system:serviceaccount:apps:bot", "aud": []string{"x"}})

type reply struct {
	status int
	body   string
}

func ok(rpst string) reply {
	body, _ := json.Marshal(map[string]any{"token": rpst})
	return reply{200, string(body)}
}

type seenRequest struct {
	method, path string
	header       http.Header
	form         url.Values
	raw          string
}

// fakeDomain is an identity domain that answers with each reply in turn and records the requests.
type fakeDomain struct {
	server   *httptest.Server
	mu       sync.Mutex
	replies  []reply
	requests []seenRequest
}

func newFakeDomain(t *testing.T, replies ...reply) *fakeDomain {
	t.Helper()
	fake := &fakeDomain{replies: replies}
	fake.server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		form, _ := url.ParseQuery(string(raw))
		fake.mu.Lock()
		fake.requests = append(fake.requests, seenRequest{r.Method, r.URL.Path, r.Header.Clone(), form, string(raw)})
		if len(fake.replies) == 0 {
			fake.mu.Unlock()
			t.Errorf("unexpected request %d", len(fake.requests))
			w.WriteHeader(599)
			return
		}
		next := fake.replies[0]
		fake.replies = fake.replies[1:]
		fake.mu.Unlock()
		w.WriteHeader(next.status)
		io.Copy(w, bytes.NewBufferString(next.body))
	}))
	t.Cleanup(fake.server.Close)
	return fake
}

func (f *fakeDomain) calls() []seenRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]seenRequest(nil), f.requests...)
}

func (f *fakeDomain) last(t *testing.T) seenRequest {
	t.Helper()
	calls := f.calls()
	if len(calls) == 0 {
		t.Fatal("no request was made")
	}
	return calls[len(calls)-1]
}

func (f *fakeDomain) client() *http.Client {
	client := f.server.Client()
	client.CheckRedirect = newHTTPClient(Config{}).CheckRedirect
	return client
}

// testConfig points at a temp tree with client creds and an SA token in place.
func testConfig(t *testing.T, domain string) Config {
	t.Helper()
	dir := t.TempDir()
	client := filepath.Join(dir, "client")
	out := filepath.Join(dir, "out")
	mustMkdir(t, client)
	mustMkdir(t, out)
	mustWrite(t, filepath.Join(client, "client_id"), "the-client-id\n")
	mustWrite(t, filepath.Join(client, "client_secret"), "the-client-secret\n")
	mustWrite(t, filepath.Join(dir, "token"), saToken+"\n")
	return Config{
		DomainURL:          domain,
		ClientDir:          client,
		TokenFile:          filepath.Join(dir, "token"),
		OutputDir:          out,
		ExpMinutes:         180,
		RefreshFraction:    0.5,
		RequestTimeout:     5e9,
		PodName:            "pod-1",
		PodNamespace:       "apps",
		ServiceAccountName: "bot",
	}
}

func mustMkdir(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
}

func mustWrite(t *testing.T, path, data string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
}

func mustRead(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// captureLog sends log lines to a buffer for the rest of the test.
func captureLog(t *testing.T) *syncBuffer {
	t.Helper()
	buf := &syncBuffer{}
	previous := logOut
	logOut = buf
	t.Cleanup(func() { logOut = previous })
	return buf
}

type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// setNow pins the clock for the rest of the test.
func setNow(t *testing.T, value float64) {
	t.Helper()
	previous := now
	now = func() float64 { return value }
	t.Cleanup(func() { now = previous })
}

// unsetEnv removes a variable for the rest of the test; a t.Setenv beforehand registers the restore.
func unsetEnv(t *testing.T, name string) {
	t.Helper()
	if err := os.Unsetenv(name); err != nil {
		t.Fatal(err)
	}
}
