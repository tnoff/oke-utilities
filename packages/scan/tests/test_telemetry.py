"""Tests for the scan-specific telemetry module (the image_scan gauges).

setup_telemetry/shutdown_telemetry are oke_scanner_core.telemetry's own
and are tested there.
"""

from unittest.mock import Mock
from scan.telemetry import create_metrics, Metrics


class TestCreateMetrics:
    """Tests for create_metrics function."""

    def test_create_metrics_returns_metrics_dataclass(self):
        """Test that create_metrics returns a Metrics dataclass."""
        mock_meter_provider = Mock()
        mock_meter = Mock()
        mock_gauge = Mock()

        mock_meter_provider.get_meter.return_value = mock_meter
        mock_meter.create_gauge.return_value = mock_gauge

        result = create_metrics(mock_meter_provider)

        assert isinstance(result, Metrics)
        assert result.scan_total == mock_gauge

    def test_create_metrics_creates_image_scan_gauge(self):
        """Test that create_metrics creates image_scan gauge with correct parameters."""
        mock_meter_provider = Mock()
        mock_meter = Mock()
        mock_gauge = Mock()

        mock_meter_provider.get_meter.return_value = mock_meter
        mock_meter.create_gauge.return_value = mock_gauge

        create_metrics(mock_meter_provider)

        names = [call.args[0] for call in mock_meter.create_gauge.call_args_list]
        assert names == ["image_scan", "image_scan_failed"]

    def test_create_metrics_returns_none_when_meter_provider_none(self):
        """create_metrics returns None when meter_provider is None (metrics disabled)."""
        assert create_metrics(None) is None
