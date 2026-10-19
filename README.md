# OKE Utilities

Five independent packages/images for the OKE cluster. Three CronJobs share one
library: image vulnerability scanning (`oke-scan`, with OpenTelemetry
observability), OCIR tag/manifest cleanup (`ocir-cleanup`), and secret-age
tracking (`secret-age-tracker`). The JWKS publisher
(`jwks-publisher`) and the RPST sidecar (`rpst-sidecar`, a sidecar container rather than a CronJob) stand alone. The deployed manifests, schedules and
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
| JWKS publisher | Yes | Its own package/image (`packages/jwks_publisher/`, `python -m jwks_publisher`). Mirrors the cluster's ServiceAccount public key set into the Object Storage object that OCI Identity Domains fetches on every token exchange. See [JWKS publisher](#jwks-publisher). |
| RPST sidecar | No | Its own package/image (`packages/rpst_sidecar/`, `python -m rpst_sidecar`). A sidecar that trades the pod's projected ServiceAccount token for an OCI resource principal session token on a loop, so the main container needs no stored API key. See the "RPST sidecar" section. |

The scanner, cleanup and secret-age tracker share `packages/core/` (`oke-scanner-core`: image discovery, k8s
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
$ docker build -f packages/jwks_publisher/Dockerfile .  # JWKS publisher
$ docker build -f packages/rpst_sidecar/Dockerfile .    # RPST sidecar
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

## JWKS publisher

Part of federating in-cluster workloads to OCI with ServiceAccount tokens
(tnoff/terraform#191; [operator doc](https://github.com/tnoff/terraform-admin/blob/main/docs/k8s-workload-federation.md)
covers the whole design, onboarding and failure modes). OCI Identity Domains verifies a workload's token against
the cluster's public key set, but the API server is private, so the set is
hosted in a public bucket. Each run reads the live set from the API server with
the pod's own ServiceAccount and overwrites the hosted object if it differs.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWKS_BUCKET` | Yes | | Bucket holding the hosted key set (terraform `oci/`: `k8s_oidc_jwks`) |
| `JWKS_OBJECT` | No | `jwks.json` | Object to overwrite |
| `JWKS_URL` | No | `https://kubernetes.default.svc/openid/v1/jwks` | Live key set |
| `SERVICE_ACCOUNT_DIR` | No | `/var/run/secrets/kubernetes.io/serviceaccount` | Where `token` and `ca.crt` are read from |
| `OCI_CONFIG_FILE` / `OCI_PROFILE` | No | `~/.oci/config` / `DEFAULT` | The publisher bot's OCI config (its API key) |
| `REQUEST_TIMEOUT` | No | `15` | Seconds for the API server call |
| `DRY_RUN` | No | `false` | Log what would change, write nothing |

Safety rules, all enforced before anything is written:

- The live set must parse and contain at least one RSA RS256 signing key with a
  `kid`, `n` and `e`. Anything else is a failed run and the hosted copy is left
  alone.
- A key carrying any private member (`d`, `p`, `q`, `dp`, `dq`, `qi`, `oth`,
  `k`) is refused, never stripped. The hosted file is world readable.
- The write is conditional on the ETag read a moment earlier (`if_match`), so a
  concurrent change fails the run instead of being overwritten; the next run
  retries. A read-back confirms the write.
- The bot's IAM policy is `use objects` on that one bucket: it can overwrite but
  not create or delete, so terraform seeds the object and a missing object is an
  error, not something the publisher repairs.
- Every change logs the key ids added, removed and kept, which is the audit
  trail for an OKE key rotation.

Exit code 0 means the hosted set is current (or was just updated); 1 means it
could not be confirmed current. There is no OpenTelemetry: the CronJob's failure
is the signal.

## RPST sidecar

Part of federating in-cluster workloads to OCI with ServiceAccount tokens
(tnoff/terraform#191; [operator doc](https://github.com/tnoff/terraform-admin/blob/main/docs/k8s-workload-federation.md)
covers onboarding a workload, the policy rules and failure modes). The sidecar trades the pod's projected ServiceAccount
token for an OCI resource principal session token (RPST) at the identity
domain's token endpoint, writes it and its private key to a shared in-memory
volume, and repeats before it expires. The main container then signs OCI SDK
calls with no stored API key. Only `cryptography` is installed: HTTP is urllib,
and there is no OCI SDK in the image.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RPST_DOMAIN_URL` | Yes | | Identity domain endpoint, e.g. `https://idcs-xxxx.identity.oraclecloud.com:443`. Must be `https` |
| `RPST_CLIENT_DIR` | No | `/var/run/rpst/client` | Directory with files `client_id` and `client_secret` (the exchange app's credentials). Re-read on every exchange; trailing whitespace is stripped |
| `SERVICE_ACCOUNT_TOKEN_FILE` | No | `/var/run/secrets/tokens/oci/token` | Projected ServiceAccount token, audience = `RPST_DOMAIN_URL`. Re-read on every exchange |
| `OUTPUT_DIR` | No | `/var/run/rpst/out` | Shared memory-backed volume that receives the files below |
| `RPST_EXP_MINUTES` | No | `180` | Requested RPST lifetime, integer 15..720 |
| `REFRESH_FRACTION` | No | `0.5` | Refresh once this fraction (0 < x < 1) of the token's real lifetime (its `exp` minus `iat`) has passed; falls back to `RPST_EXP_MINUTES` if the claims are missing |
| `POD_NAME`, `POD_NAMESPACE`, `SERVICE_ACCOUNT_NAME` | No | `unknown` | Downward API values, used only in the audit log line |

Files written to `OUTPUT_DIR`, mode `0640`, each atomically (temp file in the
same directory, fsync, rename):

- `private.pem`: the unencrypted PKCS8 PEM private key. One RSA-2048 key is
  generated at process start and reused for every exchange, so this file is
  written once, before the first token, and never changes while the pod lives.
  The key exists only in memory and on the memory-backed volume.
- `rpst`: the token, with no trailing newline (the OCI SDK fails with "Invalid
  crypto padding" otherwise).

Failures never stop the sidecar: each exchange failure logs a WARNING with the
HTTP status and the first 200 characters of the error body (never the client
secret, ServiceAccount token or RPST) and retries with exponential backoff and
jitter, 2s doubling to a 120s cap. This includes startup: exchanges can fail
for around ten minutes after policy or IAM changes. Every successful exchange
logs one audit line (`jti`, the ServiceAccount token's `sub`, namespace, pod,
service account, issued and expires in UTC, lifetime in minutes). SIGTERM and
SIGINT stop it cleanly with exit code 0.

`python -m rpst_sidecar --check` starts no server: it exits 0 only if `rpst`
and `private.pem` exist and the token has more than 60 seconds left, otherwise
exits 1 with one line saying why.

### Pod wiring

- A projected `serviceAccountToken` volume with `audience` set to
  `RPST_DOMAIN_URL` and `expirationSeconds: 600`, mounted at the directory of
  `SERVICE_ACCOUNT_TOKEN_FILE`.
- The exchange app's credentials as a Secret volume at `RPST_CLIENT_DIR`.
- An `emptyDir` with `medium: Memory`, mounted at `OUTPUT_DIR` in the sidecar
  and read-only in the main container.
- `securityContext.fsGroup` on the pod so the main container can read the
  `0640` files.
- Run it as a native sidecar: an `initContainers` entry with
  `restartPolicy: Always`, with `startupProbe` and `livenessProbe` exec probes
  running `python -m rpst_sidecar --check`. The startup probe holds the main
  container back until the first token exists; the liveness probe restarts a
  sidecar that cannot refresh before the token expires.

Sketch of the sidecar container:

```yaml
initContainers:
  - name: rpst
    image: iad.ocir.io/tnoff/rpst_sidecar:<tag>
    restartPolicy: Always
    env:
      - {name: RPST_DOMAIN_URL, value: "https://idcs-xxxx.identity.oraclecloud.com:443"}
      - {name: POD_NAME, valueFrom: {fieldRef: {fieldPath: metadata.name}}}
      - {name: POD_NAMESPACE, valueFrom: {fieldRef: {fieldPath: metadata.namespace}}}
      - {name: SERVICE_ACCOUNT_NAME, valueFrom: {fieldRef: {fieldPath: spec.serviceAccountName}}}
    startupProbe:
      exec: {command: [python, -m, rpst_sidecar, --check]}
      periodSeconds: 5
      failureThreshold: 120
    livenessProbe:
      exec: {command: [python, -m, rpst_sidecar, --check]}
      periodSeconds: 60
volumes:
  - name: rpst-out
    emptyDir: {medium: Memory}
  - name: oci-token
    projected:
      sources:
        - serviceAccountToken: {audience: "https://idcs-xxxx.identity.oraclecloud.com:443", expirationSeconds: 600, path: token}
```

### Consuming it from the main container

Mount the same `emptyDir` and set the OCI SDK's resource principal variables:

```
OCI_RESOURCE_PRINCIPAL_VERSION=2.2
OCI_RESOURCE_PRINCIPAL_RPST=<dir>/rpst
OCI_RESOURCE_PRINCIPAL_PRIVATE_PEM=<dir>/private.pem
OCI_RESOURCE_PRINCIPAL_REGION=<region>
```

then `oci.auth.signers.get_resource_principals_signer()`. The SDK re-reads the
token file, so refreshes are picked up without restarting the main container.

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
