# Changelog

All notable changes to the OKE Security Scanner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.1] - 2026-10-07

### Changed

- Bumped opentelemetry-sdk to v1.45.1

## [0.7.0] - 2026-09-28

### Changed

- OCIR cleanup now ships as its own installable package (`packages/ocir_cleanup/`) with its own image, built from `packages/ocir_cleanup/Dockerfile`. No Trivy binary at all — measured 658.0 MB vs 810.8 MB for the old combined image.
- The scan image itself also shrank, from 810.8 MB to 393.4 MB, now that `oci` (and the `build-essential` compile stage its `crc32c` transitive dep needed) left with cleanup.
- Extracted `Image`, `KubernetesClient` and the low-level Discord webhook client into `packages/core` (`oke-scanner-core`), used by both the scan and cleanup images. Generic OTel setup/teardown moved there too, behind a new `oke-scanner-core[telemetry]` extra so `secret_age` (which has no OpenTelemetry setup at all) stays free of the SDK.

## [0.6.0] - 2026-09-27

### Changed

- secret-age-tracker now ships as its own installable package (`packages/secret_age/`) with its own image, built from `packages/secret_age/Dockerfile`. Its pod no longer pulls the Trivy binary or the OpenTelemetry SDK/exporters — measured 810.8 MB (combined image) vs 630.5 MB (dedicated).
- Extracted the shared k8s in-cluster/kubeconfig-fallback auth bootstrap into a new minimal `packages/core/` package (`oke-scanner-core`), used by `packages/secret_age`.

## [0.5.18] - 2026-09-03

### Changed

- Bumped oci to v2.185.0

## [0.5.17] - 2026-08-19

### Changed

- OCIR cleanup now fails safe when a digest does not resolve: a repo with an unprotectable surviving tag is skipped entirely, and a delete candidate with no digest of its own is never pruned.

## [0.5.16] - 2026-08-13

### Changed

- Bumped oci to v2.184.1

## [0.5.15] - 2026-08-06

### Changed

- Bumped oci to v2.184.0

## [0.5.14] - 2026-07-30

### Changed

- Bumped python-gitlab to v8.5.0

## [0.5.13] - 2026-07-30

### Changed

- Bumped oci to v2.183.0

## [0.5.12] - 2026-07-23

### Changed

- Bumped oci to v2.182.1

## [0.5.11] - 2026-07-17

### Changed

- Bumped opentelemetry-sdk to v1.44.0

## [0.5.10] - 2026-07-17

### Changed

- Bumped kubernetes to v36.0.3

## [0.5.9] - 2026-07-17

### Changed

- pyproject.toml now reads its version dynamically from the VERSION file

## [0.5.8] - 2026-07-17

### Changed

- Bumped oci to v2.182.0

## [0.5.7] - 2026-07-09

### Changed

- Bumped oci to v2.181.1

## [0.5.6] - 2026-07-04

### Fixed

- Extended the shared-digest cleanup guard to protect the digest of *every* tag
  that survives the run, not just the keep-window/deployed tags. The 0.5.4 fix
  missed `CLEANUP_PROTECT_TAGS_REGEX` channel tags: a byte-identical
  `ci-base-images:3.13-<sha>` rebuild shared `:3.13`'s digest, sorted "old" (OCIR
  dates a container image by when the digest first appeared), and was pruned by
  OCID — destroying the manifest `:3.13` still pointed at and breaking every
  runner on that image (`manifest unknown`). A candidate is now deleted only when
  no surviving tag shares its digest.

## [0.5.5] - 2026-07-04

### Changed

- Bumped python-gitlab to v8

## [0.5.4] - 2026-07-04

### Fixed

- OCIR cleanup no longer deletes a tag that shares its manifest digest with a
  kept/deployed tag. A byte-identical rebuild produces a commit-hash tag that
  resolves to the same digest as `:latest`; because OCIR dates a container image
  by when the digest first appeared, the fresh tag sorted "old" and was pruned by
  OCID, destroying the shared manifest and breaking `:latest`
  (`ImagePullBackOff: manifest unknown` on every runner using that image). Each
  kept image's own digest is now protected, not just its sub-manifests.

## [0.5.3] - 2026-07-03

### Changed

- Bumped opentelemetry-sdk to v1.43.0

## [0.5.2] - 2026-07-02

### Changed

