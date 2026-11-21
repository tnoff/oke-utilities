package main

import (
	"errors"
	"strings"
	"testing"
)

var envVars = []string{
	"RPST_DOMAIN_URL", "RPST_CLIENT_DIR", "SERVICE_ACCOUNT_TOKEN_FILE", "OUTPUT_DIR",
	"RPST_EXP_MINUTES", "REFRESH_FRACTION", "POD_NAME", "POD_NAMESPACE", "SERVICE_ACCOUNT_NAME",
}

// cleanEnv starts the test with none of the sidecar's variables set, then sets `pairs`.
func cleanEnv(t *testing.T, pairs ...string) {
	t.Helper()
	for _, name := range envVars {
		t.Setenv(name, "")
	}
	for _, name := range envVars {
		unsetEnv(t, name)
	}
	for i := 0; i < len(pairs); i += 2 {
		t.Setenv(pairs[i], pairs[i+1])
	}
}

func expectConfigError(t *testing.T, contains string) {
	t.Helper()
	_, err := configFromEnv()
	var cfgErr *ConfigError
	if !errors.As(err, &cfgErr) {
		t.Fatalf("want a ConfigError, got %v", err)
	}
	if !strings.Contains(err.Error(), contains) {
		t.Fatalf("error %q does not mention %q", err, contains)
	}
}

func TestConfigDefaults(t *testing.T) {
	cleanEnv(t, "RPST_DOMAIN_URL", " https://d.example:443/ ")
	cfg, err := configFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	want := Config{
		DomainURL: "https://d.example:443", ClientDir: "/var/run/rpst/client",
		TokenFile: "/var/run/secrets/tokens/oci/token", OutputDir: "/var/run/rpst/out",
		ExpMinutes: 180, RefreshFraction: 0.5, RequestTimeout: 30e9,
		PodName: "unknown", PodNamespace: "unknown", ServiceAccountName: "unknown",
	}
	if cfg != want {
		t.Fatalf("got %+v\nwant %+v", cfg, want)
	}
}

func TestConfigOverrides(t *testing.T) {
	cleanEnv(t,
		"RPST_DOMAIN_URL", "https://d.example", "RPST_CLIENT_DIR", "/c", "SERVICE_ACCOUNT_TOKEN_FILE", "/t",
		"OUTPUT_DIR", "/o", "RPST_EXP_MINUTES", "60", "REFRESH_FRACTION", "0.8",
		"POD_NAME", "p", "POD_NAMESPACE", "n", "SERVICE_ACCOUNT_NAME", "sa",
	)
	cfg, err := configFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ClientDir != "/c" || cfg.TokenFile != "/t" || cfg.OutputDir != "/o" {
		t.Fatalf("paths: %+v", cfg)
	}
	if cfg.ExpMinutes != 60 || cfg.RefreshFraction != 0.8 {
		t.Fatalf("numbers: %+v", cfg)
	}
	if cfg.PodName != "p" || cfg.PodNamespace != "n" || cfg.ServiceAccountName != "sa" {
		t.Fatalf("identity: %+v", cfg)
	}
}

func TestConfigDomainRequired(t *testing.T) {
	cleanEnv(t)
	expectConfigError(t, "not set")
}

func TestConfigDomainMustBeHTTPS(t *testing.T) {
	for _, u := range []string{"http://d.example", "ftp://d.example", "d.example"} {
		t.Run(u, func(t *testing.T) {
			cleanEnv(t, "RPST_DOMAIN_URL", u)
			expectConfigError(t, "https")
		})
	}
}

func TestConfigDomainSchemeIsCaseInsensitive(t *testing.T) {
	cleanEnv(t, "RPST_DOMAIN_URL", "HTTPS://d.example")
	if _, err := configFromEnv(); err != nil {
		t.Fatal(err)
	}
}

func TestConfigExpMinutesRange(t *testing.T) {
	for _, minutes := range []string{"14", "721", "0", "-5"} {
		t.Run(minutes, func(t *testing.T) {
			cleanEnv(t, "RPST_DOMAIN_URL", "https://d.example", "RPST_EXP_MINUTES", minutes)
			expectConfigError(t, "RPST_EXP_MINUTES")
		})
	}
}

func TestConfigExpMinutesBoundsAllowed(t *testing.T) {
	for minutes, want := range map[string]int{"15": 15, "720": 720} {
		t.Run(minutes, func(t *testing.T) {
			cleanEnv(t, "RPST_DOMAIN_URL", "https://d.example", "RPST_EXP_MINUTES", minutes)
			cfg, err := configFromEnv()
			if err != nil || cfg.ExpMinutes != want {
				t.Fatalf("got %v, %v", cfg.ExpMinutes, err)
			}
		})
	}
}

func TestConfigRefreshFractionRange(t *testing.T) {
	for _, fraction := range []string{"0", "1", "1.5", "-0.1", "NaN", "inf"} {
		t.Run(fraction, func(t *testing.T) {
			cleanEnv(t, "RPST_DOMAIN_URL", "https://d.example", "REFRESH_FRACTION", fraction)
			expectConfigError(t, "REFRESH_FRACTION")
		})
	}
}

func TestConfigNonNumeric(t *testing.T) {
	for _, name := range []string{"RPST_EXP_MINUTES", "REFRESH_FRACTION"} {
		t.Run(name, func(t *testing.T) {
			cleanEnv(t, "RPST_DOMAIN_URL", "https://d.example", name, "abc")
			expectConfigError(t, "Not a number")
		})
	}
}
