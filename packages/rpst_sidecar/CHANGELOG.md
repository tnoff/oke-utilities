# Changelog

All notable changes to `rpst-sidecar` (`packages/rpst_sidecar`) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-25

### Changed

- Removed the `python` link and the `-m rpst_sidecar` argument shim: `python -m rpst_sidecar ...` no longer works, call `rpst-sidecar ...` (every manifest in docker-apps already does).

## [0.2.0] - 2026-11-24

### Changed

- Rewritten in Go (standard library only) on a distroless image, replacing Python and `cryptography`. Env vars, output files, `--check`/`--once`, exit codes and log lines are unchanged. Redirects from the token endpoint are no longer followed. `python -m rpst_sidecar ...` still works through a `python` link to the binary until manifests move to `rpst-sidecar ...`. New `--version` flag.

## [0.1.0] - 2026-11-21

### Changed

- Versioned and tagged on its own (`rpst_sidecar-v0.1.0`). Earlier changes shipped under the
  repo-wide version; see the root `CHANGELOG.md` (through 0.10.19).
