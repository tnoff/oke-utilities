# AI Agent Context for OKE Utilities

Context for AI agents (Claude, GPT, etc.) working on this codebase.

For **what the project does**, **how to install/run it**, **env-var reference** (all three packages) and **authentication setup**, read [README.md](README.md). The deployed CronJobs (schedules, RBAC, Secrets, operations) live in `tnoff/docker-apps` (`apps/security-scanner/`, TechDocs at `techdocs/oke-utilities`). For **local-dev setup**, read [DEVELOPMENT.md](DEVELOPMENT.md). This file covers only the things an agent needs that aren't in those docs.

## File Structure

```
oke-utilities/
├── packages/             # Core library plus three separate installable
│   │                     # packages/images. None carries its own dev-tool versions or pylint
│   │                     # config; root pyproject.toml is the single source
│   │                     # for both (see Code Quality below).
│   ├── jwks_publisher/   # jwks-publisher: own package, own Dockerfile, own CronJob +
│   │   │                 # entry point (`python -m jwks_publisher`). Does NOT depend on
│   │   │                 # packages/core, kubernetes or OpenTelemetry: stdlib for the API
│   │   │                 # server call, the OCI SDK for the one Object Storage object.
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/jwks_publisher/
│   │   │   ├── main.py, __main__.py, config.py, jwks.py, hosted.py
│   │   └── tests/       # helpers.py holds the key-set builders (tests/ has no __init__.py)
│   ├── rpst_sidecar/     # rpst-sidecar: own package, own Dockerfile, runs as a native
│   │   │                 # sidecar (`python -m rpst_sidecar`; `--check` is the exec probe).
│   │   │                 # Stands alone: only `cryptography`, urllib for HTTP (no OCI SDK).
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/rpst_sidecar/
│   │   │   ├── main.py, __main__.py, config.py, exchange.py
│   │   └── tests/       # helpers.py holds the JWT/response builders (tests/ has no __init__.py)
│   ├── audit_watch/      # audit-watch: own package, own Dockerfile, a CronJob (`python -m audit_watch`).
│   │   │                 # Resource principal auth; the OCI SDK is its only dependency.
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/audit_watch/
│   │   │   ├── main.py, __main__.py, config.py, classify.py
│   │   └── tests/       # conftest.py holds the fake Audit/Identity clients
│   ├── core/             # oke-scanner-core: shared by scan/secret_age/ocir_cleanup.
│   │   └── src/oke_scanner_core/
│   │       ├── k8s_auth.py        # load_k8s_config(): incluster, falls back to kubeconfig
│   │       ├── image.py           # Image dataclass
│   │       ├── k8s_client.py      # KubernetesClient: image discovery
│   │       └── telemetry.py       # setup_telemetry/shutdown_telemetry. Behind the
│   │                               # `oke-scanner-core[telemetry]` extra -- secret_age
│   │                               # depends on bare oke-scanner-core and never installs
│   │                               # the OTel SDK; scan and cleanup both request the extra.
│   ├── scan/             # oke-scan: own package, own Dockerfile, own
│   │   │                 # CronJob + entry point (`python -m scan`). Discovers all
│   │   │                 # images deployed in OKE and scans each with Trivy.
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/scan/
│   │   │   ├── main.py, __main__.py, config.py, scanner.py, discord_notifier.py
│   │   │   └── telemetry.py  # the image_scan/image_scan_failed gauges only; setup/shutdown re-exported
│   │   │                     # from oke_scanner_core.telemetry, not defined here
│   │   └── tests/
│   ├── secret_age/       # secret-age-tracker: own package, own Dockerfile, own
│   │   │                 # CronJob + entry point (`python -m secret_age`).
│   │   │                 # No Trivy, no OCIR cleanup code, no OpenTelemetry.
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── src/secret_age/
│   │   │   ├── main.py, __main__.py, config.py, aggregator.py, finding.py, discord_report.py
│   │   │   └── readers/  # k8s.py, layer1_ledger.py, oci_iam.py -- one per secret source
│   │   └── tests/
│   └── ocir_cleanup/     # ocir-cleanup: own package, own Dockerfile, own CronJob +
│       │                 # entry point (`python -m ocir_cleanup`). No Trivy binary at all;
│       │                 # does use OpenTelemetry (unlike secret_age; the docker-apps
│       │                 # ocir-cleanup-cronjob enables OTLP metrics and logs).
│       ├── Dockerfile
│       ├── pyproject.toml
│       ├── src/ocir_cleanup/
│       │   ├── main.py, __main__.py, config.py, registry_client.py, discord_notifier.py
│       └── tests/
├── k8s/                  # Generic scanner CronJob + RBAC + Secret examples (not the deployed manifests)
├── .github/workflows/    # GitHub Actions: ci.yml, release.yml, scheduled.yml
├── pyproject.toml        # No importable code of its own -- see Code Quality below.
│                         # Just the dev-tool versions tox installs and the
│                         # pylint config tox's combined lint invocation reads
│                         # from repo-root cwd, plus VERSION's dynamic-version
│                         # wiring (unused by anything else; tag.yml/
│                         # bump-version.yml read VERSION directly as text).
├── tox.ini               # pytest / pylint / bandit envs -- covers all four
│                         # packages/*/src, see its own header comment
├── VERSION               # Semantic version for the whole repo's release stream
│                         # (tag.yml/bump-version.yml), independent of each
│                         # package's own pyproject.toml version field
├── README.md             # User-facing documentation
├── mkdocs.yml            # Backstage TechDocs site config
└── docs/
    ├── README.md         # Symlink to ../README.md (single copy for GitHub + TechDocs)
    ├── DEVELOPMENT.md    # Local-dev setup
    ├── AGENTS.md         # This file
    └── CONTRIBUTING.md   # Canonical-remote statement
```

