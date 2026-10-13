"""Tests for JwksPublisherConfig."""

from jwks_publisher.config import DEFAULT_JWKS_URL, DEFAULT_SERVICE_ACCOUNT_DIR, JwksPublisherConfig


def test_from_env_defaults(monkeypatch):
    for name in ("JWKS_BUCKET", "JWKS_OBJECT", "JWKS_URL", "SERVICE_ACCOUNT_DIR", "OCI_CONFIG_FILE",
                 "OCI_PROFILE", "REQUEST_TIMEOUT", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    cfg = JwksPublisherConfig.from_env()
    assert cfg.bucket_name == ""
    assert cfg.object_name == "jwks.json"
    assert cfg.jwks_url == DEFAULT_JWKS_URL
    assert cfg.service_account_dir == DEFAULT_SERVICE_ACCOUNT_DIR
    assert cfg.oci_config_file == "~/.oci/config"
    assert cfg.oci_profile == "DEFAULT"
    assert cfg.request_timeout == 15
    assert cfg.dry_run is False


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("JWKS_BUCKET", "b")
    monkeypatch.setenv("JWKS_OBJECT", "o.json")
    monkeypatch.setenv("JWKS_URL", "https://example.test/jwks")
    monkeypatch.setenv("SERVICE_ACCOUNT_DIR", "/sa")
    monkeypatch.setenv("OCI_CONFIG_FILE", "/oci/config")
    monkeypatch.setenv("OCI_PROFILE", "bot")
    monkeypatch.setenv("REQUEST_TIMEOUT", "5")
    monkeypatch.setenv("DRY_RUN", "TRUE")
    cfg = JwksPublisherConfig.from_env()
    assert (cfg.bucket_name, cfg.object_name) == ("b", "o.json")
    assert cfg.jwks_url == "https://example.test/jwks"
    assert cfg.service_account_dir == "/sa"
    assert (cfg.oci_config_file, cfg.oci_profile) == ("/oci/config", "bot")
    assert cfg.request_timeout == 5
    assert cfg.dry_run is True
