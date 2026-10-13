"""Tests for the mirror pass."""

import logging
from unittest.mock import MagicMock

import oci
import pytest

from jwks_publisher import main as main_module
from jwks_publisher.jwks import JwksError, canonicalize

from helpers import jwks_text, make_key

LIVE = jwks_text(make_key("new"), make_key("old"))
LIVE_CANONICAL = canonicalize(LIVE)[0]


def service_error(status, message="message"):
    return oci.exceptions.ServiceError(status, "Code", {}, message)


@pytest.fixture
def live(mocker):
    return mocker.patch("jwks_publisher.main.fetch_live_jwks", return_value=LIVE.encode())


@pytest.fixture
def hosted():
    mock = MagicMock()
    mock.read.return_value = (jwks_text(make_key("old")), "etag-1")
    return mock


def test_requires_a_bucket(cfg, caplog):
    cfg.bucket_name = ""
    with caplog.at_level(logging.ERROR):
        assert main_module.run(cfg, MagicMock()) == 1
    assert "JWKS_BUCKET" in caplog.text


@pytest.mark.parametrize("error", [JwksError("bad"), OSError("refused")])
def test_leaves_the_hosted_copy_alone_when_the_live_set_is_unusable(cfg, hosted, mocker, error):
    mocker.patch("jwks_publisher.main.fetch_live_jwks", side_effect=error)
    assert main_module.run(cfg, hosted) == 1
    hosted.read.assert_not_called()
    hosted.write.assert_not_called()


def test_rejects_a_live_set_with_private_material(cfg, hosted, mocker):
    mocker.patch("jwks_publisher.main.fetch_live_jwks", return_value=jwks_text(make_key("a", d="x")).encode())
    assert main_module.run(cfg, hosted) == 1
    hosted.write.assert_not_called()


def test_fails_when_the_hosted_set_cannot_be_read(cfg, live, hosted):
    hosted.read.side_effect = service_error(403)
    assert main_module.run(cfg, hosted) == 1


def test_builds_the_default_hosted_client(cfg, live, mocker):
    built = mocker.patch("jwks_publisher.main.HostedJwks")
    built.return_value.read.return_value = (LIVE_CANONICAL, "etag")
    assert main_module.run(cfg) == 0
    built.assert_called_once_with(cfg)


def test_reports_a_missing_hosted_object(cfg, live, hosted, caplog):
    hosted.read.return_value = None
    with caplog.at_level(logging.ERROR):
        assert main_module.run(cfg, hosted) == 1
    assert "does not exist" in caplog.text
    hosted.write.assert_not_called()


def test_does_nothing_when_the_hosted_set_is_current(cfg, live, hosted, caplog):
    # Same keys, different order and whitespace: still current.
    hosted.read.return_value = (jwks_text(make_key("old"), make_key("new")), "etag-1")
    with caplog.at_level(logging.INFO):
        assert main_module.run(cfg, hosted) == 0
    hosted.write.assert_not_called()
    assert "current" in caplog.text


def test_publishes_a_changed_set_and_logs_the_difference(cfg, live, hosted, caplog):
    hosted.read.side_effect = [(jwks_text(make_key("old")), "etag-1"), (LIVE_CANONICAL, "etag-2")]
    with caplog.at_level(logging.INFO):
        assert main_module.run(cfg, hosted) == 0
    hosted.write.assert_called_once_with(LIVE_CANONICAL, "etag-1")
    assert "added=['new'] removed=[] kept=['old']" in caplog.text
    assert "Published" in caplog.text


def test_replaces_the_empty_placeholder(cfg, live, hosted, caplog):
    hosted.read.side_effect = [('{"keys":[]}', "etag-1"), (LIVE_CANONICAL, "etag-2")]
    with caplog.at_level(logging.WARNING):
        assert main_module.run(cfg, hosted) == 0
    hosted.write.assert_called_once_with(LIVE_CANONICAL, "etag-1")
    assert "will be replaced" in caplog.text


def test_dry_run_writes_nothing(cfg, live, hosted):
    cfg.dry_run = True
    assert main_module.run(cfg, hosted) == 0
    hosted.write.assert_not_called()


def test_a_concurrent_change_fails_the_run_so_the_next_one_retries(cfg, live, hosted, caplog):
    hosted.write.side_effect = service_error(412, "etag mismatch")
    with caplog.at_level(logging.ERROR):
        assert main_module.run(cfg, hosted) == 1
    assert "412" in caplog.text


@pytest.mark.parametrize("readback", [None, ("something else", "etag-2")])
def test_fails_when_the_readback_does_not_match(cfg, live, hosted, readback):
    hosted.read.side_effect = [(jwks_text(make_key("old")), "etag-1"), readback]
    assert main_module.run(cfg, hosted) == 1


def test_main_wires_config_to_run(mocker):
    run = mocker.patch("jwks_publisher.main.run", return_value=0)
    mocker.patch("jwks_publisher.main.logging.basicConfig")
    assert main_module.main() == 0
    run.assert_called_once()
