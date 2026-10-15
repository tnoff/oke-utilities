"""Configuration for the RPST sidecar, loaded from env vars."""

import os
from dataclasses import dataclass

DEFAULT_CLIENT_DIR = "/var/run/rpst/client"
DEFAULT_TOKEN_FILE = "/var/run/secrets/tokens/oci/token"  # nosec B105 - a path, not a credential
DEFAULT_OUTPUT_DIR = "/var/run/rpst/out"

MIN_EXP_MINUTES = 15
MAX_EXP_MINUTES = 720


class ConfigError(ValueError):
    """The environment does not describe a usable configuration."""


@dataclass
class RpstConfig:
    """Sidecar config; populated by `from_env`."""

    # Identity domain endpoint, e.g. https://idcs-xxxx.identity.oraclecloud.com:443
    domain_url: str

    # Directory holding the exchange application's `client_id` and `client_secret` files.
    client_dir: str = DEFAULT_CLIENT_DIR

    # Projected ServiceAccount token whose audience is the domain URL.
    token_file: str = DEFAULT_TOKEN_FILE

    # Shared memory-backed volume that receives `rpst` and `private.pem`.
    output_dir: str = DEFAULT_OUTPUT_DIR

    # Requested RPST lifetime, in minutes (OCI accepts 15..720).
    exp_minutes: int = 180

    # Refresh once this fraction of the actual lifetime has elapsed.
    refresh_fraction: float = 0.5

    # Seconds allowed for the token endpoint call.
    request_timeout: int = 30

    # Identity for logs (downward API).
    pod_name: str = "unknown"
    pod_namespace: str = "unknown"
    service_account_name: str = "unknown"

    def __post_init__(self) -> None:
        if not self.domain_url:
            raise ConfigError("RPST_DOMAIN_URL is not set")
        if not self.domain_url.lower().startswith("https://"):
            raise ConfigError("RPST_DOMAIN_URL must be an https:// URL")
        if not MIN_EXP_MINUTES <= self.exp_minutes <= MAX_EXP_MINUTES:
            raise ConfigError(f"RPST_EXP_MINUTES must be between {MIN_EXP_MINUTES} and {MAX_EXP_MINUTES}")
        if not 0 < self.refresh_fraction < 1:
            raise ConfigError("REFRESH_FRACTION must be greater than 0 and less than 1")

    @classmethod
    def from_env(cls) -> "RpstConfig":
        """Build the config from the environment; raises ConfigError when it is unusable."""
        try:
            exp_minutes = int(os.getenv("RPST_EXP_MINUTES", "180"))
            refresh_fraction = float(os.getenv("REFRESH_FRACTION", "0.5"))
        except ValueError as exc:
            raise ConfigError(f"Not a number: {exc}") from exc
        return cls(
            domain_url=os.getenv("RPST_DOMAIN_URL", "").strip().rstrip("/"),
            client_dir=os.getenv("RPST_CLIENT_DIR", DEFAULT_CLIENT_DIR),
            token_file=os.getenv("SERVICE_ACCOUNT_TOKEN_FILE", DEFAULT_TOKEN_FILE),
            output_dir=os.getenv("OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
            exp_minutes=exp_minutes,
            refresh_fraction=refresh_fraction,
            pod_name=os.getenv("POD_NAME", "unknown"),
            pod_namespace=os.getenv("POD_NAMESPACE", "unknown"),
            service_account_name=os.getenv("SERVICE_ACCOUNT_NAME", "unknown"),
        )
