package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
)

func newTestSidecar(t *testing.T, replies ...reply) (*Sidecar, *fakeDomain, Config) {
	t.Helper()
	fake := newFakeDomain(t, replies...)
	cfg := testConfig(t, fake.server.URL)
	sidecar, err := newSidecar(cfg, testKey(t), fake.client())
	if err != nil {
		t.Fatal(err)
	}
	return sidecar, fake, cfg
}

func listDir(t *testing.T, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	return names
}

func permOf(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm()
}

// recordWrites wraps the file writer for the rest of the test and returns the base names written.
func recordWrites(t *testing.T, fail error) *[]string {
	t.Helper()
	var names []string
	previous := writer
	writer = func(path string, data []byte, mode os.FileMode) error {
		names = append(names, filepath.Base(path))
		if fail != nil {
			return fail
		}
		return previous(path, data, mode)
	}
	t.Cleanup(func() { writer = previous })
	return &names
}

// ---- file writing -------------------------------------------------------------------

func TestRefreshWritesBothFiles(t *testing.T) {
	rpst := rpstFor("jti-1", testNow, testNow+10800)
	sidecar, _, cfg := newTestSidecar(t, ok(rpst))
	delay, err := sidecar.refreshOnce()
	if err != nil {
		t.Fatal(err)
	}
	if got := mustRead(t, filepath.Join(cfg.OutputDir, "rpst")); got != rpst {
		t.Fatalf("rpst %q (no trailing newline allowed)", got)
	}
	wantPEM, _ := privatePEM(testKey(t))
	if got := mustRead(t, filepath.Join(cfg.OutputDir, "private.pem")); got != string(wantPEM) {
		t.Fatal("private.pem differs")
	}
	for _, name := range []string{"rpst", "private.pem"} {
		if mode := permOf(t, filepath.Join(cfg.OutputDir, name)); mode != 0o640 {
			t.Errorf("%s mode %o", name, mode)
		}
	}
	if delay != 10800*0.5 {
		t.Fatalf("delay %v", delay)
	}
	if got := listDir(t, cfg.OutputDir); !reflect.DeepEqual(got, []string{"private.pem", "rpst"}) {
		t.Fatalf("output dir holds %v", got) // no temp files left
	}
}

func TestKeyReusedAcrossExchanges(t *testing.T) {
	sidecar, fake, cfg := newTestSidecar(t, ok(rpstFor("a", testNow, testNow+60)), ok(rpstFor("b", testNow, testNow+60)))
	if _, err := sidecar.refreshOnce(); err != nil {
		t.Fatal(err)
	}
	firstPEM := mustRead(t, filepath.Join(cfg.OutputDir, "private.pem"))
	firstRPST := mustRead(t, filepath.Join(cfg.OutputDir, "rpst"))
	if _, err := sidecar.refreshOnce(); err != nil {
		t.Fatal(err)
	}
	if mustRead(t, filepath.Join(cfg.OutputDir, "private.pem")) != firstPEM {
		t.Fatal("private.pem changed")
	}
	if mustRead(t, filepath.Join(cfg.OutputDir, "rpst")) == firstRPST {
		t.Fatal("rpst did not change")
	}
	calls := fake.calls()
	if calls[0].form.Get("public_key") != calls[1].form.Get("public_key") {
		t.Fatal("public key changed between exchanges")
	}
}

func TestPrivateKeyWrittenOnceAndFirst(t *testing.T) {
	sidecar, _, _ := newTestSidecar(t, ok(rpstFor("a", 1, 2)), ok(rpstFor("b", 1, 2)))
	names := recordWrites(t, nil)
	for i := 0; i < 2; i++ {
		if _, err := sidecar.refreshOnce(); err != nil {
			t.Fatal(err)
		}
	}
	if want := []string{"private.pem", "rpst", "rpst"}; !reflect.DeepEqual(*names, want) {
		t.Fatalf("writes %v", *names)
	}
}

func TestFailedExchangeLeavesFilesAlone(t *testing.T) {
	sidecar, _, cfg := newTestSidecar(t, ok(rpstFor("old", 1, 2)), reply{500, "nope"})
	if _, err := sidecar.refreshOnce(); err != nil {
		t.Fatal(err)
	}
	before := mustRead(t, filepath.Join(cfg.OutputDir, "rpst"))
	if _, err := sidecar.refreshOnce(); err == nil {
		t.Fatal("want an error")
	}
	if mustRead(t, filepath.Join(cfg.OutputDir, "rpst")) != before {
		t.Fatal("rpst changed")
	}
}

