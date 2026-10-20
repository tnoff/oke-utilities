"""Tests for ocir_cleanup.config module."""

import pytest
from ocir_cleanup.config import CleanupConfig


class TestCleanupConfig:
    """Tests for CleanupConfig class."""

    def test_from_env_with_all_values(self, monkeypatch):
        """Test CleanupConfig.from_env with all environment variables set."""
        monkeypatch.setenv("OTLP_ENDPOINT", "http://localhost:4318")
        monkeypatch.setenv("OTLP_INSECURE", "true")
        monkeypatch.setenv("OTLP_METRICS_ENABLED", "true")
        monkeypatch.setenv("OTLP_LOGS_ENABLED", "true")
        monkeypatch.setenv("SCAN_NAMESPACES", "default,kube-system")
        monkeypatch.setenv("EXCLUDE_NAMESPACES", "kube-node-lease")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")
        monkeypatch.setenv("OCIR_CLEANUP_ENABLED", "true")
        monkeypatch.setenv("OCIR_CLEANUP_KEEP_COUNT", "10")
        monkeypatch.setenv("OCIR_EXTRA_REPOSITORIES", "repo1,repo2")
        monkeypatch.setenv("CLEANUP_REPO", "tnoff/discord_bot")

        config = CleanupConfig.from_env()

        assert config.otlp_endpoint == "http://localhost:4318"
        assert config.otlp_insecure is True
        assert config.otlp_metrics_enabled is True
        assert config.otlp_logs_enabled is True
        assert config.namespaces == ["default", "kube-system"]
        assert config.exclude_namespaces == ["kube-node-lease"]
        assert config.discord_webhook_url == "https://discord.com/api/webhooks/test"
        assert config.ocir_cleanup_enabled is True
        assert config.ocir_cleanup_keep_count == 10
        assert config.ocir_extra_repositories == ["repo1", "repo2"]
        assert config.cleanup_repo == "tnoff/discord_bot"

    def test_from_env_with_defaults(self):
        """Test CleanupConfig.from_env with default values."""
        config = CleanupConfig.from_env()

        assert config.otlp_endpoint == "http://localhost:4317"
        assert config.otlp_insecure is True
        assert config.otlp_metrics_enabled is False
        assert config.otlp_logs_enabled is False
        assert config.namespaces == []
        assert config.exclude_namespaces == ["kube-system", "kube-public", "kube-node-lease"]
        assert config.discord_webhook_url == ""
        assert config.ocir_cleanup_enabled is False
        assert config.ocir_cleanup_keep_count == 5
        # Unset env var must yield empty list (not [""])
        assert config.ocir_extra_repositories == []
        assert config.cleanup_repo == ""

    def test_from_env_otlp_insecure_false(self, monkeypatch):
        """Test OTLP_INSECURE=false."""
        monkeypatch.setenv("OTLP_INSECURE", "false")

        config = CleanupConfig.from_env()
        assert config.otlp_insecure is False

    def test_discord_webhook_url_empty_by_default(self):
        """Test that Discord webhook URL is empty by default."""
        config = CleanupConfig.from_env()
        assert config.discord_webhook_url == ""

    def test_otlp_metrics_enabled(self, monkeypatch):
        """Test OTLP_METRICS_ENABLED=true."""
        monkeypatch.setenv("OTLP_METRICS_ENABLED", "true")

        config = CleanupConfig.from_env()
        assert config.otlp_metrics_enabled is True

    def test_otlp_logs_enabled(self, monkeypatch):
        """Test OTLP_LOGS_ENABLED=true."""
        monkeypatch.setenv("OTLP_LOGS_ENABLED", "true")

        config = CleanupConfig.from_env()
        assert config.otlp_logs_enabled is True

    def test_ocir_cleanup_enabled(self, monkeypatch):
        """Test OCIR_CLEANUP_ENABLED=true."""
        monkeypatch.setenv("OCIR_CLEANUP_ENABLED", "true")

        config = CleanupConfig.from_env()
        assert config.ocir_cleanup_enabled is True

    def test_ocir_cleanup_keep_count(self, monkeypatch):
        """Test OCIR_CLEANUP_KEEP_COUNT setting."""
        monkeypatch.setenv("OCIR_CLEANUP_KEEP_COUNT", "10")

        config = CleanupConfig.from_env()
        assert config.ocir_cleanup_keep_count == 10

    def test_cleanup_repo_default_empty(self):
        """CLEANUP_REPO defaults to empty (sweep everything)."""
        config = CleanupConfig.from_env()
        assert config.cleanup_repo == ""

    def test_cleanup_protect_tags_regex_invalid_raises(self, monkeypatch):
        """Invalid CLEANUP_PROTECT_TAGS_REGEX is rejected at load time."""
        monkeypatch.setenv("CLEANUP_PROTECT_TAGS_REGEX", "[unclosed")

        with pytest.raises(ValueError, match="CLEANUP_PROTECT_TAGS_REGEX"):
            CleanupConfig.from_env()

    def test_cleanup_group_by_regex_invalid_raises(self, monkeypatch):
        """Invalid CLEANUP_GROUP_BY_REGEX is rejected at load time."""
        monkeypatch.setenv("CLEANUP_GROUP_BY_REGEX", "(unclosed")

        with pytest.raises(ValueError, match="CLEANUP_GROUP_BY_REGEX"):
            CleanupConfig.from_env()

    def test_cleanup_group_by_regex_without_capture_group_raises(self, monkeypatch):
        """CLEANUP_GROUP_BY_REGEX without a capture group is rejected.

        The first capture group is the group key, so a regex with no
        groups can't drive per-group keep_count.
        """
        monkeypatch.setenv("CLEANUP_GROUP_BY_REGEX", r"\d+\.\d+")

        with pytest.raises(ValueError, match="capture group"):
            CleanupConfig.from_env()

    def test_cleanup_regex_valid(self, monkeypatch):
        """Valid protect + group regexes load without error."""
        monkeypatch.setenv("CLEANUP_PROTECT_TAGS_REGEX", r"^\d+\.\d+$")
        monkeypatch.setenv("CLEANUP_GROUP_BY_REGEX", r"^(\d+\.\d+)")

        config = CleanupConfig.from_env()
        assert config.cleanup_protect_tags_regex == r"^\d+\.\d+$"
        assert config.cleanup_group_by_regex == r"^(\d+\.\d+)"

    def test_ocir_compartment_ids_parsed_and_default_empty(self, monkeypatch):
        """OCIR_COMPARTMENT_IDS is a comma list; unset means search every compartment."""
        assert CleanupConfig.from_env().ocir_compartment_ids == []
        monkeypatch.setenv("OCIR_COMPARTMENT_IDS", "ocid1.compartment.a,,ocid1.compartment.b")
        assert CleanupConfig.from_env().ocir_compartment_ids == [
            "ocid1.compartment.a", "ocid1.compartment.b"]
