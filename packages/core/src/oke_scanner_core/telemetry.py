"""OpenTelemetry configuration for logs and metrics, shared by every image
that runs with OTLP enabled (scan, cleanup -- not secret_age, which has no
OpenTelemetry setup at all).

Behind the `oke-scanner-core[telemetry]` extra: bare `oke-scanner-core`
does not pull in the OpenTelemetry SDK/exporters, so secret_age's image
stays exactly as lean as before this module existed.
"""

import logging
from logging import Logger, getLogger
from typing import Any, Optional
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import get_aggregated_resources, OTELResourceDetector
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.logging.handler import LoggingHandler


# Loggers whose records must never reach the OTLP log handler. The log
# exporter POSTs over urllib3 from the batch processor's worker thread, and
# urllib3 emits a DEBUG line per request; with the root logger at DEBUG that
# line is itself exported, whose POST emits another line, and so on. The
# queue never drains, so `force_flush()` / `shutdown()` never return and the
# process hangs at exit until the Job's activeDeadlineSeconds kills it.
_EXPORTER_LOGGERS = ("urllib3", "opentelemetry")


class _ExcludeExporterLogs(logging.Filter):  # pylint: disable=too-few-public-methods
    """Drop records emitted by the OTLP exporter's own transport stack."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_EXPORTER_LOGGERS)


def setup_telemetry(cfg: Any) -> tuple[Optional[MeterProvider], Optional[LoggerProvider]]:
    """Initialize OpenTelemetry with OTLP exporters based on configuration.

    `cfg` only needs `otlp_metrics_enabled` / `otlp_logs_enabled` attributes
    -- any package's own Config dataclass satisfies this structurally.
    """
    resource = get_aggregated_resources(detectors=[OTELResourceDetector()])

    meter_provider = None
    logger_provider = None

    # Metrics
    if cfg.otlp_metrics_enabled:
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(),
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        logging.info("OTLP metrics enabled")
    else:
        logging.info("OTLP metrics disabled")

    # Logs
    if cfg.otlp_logs_enabled:
        logger_provider = LoggerProvider()
        set_logger_provider(logger_provider)
        log_exporter = OTLPLogExporter()
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        handler.addFilter(_ExcludeExporterLogs())
        logging.getLogger().addHandler(handler)
        logging.info("OTLP logs enabled")
    else:
        logging.info("OTLP logs disabled")

    return meter_provider, logger_provider


def shutdown_telemetry(meter_provider: Optional[MeterProvider],
                        logger_provider: Optional[LoggerProvider],
                        logger: Optional[Logger] = None) -> None:
    """Flush and shut down whichever OTel providers are non-None.

    Duck-typed on `.force_flush()`/`.shutdown()`, so the function body
    itself needs no OTel imports -- shares this module with
    `setup_telemetry` because nothing calls one without the other.
    """
    log = logger or getLogger(__name__)
    log.info("Shutting down telemetry...")

    if meter_provider:
        log.debug("Flushing metrics...")
        meter_provider.force_flush(timeout_millis=30000)
        meter_provider.shutdown()
        log.debug("Metrics flushed")

    if logger_provider:
        log.debug("Flushing logs...")
        logger_provider.force_flush(timeout_millis=30000)
        logger_provider.shutdown()
        log.debug("Logs flushed")

    log.info("Telemetry shutdown complete")
