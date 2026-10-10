"""Scan-specific telemetry: the "image_scan" and "image_scan_failed" gauge metrics. Generic OTel
setup/teardown lives in oke_scanner_core.telemetry, shared with cleanup.
"""

from dataclasses import dataclass
from typing import Any, Optional
from opentelemetry.sdk.metrics import MeterProvider
from oke_scanner_core.telemetry import setup_telemetry, shutdown_telemetry

__all__ = ["setup_telemetry", "shutdown_telemetry", "Metrics", "create_metrics"]


@dataclass
class Metrics:
    """Application metrics for vulnerability scanning."""

    scan_total: Any  # OpenTelemetry Gauge instrument
    scan_failed: Any  # OpenTelemetry Gauge instrument: 1 if the image's scan failed, else 0


def create_metrics(meter_provider: Optional[MeterProvider]) -> Optional[Metrics]:
    """Create application metrics.

    Args:
        meter_provider: MeterProvider instance, or None if metrics disabled

    Returns:
        Metrics dataclass, or None if meter_provider is None
    """
    if not meter_provider:
        return None

    meter = meter_provider.get_meter(__name__)
    return Metrics(
        scan_total=meter.create_gauge(
            "image_scan",
            description="Current vulnerability count per image by severity",
            unit="1",
        ),
        scan_failed=meter.create_gauge(
            "image_scan_failed",
            description="1 if the image's scan failed this run, 0 if it succeeded",
            unit="1",
        ),
    )
