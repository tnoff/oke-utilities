"""Tests for configuration."""

import pytest

from audit_watch.config import ConfigError, WatchConfig


def test_defaults(monkeypatch):
    for name in ("WINDOW_MINUTES", "LAG_MINUTES", "KNOWN_WRITE_EVENTS"):
        monkeypatch.delenv(name, raising=False)
    cfg = WatchConfig.from_env()
    assert (cfg.window_minutes, cfg.lag_minutes) == (12, 5)
    assert cfg.known_writes == {"UpdateInternalVnic", "UpdateNetworkLoadBalancer"}


def test_overrides(monkeypatch):
    monkeypatch.setenv("WINDOW_MINUTES", "30")
    monkeypatch.setenv("LAG_MINUTES", "0")
    monkeypatch.setenv("KNOWN_WRITE_EVENTS", " A , B ,,")
    cfg = WatchConfig.from_env()
    assert (cfg.window_minutes, cfg.lag_minutes, cfg.known_writes) == (30, 0, {"A", "B"})


@pytest.mark.parametrize("env", [{"WINDOW_MINUTES": "x"}, {"LAG_MINUTES": "1.5"}, {"WINDOW_MINUTES": "0"}, {"LAG_MINUTES": "-1"}])
def test_rejects_bad_values(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError):
        WatchConfig.from_env()
