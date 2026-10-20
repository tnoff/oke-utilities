"""Configuration management for the OCIR cleanup sweep."""

import os
import re
from dataclasses import dataclass, field


@dataclass
class CleanupConfig:
    """Application configuration from environment variables."""

    # OTLP configuration
    otlp_endpoint: str
    otlp_insecure: bool
    otlp_metrics_enabled: bool
    otlp_logs_enabled: bool

    # Image-discovery configuration (shared with the scan image's scope)
    namespaces: list[str]
    exclude_namespaces: list[str]

    # Discord webhook for cleanup reports
    discord_webhook_url: str

    # OCIR cleanup configuration
    ocir_cleanup_enabled: bool
    ocir_cleanup_keep_count: int
    ocir_extra_repositories: list[str]
    # Optional per-repo tag-shape knobs. Both are passed straight to
    # registry_client.get_old_ocir_images; empty string means "off". See the
    # ci-base-images cleanup CronJob for a real usage example.
    #
    # cleanup_protect_tags_regex: tags whose name fully matches are excluded
    #   from the deletion candidate pool (no matter how old). Used to protect
    #   mutable "channel" tags like ci-base-images' :3.11/:3.12/:3.13/:3.14
    #   which are overwritten on every build and must never be pruned.
    #
    # cleanup_group_by_regex: when set, the candidate pool is grouped by the
    #   regex's first capture group and keep_count is applied per-group
    #   rather than globally. Used so heavy churn in one Python minor's
    #   :3.X-<sha> tags can't push other minors' tags out of the keep window.
    cleanup_protect_tags_regex: str
    cleanup_group_by_regex: str
    # When set, the cleanup pass is scoped to this OCIR repo
    # (namespace-qualified, e.g. `tnoff/discord_bot`).
    cleanup_repo: str
    # Compartments that hold the OCIR repositories. Empty means search every compartment the
    # principal can see, which produces a 404 per compartment that lacks the repository.
    ocir_compartment_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.cleanup_protect_tags_regex:
            try:
                re.compile(self.cleanup_protect_tags_regex)
            except re.error as e:
                raise ValueError(f"CLEANUP_PROTECT_TAGS_REGEX is not a valid regex: {e}") from e
        if self.cleanup_group_by_regex:
            try:
                compiled = re.compile(self.cleanup_group_by_regex)
            except re.error as e:
                raise ValueError(f"CLEANUP_GROUP_BY_REGEX is not a valid regex: {e}") from e
            if compiled.groups < 1:
                raise ValueError(
                    "CLEANUP_GROUP_BY_REGEX must define at least one capture group "
                    "(the first one becomes the group key)"
                )

    @classmethod
    def from_env(cls) -> "CleanupConfig":
        """Load configuration from environment variables."""
        return cls(
            # OTLP configuration
            otlp_endpoint=os.getenv("OTLP_ENDPOINT", "http://localhost:4317"),
            otlp_insecure=os.getenv("OTLP_INSECURE", "true").lower() == "true",
            otlp_metrics_enabled=os.getenv("OTLP_METRICS_ENABLED", "false").lower() == "true",
            otlp_logs_enabled=os.getenv("OTLP_LOGS_ENABLED", "false").lower() == "true",

            # Image-discovery configuration
            namespaces=os.getenv("SCAN_NAMESPACES", "").split(",") if os.getenv("SCAN_NAMESPACES") else [],
            exclude_namespaces=os.getenv("EXCLUDE_NAMESPACES", "kube-system,kube-public,kube-node-lease").split(","),

            # Discord webhook configuration
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),

            # OCIR cleanup configuration
            ocir_cleanup_enabled=os.getenv("OCIR_CLEANUP_ENABLED", "false").lower() == "true",
            ocir_cleanup_keep_count=int(os.getenv("OCIR_CLEANUP_KEEP_COUNT", "5")),
            # Comma separated list of extra repos (filter empties so unset → [])
            ocir_extra_repositories=[r for r in os.getenv('OCIR_EXTRA_REPOSITORIES', "").split(',') if r],
            cleanup_protect_tags_regex=os.getenv("CLEANUP_PROTECT_TAGS_REGEX", ""),
            cleanup_group_by_regex=os.getenv("CLEANUP_GROUP_BY_REGEX", ""),
            cleanup_repo=os.getenv("CLEANUP_REPO", ""),
            ocir_compartment_ids=[c for c in os.getenv("OCIR_COMPARTMENT_IDS", "").split(",") if c],
        )