- Bumped oci to v2.181.0
- Bumped opentelemetry-sdk to v1.43.0

## [0.5.1] - 2026-06-28

### Changed

- Bumped oci to v2.180.0

## [0.5.0] - 2026-06-16

### Added

- Secret-age tracker: full-detail CSV attachment on the weekly Discord
  report (`YYYY-MM-DD.secret-ages.csv`). The monospace tables truncate
  identifiers to 40 chars and drop `rotation_command`/`notes`/`last_rotated`
  and the OK rows; the CSV carries one row per tracked secret with every
  `Finding` field across all severities, sent via the same multipart
  `_send_file` the vuln scanner uses.
- Secret-age tracker: provider-enforced expiry surfacing. Any k8s Secret
  carrying a `secret-age-tracker.tnoff/expires-at` annotation now also
  yields a days-to-expiry finding (WARN inside the window, ROTATE once
  past) alongside its rotation-age finding — fail-closed for credentials
  with a hard expiry (e.g. the Flux HTTPS deploy token).

### Removed

- Secret-age tracker: dropped the fabricated per-credential rotation-command
  hints from the OCI IAM reader. Most of those identities are
  terraform-managed, so the synthesized `oci iam …` / `terraform apply
  -replace=…` strings were misleading; rotation steps live in
  `runbooks/secret-rotation.md` (the single source of truth). OCI findings
  now carry no `rotation_command`.

## [0.4.0] - 2026-06-15

### Added

- `DISCORD_CLEANUP_WEBHOOK_URL`: optional separate webhook for cleanup
  chatter (`send_cleanup_recommendations` + `send_deletion_results`). When
  unset, cleanup falls back to `DISCORD_WEBHOOK_URL` so existing deploys
  keep working unchanged. Lets the daily Trivy scan and the per-push /
  Sunday OCIR cleanup output land in separate Discord channels. The
  secret-age tracker is a separate entrypoint and routes via its own
  `DISCORD_WEBHOOK_URL` value set at the manifest level — no scanner-side
  change needed there.

## [0.3.1] - 2026-06-14

### Changed

- Bumped oci to v2.178.0

## [0.3.0] - 2026-06-07

### Added

- `CLEANUP_PROTECT_TAGS_REGEX`: tags whose name fully matches are excluded
  from the deletion candidate pool entirely, regardless of `keep_count` or
  creation date. Lets per-repo cleanup CronJobs protect mutable "channel"
  tags that get overwritten on every build (e.g. ci-base-images'
  `:3.11/:3.12/:3.13/:3.14`).
- `CLEANUP_GROUP_BY_REGEX`: when set, the candidate pool is grouped by the
  regex's first capture group and `keep_count` is applied per group. Heavy
  churn in one group (e.g. lots of `:3.14-<sha>` rebuilds) can no longer
  push other groups' tags out of the keep window. Empty (the default)
  preserves the original "newest N across the whole repo" behavior.
- Both knobs are passed through `Config` and validated at load time
  (invalid regex → fail fast; group regex without a capture group → fail
  fast).

## [0.2.11] - 2026-06-03

### Changed

- Bumped oci to v2.177.0

## [0.2.10] - 2026-06-02

### Changed

- Bumped kubernetes to v36.0.2

## [0.2.9] - 2026-05-28

### Fixed

- `RegistryClient.get_old_ocir_images` / `get_orphaned_manifests` unconditionally
  added a synthetic `:latest` Image for every `extra_repositories` entry into the
  same scan set as discovered pod images. When `CLEANUP_REPO` matched a real
  deployed image, the resulting two-entry set was iterated in non-deterministic
  order; if the synthetic visited first, the deployed tag wasn't excluded by the
  `im.full_name != image.full_name` filter, fell into `filtered_images`, and got
  selected for deletion once enough newer tags existed — then `repo_names_processed`
  blocked the second iteration that would have protected it. Producers like
  `cleanup-magic-mirror` could silently delete the deployed image's tag,
  surfacing later as `ImagePullBackOff: manifest unknown` on the next pod
  restart. Extras now skip any repo already represented by a real OCIR image.

## [0.2.8] - 2026-05-27

### Changed

- Bumped kubernetes to v36.0.1

## [0.2.7] - 2026-05-27

### Changed

- Bumped oci to v2.176.0
- Bumped kubernetes to v36.0.1

## [0.2.6] - 2026-05-25

