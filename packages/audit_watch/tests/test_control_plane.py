"""Tests for reading the API server addresses."""

import io
import json
import urllib.error
from unittest import mock

import pytest

from audit_watch import control_plane as cp

SLICES = {"items": [{"endpoints": [{"addresses": ["10.0.0.10"]}, {"addresses": ["10.0.0.11"]}]}]}


class Response:
    """What urlopen's context manager yields."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return io.BytesIO(self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode())

    def __exit__(self, *_):
        return False


@pytest.fixture
def sa_dir(tmp_path, monkeypatch):
    """A ServiceAccount directory, in a cluster (KUBERNETES_SERVICE_* set), with the CA load stubbed."""
    (tmp_path / "token").write_text("TOKEN\n", encoding="utf-8")
    (tmp_path / "ca.crt").write_text("not a real certificate", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    with mock.patch.object(cp.ssl, "create_default_context", return_value="ctx"):
        yield tmp_path


def test_reads_every_address_of_the_kubernetes_endpointslice(sa_dir):
    with mock.patch.object(cp, "urlopen", return_value=Response(SLICES)) as urlopen:
        assert cp.api_server_ips(sa_dir) == {"10.0.0.10", "10.0.0.11"}
    request, timeout, context = urlopen.call_args.args
    assert request.full_url == (
        "https://10.0.0.1:443/apis/discovery.k8s.io/v1/namespaces/default/endpointslices"
        "?labelSelector=kubernetes.io%2Fservice-name%3Dkubernetes"
    )
    assert request.get_header("Authorization") == "Bearer TOKEN"
    assert (timeout, context) == (cp.REQUEST_TIMEOUT, "ctx")


def test_not_in_a_cluster(sa_dir, monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    with pytest.raises(cp.ControlPlaneError, match="not set"):
        cp.api_server_ips(sa_dir)


def test_port_defaults_to_443(sa_dir, monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT")
    with mock.patch.object(cp, "urlopen", return_value=Response(SLICES)) as urlopen:
        cp.api_server_ips(sa_dir)
    assert urlopen.call_args.args[0].full_url.startswith("https://10.0.0.1:443/")


def test_missing_token(sa_dir):
    (sa_dir / "token").unlink()
    with pytest.raises(cp.ControlPlaneError, match="token or CA"):
        cp.api_server_ips(sa_dir)


def test_unusable_ca(sa_dir):
    with mock.patch.object(cp.ssl, "create_default_context", side_effect=cp.ssl.SSLError("bad pem")):
        with pytest.raises(cp.ControlPlaneError, match="token or CA"):
            cp.api_server_ips(sa_dir)


def test_forbidden_names_the_status_and_not_the_token(sa_dir):
    error = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)
    with mock.patch.object(cp, "urlopen", side_effect=error):
        with pytest.raises(cp.ControlPlaneError, match="HTTP 403") as caught:
            cp.api_server_ips(sa_dir)
    assert "TOKEN" not in str(caught.value)


def test_network_failure(sa_dir):
    with mock.patch.object(cp, "urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(cp.ControlPlaneError, match="request failed"):
            cp.api_server_ips(sa_dir)


@pytest.mark.parametrize("body", [b"not json", {"no": "items"}, {"items": 5}, {"items": [5]}])
def test_unexpected_response(sa_dir, body):
    with mock.patch.object(cp, "urlopen", return_value=Response(body)):
        with pytest.raises(cp.ControlPlaneError, match="unexpected"):
            cp.api_server_ips(sa_dir)


@pytest.mark.parametrize("body", [{"items": []}, {"items": [{"endpoints": None}]}, {"items": [{"endpoints": [{}]}]}])
def test_no_addresses_is_an_error(sa_dir, body):
    with mock.patch.object(cp, "urlopen", return_value=Response(body)):
        with pytest.raises(cp.ControlPlaneError, match="no addresses"):
            cp.api_server_ips(sa_dir)


def test_urlopen_passes_the_context_through():
    with mock.patch.object(cp.urllib.request, "urlopen", return_value="resp") as urlopen:
        assert cp.urlopen("req", 5, "ctx") == "resp"
    urlopen.assert_called_once_with("req", timeout=5, context="ctx")
