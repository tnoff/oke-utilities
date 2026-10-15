"""Tests for RpstConfig."""

import pytest

from rpst_sidecar.config import ConfigError, RpstConfig

ENV_VARS = (
    "RPST_DOMAIN_URL", "RPST_CLIENT_DIR", "SERVICE_ACCOUNT_TOKEN_FILE", "OUTPUT_DIR",
    "RPST_EXP_MINUTES", "REFRESH_FRACTION", "POD_NAME", "POD_NAMESPACE", "SERVICE_ACCOUNT_NAME",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with none of the sidecar's variables set."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch):
    monkeypatch.setenv("RPST_DOMAIN_URL", "https://d.example:443/")
    cfg = RpstConfig.from_env()
    assert cfg.domain_url == "https://d.example:443"
    assert cfg.client_dir == "/var/run/rpst/client"
    assert cfg.token_file == "/var/run/secrets/tokens/oci/token"
    assert cfg.output_dir == "/var/run/rpst/out"
    assert cfg.exp_minutes == 180
    assert cfg.refresh_fraction == 0.5
    assert cfg.pod_name == cfg.pod_namespace == cfg.service_account_name == "unknown"


def test_overrides(monkeypatch):
    monkeypatch.setenv("RPST_DOMAIN_URL", "https://d.example")
    monkeypatch.setenv("RPST_CLIENT_DIR", "/c")
    monkeypatch.setenv("SERVICE_ACCOUNT_TOKEN_FILE", "/t")
    monkeypatch.setenv("OUTPUT_DIR", "/o")
    monkeypatch.setenv("RPST_EXP_MINUTES", "60")
    monkeypatch.setenv("REFRESH_FRACTION", "0.8")
    monkeypatch.setenv("POD_NAME", "p")
    monkeypatch.setenv("POD_NAMESPACE", "n")
    monkeypatch.setenv("SERVICE_ACCOUNT_NAME", "sa")
    cfg = RpstConfig.from_env()
    assert (cfg.client_dir, cfg.token_file, cfg.output_dir) == ("/c", "/t", "/o")
    assert (cfg.exp_minutes, cfg.refresh_fraction) == (60, 0.8)
    assert (cfg.pod_name, cfg.pod_namespace, cfg.service_account_name) == ("p", "n", "sa")


def test_domain_required():
    with pytest.raises(ConfigError, match="not set"):
        RpstConfig.from_env()


@pytest.mark.parametrize("url", ["http://d.example", "ftp://d.example", "d.example"])
def test_domain_must_be_https(monkeypatch, url):
    monkeypatch.setenv("RPST_DOMAIN_URL", url)
    with pytest.raises(ConfigError, match="https"):
        RpstConfig.from_env()


@pytest.mark.parametrize("minutes", ["14", "721", "0", "-5"])
def test_exp_minutes_range(monkeypatch, minutes):
    monkeypatch.setenv("RPST_DOMAIN_URL", "https://d.example")
    monkeypatch.setenv("RPST_EXP_MINUTES", minutes)
    with pytest.raises(ConfigError, match="RPST_EXP_MINUTES"):
        RpstConfig.from_env()


@pytest.mark.parametrize("minutes", ["15", "720"])
def test_exp_minutes_bounds_allowed(monkeypatch, minutes):
    monkeypatch.setenv("RPST_DOMAIN_URL", "https://d.example")
    monkeypatch.setenv("RPST_EXP_MINUTES", minutes)
    assert RpstConfig.from_env().exp_minutes == int(minutes)


@pytest.mark.parametrize("fraction", ["0", "1", "1.5", "-0.1"])
def test_refresh_fraction_range(monkeypatch, fraction):
    monkeypatch.setenv("RPST_DOMAIN_URL", "https://d.example")
    monkeypatch.setenv("REFRESH_FRACTION", fraction)
    with pytest.raises(ConfigError, match="REFRESH_FRACTION"):
        RpstConfig.from_env()


@pytest.mark.parametrize("name", ["RPST_EXP_MINUTES", "REFRESH_FRACTION"])
def test_non_numeric(monkeypatch, name):
    monkeypatch.setenv("RPST_DOMAIN_URL", "https://d.example")
    monkeypatch.setenv(name, "abc")
    with pytest.raises(ConfigError, match="Not a number"):
        RpstConfig.from_env()
