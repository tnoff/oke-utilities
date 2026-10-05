"""Configuration management for the vulnerability scan."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Application configuration from environment variables."""

    # OTLP configuration
    otlp_endpoint: str
    otlp_insecure: bool
    otlp_metrics_enabled: bool
    otlp_logs_enabled: bool

    # Trivy configuration
    trivy_severity: str
    trivy_timeout: int
    trivy_platform: str

    # Scanning configuration
    namespaces: list[str]
    exclude_namespaces: list[str]
    # Extra full image references to scan in addition to deployed images
    extra_images: list[str]

    # Discord webhook (optional — enabled if URL provided)
    discord_webhook_url: str

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            # OTLP configuration
            otlp_endpoint=os.getenv("OTLP_ENDPOINT", "http://localhost:4317"),
            otlp_insecure=os.getenv("OTLP_INSECURE", "true").lower() == "true",
            otlp_metrics_enabled=os.getenv("OTLP_METRICS_ENABLED", "false").lower() == "true",
            otlp_logs_enabled=os.getenv("OTLP_LOGS_ENABLED", "false").lower() == "true",

            # Trivy configuration
            trivy_severity=os.getenv("TRIVY_SEVERITY", "CRITICAL,HIGH"),
            trivy_timeout=int(os.getenv("TRIVY_TIMEOUT", "300")),
            trivy_platform=os.getenv("TRIVY_PLATFORM", ""),

            # Scanning configuration
            namespaces=os.getenv("SCAN_NAMESPACES", "").split(",") if os.getenv("SCAN_NAMESPACES") else [],
            exclude_namespaces=os.getenv("EXCLUDE_NAMESPACES", "kube-system,kube-public,kube-node-lease").split(","),
            extra_images=[i.strip() for i in os.getenv("SCAN_EXTRA_IMAGES", "").split(",") if i.strip()],

            # Discord webhook configuration (optional - enabled if URL provided)
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        )
