#!/usr/bin/env bash
# End-to-end check of the RPST sidecar against the real identity domain, run from a laptop.
#
# 1. Mints a real ServiceAccount token for an onboarded workload (`kubectl create token`, same
#    issuer and signing keys as the projected token a pod gets, audience = the domain URL).
# 2. Copies the exchange app's client_id / client_secret out of the cluster.
# 3. Runs the sidecar's --once (one real exchange), then --check and the legacy
#    `python -m rpst_sidecar --check` command line.
# 4. Signs a real OCI call with the RPST through the OCI Python SDK's resource principal
#    signer, exactly as a main container does.
#
# Required env:
#   RPST_DOMAIN_URL   identity domain endpoint, e.g. https://idcs-xxxx.identity.oraclecloud.com:443
#   NAMESPACE         namespace of an onboarded workload
#   SERVICE_ACCOUNT   that workload's ServiceAccount (the one the federation trust names)
#   CLIENT_SECRET     name of the Secret in NAMESPACE holding the exchange app's credentials
#   OCI_REGION        region for the SDK call, e.g. us-sanjose-1
# Optional env:
#   CLIENT_ID_KEY / CLIENT_SECRET_KEY   keys in that Secret (default client_id / client_secret)
#   IMAGE             run this image instead of `go run .`, e.g. a local `docker build` tag
#   DOCKER_ARGS       extra `docker run` arguments with IMAGE, e.g. "--network host"
#   KUBECTL           kubectl command (default "kubectl"; e.g. "kubectl --context oke")
#
# Nothing secret is printed. Everything is written to a 0700 temp dir that is removed on exit.
# Each run is one real exchange: it shows up in the identity domain's audit like any refresh.
set -euo pipefail

: "${RPST_DOMAIN_URL:?set RPST_DOMAIN_URL}"
: "${NAMESPACE:?set NAMESPACE}"
: "${SERVICE_ACCOUNT:?set SERVICE_ACCOUNT}"
: "${CLIENT_SECRET:?set CLIENT_SECRET (the Secret name)}"
: "${OCI_REGION:?set OCI_REGION}"
CLIENT_ID_KEY="${CLIENT_ID_KEY:-client_id}"
CLIENT_SECRET_KEY="${CLIENT_SECRET_KEY:-client_secret}"
KUBECTL="${KUBECTL:-kubectl}"
IMAGE="${IMAGE:-}"
read -r -a docker_args <<< "${DOCKER_ARGS:-}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
work="$(mktemp -d "${TMPDIR:-/tmp}/rpst-smoke.XXXXXX")"
cleanup() { [ -n "${work}" ] && [ -d "${work}" ] && rm -rf -- "${work}"; }
trap cleanup EXIT
mkdir -p "${work}/client" "${work}/token" "${work}/out"

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

step "Minting a ServiceAccount token for ${NAMESPACE}/${SERVICE_ACCOUNT} (10 min, audience ${RPST_DOMAIN_URL})"
${KUBECTL} create token "${SERVICE_ACCOUNT}" -n "${NAMESPACE}" \
  --audience "${RPST_DOMAIN_URL}" --duration 10m > "${work}/token/token" \
  || fail "kubectl create token"

step "Reading ${NAMESPACE}/${CLIENT_SECRET} (keys ${CLIENT_ID_KEY}, ${CLIENT_SECRET_KEY})"
for pair in "client_id:${CLIENT_ID_KEY}" "client_secret:${CLIENT_SECRET_KEY}"; do
  file="${pair%%:*}" key="${pair#*:}"
  ${KUBECTL} get secret "${CLIENT_SECRET}" -n "${NAMESPACE}" -o "jsonpath={.data.${key}}" \
    | base64 -d > "${work}/client/${file}"
  [ -s "${work}/client/${file}" ] || fail "Secret ${CLIENT_SECRET} has no non-empty key ${key}"
done

