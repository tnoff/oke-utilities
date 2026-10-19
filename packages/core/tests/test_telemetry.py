"""Tests for oke_scanner_core.telemetry module."""

import logging
from unittest.mock import Mock, patch
from oke_scanner_core.telemetry import _ExcludeExporterLogs, setup_telemetry, shutdown_telemetry


class TestSetupTelemetry:
    """Tests for setup_telemetry function."""

    @patch('oke_scanner_core.telemetry.get_aggregated_resources')
    @patch('oke_scanner_core.telemetry.MeterProvider')
    @patch('oke_scanner_core.telemetry.LoggerProvider')
    @patch('oke_scanner_core.telemetry.PeriodicExportingMetricReader')
    @patch('oke_scanner_core.telemetry.OTLPMetricExporter')
    @patch('oke_scanner_core.telemetry.OTLPLogExporter')
    @patch('oke_scanner_core.telemetry.BatchLogRecordProcessor')
    @patch('oke_scanner_core.telemetry.metrics')
    def test_setup_telemetry_returns_meter_logger(
        self,
        mock_metrics_module,
        mock_batch_log_processor,
        mock_log_exporter,
        mock_metric_exporter,
        mock_metric_reader,
        mock_logger_provider_class,
        mock_meter_provider_class,
        mock_get_resources,
    ):
        """Test that setup_telemetry returns meter_provider and logger_provider."""
        mock_meter_provider = Mock()
        mock_logger_provider = Mock()

        mock_meter_provider_class.return_value = mock_meter_provider
        mock_logger_provider_class.return_value = mock_logger_provider

        cfg = Mock(otlp_metrics_enabled=True, otlp_logs_enabled=True)

        meter_provider, logger_provider = setup_telemetry(cfg)

        assert meter_provider == mock_meter_provider
        assert logger_provider == mock_logger_provider

    @patch('oke_scanner_core.telemetry.metrics')
    @patch('oke_scanner_core.telemetry.MeterProvider')
    @patch('oke_scanner_core.telemetry.LoggerProvider')
    @patch('oke_scanner_core.telemetry.PeriodicExportingMetricReader')
    @patch('oke_scanner_core.telemetry.OTLPMetricExporter')
    @patch('oke_scanner_core.telemetry.OTLPLogExporter')
    @patch('oke_scanner_core.telemetry.BatchLogRecordProcessor')
    def test_setup_telemetry_sets_global_providers(
        self,
        mock_batch_log_processor,
        mock_log_exporter,
        mock_metric_exporter,
        mock_metric_reader,
        mock_logger_provider_class,
        mock_meter_provider_class,
        mock_metrics,
    ):
        """Test that setup_telemetry sets global meter provider."""
        mock_meter_provider = Mock()
        mock_meter_provider_class.return_value = mock_meter_provider
        mock_logger_provider_class.return_value = Mock()

        cfg = Mock(otlp_metrics_enabled=True, otlp_logs_enabled=True)

        setup_telemetry(cfg)

        mock_metrics.set_meter_provider.assert_called_once_with(mock_meter_provider)

    @patch('oke_scanner_core.telemetry.get_aggregated_resources')
    def test_setup_telemetry_disabled_returns_none_providers(self, _mock_get_resources):
        """When both OTLP flags are off, setup_telemetry returns (None, None)."""
        cfg = Mock(otlp_metrics_enabled=False, otlp_logs_enabled=False)

        meter_provider, logger_provider = setup_telemetry(cfg)

        assert meter_provider is None
        assert logger_provider is None


class TestShutdownTelemetry:
    """Tests for shutdown_telemetry function."""

    def test_flushes_and_shuts_down_both_providers(self):
        meter_provider = Mock()
        logger_provider = Mock()

        shutdown_telemetry(meter_provider, logger_provider)

        meter_provider.force_flush.assert_called_once_with(timeout_millis=30000)
        meter_provider.shutdown.assert_called_once()
        logger_provider.force_flush.assert_called_once_with(timeout_millis=30000)
        logger_provider.shutdown.assert_called_once()

    def test_skips_none_providers(self):
        # Should not raise when both are None.
        shutdown_telemetry(None, None)

    def test_uses_caller_supplied_logger(self):
        meter_provider = Mock()
        logger = Mock()

        shutdown_telemetry(meter_provider, None, logger)

        logger.info.assert_any_call("Shutting down telemetry...")
        logger.info.assert_any_call("Telemetry shutdown complete")


class TestExcludeExporterLogs:
    """The OTLP log handler must not re-export the exporter's own HTTP logs."""

    @staticmethod
    def _record(name):
        return logging.LogRecord(name, logging.DEBUG, __file__, 1, "msg", None, None)

    def test_drops_exporter_transport_loggers(self):
        flt = _ExcludeExporterLogs()
        assert not flt.filter(self._record("urllib3.connectionpool"))
        assert not flt.filter(self._record("opentelemetry.exporter.otlp.proto.http._log_exporter"))

    def test_keeps_application_loggers(self):
        flt = _ExcludeExporterLogs()
        assert flt.filter(self._record("ocir_cleanup.main"))
        assert flt.filter(self._record("scan.main"))

    @patch('oke_scanner_core.telemetry.get_aggregated_resources')
    @patch('oke_scanner_core.telemetry.LoggerProvider')
    @patch('oke_scanner_core.telemetry.OTLPLogExporter')
    @patch('oke_scanner_core.telemetry.BatchLogRecordProcessor')
    @patch('oke_scanner_core.telemetry.LoggingHandler')
    def test_setup_attaches_filter_to_log_handler(self, mock_handler_class, *_mocks):
        """setup_telemetry installs the exporter-log filter on the root handler."""
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            setup_telemetry(Mock(otlp_metrics_enabled=False, otlp_logs_enabled=True))
            (flt,) = mock_handler_class.return_value.addFilter.call_args.args
            assert isinstance(flt, _ExcludeExporterLogs)
        finally:
            root.handlers[:] = before
