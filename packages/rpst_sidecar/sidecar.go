package main

import (
	"context"
	"crypto/rsa"
	"fmt"
	"math"
	"math/rand/v2"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	rpstFile       = "rpst"
	privateKeyFile = "private.pem"
	fileMode       = 0o640

	backoffStart = 2.0
	backoffCap   = 120.0

	// --check fails when the token has less than this many seconds left.
	checkMinRemaining = 60
)

// Seams for tests.
var (
	now    = func() float64 { return float64(time.Now().UnixNano()) / 1e9 }
	fsync  = func(f *os.File) error { return f.Sync() }
	writer = writeAtomic
	// The HTTP client a sidecar gets when none is passed in.
	clientFor = newHTTPClient
)

// defaultJitter spreads retries over the upper half of the backoff window.
func defaultJitter(delay float64) float64 {
	return delay * (0.5 + 0.5*rand.Float64()) // not security: scheduling jitter
}

// sleepCtx waits for `seconds` or until ctx is done.
func sleepCtx(ctx context.Context, seconds float64) {
	timer := time.NewTimer(time.Duration(seconds * float64(time.Second)))
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}

// writeAtomic writes `data` so a reader sees the old file or the new one, never a partial one.
func writeAtomic(path string, data []byte, mode os.FileMode) (err error) {
	handle, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".")
	if err != nil {
		return err
	}
	tmp := handle.Name()
	defer func() {
		if err != nil {
			handle.Close()
			os.Remove(tmp)
		}
	}()
	if err = handle.Chmod(mode); err != nil {
		return err
	}
	if _, err = handle.Write(data); err != nil {
		return err
	}
	if err = fsync(handle); err != nil {
		return err
	}
	if err = handle.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// iso is UTC ISO-8601, or "unknown".
func iso(timestamp float64, ok bool) string {
	if !ok {
		return "unknown"
	}
	sec, frac := math.Modf(timestamp)
	return time.Unix(int64(sec), int64(frac*1e9)).UTC().Format("2006-01-02T15:04:05Z")
}

// number is a claim as a float64; JSON booleans, strings and nulls are not numbers.
func number(claims map[string]any, name string) (float64, bool) {
	value, ok := claims[name].(float64)
	return value, ok
}

func claimString(claims map[string]any, name string) string {
	value, ok := claims[name]
	if !ok || value == nil {
		return "unknown"
	}
	return fmt.Sprint(value)
}

// lifetimeSeconds is the RPST's own exp - iat; the requested lifetime when they are unusable.
func lifetimeSeconds(claims map[string]any, fallbackMinutes int) float64 {
	iat, iatOK := number(claims, "iat")
	exp, expOK := number(claims, "exp")
	if iatOK && expOK && exp > iat {
		return exp - iat
	}
	return float64(fallbackMinutes) * 60
}

// refreshDelay is the seconds to wait after a successful exchange before the next one.
func refreshDelay(claims map[string]any, cfg Config) float64 {
	return lifetimeSeconds(claims, cfg.ExpMinutes) * cfg.RefreshFraction
}

// Sidecar is one process's worth of state: the config, the one key, and whether it is on disk yet.
type Sidecar struct {
	cfg        Config
	key        *rsa.PrivateKey
	client     *http.Client
	keyWritten bool
	jitter     func(float64) float64
	sleep      func(context.Context, float64)
}

func newSidecar(cfg Config, key *rsa.PrivateKey, client *http.Client) (*Sidecar, error) {
	if key == nil {
		var err error
		if key, err = generateKey(); err != nil {
			return nil, err
		}
	}
	if client == nil {
		client = clientFor(cfg)
	}
	return &Sidecar{cfg: cfg, key: key, client: client, jitter: defaultJitter, sleep: sleepCtx}, nil
}

// refreshOnce does one exchange and write, and returns seconds until the next refresh.
//
// Fails with an *ExchangeError on an exchange failure, or a filesystem error if the volume is not writable.
func (s *Sidecar) refreshOnce() (float64, error) {
	rpst, saToken, err := exchange(s.cfg, s.key, s.client)
	if err != nil {
		return 0, err
	}
	if !s.keyWritten {
		// Once, and before the first rpst, so a reader never sees a token without its key.
		keyPEM, err := privatePEM(s.key)
		if err != nil {
			return 0, err
		}
		if err := writer(filepath.Join(s.cfg.OutputDir, privateKeyFile), keyPEM, fileMode); err != nil {
			return 0, err
		}
		s.keyWritten = true
	}
	// No trailing newline: the OCI SDK fails with "Invalid crypto padding" otherwise.
	if err := writer(filepath.Join(s.cfg.OutputDir, rpstFile), []byte(rpst), fileMode); err != nil {
		return 0, err
	}
	claims := jwtClaims(rpst)
	s.audit(claims, jwtClaims(saToken))
	return refreshDelay(claims, s.cfg), nil
}

// audit logs one INFO line per successful exchange: which workload got which token. No secrets.
func (s *Sidecar) audit(rpstClaims, saClaims map[string]any) {
	iat, iatOK := number(rpstClaims, "iat")
	exp, expOK := number(rpstClaims, "exp")
	lifetime := "unknown"
	if iatOK && expOK {
		lifetime = fmt.Sprintf("%.1f", (exp-iat)/60)
	}
	logInfo(
		"rpst issued jti=%s sub=%s namespace=%s pod=%s service_account=%s issued=%s expires=%s lifetime_minutes=%s",
		claimString(rpstClaims, "jti"),
		claimString(saClaims, "sub"),
		s.cfg.PodNamespace,
		s.cfg.PodName,
		s.cfg.ServiceAccountName,
		iso(iat, iatOK),
		iso(exp, expOK),
		lifetime,
	)
}

// run refreshes until ctx is done. Failures are retried forever with backoff.
func (s *Sidecar) run(ctx context.Context) int {
	failures := 0
	for ctx.Err() == nil {
		delay, err := s.refreshOnce()
		if err != nil {
			failures++
			delay = s.jitter(math.Min(backoffCap, backoffStart*math.Pow(2, float64(failures-1))))
			logWarning("RPST exchange failed (attempt %d), retrying in %.1fs: %v", failures, delay, err)
		} else {
			failures = 0
			logInfo("Next refresh in %.0fs", delay)
		}
		s.sleep(ctx, delay)
	}
	logInfo("Stopping")
	return 0
}

// check says why the output is not healthy, or "" when it is.
func check(cfg Config) string {
	rpstPath := filepath.Join(cfg.OutputDir, rpstFile)
	keyPath := filepath.Join(cfg.OutputDir, privateKeyFile)
	data, err := os.ReadFile(rpstPath)
	if err != nil {
		return fmt.Sprintf("cannot read %s: %v", rpstPath, err)
	}
	if !isASCII(data) {
		return fmt.Sprintf("cannot read %s: not ASCII", rpstPath)
	}
	if info, err := os.Stat(keyPath); err != nil || !info.Mode().IsRegular() {
		return fmt.Sprintf("%s does not exist", keyPath)
	}
	exp, ok := number(jwtClaims(strings.TrimSpace(string(data))), "exp")
	if !ok {
		return "rpst has no usable exp claim"
	}
	remaining := exp - now()
	if remaining <= checkMinRemaining {
		return fmt.Sprintf("rpst expires in %.0fs (needs more than %ds)", remaining, checkMinRemaining)
	}
	return ""
}

func isASCII(data []byte) bool {
	for _, b := range data {
		if b > 0x7f {
			return false
		}
	}
	return true
}
