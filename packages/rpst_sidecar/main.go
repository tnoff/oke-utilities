// Command rpst-sidecar trades a projected ServiceAccount token for an OCI resource principal
// session token (RPST) on a loop.
//
// It runs forever beside the main container: exchange the token, write it and its private key
// to a shared memory-backed volume, sleep, repeat (tnoff/terraform#191). `--check` is the exec
// probe and `--once` a single-exchange canary.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

var (
	logOut io.Writer = os.Stdout
	logMu  sync.Mutex
)

// The same line shape the Python sidecar's logging.basicConfig produced, so log queries keep matching.
func logLine(level, format string, args ...any) {
	logMu.Lock()
	defer logMu.Unlock()
	stamp := time.Now().Format("2006-01-02 15:04:05.000")
	stamp = strings.Replace(stamp, ".", ",", 1)
	fmt.Fprintf(logOut, "%s - rpst_sidecar.main - %s - %s\n", stamp, level, fmt.Sprintf(format, args...))
}

func logInfo(format string, args ...any)    { logLine("INFO", format, args...) }
func logWarning(format string, args ...any) { logLine("WARNING", format, args...) }
func logError(format string, args ...any)   { logLine("ERROR", format, args...) }

// legacyArgs drops a leading `-m rpst_sidecar`. The image links `python` to this binary so probes
// and commands written for the Python image (`python -m rpst_sidecar --check`) keep working until
// the manifests that use them are updated.
func legacyArgs(args []string) []string {
	if len(args) >= 2 && args[0] == "-m" && args[1] == "rpst_sidecar" {
		return args[2:]
	}
	return args
}

// realMain runs the sidecar, or with --check, probes the files it wrote. Returns the exit code.
func realMain(ctx context.Context, args []string, stdout io.Writer) int {
	flags := flag.NewFlagSet("rpst-sidecar", flag.ContinueOnError)
	checkFlag := flags.Bool("check", false, "exit 0 only if a fresh RPST is on disk")
	onceFlag := flags.Bool("once", false,
		"do one token exchange and exit 0 on success, 1 on failure (a canary for the whole chain)")
	if err := flags.Parse(legacyArgs(args)); err != nil {
		if err == flag.ErrHelp {
			return 0
		}
		return 2
	}
	if flags.NArg() > 0 {
		fmt.Fprintf(flags.Output(), "unexpected arguments: %s\n", strings.Join(flags.Args(), " "))
		return 2
	}

	cfg, err := configFromEnv()
	if err != nil {
		logError("Bad configuration: %v", err)
		return 1
	}

	if *checkFlag {
		if problem := check(cfg); problem != "" {
			fmt.Fprintf(stdout, "unhealthy: %s\n", problem)
			return 1
		}
		return 0
	}

	sidecar, err := newSidecar(cfg, nil, nil)
	if err != nil {
		logError("Cannot generate a key: %v", err)
		return 1
	}

	if *onceFlag {
		if _, err := sidecar.refreshOnce(); err != nil {
			logError("RPST exchange failed: %v", err)
			return 1
		}
		return 0
	}

	logInfo("Initializing RPST sidecar for %s/%s", cfg.PodNamespace, cfg.PodName)
	return sidecar.run(ctx)
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	code := realMain(ctx, os.Args[1:], os.Stdout)
	stop()
	os.Exit(code)
}