func TestGeneratesKeyWhenNoneGiven(t *testing.T) {
	sidecar, err := newSidecar(Config{}, nil, nil)
	if err != nil || sidecar.key.N.BitLen() != 2048 {
		t.Fatalf("%v", err)
	}
}

func TestWriteAtomicReplacesWholeFile(t *testing.T) {
	target := filepath.Join(t.TempDir(), "f")
	mustWrite(t, target, "old")
	if err := writeAtomic(target, []byte("new"), 0o640); err != nil {
		t.Fatal(err)
	}
	if got := mustRead(t, target); got != "new" {
		t.Fatalf("%q", got)
	}
	if mode := permOf(t, target); mode != 0o640 {
		t.Fatalf("mode %o", mode)
	}
}

func TestWriteAtomicFailureCleansUpAndKeepsOld(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "f")
	mustWrite(t, target, "old")
	previous := fsync
	fsync = func(*os.File) error { return errors.New("disk") }
	t.Cleanup(func() { fsync = previous })
	if err := writeAtomic(target, []byte("new"), 0o640); err == nil {
		t.Fatal("want an error")
	}
	if got := mustRead(t, target); got != "old" {
		t.Fatalf("%q", got)
	}
	if got := listDir(t, dir); !reflect.DeepEqual(got, []string{"f"}) {
		t.Fatalf("dir holds %v", got)
	}
}

func TestUnwritableOutputDirIsAFailure(t *testing.T) {
	sidecar, _, cfg := newTestSidecar(t, ok(rpstFor("a", 1, 2)))
	os.Remove(cfg.OutputDir)
	if _, err := sidecar.refreshOnce(); err == nil {
		t.Fatal("want an error")
	}
}

// ---- scheduling ----------------------------------------------------------------------

func TestLifetimeFromClaims(t *testing.T) {
	claims := map[string]any{"iat": 100.0, "exp": 1100.0}
	cfg := Config{ExpMinutes: 180, RefreshFraction: 0.5}
	if got := lifetimeSeconds(claims, 180); got != 1000 {
		t.Fatalf("%v", got)
	}
	if got := refreshDelay(claims, cfg); got != 500 {
		t.Fatalf("%v", got)
	}
	cfg.RefreshFraction = 0.8
	if got := refreshDelay(claims, cfg); got != 800 {
		t.Fatalf("%v", got)
	}
}

func TestLifetimeFallsBackToRequestedMinutes(t *testing.T) {
	for _, claims := range []map[string]any{
		{}, {"exp": 5000.0}, {"iat": 5000.0}, {"iat": "x", "exp": "y"}, {"iat": 10.0, "exp": 10.0},
		{"iat": 20.0, "exp": 10.0}, {"iat": true, "exp": 1000.0}, {"iat": nil, "exp": 1000.0},
	} {
		if got := lifetimeSeconds(claims, 120); got != 7200 {
			t.Errorf("%v: %v", claims, got)
		}
	}
}

func TestRefreshOnceUsesFallbackForOpaqueToken(t *testing.T) {
	sidecar, _, _ := newTestSidecar(t, ok("opaque-not-a-jwt"))
	sidecar.cfg.ExpMinutes = 60
	if delay, err := sidecar.refreshOnce(); err != nil || delay != 1800 {
		t.Fatalf("%v, %v", delay, err)
	}
}

// ---- audit ---------------------------------------------------------------------------

func TestAuditLine(t *testing.T) {
	logs := captureLog(t)
	rpst := rpstFor("jti-xyz", testNow, testNow+3*3600)
	sidecar, _, _ := newTestSidecar(t, ok(rpst))
	if _, err := sidecar.refreshOnce(); err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, line := range strings.Split(logs.String(), "\n") {
		if strings.Contains(line, "rpst issued") {
			lines = append(lines, line)
		}
	}
	if len(lines) != 1 {
		t.Fatalf("audit lines: %v", lines)
	}
	if !strings.Contains(lines[0], " - rpst_sidecar.main - INFO - rpst issued ") {
		t.Fatalf("line shape: %q", lines[0])
	}
	for _, want := range []string{
		"jti=jti-xyz", "sub=system:serviceaccount:apps:bot", "namespace=apps", "pod=pod-1", "service_account=bot",
		"issued=2027-01-15T08:00:00Z", "expires=2027-01-15T11:00:00Z", "lifetime_minutes=180.0",
	} {
		if !strings.Contains(lines[0], want) {
			t.Errorf("missing %q in %q", want, lines[0])
		}
	}
	for _, secret := range []string{rpst, saToken, "the-client-secret"} {
		if strings.Contains(logs.String(), secret) {
			t.Errorf("log leaks %q", secret)
		}
	}
}

