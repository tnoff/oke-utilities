package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	defaultClientDir = "/var/run/rpst/client"
	defaultTokenFile = "/var/run/secrets/tokens/oci/token"
	defaultOutputDir = "/var/run/rpst/out"

	minExpMinutes = 15
	maxExpMinutes = 720
)

// Config is the sidecar's configuration; populated by configFromEnv.
type Config struct {
	// Identity domain endpoint, e.g. https://idcs-xxxx.identity.oraclecloud.com:443
	DomainURL string
	// Directory holding the exchange application's `client_id` and `client_secret` files.
	ClientDir string
	// Projected ServiceAccount token whose audience is the domain URL.
	TokenFile string
	// Shared memory-backed volume that receives `rpst` and `private.pem`.
	OutputDir string
	// Requested RPST lifetime, in minutes (OCI accepts 15..720).
	ExpMinutes int
	// Refresh once this fraction of the actual lifetime has elapsed.
	RefreshFraction float64
	// Time allowed for the token endpoint call.
	RequestTimeout time.Duration
	// Identity for logs (downward API).
	PodName            string
	PodNamespace       string
	ServiceAccountName string
}

// ConfigError means the environment does not describe a usable configuration.
type ConfigError struct{ msg string }

func (e *ConfigError) Error() string { return e.msg }

func configErrorf(format string, args ...any) error {
	return &ConfigError{fmt.Sprintf(format, args...)}
}

func getenv(name, fallback string) string {
	if value, ok := os.LookupEnv(name); ok {
		return value
	}
	return fallback
}

// configFromEnv builds the config from the environment; returns a *ConfigError when it is unusable.
func configFromEnv() (Config, error) {
	expRaw := getenv("RPST_EXP_MINUTES", "180")
	expMinutes, err := strconv.Atoi(strings.TrimSpace(expRaw))
	if err != nil {
		return Config{}, configErrorf("Not a number: RPST_EXP_MINUTES=%q", expRaw)
	}
	fractionRaw := getenv("REFRESH_FRACTION", "0.5")
	fraction, err := strconv.ParseFloat(strings.TrimSpace(fractionRaw), 64)
	if err != nil {
		return Config{}, configErrorf("Not a number: REFRESH_FRACTION=%q", fractionRaw)
	}
	cfg := Config{
		DomainURL:          strings.TrimRight(strings.TrimSpace(os.Getenv("RPST_DOMAIN_URL")), "/"),
		ClientDir:          getenv("RPST_CLIENT_DIR", defaultClientDir),
		TokenFile:          getenv("SERVICE_ACCOUNT_TOKEN_FILE", defaultTokenFile),
		OutputDir:          getenv("OUTPUT_DIR", defaultOutputDir),
		ExpMinutes:         expMinutes,
		RefreshFraction:    fraction,
		RequestTimeout:     30 * time.Second,
		PodName:            getenv("POD_NAME", "unknown"),
		PodNamespace:       getenv("POD_NAMESPACE", "unknown"),
		ServiceAccountName: getenv("SERVICE_ACCOUNT_NAME", "unknown"),
	}
	return cfg, cfg.validate()
}

func (c Config) validate() error {
	if c.DomainURL == "" {
		return configErrorf("RPST_DOMAIN_URL is not set")
	}
	if !strings.HasPrefix(strings.ToLower(c.DomainURL), "https://") {
		return configErrorf("RPST_DOMAIN_URL must be an https:// URL")
	}
	if c.ExpMinutes < minExpMinutes || c.ExpMinutes > maxExpMinutes {
		return configErrorf("RPST_EXP_MINUTES must be between %d and %d", minExpMinutes, maxExpMinutes)
	}
	// Written so NaN fails too.
	if !(c.RefreshFraction > 0 && c.RefreshFraction < 1) {
		return configErrorf("REFRESH_FRACTION must be greater than 0 and less than 1")
	}
	return nil
}
