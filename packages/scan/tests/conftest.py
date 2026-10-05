"""Shared test fixtures for OKE Security Scanner tests."""

import pytest
from unittest.mock import Mock
from scan.config import Config


@pytest.fixture
def base_config():
    """Create a base test configuration with all required fields."""
    return Config(
        otlp_endpoint="http://localhost:4318",
        otlp_insecure=True,
        otlp_metrics_enabled=True,
        otlp_logs_enabled=True,
        trivy_severity="CRITICAL,HIGH",
        trivy_timeout=300,
        trivy_platform="",
        namespaces=[],
        exclude_namespaces=["kube-system", "kube-public"],
        extra_images=[],
        discord_webhook_url="",
    )


@pytest.fixture
def mock_logger_provider():
    """Create a mock logger provider."""
    return Mock()
