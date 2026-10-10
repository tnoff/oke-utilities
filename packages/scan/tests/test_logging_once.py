"""Regression: each log record reaches the OTLP log exporter exactly once."""

import logging
from unittest.mock import patch

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogExporter
from opentelemetry.sdk._logs import LoggingHandler

from oke_scanner_core.k8s_client import KubernetesClient
from oke_scanner_core.telemetry import setup_telemetry
from scan.scanner import TrivyScanner


class _Cfg:
    otlp_metrics_enabled = False
    otlp_logs_enabled = True


@pytest.fixture
def exporter():
    """Run setup_telemetry with an in-memory exporter and restore the root logger after."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    exporter = InMemoryLogExporter()
    with patch('oke_scanner_core.telemetry.OTLPLogExporter', return_value=exporter):
        _, logger_provider = setup_telemetry(_Cfg())
    root.setLevel(logging.DEBUG)
    yield exporter, logger_provider
    root.handlers = handlers_before
    root.setLevel(level_before)
    logger_provider.shutdown()


def test_each_record_exported_once_for_every_module(exporter, base_config):
    exp, logger_provider = exporter
    with patch('oke_scanner_core.k8s_client.load_k8s_config'), \
         patch('oke_scanner_core.k8s_client.client.CoreV1Api'):
        KubernetesClient([], [])
    TrivyScanner(base_config)

    loggers = ['scan.main', 'scan.scanner', 'oke_scanner_core.k8s_client']
    for name in loggers:
        logging.getLogger(name).info("once-check %s", name)
    logger_provider.force_flush()

    bodies = [data.log_record.body for data in exp.get_finished_logs()]
    for name in loggers:
        assert bodies.count(f"once-check {name}") == 1


def test_no_module_logger_carries_its_own_otel_handler(exporter, base_config):  # pylint: disable=unused-argument
    with patch('oke_scanner_core.k8s_client.load_k8s_config'), \
         patch('oke_scanner_core.k8s_client.client.CoreV1Api'):
        KubernetesClient([], [])
    TrivyScanner(base_config)

    for name in ['scan.main', 'scan.scanner', 'oke_scanner_core.k8s_client']:
        assert not [h for h in logging.getLogger(name).handlers if isinstance(h, LoggingHandler)]
