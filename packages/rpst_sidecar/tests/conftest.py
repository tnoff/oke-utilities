"""Shared fixtures for rpst_sidecar tests."""

import pytest

from rpst_sidecar.config import RpstConfig
from rpst_sidecar.exchange import generate_key

from helpers import make_jwt

DOMAIN = "https://idcs-test.identity.oraclecloud.com:443"
SA_TOKEN = make_jwt(sub="system:serviceaccount:apps:bot", aud=[DOMAIN])


@pytest.fixture(scope="session")
def key():
    """One RSA key for the whole test session (generation is slow)."""
    return generate_key()


@pytest.fixture
def cfg(tmp_path):
    """Config pointing at a temp tree with client creds and an SA token in place."""
    client = tmp_path / "client"
    out = tmp_path / "out"
    client.mkdir()
    out.mkdir()
    (client / "client_id").write_text("the-client-id\n")
    (client / "client_secret").write_text("the-client-secret\n")
    token = tmp_path / "token"
    token.write_text(SA_TOKEN + "\n")
    return RpstConfig(
        domain_url=DOMAIN,
        client_dir=str(client),
        token_file=str(token),
        output_dir=str(out),
        pod_name="pod-1",
        pod_namespace="apps",
        service_account_name="bot",
    )
