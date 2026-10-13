"""Tests for reading and validating the key set."""

import json
from unittest.mock import MagicMock

import pytest

from jwks_publisher import jwks
from jwks_publisher.jwks import JwksError, canonicalize, fetch_live_jwks

from helpers import jwks_text, make_key


def test_canonicalize_sorts_keys_and_ignores_whitespace_and_order():
    a, b = make_key("a"), make_key("b")
    first, kids = canonicalize(jwks_text(b, a))
    second, _ = canonicalize(json.dumps({"keys": [a, b]}, indent=2))
    assert first == second
    assert kids == ["a", "b"]
    assert " " not in first


def test_canonicalize_accepts_missing_alg_and_use():
    key = make_key("a")
    del key["alg"], key["use"]
    assert canonicalize(jwks_text(key))[1] == ["a"]


@pytest.mark.parametrize("body", [b"not json", "{", b""])
def test_canonicalize_rejects_invalid_json(body):
    with pytest.raises(JwksError, match="not valid JSON"):
        canonicalize(body)


@pytest.mark.parametrize("body", ["[]", '"x"', "{}", '{"keys": "x"}', '{"keys": []}'])
def test_canonicalize_rejects_missing_or_empty_keys(body):
    with pytest.raises(JwksError, match="no keys"):
        canonicalize(body)


def test_canonicalize_rejects_non_object_key():
    with pytest.raises(JwksError, match="not an object"):
        canonicalize('{"keys": ["x"]}')


@pytest.mark.parametrize("member", sorted(jwks.PRIVATE_MEMBERS))
def test_canonicalize_refuses_private_members(member):
    with pytest.raises(JwksError, match="private members"):
        canonicalize(jwks_text(make_key("a", **{member: "secret"})))


def test_canonicalize_rejects_unsupported_key_type():
    with pytest.raises(JwksError, match="unsupported key type"):
        canonicalize(jwks_text(make_key("a", kty="EC")))


@pytest.mark.parametrize("member", ["kid", "n", "e"])
@pytest.mark.parametrize("bad", [None, "", 5])
def test_canonicalize_requires_kid_n_e(member, bad):
    key = make_key("a")
    key[member] = bad
    with pytest.raises(JwksError, match=f"missing {member}"):
        canonicalize(jwks_text(key))


@pytest.mark.parametrize("override", [{"alg": "HS256"}, {"use": "enc"}])
def test_canonicalize_requires_rs256_signing_key(override):
    with pytest.raises(JwksError, match="not an RS256 signing key"):
        canonicalize(jwks_text(make_key("a", **override)))


def test_canonicalize_rejects_duplicate_kid():
    with pytest.raises(JwksError, match="duplicate kid"):
        canonicalize(jwks_text(make_key("a"), make_key("a")))


def test_fetch_live_jwks_sends_service_account_token(tmp_path, mocker):
    (tmp_path / "token").write_text("tok\n", encoding="utf-8")
    (tmp_path / "ca.crt").write_text("ca", encoding="utf-8")
    context = mocker.patch("jwks_publisher.jwks.ssl.create_default_context")
    response = MagicMock()
    response.read.return_value = b'{"keys":[]}'
    opener = mocker.patch("jwks_publisher.jwks.urllib.request.urlopen")
    opener.return_value.__enter__.return_value = response

    body = fetch_live_jwks("https://api.test/openid/v1/jwks", str(tmp_path), 7)

    assert body == b'{"keys":[]}'
    context.assert_called_once_with(cafile=str(tmp_path / "ca.crt"))
    request = opener.call_args.args[0]
    assert request.full_url == "https://api.test/openid/v1/jwks"
    assert request.get_header("Authorization") == "Bearer tok"
    assert opener.call_args.kwargs == {"timeout": 7, "context": context.return_value}


@pytest.mark.parametrize("url", ["http://api.test/jwks", "file:///etc/passwd", "ftp://x/jwks"])
def test_fetch_live_jwks_refuses_non_https(url, tmp_path):
    with pytest.raises(JwksError, match="non-https"):
        fetch_live_jwks(url, str(tmp_path), 5)
