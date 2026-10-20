"""Tests for oke_scanner_core.log_redaction."""

import io
import logging

import pytest
import requests

from oke_scanner_core.log_redaction import REDACTED, install_log_redaction, redact

TOKEN = "wp9Kg9JSBa7PQ0sKkA4gF_j4wqtdZ1q4oXUxfz8Wx1qrBcG1KFBSknc5CDBaXopAgeJI"
URL = f"https://discord.com/api/webhooks/1519812820152094911/{TOKEN}"


@pytest.fixture
def captured():
    """Install redaction on a throwaway logger and return (logger, stream), restoring the factory after."""
    original = logging.getLogRecordFactory()
    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("test_log_redaction")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    yield logger, stream
    logger.removeHandler(handler)
    logging.setLogRecordFactory(original)


class TestRedact:
    def test_replaces_the_token_and_keeps_the_rest_of_the_url(self):
        assert redact(f"POST {URL} HTTP/1.1") == (
            f"POST https://discord.com/api/webhooks/1519812820152094911/{REDACTED} HTTP/1.1")

    def test_stops_at_the_query_string(self):
        assert redact(f"{URL}?wait=true") == (
            f"https://discord.com/api/webhooks/1519812820152094911/{REDACTED}?wait=true")

    def test_handles_the_urllib3_request_line(self):
        line = f'https://discord.com:443 "POST /api/webhooks/1519812820152094911/{TOKEN} HTTP/1.1" 204 0'
        assert TOKEN not in redact(line)

    def test_leaves_unrelated_text_alone(self):
        text = "GET https://iad.ocir.io/v2/tnoff/app/manifests/abc 200"
        assert redact(text) == text


class TestInstalledFactory:
    def test_scrubs_a_message_built_from_args_like_urllib3_does(self, captured):
        logger, stream = captured
        logger.debug('%s://%s:%s "%s %s %s" %s %s', "https", "discord.com", 443, "POST",
                     f"/api/webhooks/1519812820152094911/{TOKEN}", "HTTP/1.1", 204, 0)

        out = stream.getvalue()
        assert TOKEN not in out
        assert f"/api/webhooks/1519812820152094911/{REDACTED}" in out
        assert '"POST ' in out and " 204 0" in out

    def test_scrubs_an_f_string_message(self, captured):
        logger, stream = captured
        logger.error(f"Webhook failed: 400 Client Error: Bad Request for url: {URL}")

        assert TOKEN not in stream.getvalue()

    def test_scrubs_a_token_inside_an_exception_traceback(self, captured):
        logger, stream = captured
        try:
            raise requests.HTTPError(f"400 Client Error: Bad Request for url: {URL}")
        except requests.HTTPError:
            logger.exception("sending report failed")

        out = stream.getvalue()
        assert TOKEN not in out
        assert "sending report failed" in out
        assert "HTTPError" in out and REDACTED in out

    def test_keeps_exc_info_when_the_traceback_has_no_secret(self, captured):
        logger, stream = captured
        try:
            raise ValueError("plain failure")
        except ValueError:
            logger.exception("oops")

        assert "ValueError: plain failure" in stream.getvalue()

    def test_leaves_ordinary_records_untouched(self, captured):
        logger, stream = captured
        logger.info("scanned %d images in %s", 48, "13m")

        assert stream.getvalue() == "INFO test_log_redaction scanned 48 images in 13m\n"

    def test_mismatched_format_args_still_reach_the_handler_to_report(self, captured):
        logger, _stream = captured
        # logging swallows this at emit time (handleError); the factory must not raise from logger.info
        logging.raiseExceptions = False
        try:
            logger.info("needs two %s %s", "only-one")
        finally:
            logging.raiseExceptions = True

    def test_install_is_idempotent(self, captured):
        first = logging.getLogRecordFactory()
        install_log_redaction()

        assert logging.getLogRecordFactory() is first

    def test_covers_handlers_and_loggers_added_after_install(self, captured):
        _logger, _stream = captured
        other_stream = io.StringIO()
        handler = logging.StreamHandler(other_stream)
        other = logging.getLogger("test_log_redaction.other")
        other.setLevel(logging.DEBUG)
        other.propagate = False
        other.addHandler(handler)
        try:
            other.warning("posting to %s", URL)
        finally:
            other.removeHandler(handler)

        assert TOKEN not in other_stream.getvalue()