## Observability Pattern

Logs and metrics are exported via OTLP. **Tracing was deliberately removed** — do not add `tracer.start_as_current_span(...)` blocks back without explicit user direction.

```python
from logging import getLogger

logger = getLogger(__name__)
```

- Use standard Python `logging.getLogger()` — never `structlog`.
- The OTLP `LoggingHandler` is attached **once**, to the root logger, by `oke_scanner_core.telemetry.setup_telemetry`. Never attach one to a module logger: records propagate to root, so a second handler exports every line twice (`packages/scan/tests/test_logging_once.py` guards this).
- `logger_provider` and `meter_provider` may both be `None` when their OTLP component is disabled in config. They are only needed by `main()` for `shutdown_telemetry`; `meter_provider` gauges are reached via `scanner_metrics`.

## Metrics

Two gauges, defined in `telemetry.create_metrics()`:
- `image_scan` — attributes `image` (string), `severity` (`critical` | `high`).
- `image_scan_failed` — attribute `image` (string, the repo name); `1` if that image's scan failed this run, `0` if it succeeded, so a clean run reads zero rather than absent. An unparseable `SCAN_EXTRA_IMAGES` entry has no image to label and only shows up in the exit code.

`create_metrics()` returns `None` when `meter_provider` is `None`. Always check before recording (`if self.metrics: ...`).

## Important Implementation Details

### `packages/core/src/oke_scanner_core/telemetry.py`
`setup_telemetry(cfg) -> tuple[Optional[MeterProvider], Optional[LoggerProvider]]`. `cfg` just needs `otlp_metrics_enabled`/`otlp_logs_enabled` attributes -- any package's own Config dataclass satisfies this structurally. Each provider is `None` if that OTLP component is disabled. Resource attributes come from `OTELResourceDetector()`. `shutdown_telemetry(meter_provider, logger_provider, logger=None)` flushes and shuts down whichever providers are non-`None`; duck-typed on `.force_flush()`/`.shutdown()`, called from each package's own `main()` `finally:` block.

Gated behind the `oke-scanner-core[telemetry]` extra (see File Structure above) -- don't add a new import here without checking it doesn't leak the OTel SDK into secret_age's dependency set.

