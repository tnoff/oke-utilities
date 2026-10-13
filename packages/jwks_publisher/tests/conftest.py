"""Shared fixtures for jwks_publisher tests."""

import pytest

from jwks_publisher.config import JwksPublisherConfig


@pytest.fixture
def cfg():
    """Default publisher config."""
    return JwksPublisherConfig(bucket_name="k8s_oidc_jwks")
