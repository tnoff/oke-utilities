# OKE Utilities

Three independent CronJob packages/images for the OKE cluster, sharing one
library: image vulnerability scanning (`oke-scan`, with OpenTelemetry
observability), OCIR tag/manifest cleanup (`ocir-cleanup`), and secret-age
tracking (`secret-age-tracker`). The deployed manifests, schedules and
operations live in `tnoff/docker-apps` (`apps/security-scanner/`, with a
TechDocs site at `techdocs/oke-utilities`); this repo holds the code and images.

## Features

| Feature | OKE Specific | Description |
| ------- | ------------ | ----------- |
| Security Scanner | No | Its own package/image (`packages/scan/`, `python -m scan`). Discovers all images in the K8s cluster and scans each with Trivy. |
| OCIR Image Cleanup | Yes | Its own package/image (`packages/ocir_cleanup/`, `python -m ocir_cleanup`). Deletes old OCIR tags beyond a configurable `keep_count`, while protecting the deployed tag, `latest`, and any multi-arch sub-manifest digests referenced by kept tags. |
| Orphan Manifest Cleanup | Yes | Same package/image as OCIR Image Cleanup. Detects and removes `unknown@sha256:...` platform manifests in OCIR whose digest is no longer referenced by any tagged manifest list. |
| Cache Management | No | Security Scanner only. Automatic cleanup of Trivy image cache after each scan to minimize disk usage. |
| Secret-age Tracker | Yes | Its own package/image (`packages/secret_age/`, `python -m secret_age`). Reports credential ages across OCI IAM credentials, Kubernetes Secrets (by the `secret-age-tracker.tnoff/last-rotated` annotation, falling back to `creationTimestamp`), and operator-tracked admin tfvars (a ledger ConfigMap). See [Secret-age tracker](#secret-age-tracker). |

All three share `packages/core/` (`oke-scanner-core`: image discovery, k8s
auth, the Discord webhook client, and, for the scanner and cleanup, which run
with OpenTelemetry, the OTel setup/teardown helpers behind a `[telemetry]`
extra).

## Install and Usage

Install and run the scanner locally:

```
$ pip install packages/core packages/scan
$ python -m scan
```

Cleanup and secret-age-tracker are separate installs — see
[`packages/ocir_cleanup/`](./packages/ocir_cleanup) and
[`packages/secret_age/`](./packages/secret_age) respectively (each has its
own `pyproject.toml`; e.g. `pip install packages/core packages/ocir_cleanup &&
python -m ocir_cleanup`).

See [DEVELOPMENT.md](https://github.com/tnoff/oke-utilities/blob/main/docs/DEVELOPMENT.md) for full local setup instructions (including the `[dev]` extras for running tests / linting).

Or use the docker build (one per image):

```
$ docker build -f packages/scan/Dockerfile .            # security scanner
$ docker build -f packages/ocir_cleanup/Dockerfile .    # OCIR cleanup
$ docker build -f packages/secret_age/Dockerfile .      # secret-age tracker
```

### Running in Kubernetes

The real manifests for all three CronJobs, their RBAC and Secrets are in
`tnoff/docker-apps` (`apps/security-scanner/`; see `techdocs/oke-utilities`
there for schedules and operations). The [`k8s/`](./k8s) folder is a generic,
standalone starting point for the scanner only: `rbac.yaml` (ServiceAccount +
read-only ClusterRole), `cronjob.yaml`, and `secret-example.yaml` (copy, fill in,
apply). Secret names and schedules there are illustrative and differ from the
deployed ones.

## Authentication

### Kubernetes
For kubernetes auth, you can use local auth creds or give a pod permissions to view the deployed images. See the [k8s](./k8s) folder for example auth roles.

### OCI SDK

`oci` is a dependency of `packages/ocir_cleanup` (and, separately, of
`packages/secret_age`'s OCI IAM reader) — the security scanner itself no
longer depends on it at all. Cleanup uses the OCI Python SDK for OCIR
operations. It automatically derives:
- **OCI Registry URL** from the region in your OCI config (e.g., `us-ashburn-1` → `iad.ocir.io`)
- **OCI Namespace** from the Object Storage API

Configure your OCI credentials in `~/.oci/config`:

```ini
[DEFAULT]
user=ocid1.user.oc1..your-user-ocid
fingerprint=your:fingerprint:here
tenancy=ocid1.tenancy.oc1..your-tenancy-ocid
region=us-ashburn-1
key_file=~/.oci/oci_api_key.pem
```

### Docker Registry (`~/.docker/config.json`)

Docker credentials from `~/.docker/config.json` are used in two places, each in its own image:
- **Trivy** (security scanner) uses them to pull images for vulnerability scanning.
- **Image Cleanup** (`packages/ocir_cleanup`) uses them to fetch manifests via the Docker V2 API. When a kept image is a manifest list (multi-arch), it reads its sub-manifests and protects them from deletion, preventing "manifest unknown" pull errors in the cluster.

## Cache Management

The scanner automatically manages Trivy's cache to minimize disk usage, which is important when running in Kubernetes with ephemeral storage.

After each image scan, the scanner removes the `fanal/` directory (cached image layers) while preserving:
- `db/` - Vulnerability database (~50MB, updated once per run)
- `java-db/` - Java vulnerability index

This approach:
- Prevents disk exhaustion when scanning many large images
- Avoids re-downloading the vulnerability database for each scan
- Ensures cleanup happens even if scans fail or timeout

The Trivy cache is located at `~/.cache/trivy/` (the Docker image sets `TRIVY_CACHE_DIR` to this by default). Note this is Trivy's own env var, read by the `trivy` binary -- this app's cache-cleanup code does not read it and always cleans `~/.cache/trivy/`, so overriding `TRIVY_CACHE_DIR` away from the default would point Trivy at a location the cleanup logic no longer manages.

## Configuration

### Environment Variables

Scan and cleanup are separate processes/images now, each with its own env
vars — there's no longer a single combined `Config`. Both are provided via
Kubernetes Secrets.

**Security Scanner** (`packages/scan/src/scan/main.py`, `python -m scan`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTLP_ENDPOINT` | No | `http://localhost:4317` | OTLP collector endpoint |
| `OTLP_INSECURE` | No | `true` | Use insecure gRPC connection |
| `OTLP_METRICS_ENABLED` | No | `false` | Enable OTLP metrics export |
| `OTLP_LOGS_ENABLED` | No | `false` | Enable OTLP logs export |
| `TRIVY_SEVERITY` | No | `CRITICAL,HIGH` | Vulnerability severities to report |
| `TRIVY_TIMEOUT` | No | `300` | Scan timeout in seconds |
| `TRIVY_PLATFORM` | No | (auto) | Target platform for Trivy scans (e.g. `linux/amd64`) |
| `SCAN_NAMESPACES` | No | (all) | Comma-separated namespaces to scan |
| `EXCLUDE_NAMESPACES` | No | `kube-system,...` | Namespaces to exclude |
| `SCAN_EXTRA_IMAGES` | No | `''` | Comma-separated full image references (`registry/ns/repo:tag`) to scan in addition to deployed images, e.g. images published but not run in the cluster. Entries need a tag or digest; a bad entry is logged and counted under "Scans Failed" without aborting the run |
| `DISCORD_WEBHOOK_URL` | No | (disabled) | Discord webhook URL for the scan report |

**OCIR Cleanup** (`packages/ocir_cleanup/src/ocir_cleanup/main.py`, `python -m ocir_cleanup`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTLP_ENDPOINT` | No | `http://localhost:4317` | OTLP collector endpoint |
| `OTLP_INSECURE` | No | `true` | Use insecure gRPC connection |
| `OTLP_METRICS_ENABLED` | No | `false` | Enable OTLP metrics export |
| `OTLP_LOGS_ENABLED` | No | `false` | Enable OTLP logs export |
| `SCAN_NAMESPACES` | No | (all) | Comma-separated namespaces to scan for deployed images |
| `EXCLUDE_NAMESPACES` | No | `kube-system,...` | Namespaces to exclude |
| `DISCORD_WEBHOOK_URL` | No | (disabled) | Discord webhook URL for cleanup recommendations / deletion results |
| `OCIR_CLEANUP_ENABLED` | No | `false` | Enable automatic deletion of old OCIR commit hash tags |
| `OCIR_CLEANUP_KEEP_COUNT` | No | `5` | Number of recent commit hash tags to keep per repository (or per group, if `CLEANUP_GROUP_BY_REGEX` is set) |
| `OCIR_EXTRA_REPOSITORIES` | No | `''` | Check extra repos for old images to remove |
| `CLEANUP_PROTECT_TAGS_REGEX` | No | `''` | Tags whose name fully matches are excluded from the deletion pool. Used to protect mutable "channel" tags (e.g. `^\d+\.\d+$` for ci-base-images' `:3.X`). |
| `CLEANUP_GROUP_BY_REGEX` | No | `''` | When set, the candidate pool is grouped by the first capture group and `keep_count` is applied per group. Prevents heavy churn in one group from pushing other groups' tags out of the keep window (e.g. `^(\d+\.\d+)` keeps the last N `:3.X-<sha>` per minor independently). |
| `CLEANUP_REPO` | No | `''` | Scope the run to one OCIR repo (namespace-qualified, e.g. `tnoff/discord_bot`) |

**Secret-age tracker** (`packages/secret_age/src/secret_age/config.py`, `python -m secret_age`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | No | (disabled) | Webhook for the report |
| `SECRET_AGE_WARN_DAYS` | No | `90` | Age at which a credential is reported as WARN |
| `SECRET_AGE_ROTATE_DAYS` | No | `180` | Age at which a credential is reported as ROTATE |
| `OCI_TENANCY_OCID` | For OCI reader | (empty) | Tenancy to enumerate IAM users in; the OCI reader is skipped when unset |
| `LAYER1_CONFIGMAP_NAME` | No | `layer-1-rotation-ledger` | Ledger ConfigMap name |
| `LAYER1_CONFIGMAP_NAMESPACE` | No | `security-scanner` | Ledger ConfigMap namespace |
| `ENABLE_OCI_READER` / `ENABLE_K8S_READER` / `ENABLE_LAYER1_READER` | No | `true` | Turn individual readers off |

## Secret-age tracker

Three readers, each failing independently (a broken reader logs and the others
still report):

- **OCI IAM**: per user, the `time_created` of auth tokens, customer secret
  keys and API keys. Needs `inspect users` on the tenancy via `~/.oci/config`.
- **Kubernetes Secrets**: every non-system Secret (service-account-token, helm
  release and bootstrap-token types are skipped). Age comes from the
  `secret-age-tracker.tnoff/last-rotated` annotation (`YYYY-MM-DD`), falling
  back to `creationTimestamp`, which is unreliable for Secrets updated in
  place. The annotation value `unknown` is the terraform-seeded sentinel: it
  is reported as its own UNKNOWN bucket, never as age 0. A separate
  `secret-age-tracker.tnoff/expires-at` annotation yields a days-to-expiry
  finding for credentials with a hard provider-enforced expiry. Needs cluster-wide
  `list` on `secrets` (metadata only; `.data` is never read).
- **Layer-1 ledger**: a ConfigMap of `{tfvar_name: date}` for admin tfvars that
  never reach the cluster, written from terraform-admin.

Findings are bucketed ROTATE / WARN / UNKNOWN / OK, oldest first, and posted to
Discord as monospace tables plus a CSV. OCI IAM findings carry no rotation
command on purpose: those credentials are mostly terraform-managed, and rotating
them out of band would cause state drift (see the secret-rotation runbook in
`tnoff/terraform-admin`).

## Scoping a cleanup run

Setting `CLEANUP_REPO` scopes a run to a single OCIR repo; unset, cleanup sweeps
every image deployed in the cluster plus `OCIR_EXTRA_REPOSITORIES`. The deployed
CronJob leaves it unset and is the only scheduled pruner. For an ad hoc
single-repo run, create a Job from the cleanup CronJob with `CLEANUP_REPO` set.
The currently deployed tag is always protected via k8s discovery.

## Required Permissions

To enable OCIR cleanup, the OCI user/principal used by the **cleanup**
image must have the `manage repos in compartment <name>` permission for
each compartment containing OCIR repositories. Read-only operations
(listing tags, resolving manifests) only require `inspect repos` /
`read repos`.

## Reporting

Console logs are enabled by default for both images; logs and metrics can
additionally be exported via OTLP (tracing is not wired in). Set each
image's own `DISCORD_WEBHOOK_URL` independently — the scanner posts the
scan report, cleanup posts recommendations/deletion results; they are two
separate webhook configs now, not a shared URL with a cleanup-specific
override.

### Deletion report format

Both cleanup passes (old tags, orphan manifests) post one entry per scanned repo, not a table:
`Deleted N <repo> images:` plus a code block of tags for repos with deletions, and an explicit
`No <repo> images deleted.` line for repos that came back clean. The `## Images Deleted` heading appears
only when something was deleted; a run that scanned nothing posts `No images were deleted.`. The clean
repo names come from the scanned set (deployed OCIR repos plus `OCIR_EXTRA_REPOSITORIES`) that `main.py`
passes to the notifier, because a `CleanupRecommendation` exists only for a repo with something to delete.