### `packages/scan/src/scan/telemetry.py`
Scan-only now: `create_metrics(meter_provider)` builds the `image_scan` and `image_scan_failed` gauges (returns `None` when its argument is `None`); `Metrics` is the dataclass wrapping it. Re-exports `setup_telemetry`/`shutdown_telemetry` from `oke_scanner_core.telemetry` so `main.py`'s existing `from .telemetry import ...` line didn't need to change.

### `packages/scan/src/scan/main.py`
Orchestration only (cleanup is in `packages/ocir_cleanup/src/ocir_cleanup/main.py`):

- `run_scan(config, scanner_metrics, notifier) -> CompleteScanResult` — updates the Trivy DB, lists pods via `oke_scanner_core.k8s_client.KubernetesClient`, scans every discovered image plus `Config.extra_images` (`SCAN_EXTRA_IMAGES`, parsed by `parse_extra_images`; unioned with the discovered set so an image that is both is scanned once; an unparseable entry is logged and counted in `failed_scans` rather than aborting), posts the Discord report, emits metrics. Extras are scanned with the same `TRIVY_PLATFORM` as everything else, so a multi-arch extra is only scanned for that one platform. Do not add an extra image's repo to `OCIR_EXTRA_REPOSITORIES` unless you want `ocir-cleanup` pruning it; the two settings are independent.

The `if __name__ == "__main__":` guard is marked `# pragma: no cover` (standard untestable pattern). A separate `__main__.py` does `sys.exit(main())` so `python -m scan` works.

### `packages/scan/src/scan/scanner.py`
- `update_database()` — runs `trivy image --download-db-only` once at startup; logs and continues on timeout/error.
- `scan_image(image)` — invokes Trivy with JSON output, parses CVE results into `ScanResult`.
- `_cleanup_image_cache()` — removes `fanal/` from the Trivy cache after each scan to bound disk usage; the vulnerability DB is preserved.

### `packages/core/src/oke_scanner_core/k8s_client.py`
- `KubernetesClient(namespaces, exclude_namespaces)` — takes the two discovery-scope lists directly, not a `Config` object, so it doesn't depend on any package's own Config shape.
- Calls `oke_scanner_core.k8s_auth.load_k8s_config()` for the incluster/kubeconfig-fallback bootstrap (see below) rather than inlining it.
- `get_all_images()` enumerates namespaces (configured set or all-minus-exclusions), then collects images from regular + init containers across all pods.
- Still carries the `kubernetes==36.0.0` bearer-token mirror workaround (`api_key['authorization']` → `api_key['BearerToken']`) — a no-op once upstream's naming agrees, so it's safe to leave even if the bug gets fixed.

### `packages/core/src/oke_scanner_core/image.py`
The `Image` dataclass: parses `registry / repo_name / tag`, strips digest suffixes (`@sha256:...`), and exposes `is_ocir_image`. There is **no** `Image.version` / semver comparison anymore — don't reintroduce it.

### `packages/core/src/oke_scanner_core/k8s_auth.py`
`load_k8s_config()` — tries `load_incluster_config()` first, falls back to `load_kube_config()` for local dev. Used by `KubernetesClient` above, and directly by `secret_age`'s own k8s/layer1_ledger readers (which don't use `KubernetesClient` at all).

### `packages/core/src/oke_scanner_core/discord_webhook.py`
`DiscordWebhookClient(webhook_url)` — `send_message(content_list)` and `send_file(message_content, file_contents, file_name)`. Just the low-level webhook mechanics; report-shape formatting (which table columns, which result type) stays in the package that owns that report (`packages/scan/src/scan/discord_notifier.py`'s `send_image_scan_report`, `packages/ocir_cleanup/src/ocir_cleanup/discord_notifier.py`'s `send_cleanup_recommendations`/`send_deletion_results`). Each of those wraps a `DiscordWebhookClient` internally rather than importing `requests` directly.