func TestAuditLineWithUnreadableTokens(t *testing.T) {
	logs := captureLog(t)
	sidecar, _, _ := newTestSidecar(t)
	sidecar.audit(map[string]any{}, map[string]any{})
	for _, want := range []string{"jti=unknown", "sub=unknown", "issued=unknown", "expires=unknown", "lifetime_minutes=unknown"} {
		if !strings.Contains(logs.String(), want) {
			t.Errorf("missing %q", want)
		}
	}
}

// ---- loop: retry, backoff, stop ------------------------------------------------------

// stopAfter records each requested sleep and cancels the run after `limit` of them.
func stopAfter(sidecar *Sidecar, limit int) (context.Context, *[]float64) {
	ctx, cancel := context.WithCancel(context.Background())
	var waits []float64
	sidecar.jitter = func(d float64) float64 { return d }
	sidecar.sleep = func(_ context.Context, d float64) {
		waits = append(waits, d)
		if len(waits) >= limit {
			cancel()
		}
	}
	return ctx, &waits
}

func repeat(r reply, n int) []reply {
	out := make([]reply, n)
	for i := range out {
		out[i] = r
	}
	return out
}

func TestBackoffGrowsThenCaps(t *testing.T) {
	captureLog(t)
	sidecar, _, _ := newTestSidecar(t, repeat(reply{401, "denied"}, 10)...)
	ctx, waits := stopAfter(sidecar, 10)
	if code := sidecar.run(ctx); code != 0 {
		t.Fatalf("exit %d", code)
	}
	if want := []float64{2, 4, 8, 16, 32, 64, 120, 120, 120, 120}; !reflect.DeepEqual(*waits, want) {
		t.Fatalf("waits %v", *waits)
	}
}

func TestDefaultJitterStaysInsideTheWindow(t *testing.T) {
	for i := 0; i < 200; i++ {
		if d := defaultJitter(2); d < 1 || d > 2 {
			t.Fatal(d)
		}
		if d := defaultJitter(120); d < 60 || d > 120 {
			t.Fatal(d)
		}
	}
}

func TestBackoffResetsAfterSuccess(t *testing.T) {
	captureLog(t)
	fail := reply{500, "x"}
	sidecar, _, _ := newTestSidecar(t, fail, fail, ok(rpstFor("a", testNow, testNow+10800)), fail, fail)
	ctx, waits := stopAfter(sidecar, 5)
	sidecar.run(ctx)
	if want := []float64{2, 4, 5400, 2, 4}; !reflect.DeepEqual(*waits, want) {
		t.Fatalf("waits %v", *waits)
	}
}

func TestRetriesForeverAtStartupThenRecovers(t *testing.T) {
	captureLog(t)
	replies := append(repeat(reply{400, "not yet"}, 3), ok(rpstFor("a", testNow, testNow+10800)))
	sidecar, _, cfg := newTestSidecar(t, replies...)
	ctx, waits := stopAfter(sidecar, 4)
	sidecar.run(ctx)
	if _, err := os.Stat(filepath.Join(cfg.OutputDir, "rpst")); err != nil {
		t.Fatal(err)
	}
	if want := []float64{2, 4, 8}; !reflect.DeepEqual((*waits)[:3], want) {
		t.Fatalf("waits %v", *waits)
	}
}

func TestFailureWarningHasStatusAndBodyButNoSecrets(t *testing.T) {
	logs := captureLog(t)
	body := "denied " + strings.Repeat("y", 400) + " the-client-secret " + saToken
	sidecar, _, _ := newTestSidecar(t, reply{403, body})
	ctx, _ := stopAfter(sidecar, 1)
	sidecar.run(ctx)
	var warnings []string
	for _, line := range strings.Split(logs.String(), "\n") {
		if strings.Contains(line, " - WARNING - ") {
			warnings = append(warnings, line)
		}
	}
	if len(warnings) != 1 {
		t.Fatalf("warnings %v", warnings)
	}
	text := warnings[0]
	if !strings.Contains(text, "HTTP 403") || !strings.Contains(text, "denied") || !strings.Contains(text, "[redacted]") {
		t.Fatalf("warning %q", text)
	}
	if len(text) >= 800 {
		t.Fatalf("warning is %d chars", len(text))
	}
	for _, secret := range []string{"the-client-secret", saToken, "Basic "} {
		if strings.Contains(logs.String(), secret) {
			t.Errorf("log leaks %q", secret)
		}
	}
}

