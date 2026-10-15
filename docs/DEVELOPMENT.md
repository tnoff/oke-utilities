# Development

## Prerequisites

- Python 3.11+
- [Trivy](https://trivy.dev/latest/getting-started/installation/) installed and on `$PATH`
- OCI config (`~/.oci/config`) with credentials for the tenancy you're targeting
- A kubeconfig with access to the OKE cluster you want to scan

## Setup

Scan, cleanup and secret-age-tracker are separate installable packages (see
`AGENTS.md`'s File Structure). To work on the scanner:

```bash
pip install -e ".[dev]" -e packages/core[telemetry] -e packages/scan
```

To also work on cleanup, secret-age-tracker, the JWKS publisher or the RPST sidecar:

```bash
pip install -e packages/ocir_cleanup -e packages/secret_age -e packages/jwks_publisher -e packages/rpst_sidecar
```

## Running tests

Run the full suite (pytest, pylint, bandit):

```bash
tox
```

Or run individual steps:

```bash
tox -e pytest    # tests only
tox -e pylint    # linting only
tox -e bandit    # security scan only
```

## Configuration

Each package has its own env vars and `Config` dataclass (no shared `Config`);
all are optional with defaults. The full per-package reference is in
[README.md](README.md#environment-variables). `OCIR_CLEANUP_ENABLED` defaults to
`false` (dry run), so a local `python -m ocir_cleanup` only reports.

## Running locally

```bash
export KUBECONFIG=~/.kube/config
# set any other variables you need ...

python -m scan             # scanner
python -m ocir_cleanup     # OCIR cleanup (pip install -e packages/ocir_cleanup first)
python -m secret_age       # secret-age tracker (pip install -e packages/secret_age first)
python -m jwks_publisher   # JWKS publisher (pip install -e packages/jwks_publisher first; set JWKS_BUCKET, DRY_RUN=true to preview)
python -m rpst_sidecar     # RPST sidecar (pip install -e packages/rpst_sidecar first; set RPST_DOMAIN_URL, point RPST_CLIENT_DIR / SERVICE_ACCOUNT_TOKEN_FILE / OUTPUT_DIR at local files)
python -m rpst_sidecar --check  # exit 0 only if OUTPUT_DIR holds a fresh rpst and private.pem
```