### `packages/ocir_cleanup/src/ocir_cleanup/registry_client.py`
OCIR-only; there is no Docker Hub / ghcr.io version-check logic. `oci` is a dependency of `ocir_cleanup` and `secret_age` only, not of scan. Both build their OCI clients as a resource principal when `OCI_RESOURCE_PRINCIPAL_VERSION` is set (region and tenancy from the signer), otherwise from `~/.oci/config`. `OCIR_COMPARTMENT_IDS` replaces the all-compartments search in `_list_all_compartments`: that search 404s once per compartment lacking the repo, which audit-watch reads as a refused call.

Properties:
- `oci_registry` — derived from the OCI config region.
- `oci_namespace` — fetched (and cached) via Object Storage API.

Key methods:
- `_get_ocir_images_via_sdk(image)` — lists all images in an OCIR repo via `oci.artifacts.ArtifactsClient.list_container_images` (paginated). Cached per repo.
- `_find_repository_compartment(repo)` — searches all accessible compartments for a repo; results cached.
- `_get_docker_auth(image)` — reads `~/.docker/config.json` when present, handles Basic-vs-Bearer token exchange against the registry's `/v2/` endpoint, and with no config or no entry for the registry asks for an anonymous Bearer token (public repositories grant one). Returns `{}` when the registry needs no auth and `None` only when access is refused, so callers test `is None`, not truthiness: with no auth the sub-manifest digests of kept multi-arch tags would go unprotected and cleanup could delete them. Used only for fetching manifest lists.
- `_get_manifest_list_sub_digests(image)` — fetches a manifest list (multi-arch index) via Docker V2 API to enumerate sub-manifest digests; used to protect referenced platform manifests during cleanup.
- `get_old_ocir_images(images, keep_count, extra_repositories)` — returns `CleanupRecommendation`s of old commit-hash tags eligible for deletion, while preserving the deployed tag, `latest`, the newest `keep_count` tags, and any sub-manifests of kept tags.
- `get_orphaned_manifests(images, extra_repositories)` — finds `unknown@sha256:...` platform manifests no longer referenced by any tagged manifest list.
- `delete_ocir_images(cleanup_recommendations)` — deletes by OCID; 404s are treated as already-deleted. Returns `list[Image]` (returns `[]` when SDK unavailable — **not** `{}`).
- `get_image_creation_date(image) -> Optional[datetime]` — public, but not called anywhere in `packages/ocir_cleanup/src/`; only `packages/ocir_cleanup/tests/test_registry_client.py` exercises it. Don't assume something calls this in production.

Safety guards:
- Only OCIR images are considered (`image.is_ocir_image`).
- `latest` and the currently deployed tag are never deleted.
- Manifest-list sub-digests of kept tags are explicitly protected.
- Orphan detection skips a repo entirely if no manifest lists can be resolved (avoids deleting needed manifests when Docker auth fails).
- Deletion is opt-in via `OCIR_CLEANUP_ENABLED=true`.

### `packages/ocir_cleanup/src/ocir_cleanup/main.py`
`run_cleanup(config, notifier)` — always lists pods itself via `KubernetesClient`. If `CLEANUP_REPO` is set, the run is scoped to that single OCIR repo (image set filtered + `extra_repositories=[cleanup_repo]` so cleanup happens even with nothing deployed); otherwise it sweeps every image and uses `config.ocir_extra_repositories`.

A one-off run with `CLEANUP_REPO=<repo>` still protects the deployed tag: the cluster is running the old tag, so `get_old_ocir_images` finds it via k8s discovery. The deployed CronJob leaves `CLEANUP_REPO` unset (it is the only scheduled pruner).

`main()` returns an `int` (0/1); `__main__.py` does `sys.exit(main())`. Scan's `main()` also returns an `int`: 1 if any scan failed (`CompleteScanResult.failed_scans`, which includes unparseable extras), else 0, after telemetry is flushed. A config error still raises.

