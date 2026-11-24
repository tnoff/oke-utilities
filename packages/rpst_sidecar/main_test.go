package main

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestMain keeps log lines out of the test output; captureLog collects them where a test checks them.
func TestMain(m *testing.M) {
	logOut = io.Discard
	os.Exit(m.Run())
}

// envFor sets the environment equivalent to cfg and makes new sidecars use the fake's client.
func envFor(t *testing.T, cfg Config, fake *fakeDomain) {
	t.Helper()
	cleanEnv(t,
		"RPST_DOMAIN_URL", cfg.DomainURL, "RPST_CLIENT_DIR", cfg.ClientDir,
		"SERVICE_ACCOUNT_TOKEN_FILE", cfg.TokenFile, "OUTPUT_DIR", cfg.OutputDir,
	)
	if fake != nil {
		previous := clientFor
		clientFor = func(Config) *http.Client { return fake.client() }
		t.Cleanup(func() { clientFor = previous })
	}
}

func TestMainVersion(t *testing.T) {
	cleanEnv(t) // needs no configuration
	previous := version
	version = "1.2.3"
	t.Cleanup(func() { version = previous })
	var out bytes.Buffer
	if code := realMain(context.Background(), []string{"--version"}, &out); code != 0 || out.String() != "1.2.3\n" {
		t.Fatalf("exit %d, output %q", code, out.String())
	}
}

func TestMainCheckOK(t *testing.T) {
	captureLog(t)
	cfg := testConfig(t, "https://d.example")
	envFor(t, cfg, nil)
	writeOutputs(t, cfg, ptr(rpstFor("a", testNow, testNow+3600)), true)
	setNow(t, testNow)
	var out bytes.Buffer
	if code := realMain(context.Background(), []string{"--check"}, &out); code != 0 || out.Len() != 0 {
		t.Fatalf("exit %d, output %q", code, out.String())
	}
}

func TestMainCheckUnhealthyPrintsOneLine(t *testing.T) {
	captureLog(t)
	envFor(t, testConfig(t, "https://d.example"), nil)
	var out bytes.Buffer
	if code := realMain(context.Background(), []string{"--check"}, &out); code != 1 {
		t.Fatalf("exit %d", code)
	}
	if !strings.HasPrefix(out.String(), "unhealthy: ") || strings.Count(out.String(), "\n") != 1 {
		t.Fatalf("output %q", out.String())
	}
}

func TestMainAcceptsThePythonImageCommandLine(t *testing.T) {
	captureLog(t)
	envFor(t, testConfig(t, "https://d.example"), nil)
	var out bytes.Buffer
	if code := realMain(context.Background(), []string{"-m", "rpst_sidecar", "--check"}, &out); code != 1 {
		t.Fatalf("exit %d", code)
	}
	if !strings.HasPrefix(out.String(), "unhealthy: ") {
		t.Fatalf("output %q", out.String())
	}
}

func TestMainOnceSucceedsAfterOneExchange(t *testing.T) {
	logs := captureLog(t)
	fake := newFakeDomain(t, ok(rpstFor("a", testNow, testNow+3600)))
	envFor(t, testConfig(t, fake.server.URL), fake)
	if code := realMain(context.Background(), []string{"--once"}, &bytes.Buffer{}); code != 0 {
		t.Fatalf("exit %d", code)
	}
	if len(fake.calls()) != 1 || !strings.Contains(logs.String(), "rpst issued") {
		t.Fatalf("%d calls, log %q", len(fake.calls()), logs.String())
	}
}

func TestMainOnceFailsWithoutRetrying(t *testing.T) {
	logs := captureLog(t)
	fake := newFakeDomain(t, reply{status: 401, body: "aud is missing or invalid"})
	envFor(t, testConfig(t, fake.server.URL), fake)
	if code := realMain(context.Background(), []string{"--once"}, &bytes.Buffer{}); code != 1 {
		t.Fatalf("exit %d", code)
	}
	if len(fake.calls()) != 1 || !strings.Contains(logs.String(), "RPST exchange failed") {
		t.Fatalf("%d calls, log %q", len(fake.calls()), logs.String())
	}
}