### Fixed

- `DiscordNotifier.send_deletion_results` used to fall through to a generic
  `## No images deleted` header when the deletion list was empty, dropping the
  `is_orphaned` distinction. Back-to-back webhooks from the two cleanup phases
  (old tags + orphan platform manifests) could then read like a contradiction
  in Discord — one message says "Images Deleted" with a list, the next says
  "No images deleted". Empty results now preserve the phase label
  (`## No Orphan Intermediate Images Deleted` vs `## No Images Deleted`).

### Changed

- `RegistryClient.delete_ocir_images` now logs each delete (and each already-
  absent skip) at INFO level so pod logs are self-contained instead of relying
  on the Discord notification to know what happened.
- `RegistryClient._get_manifest_list_sub_digests` now logs manifest-fetch
  failures at INFO rather than DEBUG. The previous default-DEBUG made auth /
  network problems against the registry's v2 API invisible in normal runs.
- `RegistryClient.get_orphaned_manifests` reworded the safety-skip log:
  `Could not resolve manifest lists for X` → `No referenced sub-manifests
  found for X across N normal tags (likely all single-platform)`. The old
  wording implied a failure; the common case is just that a repo's normal
  tags are single-platform images with no manifest list to enumerate, in
  which case there's nothing to compare orphan platform manifests against.

## [0.2.5] - 2026-05-22

### Fixed

- In-cluster auth broken after the `kubernetes` 35 → 36 bump (`27560cf`): v36's
  `load_incluster_config()` stores the bearer token under
  `Configuration.api_key['authorization']`, but the generated API methods look
  it up under the `BearerToken` security-scheme key, so no `Authorization`
  header was sent and every call 401'd. `KubernetesClient.__init__` now mirrors
  the value across both keys after loading config.

## [0.2.4] - 2026-05-22

### Changed

- Bumped opentelemetry-sdk to v1.42.1

## [0.2.3] - 2026-05-21

### Changed

- Bumped kubernetes to v36

## [0.2.2] - 2026-05-20

### Changed

- Bumped opentelemetry-sdk to v1.42.0

## [0.2.1] - 2026-05-19

### Changed

- Bumped oci to v2.175.0

## [0.2.0] - 2026-05-18

### Added
- `ENABLE_SCAN` and `ENABLE_CLEANUP` env vars (both default `true`) gating the Trivy scan and OCIR cleanup phases independently. Producer pipelines that fire a one-off Job after pushing a new image set `ENABLE_SCAN=false` so only cleanup runs, without waiting for the daily cron.
- `CLEANUP_REPO` env var that scopes the cleanup phase to a single OCIR repo (namespace-qualified, e.g. `tnoff/discord_bot`). Unset, cleanup sweeps every deployed image.
- `k8s/rbac-cleanup-trigger.yaml` — Role + RoleBinding scoped to `default` namespace granting a CI ServiceAccount permission to read the `security-scanner` CronJob and create Jobs from it.
- Config validation: fails fast if both phases are disabled, or if `CLEANUP_REPO` is set while `ENABLE_CLEANUP=false`.

### Changed
- `main()` reshaped into two phase calls (`run_scan` / `run_cleanup`); the scan phase passes its discovered image set to cleanup so both phases share one k8s pod listing.

## [0.1.0] - 2026-05-15

This release refocuses the scanner on its two surviving features — **Trivy vulnerability scanning** and **OCIR cleanup** (old tags + orphaned platform manifests) — and removes everything else. It also slims the Docker image, drops OTLP tracing, migrates to the latest `dappertable`, and brings test coverage to 100%.

### Added
- `.dockerignore` so the build context excludes `venv/`, `.tox/`, `tests/`, `.git/`, coverage artifacts, etc.
- `.gitattributes` for consistent LF line endings, Python-aware diffs, and `export-ignore` rules so `git archive` / GitLab release tarballs skip CI/dev-only files (`tests/`, `.gitlab-ci.yml`, `AGENTS.md`, etc.).
- Three-Secret deployment shape (`security-scanner-config`, `security-scanner-oci-config`, `security-scanner-docker-config`) documented in `k8s/secret-example.yaml`.

