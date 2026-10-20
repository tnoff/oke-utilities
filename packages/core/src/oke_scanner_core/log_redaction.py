"""Keep credentials out of log output.

Every image here logs at DEBUG, and urllib3 logs the full request path for
each call, which for a Discord webhook is `/api/webhooks/<id>/<token>`: the
token is the credential. `requests` errors also embed the URL in their text.
Both land in pod logs and, through the OTLP handler, in Loki.

The redaction happens in the LogRecord factory rather than on a handler or
logger, so it covers every handler (stdout, OTLP, any added later) and
every logger, including third-party ones.
"""

import logging
import re
import traceback

REDACTED = "[REDACTED]"

# Discord puts the credential in the URL path: /api/webhooks/<id>/<token>
# (also matches discordapp.com and a trailing ?wait=true, which is not part of the token)
_DISCORD_WEBHOOK = re.compile(r"(/api/webhooks/\d+/)[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    """Return text with any Discord webhook token replaced."""
    return _DISCORD_WEBHOOK.sub(rf"\1{REDACTED}", text)


def install_log_redaction() -> None:
    """Make every LogRecord created from now on free of webhook tokens.

    Idempotent: calling it again, or from several entry points, installs one wrapper.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "redacts_secrets", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # pylint: disable=broad-except
            # A malformed format/args pair is the logging module's to report at emit time
            return record
        scrubbed = redact(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
        if record.exc_info and record.exc_info[0] is not None:
            trace = "".join(traceback.format_exception(*record.exc_info))
            scrubbed_trace = redact(trace)
            if scrubbed_trace != trace:
                # Handlers format exc_info themselves, so drop it and hand over the scrubbed text
                record.exc_info = None
                record.exc_text = scrubbed_trace.rstrip("\n")
        return record

    factory.redacts_secrets = True
    logging.setLogRecordFactory(factory)