func TestMainOnceFailsWhenOutputUnwritable(t *testing.T) {
	logs := captureLog(t)
	fake := newFakeDomain(t, ok(rpstFor("a", 1, 2)))
	envFor(t, testConfig(t, fake.server.URL), fake)
	recordWrites(t, os.ErrPermission)
	if code := realMain(context.Background(), []string{"--once"}, &bytes.Buffer{}); code != 1 {
		t.Fatalf("exit %d", code)
	}
	if !strings.Contains(logs.String(), "RPST exchange failed") {
		t.Fatalf("log %q", logs.String())
	}
}

func TestMainBadConfig(t *testing.T) {
	logs := captureLog(t)
	cleanEnv(t)
	if code := realMain(context.Background(), nil, &bytes.Buffer{}); code != 1 {
		t.Fatalf("exit %d", code)
	}
	if !strings.Contains(logs.String(), " - ERROR - Bad configuration") {
		t.Fatalf("log %q", logs.String())
	}
}

func TestMainHTTPDomainRejected(t *testing.T) {
	captureLog(t)
	cleanEnv(t, "RPST_DOMAIN_URL", "http://insecure.example")
	for _, args := range [][]string{nil, {"--check"}, {"--once"}} {
		if code := realMain(context.Background(), args, &bytes.Buffer{}); code != 1 {
			t.Fatalf("%v: exit %d", args, code)
		}
	}
}

func TestMainUsageErrors(t *testing.T) {
	captureLog(t)
	cleanEnv(t, "RPST_DOMAIN_URL", "https://d.example")
	for _, args := range [][]string{{"--bogus"}, {"extra"}, {"-m", "something_else"}} {
		if code := realMain(context.Background(), args, &bytes.Buffer{}); code != 2 {
			t.Fatalf("%v: exit %d", args, code)
		}
	}
}

func TestMainRunsUntilCancelled(t *testing.T) {
	captureLog(t)
	fake := newFakeDomain(t, ok(rpstFor("a", testNow, testNow+10800)))
	cfg := testConfig(t, fake.server.URL)
	envFor(t, cfg, fake)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan int)
	go func() { done <- realMain(ctx, nil, &bytes.Buffer{}) }()
	rpst := filepath.Join(cfg.OutputDir, "rpst")
	for deadline := time.Now().Add(20 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		if _, err := os.Stat(rpst); err == nil {
			break
		}
	}
	cancel() // the sidecar is now in its 5400s sleep
	select {
	case code := <-done:
		if code != 0 {
			t.Fatalf("exit %d", code)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("did not stop")
	}
	if _, err := os.Stat(rpst); err != nil {
		t.Fatal(err)
	}
}

// The real binary, its real signal handling: SIGTERM and SIGINT mid-backoff exit 0.
func TestSignalsStopTheProcess(t *testing.T) {
	if os.Getenv("RPST_SIDECAR_HELPER") == "1" {
		logOut = os.Stdout
		os.Args = []string{"rpst-sidecar"}
		main()
		return
	}
	for _, sig := range []syscall.Signal{syscall.SIGTERM, syscall.SIGINT} {
		t.Run(sig.String(), func(t *testing.T) {
			cfg := testConfig(t, "https://127.0.0.1:1") // refused: the sidecar sits in backoff
			cmd := exec.Command(os.Args[0], "-test.run=^TestSignalsStopTheProcess$")
			cmd.Env = append(os.Environ(),
				"RPST_SIDECAR_HELPER=1", "RPST_DOMAIN_URL="+cfg.DomainURL, "RPST_CLIENT_DIR="+cfg.ClientDir,
				"SERVICE_ACCOUNT_TOKEN_FILE="+cfg.TokenFile, "OUTPUT_DIR="+cfg.OutputDir)
			out := &syncBuffer{}
			cmd.Stdout = out
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(10 * time.Second)
			for !strings.Contains(out.String(), "retrying in") && time.Now().Before(deadline) {
				time.Sleep(20 * time.Millisecond)
			}
			if err := cmd.Process.Signal(sig); err != nil {
				t.Fatal(err)
			}
			if err := cmd.Wait(); err != nil {
				t.Fatalf("%v\n%s", err, out.String())
			}
			if !strings.Contains(out.String(), " - INFO - Stopping") {
				t.Fatalf("output %q", out.String())
			}
		})
	}
}