# Runs the sidecar with the env a pod would give it. Arguments are the command line after the binary.
sidecar() {
  if [ -n "${IMAGE}" ]; then
    # As the invoking user so the 0640 outputs stay readable for the SDK step below.
    docker run --rm --user "$(id -u):$(id -g)" ${docker_args[@]+"${docker_args[@]}"} \
      -e RPST_DOMAIN_URL="${RPST_DOMAIN_URL}" -e RPST_CLIENT_DIR=/c \
      -e SERVICE_ACCOUNT_TOKEN_FILE=/t/token -e OUTPUT_DIR=/o \
      -e POD_NAME=oci-smoke -e POD_NAMESPACE="${NAMESPACE}" -e SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT}" \
      -v "${work}/client:/c:ro" -v "${work}/token:/t:ro" -v "${work}/out:/o" \
      "${IMAGE}" "$@"
  else
    (cd "${here}" && \
      RPST_DOMAIN_URL="${RPST_DOMAIN_URL}" RPST_CLIENT_DIR="${work}/client" \
      SERVICE_ACCOUNT_TOKEN_FILE="${work}/token/token" OUTPUT_DIR="${work}/out" \
      POD_NAME=oci-smoke POD_NAMESPACE="${NAMESPACE}" SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT}" \
      go run . "${@:2}")
  fi
}

step "Exchange: rpst-sidecar --once (${IMAGE:-go run .})"
sidecar rpst-sidecar --once || fail "--once: the exchange failed (see the ERROR line above)"
[ -s "${work}/out/rpst" ] && [ -s "${work}/out/private.pem" ] || fail "--once did not write rpst and private.pem"
if [ "$(tail -c 1 "${work}/out/rpst" | od -An -c | tr -d ' ')" = '\n' ]; then
  fail "rpst ends in a newline (the OCI SDK rejects that)"
fi
ls -l "${work}/out"

step "Probe: rpst-sidecar --check"
sidecar rpst-sidecar --check || fail "--check"
echo ok
if [ -n "${IMAGE}" ]; then
  step "Probe, Python-image command line: python -m rpst_sidecar --check"
  sidecar python -m rpst_sidecar --check || fail "legacy --check"
  echo ok
fi

step "OCI SDK: sign a call with the RPST (resource principal 2.2)"
if ! python3 -c 'import oci' 2>/dev/null; then
  echo "SKIP: the oci Python package is not installed (pip install oci), so the RPST was issued but not used"
  exit 0
fi
OCI_RESOURCE_PRINCIPAL_VERSION=2.2 \
OCI_RESOURCE_PRINCIPAL_RPST="${work}/out/rpst" \
OCI_RESOURCE_PRINCIPAL_PRIVATE_PEM="${work}/out/private.pem" \
OCI_RESOURCE_PRINCIPAL_REGION="${OCI_REGION}" \
python3 - <<'PY' || fail "the SDK could not use the RPST"
import sys

import oci

try:
    signer = oci.auth.signers.get_resource_principals_signer()
except Exception as exc:  # the SDK could not load the files the sidecar wrote
    sys.exit(f"cannot build the resource principal signer: {type(exc).__name__}: {exc}")
print("signer built: tenancy", signer.tenancy_id, "region", signer.region)
client = oci.object_storage.ObjectStorageClient(
    config={"region": signer.region}, signer=signer, retry_strategy=oci.retry.NoneRetryStrategy())
try:
    # GetNamespace needs no IAM policy: it proves the token and key sign, not what the workload may do.
    print("Object Storage namespace:", client.get_namespace().data)
except oci.exceptions.ServiceError as exc:
    sys.exit(f"OCI rejected the signed call: HTTP {exc.status} {exc.code}: {exc.message}")
except oci.exceptions.RequestException as exc:
    sys.exit(f"could not reach OCI: {str(exc).splitlines()[0][:300]}")
PY

printf '\nPASS: real exchange, files, probes and an SDK call signed with the RPST\n'
