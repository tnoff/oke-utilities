"""Tests for the hosted-object wrapper."""

from unittest.mock import MagicMock

import oci
import pytest

from jwks_publisher.hosted import HostedJwks


def service_error(status):
    return oci.exceptions.ServiceError(status, "Code", {}, "message")


@pytest.fixture
def client():
    mock = MagicMock()
    mock.get_namespace.return_value.data = "tnoff"
    return mock


def test_builds_a_client_from_the_oci_config_file(cfg, mocker):
    from_file = mocker.patch("jwks_publisher.hosted.oci.config.from_file", return_value={"region": "r"})
    object_client = mocker.patch("jwks_publisher.hosted.oci.object_storage.ObjectStorageClient")
    cfg.oci_config_file = "/cfg/config"
    cfg.oci_profile = "bot"

    hosted = HostedJwks(cfg)

    from_file.assert_called_once_with(file_location="/cfg/config", profile_name="bot")
    object_client.assert_called_once_with({"region": "r"})
    assert hosted.bucket == "k8s_oidc_jwks"


def test_namespace_is_fetched_once(cfg, client):
    hosted = HostedJwks(cfg, client=client)
    assert hosted.namespace == "tnoff"
    assert hosted.namespace == "tnoff"
    client.get_namespace.assert_called_once_with()


def test_read_returns_body_and_etag(cfg, client):
    client.get_object.return_value.data.content = b'{"keys":[]}'
    client.get_object.return_value.headers = {"etag": "abc"}
    hosted = HostedJwks(cfg, client=client)
    assert hosted.read() == ('{"keys":[]}', "abc")
    client.get_object.assert_called_once_with("tnoff", "k8s_oidc_jwks", "jwks.json")


def test_read_returns_none_when_the_object_is_missing(cfg, client):
    client.get_object.side_effect = service_error(404)
    assert HostedJwks(cfg, client=client).read() is None


def test_read_reraises_other_service_errors(cfg, client):
    client.get_object.side_effect = service_error(403)
    with pytest.raises(oci.exceptions.ServiceError):
        HostedJwks(cfg, client=client).read()


def test_write_overwrites_only_if_unchanged(cfg, client):
    HostedJwks(cfg, client=client).write("body", "etag-1")
    client.put_object.assert_called_once_with(
        "tnoff",
        "k8s_oidc_jwks",
        "jwks.json",
        b"body",
        content_type="application/json",
        cache_control="no-store",
        if_match="etag-1",
    )