func TestSleepsForComputedDelayOnSuccess(t *testing.T) {
	captureLog(t)
	sidecar, _, _ := newTestSidecar(t, ok(rpstFor("a", testNow, testNow+1000)))
	ctx, waits := stopAfter(sidecar, 1)
	sidecar.run(ctx)
	if !reflect.DeepEqual(*waits, []float64{500}) {
		t.Fatalf("waits %v", *waits)
	}
}

func TestOSErrorFromVolumeIsRetried(t *testing.T) {
	captureLog(t)
	sidecar, _, cfg := newTestSidecar(t, ok(rpstFor("a", 1, 2)), ok(rpstFor("a", 1, 2)))
	os.Remove(cfg.OutputDir)
	ctx, waits := stopAfter(sidecar, 2)
	sidecar.run(ctx)
	if !reflect.DeepEqual(*waits, []float64{2, 4}) {
		t.Fatalf("waits %v", *waits)
	}
}

func TestStopAlreadySetDoesNothing(t *testing.T) {
	captureLog(t)
	sidecar, fake, _ := newTestSidecar(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if code := sidecar.run(ctx); code != 0 {
		t.Fatalf("exit %d", code)
	}
	if len(fake.calls()) != 0 {
		t.Fatal("exchanged after stop")
	}
}

func TestStopDuringSleepExitsCleanly(t *testing.T) {
	captureLog(t)
	sidecar, _, _ := newTestSidecar(t, ok(rpstFor("a", testNow, testNow+10800)))
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	start := time.Now()
	if code := sidecar.run(ctx); code != 0 { // real 5400s sleep cut short
		t.Fatalf("exit %d", code)
	}
	if time.Since(start) > 5*time.Second {
		t.Fatal("did not stop promptly")
	}
}

// ---- --check -------------------------------------------------------------------------

func writeOutputs(t *testing.T, cfg Config, rpst *string, withPEM bool) {
	t.Helper()
	if rpst != nil {
		mustWrite(t, filepath.Join(cfg.OutputDir, "rpst"), *rpst)
	}
	if withPEM {
		mustWrite(t, filepath.Join(cfg.OutputDir, "private.pem"), "pem")
	}
}

func ptr(s string) *string { return &s }

func TestCheckOK(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr(rpstFor("a", testNow, testNow+3600)), true)
	setNow(t, testNow)
	if problem := check(cfg); problem != "" {
		t.Fatal(problem)
	}
}

func TestCheckMissingRPST(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, nil, true)
	if problem := check(cfg); !strings.Contains(problem, "cannot read") {
		t.Fatal(problem)
	}
}

func TestCheckMissingPrivateKey(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr(rpstFor("a", testNow, testNow+3600)), false)
	if problem := check(cfg); !strings.Contains(problem, "private.pem does not exist") {
		t.Fatal(problem)
	}
}

func TestCheckPrivateKeyMustBeAFile(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr(rpstFor("a", testNow, testNow+3600)), false)
	mustMkdir(t, filepath.Join(cfg.OutputDir, "private.pem"))
	if problem := check(cfg); !strings.Contains(problem, "private.pem does not exist") {
		t.Fatal(problem)
	}
}

func TestCheckExpired(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr(rpstFor("a", testNow-100, testNow-10)), true)
	setNow(t, testNow)
	if problem := check(cfg); !strings.Contains(problem, "expires in -10s") {
		t.Fatal(problem)
	}
}

func TestCheckNearExpiry(t *testing.T) {
	for remaining, healthy := range map[float64]bool{30: false, 60: false, 61: true} {
		cfg := testConfig(t, "https://x")
		writeOutputs(t, cfg, ptr(rpstFor("a", testNow, testNow+remaining)), true)
		setNow(t, testNow)
		if got := check(cfg) == ""; got != healthy {
			t.Errorf("remaining %v: healthy=%v", remaining, got)
		}
	}
}

func TestCheckUnreadableToken(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr("garbage"), true)
	if problem := check(cfg); !strings.Contains(problem, "no usable exp") {
		t.Fatal(problem)
	}
}

func TestCheckUndecodableFile(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr("\xff\xfe"), true)
	if problem := check(cfg); !strings.Contains(problem, "cannot read") {
		t.Fatal(problem)
	}
}

func TestCheckToleratesSurroundingWhitespace(t *testing.T) {
	cfg := testConfig(t, "https://x")
	writeOutputs(t, cfg, ptr(rpstFor("a", testNow, testNow+3600)+"\n"), true)
	setNow(t, testNow)
	if problem := check(cfg); problem != "" {
		t.Fatal(problem)
	}
}

func TestNowIsWallClock(t *testing.T) {
	if d := now() - float64(time.Now().Unix()); d < -5 || d > 5 {
		t.Fatal(d)
	}
}