### `packages/scan/src/scan/discord_notifier.py` / `packages/ocir_cleanup/src/ocir_cleanup/discord_notifier.py`
Each package keeps only the report-shape method(s) it needs, both wrapping `oke_scanner_core.discord_webhook.DiscordWebhookClient`:
- `packages/scan/src/scan/discord_notifier.py`: `send_image_scan_report(complete_scan_result)`
- `packages/ocir_cleanup/src/ocir_cleanup/discord_notifier.py`: `send_cleanup_recommendations(cleanup)`, `send_deletion_results(images, scanned_repos=None, is_orphaned=False)` — `scanned_repos` drives the "No `<repo>` ... deleted" reporting for clean repos

**Library API**: both use `dappertable` v1.1.x — `Column` / `Columns`, `DapperTable(columns=Columns([...]))`, `.render()`, `len(table)`. The older `DapperTableHeader` / `DapperTableHeaderOptions` / `.print()` / `.size` API is gone.

All values passed to `add_row` should be strings. Each paginated page is sent as a separate webhook POST with a 1-second sleep between requests to respect rate limits (in `DiscordWebhookClient`, not per-package).

### `packages/rpst_sidecar/`
`main.Sidecar.refresh_once()` is one exchange (testable on its own); `Sidecar.run(stop)` only sleeps (`threading.Event.wait`) and retries. The RSA-2048 key is generated once per process and reused for every exchange, so `private.pem` is written once, before the first `rpst`, and a reader never sees a token/key mismatch. Both files are written atomically (temp file, fsync, `os.replace`) with mode 0640; `rpst` has NO trailing newline (the OCI SDK fails with "Invalid crypto padding" otherwise). `exchange.exchange()` re-reads `client_id`/`client_secret` and the SA token on every call, POSTs the token-exchange form to `{RPST_DOMAIN_URL}/oauth2/v1/token` through the single `exchange.urlopen` wrapper (the seam tests patch), and raises `ExchangeError` (messages redacted of secrets; an HTTP error status keeps up to 2000 chars of OCI's error JSON so the `ecid` survives, any other body is capped at 200 because a 2xx that is not 200 could carry a token). Failures never exit: exponential backoff with jitter, 2s doubling to a 120s cap, forever. Next refresh = `REFRESH_FRACTION` x (RPST `exp` - `iat`), falling back to `RPST_EXP_MINUTES`. JWT payloads are decoded without verifying signatures, for scheduling and the audit log line only. `--check` exits 1 unless `rpst` and `private.pem` exist and `exp` is more than 60s away. `--once` calls `refresh_once()` once and exits 0/1 (no backoff), for canary CronJobs.

### `packages/audit_watch/`
`main.run(cfg, audit, identity, tenancy_id, now=None)` audits `[now - lag, now - lag - window]` in the tenancy and every compartment `list_compartments` returns, keeps events whose `identity.auth_type == "resource"` and groups them by (event name, status, event type, from-control-plane, HTTP method). `classify.flags(..., oke_service=True)` returns nothing for `LaunchInstance`/`TerminateInstance` (`classify.NODE_LIFECYCLE_EVENTS`), neither `DENIED` for the 404 the OKE service's first launch attempt gets nor `UNEXPECTED_WRITE`: `oke_service` is whether the event's source IP is inside `WatchConfig.oke_service_networks` (env `OKE_SERVICE_CIDRS`, empty by default, so nothing is excused unless the CronJob sets it), it is part of the group key, and the `seen` line says `oke_service=yes`. Never widen it to a name-only match or to other events: the same calls from a pod or node address, and every other event from that range, must still alert. `classify.flags()` marks `DENIED` (status 401/403/404; OCI answers a refused call with 404) and `UNEXPECTED_WRITE` (not a `Get/List/Summarize/Search/Inspect/Read/Describe` and not in `KNOWN_WRITE_EVENTS`; an UNNAMED event, `event=unknown`, counts as a read only when its request method is GET or HEAD or its request path is one of `classify.READ_ONLY_PATHS` (Block Volumes' `/v1/volumeAttachability` and `/v1/preLaunchAttachability`, the latter a POST), so the method and the path-check flag are part of the group key and must stay there, or an unnamed POST hides in a group of unnamed GETs or behind a check; keep that list to paths seen from OCI's own services and never match by method alone); flagged groups log at WARNING and the Grafana rules in docker-apps match the `audit-watch DENIED` / `audit-watch UNEXPECTED_WRITE` text, so changing those prefixes breaks the alerts. Resource-principal events have NO principal name (one shared principal id), so attribution is by source IP only. **Never read `identity.credentials`** (write events carry a live token there) **or the request headers and parameters.** `describe()` builds the `audit-watch detail` lines (event source, user agent, request action and path, resource name) logged after a flagged group, capped at `DETAIL_SAMPLES` per group; they deliberately do not start with `audit-watch DENIED` / `UNEXPECTED_WRITE`, so they do not count toward the alert. A test asserts it never reaches the log. `run(..., control_plane=frozenset())`: events whose source IP is in that set are grouped apart (the group key carries a `from_control_plane` flag) and `classify.flags(..., control_plane=True)` never returns `UNEXPECTED_WRITE` for them, only `DENIED`. `main()` fills the set from `control_plane.api_server_ips()`, which lists the `kubernetes` EndpointSlice in `default` through the in-cluster API with the mounted ServiceAccount token (urllib, no kubernetes client; `control_plane.urlopen` is the test seam). A failed lookup warns and continues with an empty set, never fails the Job. Returns 1 only when the span could not be audited. The SDK's pagination retries a 5xx with backoff (slow in tests): use 403 for failure tests.

### `packages/jwks_publisher/`
`main.run(cfg, hosted=None)` mirrors the live ServiceAccount key set into one world-readable object (tnoff/terraform#191). Order matters and each step is a guard: `jwks.fetch_live_jwks` (https only, pod ServiceAccount token) then `jwks.canonicalize` (must be a non-empty set of RSA RS256 signing keys with `kid`/`n`/`e`; any key with a private member is **refused, not stripped**; canonical form sorts by `kid` so ordering and whitespace never cause a write) then `HostedJwks.read` then compare, and only then `HostedJwks.write` with `if_match=<etag>` and a read-back. Any failure before the write leaves the hosted copy untouched and exits 1. The bot's policy is `use objects` (overwrite, no create/delete), so a missing object is an error by design -- terraform seeds it. The empty seed `{"keys":[]}` fails validation on purpose and is replaced on the first run. Do not weaken the private-member check, do not add `create`/`delete` paths, and do not add retries around `write`: a 412 means someone else changed the object and the next run should look at it afresh. Logs only (no OTel), by design.

### `packages/secret_age/`
`main()` runs three readers (`readers/oci_iam.py`, `readers/k8s.py`, `readers/layer1_ledger.py`), each in its own `try/except` so one failing source never blocks the report; `aggregator.aggregate()` buckets `Finding`s into rotate/warn/unknown/ok (oldest first); `discord_report.send_report()` posts tables plus a full CSV. Thresholds: `SECRET_AGE_WARN_DAYS=90`, `SECRET_AGE_ROTATE_DAYS=180`. See the README's "Secret-age tracker" section for the annotation contract (`secret-age-tracker.tnoff/last-rotated`, `unknown` sentinel, `expires-at`). Notes:

- It must keep `secrets list` metadata-only and never read `.data`; its RBAC is a separate ServiceAccount from the scanner's on purpose.
- OCI IAM findings deliberately carry no `rotation_command` (terraform-managed credentials; an out-of-band rotation causes state drift).
- `Layer.SEALED_SECRET` and its Discord title still exist in `finding.py`/`discord_report.py`, but no reader emits it; Sealed Secrets are decommissioned.
- `main()` returns an int; it does not use OpenTelemetry (stdout logging only).

## Docker Image

One `Dockerfile` per package at `packages/<name>/Dockerfile`, all built from
repo-root context (so each can `COPY packages/core`). Only `packages/scan/Dockerfile`
has a Trivy stage; `secret_age` has no OpenTelemetry deps, `ocir_cleanup` does.
Images are `iad.ocir.io/tnoff/oke-scan`, `.../ocir_cleanup`, `.../secret_age_tracker`
`.../jwks_publisher`, `.../rpst_sidecar` and `.../audit_watch` (CI builds and scans all six; `release.yml` pushes the last three, with no
docker-apps bump job until their pins exist in `images/kustomization.yaml`). This section covers `packages/scan/Dockerfile`:

1. `trivy-builder` — `python:3.15-slim` + `curl` + `ca-certificates`, runs the official Trivy install script and drops the pinned `trivy` binary in `/usr/local/bin/`. Version pinned via `ARG TRIVY_VERSION`.
2. `py-builder` — `python:3.15-slim`, `pip install --prefix=/install ./packages/core ./packages/scan`.
3. Final stage — `python:3.15-slim`, applies security upgrades, copies `trivy` and `/install`, runs as non-root `scanner` (UID 1000).

The final image carries no `curl` / `wget` / `tar` / `git` / build toolchain. The Trivy DB is **not** pre-downloaded; `main()` fetches it on startup.

## CI/CD

The project uses **GitHub Actions**. `.github/workflows/` holds three callers:

- `ci.yml` — on pull requests: trufflehog secret scan, the tox matrix (pytest + pylint + bandit across Python 3.13–3.15) with a diff-cover gate, a conditional image build + image scan for EACH image (`changes` job outputs `image`/`secret_age_image`/`ocir_cleanup_image`, gated on separate path filters -- not a matrix, so each has its own `needs`/`if`; `packages/core/*` flips all three, since all three depend on it), `bump-version`, and `check-workflow-contracts` (catches a `uses:` whose inputs/secrets no longer match the pinned callee). The scan build/scan job passes `dockerfile: packages/scan/Dockerfile` explicitly (the reusable `docker-build-check.yml` defaults to a root `Dockerfile`, which does not exist here).
- `release.yml` — on `main`: fold the changelog, tag from `VERSION` (shared across all three images), push each changed image to OCIR under its own OCIR repo name, and trigger a `docker-apps` pin bump per image (`oke-scan`, `secret-age-tracker` and `ocir-cleanup` are separate `bump_source`s, each its own explicit `push-image-*`/`trigger-bump-*` job pair -- not a matrix; matrix job outputs aren't addressable per-leg, and `trigger-bump` needs the exact tag its own paired push produced). Same `dockerfile: packages/scan/Dockerfile` override on the scan `push-image` job as `ci.yml`. Each job hardcodes its OCIR `repo_name` (`oke-scan`, `secret_age_tracker`, `ocir_cleanup`).
- `scheduled.yml` — weekly: Renovate and branch cleanup.

Each job calls a reusable workflow from `tnoff/github-workflows`, SHA-pinned in `uses:` and kept current by Renovate's github-actions manager.

## Code Quality

Configuration lives in root `pyproject.toml` -- which has no importable code
of its own (see File Structure above), but is the single source both tox
and pylint actually use:
- `[project.optional-dependencies].dev` — `bandit`, `pylint`, `pytest`, `pytest-cov`, `pytest-mock`, `pytest-asyncio`, `tox`, `coverage`. tox's `extras = dev` installs these for every testenv; no package's own pyproject.toml carries a competing copy.
- `[tool.pylint.*]` — pylint rules (max line length 120, etc.). tox's combined `pylint packages/core/src packages/scan/src ...` invocation runs from repo-root cwd, and pylint discovers config from CWD -- so this is the block in effect for all four packages. No package's own `pyproject.toml` carries a `[tool.pylint.*]` block; don't add one without checking it would actually be read.

`tox.ini` defines envs `py313`..`py315` and exposes `pytest`, `pylint`, `bandit` as individual envs.

Running locally:

```bash
pip install -e ".[dev]"
tox              # full matrix
tox -e py313     # single python version
tox -e pytest    # pytest only
tox -e pylint    # pylint only
tox -e bandit    # bandit only
```

Current state: **100% line coverage**, pylint 10.00/10, bandit clean.

## Common Tasks

### Adding a new configuration option
1. Add the field to the relevant Config -- `Config` in `packages/scan/src/scan/config.py` (scan), `CleanupConfig` in `packages/ocir_cleanup/src/ocir_cleanup/config.py`, or `SecretAgeConfig` in `packages/secret_age/src/secret_age/config.py`. These no longer share fields; adding a scan-only option doesn't touch the other two.
2. Add it to `from_env()` with `os.getenv()` (and a default).
3. Update that package's own `tests/conftest.py::base_config` fixture so existing tests still pass — it constructs the Config dataclass directly, so a missing field raises `TypeError`.
4. Update the env-var table in `README.md` and `DEVELOPMENT.md`.
5. If it gets stored as a secret, update `k8s/secret-example.yaml` and the relevant CronJob manifest in `docker-apps`.

### Adding observability to a new module
1. `from logging import getLogger`
2. `logger = getLogger(__name__)` at module level.
3. Don't attach a log handler — the root logger's OTLP handler already exports it.
4. **Don't add tracing spans** — tracing is intentionally not wired up.

### Adding a new metric
1. Add the gauge/counter creation to `create_metrics()` in `packages/scan/src/scan/telemetry.py` and add it as a field on the `Metrics` dataclass. (Scan-only concept -- `packages/ocir_cleanup` has no metrics instrument of its own today.)
2. Pass the `Metrics` instance to the module that needs it.
3. Always check for `None` before recording (`if self.metrics:`).
4. Document it in `README.md` if user-facing.

### Testing locally
1. Ensure `kubectl` can reach the target cluster.
2. Ensure `~/.oci/config` is set up if you intend to exercise OCIR paths (only relevant for `packages/ocir_cleanup` and `packages/secret_age` -- scan no longer touches OCI at all).
3. Export any env vars you need (see DEVELOPMENT.md).
4. Run: `python -m scan`, `python -m ocir_cleanup`, or `python -m secret_age`.

## Common Pitfalls

1. **DO NOT** use `structlog` — stick to standard `logging.getLogger()`.
2. **DO NOT** add tracing back without confirming intent — it was deliberately removed (logs + metrics only).
3. **DO NOT** reintroduce `Image.version` / `ImageVersion` / semver comparison — they were removed along with the image-update check.
4. **DO NOT** forget to update `packages/scan/tests/conftest.py::base_config` or `packages/ocir_cleanup/tests/conftest.py::base_config` when adding a field to the respective Config; each fixture constructs its dataclass directly, so a missing field raises `TypeError`.
5. **DO NOT** forget to check `if self.metrics:` before recording — it can be `None`.
6. **DO NOT** commit real secrets — use `k8s/secret-example.yaml` as the template.
7. **DO NOT** add a new unconditional import to `oke_scanner_core.telemetry` (or any other core module) without checking whether it belongs behind an extra — that module is the one place a careless addition would leak the OpenTelemetry SDK into `secret_age`'s image.
8. **REMEMBER** OCIR deletion is destructive — keep `OCIR_CLEANUP_ENABLED=false` for any new repo until you've reviewed the dry-run recommendations.
9. **REMEMBER** the Trivy DB is downloaded at runtime, not baked into the image; the first scan in a fresh cache will be slower.
10. **REMEMBER** `dappertable` is v1.1.x — `Column` / `Columns` / `render()` / `len(table)`, not the older `DapperTableHeader` / `print()` / `.size` API.
11. **REMEMBER** when patching `cleanup.registry_client.oci` in tests, `except oci.exceptions.ServiceError` resolves against the mock; set `mock_oci.exceptions.ServiceError = ServiceError` (the real class) if a test needs the except branch to actually catch.
