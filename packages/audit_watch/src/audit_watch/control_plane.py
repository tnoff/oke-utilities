"""The cluster's own API server addresses, read from the in-cluster API at startup.

OKE's control plane acts on OCI as a resource principal too (it attaches and detaches volumes when a
pod with a PVC moves, among other things), and its Audit events look like a workload's: no principal
name, only a source IP. What tells them apart is that IP. The API server's addresses are the
`kubernetes` Service's EndpointSlice in the default namespace, so asking the cluster for them needs no
configuration that goes stale if the endpoint is ever recreated.
"""

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from logging import getLogger
from pathlib import Path

logger = getLogger(__name__)

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SLICES_PATH = "/apis/discovery.k8s.io/v1/namespaces/default/endpointslices"
SELECTOR = "kubernetes.io/service-name=kubernetes"
REQUEST_TIMEOUT = 10


class ControlPlaneError(Exception):
    """The API server addresses could not be read."""


def urlopen(request: urllib.request.Request, timeout: int, context: ssl.SSLContext):
    """The module's single HTTP seam; tests patch this."""
    # The URL is built from the in-cluster service address, always https.
    return urllib.request.urlopen(request, timeout=timeout, context=context)  # nosec B310


def api_server_ips(service_account_dir: Path = SERVICE_ACCOUNT_DIR) -> frozenset:
    """Addresses of the cluster's API server, as the `kubernetes` EndpointSlice lists them."""
    host = os.getenv("KUBERNETES_SERVICE_HOST")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        raise ControlPlaneError("KUBERNETES_SERVICE_HOST is not set (not running in a cluster?)")
    try:
        token = (service_account_dir / "token").read_text(encoding="utf-8").strip()
        context = ssl.create_default_context(cafile=str(service_account_dir / "ca.crt"))
    except (OSError, ssl.SSLError) as exc:
        raise ControlPlaneError(f"cannot read the ServiceAccount token or CA: {exc}") from exc

    query = urllib.parse.urlencode({"labelSelector": SELECTOR})
    request = urllib.request.Request(
        f"https://{host}:{port}{SLICES_PATH}?{query}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, REQUEST_TIMEOUT, context) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ControlPlaneError(f"HTTP {exc.code} listing the kubernetes EndpointSlice") from exc
    except OSError as exc:
        # URLError and timeouts are OSErrors.
        raise ControlPlaneError(f"request failed: {exc}") from exc

    try:
        slices = json.loads(body)["items"]
        found = frozenset(
            address
            for item in slices
            for endpoint in item.get("endpoints") or []
            for address in endpoint.get("addresses") or []
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ControlPlaneError(f"unexpected EndpointSlice response: {exc}") from exc
    if not found:
        raise ControlPlaneError("the kubernetes EndpointSlice lists no addresses")
    return found