### Changed
- `Dockerfile` rewritten as a two-stage build; the final image no longer carries `curl`, `wget`, `tar`, or `git`, and skips the pre-baked Trivy DB (it's downloaded on first run).
- `dappertable` dependency switched from `git+https://github.com/tnoff/dappertable.git@v0.2.4` to the v1.1.4 tarball from GitLab, removing the build-time `git` requirement.
- `discord_notifier` migrated to the dappertable 1.1.x API: `Column` / `Columns` replace `DapperTableHeader` / `DapperTableHeaderOptions`, `header_options=` becomes `columns=`, `.print()` becomes `.render()`, and `.size` becomes `len(table)`.
- `k8s/cronjob.yaml` no longer references the dead `OCI_REGISTRY` / `OCI_USERNAME` / `OCI_TOKEN` / `OCI_NAMESPACE` env vars; the manifest now mounts `~/.oci/config` and `~/.docker/config.json` from dedicated read-only Secrets.
- Pylint configuration moved from `.pylintrc` into `[tool.pylint.*]` tables in `pyproject.toml`.
- Dropped the unused `self.apps_v1` attribute from `KubernetesClient`.
- README rewritten to match the slimmed feature set and the new Kubernetes deployment shape.
- Test suite expanded to **100% line coverage** across all `src/` modules (previously 85%).

### Fixed
- `main()` now actually flushes the `MeterProvider` on shutdown (previous code never wired the meter provider into the `finally` block, so metrics were never flushed).
- `delete_ocir_images` now returns `[]` (matching its `list[Image]` return type) instead of `{}` when the OCI SDK client is unavailable.
- `OCIR_EXTRA_REPOSITORIES` parsing no longer yields `[""]` when the env var is unset/empty (now correctly an empty list); fixed a latent code path that constructed nonsensical `Image` instances downstream.
- Removed the duplicate "Trivy database updated successfully" log line in `main()` (the scanner itself already logs it).

### Removed
- Image update report (`check_image_updates`) and the Docker Hub / registry tag-listing helpers it depended on.
- OKE node image check (`OKE_IMAGE_CHECK_ENABLED`, `OKE_CLUSTER_OCID`, `OKE_REGION`) and `src/oke_client.py`.
- `ImageVersion` parsing and `Image.version` / semver comparison from `src/k8s_client.py`.
- Discord notifications `send_version_update_info` and `send_node_image_report`.
- OTLP tracing (`OTLP_TRACES_ENABLED` env var, `TracerProvider` / span exporter setup, and every `tracer.start_as_current_span` wrapper across the codebase); logs and metrics are still exported via OTLP.

## [0.0.11] - 2026-05-13

### Changed

- Bumped oci to v2.174.0

## [0.0.8] - 2026-04-30

### Added
- `pyproject.toml` replacing `requirements.txt` and `tests/requirements.txt`
  - Main dependencies under `[project.dependencies]`
  - Dev/test/lint dependencies under `[project.optional-dependencies].dev`
- `bandit` security linting added to tox suite (`tox`, `tox -e bandit`)
- `renovate.json` with pip, Dockerfile, and GitLab CI managers
  - Dev dependencies automerge on major/minor/patch
  - Main dependencies automerge on patch only
- `DEVELOPMENT.md` with local setup instructions and full environment variable reference

### Changed
- `tox.ini` migrated from `-r requirements.txt` deps to pyproject `extras = dev`
- `Dockerfile` now installs via `pip install .` instead of `-r requirements.txt`

## [0.0.7] - 2026-02-17

### Added
- **OKE node image scanning** (`OKE_IMAGE_CHECK_ENABLED`, `OKE_CLUSTER_OCID`)
  - Scans boot volume and worker node images used by OKE node pools
  - Reports outdated node images in Discord notifications and CSV
  - New `src/oke_client.py` module for OCI Container Engine API interactions
- **Trivy platform configuration** (`TRIVY_PLATFORM`)
  - Allows specifying the target platform for Trivy scans (e.g., `linux/amd64`)
  - Useful for multi-arch images scanned from a different architecture host
- **OCIR image deletion**
  - Automatically deletes old commit-hash images beyond the configured keep count
  - Handles multi-arch manifest lists — protects sub-manifests of kept images from deletion
  - Failed scan count included in Discord report summary
- **Image cleanup CSV section**
  - OCIR image cleanup results appended as a third section in the CSV attachment

### Fixed
- **OCIR auth for manifest fetches** — corrected authentication flow when fetching image manifests via Docker V2 API
- **Intermediate image layer deletion** — only leaf image tags are deleted; shared intermediate layers are preserved
- **Orphan deletion bug** — orphaned tags (images not referenced by any running pod) are now correctly identified and removed
- **Image digest retrieval** — OCIR cleanup now fetches the correct digest before attempting deletion
- **Cache removal between scans** — Trivy `fanal/` cache is reliably removed after each image scan
- **Commit hash comparison in version logic** — git hash tags are now included in latest-version comparisons
- **Semver bug with multiple images** — fixed edge case where multiple images sharing a registry caused incorrect version comparisons
- **Critical CVE table newline formatting**
- **Commit hash output and delete message formatting**

## [0.0.6] - 2026-01-04

### Fixed
- **OCIR repository name normalization**
  - Fixed OCIR image repository lookups by stripping namespace prefix from repository names
  - OCIR API expects repository names without namespace (e.g., `discord_bot` not `namespace/discord_bot`)
  - Added `normalize_ocir_repository()` method to handle namespace stripping
  - Updates `_find_repository_compartment()` and `_get_ocir_images_via_sdk()` to use normalized names
- **Timezone-aware datetime handling**
  - Fixed `TypeError` when comparing timezone-aware datetimes from OCI SDK with timezone-naive datetimes
  - Updated `age_days()` method to use `datetime.now(timezone.utc)` for proper comparisons
  - Prevents crashes when calculating image age for OCIR images
- **Mixed version type comparison**
  - Fixed version comparison when repositories contain both semver tags and commit hash tags
  - Updated `get_latest_version()` to populate creation dates for all version types
  - When both semver and non-semver versions exist, compares by creation date to find truly latest
  - Resolves issue where semver versions were incorrectly chosen over newer commit hash versions

### Added
- **Alternate tag display for better commit hash workflows**
  - When current image uses commit hash and latest is semver, displays corresponding commit hash if available
  - New `_find_alternate_tag()` method finds non-semver tags with matching creation timestamps
  - Version reports show: `Latest: abc1234 (version 0.2.1)` instead of just `Latest: 0.2.1`
  - Helps teams using commit-hash-based deployments identify which commit to deploy
- **Comprehensive test coverage for OCIR fixes**
  - 10 new tests covering repository normalization, timezone handling, and alternate tag display
  - Tests for mixed version comparison scenarios
  - New test suite for `version_reporter.py` module (7 tests)
  - Code coverage improved from 66% to 74%
  - `version_reporter.py` coverage: 11% → 90%

### Changed
- Enhanced `check_for_updates()` to include `alternate_tag` in update info
- Updated version report formatting to handle all version type combinations:
  - Both semver
  - Both non-semver
  - Non-semver → semver (with/without alternate tag)
  - Semver → non-semver

## [0.0.5] - 2025-12-31

### Added
- **Image version update detection across multiple registries**
  - Automatically checks for newer versions of deployed images
  - Supports semver tags (v1.2.3, 1.2.3) with proper version comparison
  - Supports commit hash tags compared by image creation date
  - Multi-registry support: OCIR, Docker Hub, GitHub Container Registry
  - Categorizes updates as MAJOR (breaking changes) vs minor/patch (safe updates)
- New `src/registry_client.py` module for registry API interactions
  - Fetches available tags from registry APIs
  - Retrieves image manifests and creation dates
  - Parses and compares semver and commit hash versions
  - Supports authenticated OCIR access and public Docker Hub/ghcr.io access
- New `src/version_reporter.py` module for version update reporting
  - Generates formatted console reports with MAJOR and minor/patch sections
  - Shows version differences and image age for outdated deployments
- **Enhanced Discord notifications with two-message block system**
  - Block 1: Vulnerability scan results (summary + CRITICAL CVEs table + CSV)
  - Block 2: Version update results (summary + minor/patch updates table)
  - MAJOR updates excluded from Discord message (available in CSV only)
  - Version updates shown with type indicators (MAJOR/Minor/Patch/Commit Hash)
- **Comprehensive CSV reporting with two sections**
  - Section 1: Vulnerabilities (Image, CVE, Severity, Fixed Version)
  - Section 2: Version Updates (Image, Current Version, Latest Version, Update Type, Age, Version Diff)
  - Both sections included in single CSV attachment for complete audit trail

### Changed
- Updated Discord notification format to use two separate message blocks
  - First block focuses on security vulnerabilities
  - Second block focuses on version updates
  - Improved clarity and reduced information overload
- Enhanced `send_scan_report()` to accept `update_results` parameter
- Added `_build_update_table()` method for formatting version update tables
- Updated `_generate_csv()` to include version update data in separate section
- Updated main scan workflow to include version checking step
- Updated architecture diagram to show version checking as step 4

### Documentation
- Updated README.md with version tracking features and multi-registry support
- Added version update example outputs to Discord notifications section
- Updated AGENTS.md with complete registry_client.py and version_reporter.py documentation
- Added "Version Update Checking Implementation Details" section to AGENTS.md
  - Registry API patterns and authentication methods
  - Version comparison logic for semver and commit hash tags
  - Performance considerations and optimization strategies
  - Error handling scenarios

## [0.0.4] - 2025-12-30

### Changed
- **Discord webhook notifications now use CSV file attachments**
  - Sends a single message instead of multiple paginated messages to reduce channel spam
  - Critical vulnerabilities **with available fixes** are displayed in the channel for immediate visibility
  - Full vulnerability report attached as downloadable CSV file
  - CSV includes all vulnerabilities (CRITICAL, HIGH, MEDIUM, LOW) sorted by severity
  - CSV format: Image, CVE, Severity, Fixed Version
- Enhanced `_build_vulnerability_table()` with `only_with_fixes` parameter to filter vulnerabilities
- Updated `_send_message()` to support multipart/form-data file uploads

### Added
- New `_generate_csv()` method in `DiscordNotifier` for comprehensive vulnerability reporting
- CSV attachment support via Discord webhook file uploads
- Improved test coverage for Discord notifications:
  - Test for CSV file attachment functionality
  - Test for `only_with_fixes` filter behavior
  - Test for CSV generation and severity-based sorting

## [0.0.3] - 2025-12-26

### Added
- Discord webhook integration for scan result notifications
  - Sends formatted scan reports with vulnerability counts and image details
  - Displays top 10 vulnerable images in a paginated table format
  - Optional configuration via `DISCORD_WEBHOOK_URL` environment variable
  - Non-blocking: webhook failures don't fail the scan
- HIGH severity logging alongside CRITICAL vulnerabilities

### Changed
- Updated DapperTable library usage with new header API
  - Migrated to `DapperTableHeaderOptions` and `DapperTableHeader` for improved table formatting
  - Enhanced Discord notification table presentation
- Improved logging to include both CRITICAL and HIGH severity findings

## [0.0.2] - 2025-12-25

### Fixed
- Fixed Python logging to use f-strings instead of kwargs for standard logger compatibility
  - Updated all logging calls across `scanner.py`, `k8s_client.py`, and `main.py`
  - Resolves `TypeError: Logger._log() got an unexpected keyword argument` errors
- Fixed OCIR authentication for private images
  - Corrected username format to `{namespace}/{username}` as required by OCIR
  - Private images now authenticate successfully
- Fixed OTLP endpoint configuration
  - Changed from port 4317 (gRPC) to port 4318 (HTTP) to match HTTP exporters
  - Resolves connection reset errors when exporting telemetry
  - Simplified environment variables to use `OTEL_EXPORTER_OTLP_PROTOCOL`
- Fixed telemetry shutdown for short-lived CronJobs
  - Added `force_flush()` calls before shutdown to ensure all telemetry is exported
  - Prevents data loss when job completes quickly

### Added
- Comprehensive progress logging throughout the scan workflow
  - Startup banner and configuration details
  - Per-image scan progress indicators ([1/N], [2/N], etc.)
  - Success (✓), warning (⚠), and error (✗) indicators
  - Detailed summary table at completion
- Set default log level to DEBUG for better troubleshooting
- Configured logs to output to stdout for Kubernetes log collection
- Added `otel.access: enabled` label to pod template for network policy compatibility

### Changed
- Renamed `metrics` variable to `scanner_metrics` to avoid shadowing imported module
- Improved log message clarity with structured output and visual separators

## [0.0.1] - 2025-12-20

### Added
- Initial release of OKE Security Scanner
- Trivy-based vulnerability scanning for Kubernetes cluster images
- OpenTelemetry integration for traces, metrics, and logs
- OCIR private registry support
- Configurable namespace scanning with exclusions
- CronJob deployment for scheduled scans
