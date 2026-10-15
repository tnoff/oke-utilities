"""Tests for the exchange request and JWT helpers."""

import base64
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization

from rpst_sidecar import exchange as ex
from rpst_sidecar.exchange import ExchangeError

from conftest import DOMAIN, SA_TOKEN
from helpers import FakeResponse, http_error, make_jwt, ok_response


def sent_request(urlopen):
    """The Request and its decoded form fields from the last patched urlopen call."""
    request = urlopen.call_args.args[0]
    return request, dict(urllib.parse.parse_qsl(request.data.decode()))


def test_request_shape(cfg, key):
    with mock.patch.object(ex, "urlopen", return_value=ok_response("rpst-token")) as urlopen:
        token, sa = ex.exchange(cfg, key)
    assert token == "rpst-token"
    assert sa == SA_TOKEN
    request, form = sent_request(urlopen)
    assert urlopen.call_args.args[1] == cfg.request_timeout
    assert request.full_url == f"{DOMAIN}/oauth2/v1/token"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    expected_basic = base64.b64encode(b"the-client-id:the-client-secret").decode()
    assert request.get_header("Authorization") == f"Basic {expected_basic}"
    assert set(form) == {
        "grant_type", "requested_token_type", "subject_token_type", "subject_token",
        "public_key", "res_type", "rpst_exp",
    }
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert form["requested_token_type"] == "urn:oci:token-type:oci-rpst"
    assert form["subject_token_type"] == "jwt"
    assert form["subject_token"] == SA_TOKEN
    assert form["res_type"] == "k8sworkload"
    assert form["rpst_exp"] == "180"


def test_public_key_is_base64_der_of_the_key(cfg, key):
    with mock.patch.object(ex, "urlopen", return_value=ok_response("t")) as urlopen:
        ex.exchange(cfg, key)
    _, form = sent_request(urlopen)
    der = base64.b64decode(form["public_key"], validate=True)
    loaded = serialization.load_der_public_key(der)
    assert loaded.public_numbers() == key.public_key().public_numbers()
    assert loaded.key_size == 2048


def test_private_pem_is_unencrypted_pkcs8(key):
    pem = ex.private_pem(key)
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert serialization.load_pem_private_key(pem, password=None).private_numbers() == key.private_numbers()


def test_rpst_exp_follows_config(cfg, key):
    cfg.exp_minutes = 45
    with mock.patch.object(ex, "urlopen", return_value=ok_response("t")) as urlopen:
        ex.exchange(cfg, key)
    assert sent_request(urlopen)[1]["rpst_exp"] == "45"


def test_credentials_and_token_reread_and_stripped(cfg, key, tmp_path):
    with mock.patch.object(ex, "urlopen", return_value=ok_response("t")) as urlopen:
        ex.exchange(cfg, key)
        (tmp_path / "client" / "client_id").write_text("  id2 \n\n")
        (tmp_path / "client" / "client_secret").write_text("secret2\r\n")
        (tmp_path / "token").write_text(" a.b\n.c \n")
        ex.exchange(cfg, key)
    request, form = sent_request(urlopen)
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(b"id2:secret2").decode()
    assert form["subject_token"] == "a.b.c"


def test_missing_input_file(cfg, key):
    Path(cfg.client_dir, "client_secret").unlink()
    with mock.patch.object(ex, "urlopen") as urlopen:
        with pytest.raises(ExchangeError, match="cannot read an input file"):
            ex.exchange(cfg, key)
    urlopen.assert_not_called()


def test_http_error_reports_status_and_truncated_body(cfg, key):
    with mock.patch.object(ex, "urlopen", side_effect=http_error(400, "x" * 500)):
        with pytest.raises(ExchangeError) as info:
            ex.exchange(cfg, key)
    assert str(info.value) == "HTTP 400: " + "x" * 200


def test_error_body_is_redacted(cfg, key):
    body = f"bad: the-client-secret and {SA_TOKEN}"
    with mock.patch.object(ex, "urlopen", side_effect=http_error(401, body)):
        with pytest.raises(ExchangeError) as info:
            ex.exchange(cfg, key)
    assert "the-client-secret" not in str(info.value)
    assert SA_TOKEN not in str(info.value)
    assert "[redacted]" in str(info.value)


def test_network_error(cfg, key):
    with mock.patch.object(ex, "urlopen", side_effect=urllib.error.URLError("boom")):
        with pytest.raises(ExchangeError, match="request failed"):
            ex.exchange(cfg, key)


def test_timeout(cfg, key):
    with mock.patch.object(ex, "urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(ExchangeError, match="timed out"):
            ex.exchange(cfg, key)


def test_non_200_success_status(cfg, key):
    with mock.patch.object(ex, "urlopen", return_value=FakeResponse("accepted", status=202)):
        with pytest.raises(ExchangeError, match="HTTP 202: accepted"):
            ex.exchange(cfg, key)


@pytest.mark.parametrize("body", ["not json", "[1, 2]", "{}", '{"token": ""}', '{"token": 5}', '{"other": "x"}'])
def test_bad_bodies_fail(cfg, key, body):
    with mock.patch.object(ex, "urlopen", return_value=FakeResponse(body)):
        with pytest.raises(ExchangeError):
            ex.exchange(cfg, key)


def test_urlopen_wrapper_passes_through():
    with mock.patch("urllib.request.urlopen", return_value="resp") as inner:
        assert ex.urlopen("req", 7) == "resp"
    inner.assert_called_once_with("req", timeout=7)


def test_jwt_claims():
    assert ex.jwt_claims(make_jwt(sub="s", exp=5)) == {"sub": "s", "exp": 5}


@pytest.mark.parametrize("token", ["", "nodots", "a.!!!.c", "a.bm90LWpzb24.c", "a.WzFd.c"])
def test_jwt_claims_unparseable(token):
    assert ex.jwt_claims(token) == {}


def test_generate_key_is_rsa_2048():
    assert ex.generate_key().key_size == 2048


def test_redact_skips_empty_secrets():
    assert ex.redact("hello", ("",)) == "hello"
