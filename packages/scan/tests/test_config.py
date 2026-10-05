"""Tests for config module."""

from scan.config import Config


class TestConfig:
    """Tests for Config class."""

    def test_from_env_with_all_values(self, monkeypatch):
        """Test Config.from_env with all environment variables set."""
        monkeypatch.setenv("OTLP_ENDPOINT", "http://localhost:4318")
        monkeypatch.setenv("OTLP_INSECURE", "true")
        monkeypatch.setenv("OTLP_METRICS_ENABLED", "true")
        monkeypatch.setenv("OTLP_LOGS_ENABLED", "true")
        monkeypatch.setenv("TRIVY_SEVERITY", "CRITICAL,HIGH,MEDIUM")
        monkeypatch.setenv("TRIVY_TIMEOUT", "600")
        monkeypatch.setenv("TRIVY_PLATFORM", "linux/arm64")
        monkeypatch.setenv("SCAN_NAMESPACES", "default,kube-system")
        monkeypatch.setenv("EXCLUDE_NAMESPACES", "kube-node-lease")
        monkeypatch.setenv("SCAN_EXTRA_IMAGES", "iad.ocir.io/tnoff/playball:latest, docker.io/library/alpine:3")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")

        config = Config.from_env()

        assert config.otlp_endpoint == "http://localhost:4318"
        assert config.otlp_insecure is True
        assert config.otlp_metrics_enabled is True
        assert config.otlp_logs_enabled is True
        assert config.trivy_severity == "CRITICAL,HIGH,MEDIUM"
        assert config.trivy_timeout == 600
        assert config.trivy_platform == "linux/arm64"
        assert config.namespaces == ["default", "kube-system"]
        assert config.exclude_namespaces == ["kube-node-lease"]
        assert config.extra_images == ["iad.ocir.io/tnoff/playball:latest", "docker.io/library/alpine:3"]
        assert config.discord_webhook_url == "https://discord.com/api/webhooks/test"

    def test_from_env_with_defaults(self):
        """Test Config.from_env with default values."""
        config = Config.from_env()

        # Check defaults
        assert config.otlp_endpoint == "http://localhost:4317"
        assert config.otlp_insecure is True
        assert config.otlp_metrics_enabled is False
        assert config.otlp_logs_enabled is False
        assert config.trivy_severity == "CRITICAL,HIGH"
        assert config.trivy_timeout == 300
        assert config.trivy_platform == ""
        assert config.namespaces == []
        assert config.exclude_namespaces == ["kube-system", "kube-public", "kube-node-lease"]
        assert config.extra_images == []
        assert config.discord_webhook_url == ""

    def test_extra_images_empty_and_blank_entries_filtered(self, monkeypatch):
        """Test SCAN_EXTRA_IMAGES empty, or with stray commas/whitespace, gives no blank entries."""
        monkeypatch.setenv("SCAN_EXTRA_IMAGES", "")
        assert Config.from_env().extra_images == []

        monkeypatch.setenv("SCAN_EXTRA_IMAGES", " , iad.ocir.io/tnoff/playball:latest,, ")
        assert Config.from_env().extra_images == ["iad.ocir.io/tnoff/playball:latest"]

    def test_from_env_otlp_insecure_false(self, monkeypatch):
        """Test OTLP_INSECURE=false."""
        monkeypatch.setenv("OTLP_INSECURE", "false")

        config = Config.from_env()
        assert config.otlp_insecure is False

    def test_discord_webhook_url_empty_by_default(self):
        """Test that Discord webhook URL is empty by default."""
        config = Config.from_env()
        assert config.discord_webhook_url == ""

    def test_otlp_metrics_enabled(self, monkeypatch):
        """Test OTLP_METRICS_ENABLED=true."""
        monkeypatch.setenv("OTLP_METRICS_ENABLED", "true")

        config = Config.from_env()
        assert config.otlp_metrics_enabled is True

    def test_otlp_logs_enabled(self, monkeypatch):
        """Test OTLP_LOGS_ENABLED=true."""
        monkeypatch.setenv("OTLP_LOGS_ENABLED", "true")

        config = Config.from_env()
        assert config.otlp_logs_enabled is True

    def test_trivy_platform(self, monkeypatch):
        """Test TRIVY_PLATFORM setting."""
        monkeypatch.setenv("TRIVY_PLATFORM", "linux/arm64")

        config = Config.from_env()
        assert config.trivy_platform == "linux/arm64"

    def test_trivy_platform_empty_by_default(self):
        """Test TRIVY_PLATFORM defaults to empty string."""
        config = Config.from_env()
        assert config.trivy_platform == ""
