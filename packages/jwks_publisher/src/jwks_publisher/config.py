"""Configuration for the JWKS publisher, loaded from env vars."""

import os
from dataclasses import dataclass

DEFAULT_JWKS_URL = "https://kubernetes.default.svc/openid/v1/jwks"
DEFAULT_SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


@dataclass
class JwksPublisherConfig:
    """Publisher config; populated by `from_env`."""

    # Bucket that holds the hosted key set (terraform: oci/ `k8s_oidc_jwks`).
    bucket_name: str

    # The object the publisher overwrites. Terraform seeds it; the publisher's
    # IAM policy ("use objects") can overwrite but not create or delete.
    object_name: str = "jwks.json"

    # Where the live key set is read from, and the pod's ServiceAccount
    # credentials (token + ca.crt) used to read it.
    jwks_url: str = DEFAULT_JWKS_URL
    service_account_dir: str = DEFAULT_SERVICE_ACCOUNT_DIR

    # Standard OCI config file; the bot's API key is mounted there.
    oci_config_file: str = "~/.oci/config"
    oci_profile: str = "DEFAULT"

    request_timeout: int = 15

    # Log what would change but write nothing.
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "JwksPublisherConfig":
        return cls(
            bucket_name=os.getenv("JWKS_BUCKET", ""),
            object_name=os.getenv("JWKS_OBJECT", "jwks.json"),
            jwks_url=os.getenv("JWKS_URL", DEFAULT_JWKS_URL),
            service_account_dir=os.getenv("SERVICE_ACCOUNT_DIR", DEFAULT_SERVICE_ACCOUNT_DIR),
            oci_config_file=os.getenv("OCI_CONFIG_FILE", "~/.oci/config"),
            oci_profile=os.getenv("OCI_PROFILE", "DEFAULT"),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT", "15")),
            dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        )
