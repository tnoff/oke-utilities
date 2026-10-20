"""OCI IAM reader: per user, list auth_tokens / customer_secret_keys / api_keys
and report their `time_created` as the rotation age.

Needs the tenancy verb `read users` (`inspect users` does not cover the credential
sub-resources), either on the user behind the mounted ~/.oci/config or on the
federated ServiceAccount's policy when run as a resource principal.
"""

import os
from datetime import date, datetime, timezone
from logging import getLogger

import oci

from ..config import SecretAgeConfig
from ..finding import Finding, Layer, Severity

logger = getLogger(__name__)

# OCI IAM findings carry no rotation_command. Most of these credentials are
# terraform-managed, so any fabricated command (an `oci iam ...` CLI call in
# particular) would rotate them outside terraform and cause state drift.
# Rotation steps live in the runbook (docs/runbooks/secret-rotation.md); the
# report intentionally doesn't restate them.


def _grade(age_days: int, cfg: SecretAgeConfig) -> Severity:
    if age_days >= cfg.rotate_days:
        return Severity.ROTATE
    if age_days >= cfg.warn_days:
        return Severity.WARN
    return Severity.OK


def _to_date(time_created) -> date:
    """OCI SDK returns datetimes; coerce to date."""
    if isinstance(time_created, datetime):
        return time_created.date()
    if isinstance(time_created, str):
        return datetime.fromisoformat(time_created.replace("Z", "+00:00")).date()
    return date.today()


def read_findings(cfg: SecretAgeConfig) -> list[Finding]:
    """Enumerate every IAM credential in the tenancy and report ages."""
    if not cfg.oci_tenancy_ocid:
        logger.warning("OCI_TENANCY_OCID not set — skipping OCI IAM reader")
        return []

    today = datetime.now(timezone.utc).date()
    # Resource principal when the pod carries an rpst sidecar, otherwise ~/.oci/config.
    if os.getenv("OCI_RESOURCE_PRINCIPAL_VERSION"):
        signer = oci.auth.signers.get_resource_principals_signer()
        identity = oci.identity.IdentityClient({"region": signer.region}, signer=signer)
    else:
        identity = oci.identity.IdentityClient(oci.config.from_file())

    findings: list[Finding] = []
    users = oci.pagination.list_call_get_all_results(
        identity.list_users, compartment_id=cfg.oci_tenancy_ocid
    ).data

    for user in users:
        logger.debug("OCI: enumerating credentials for user %s", user.name)
        findings.extend(_read_user(identity, user, today, cfg))

    logger.info("OCI IAM reader: %d findings across %d users", len(findings), len(users))
    return findings


def _read_user(identity, user, today: date, cfg: SecretAgeConfig) -> list[Finding]:
    findings: list[Finding] = []

    # Auth tokens (used for OCIR pushes, IAM-based S3 access, etc.)
    for tok in identity.list_auth_tokens(user_id=user.id).data:
        last = _to_date(tok.time_created)
        age = (today - last).days
        findings.append(Finding(
            layer=Layer.OCI_IAM,
            identifier=f"auth_token {tok.description or tok.id} (user {user.name})",
            last_rotated=last,
            age_days=age,
            severity=_grade(age, cfg),
            rotation_command="",
            notes=f"OCID: {tok.id}",
        ))

    # Customer secret keys (S3-compatible signing for Object Storage)
    for key in identity.list_customer_secret_keys(user_id=user.id).data:
        last = _to_date(key.time_created)
        age = (today - last).days
        findings.append(Finding(
            layer=Layer.OCI_IAM,
            identifier=f"customer_secret_key {key.display_name or key.id} (user {user.name})",
            last_rotated=last,
            age_days=age,
            severity=_grade(age, cfg),
            rotation_command="",
            notes=f"OCID: {key.id}",
        ))

    # API keys (PEM-based RSA keys for direct API auth)
    for ak in identity.list_api_keys(user_id=user.id).data:
        last = _to_date(ak.time_created)
        age = (today - last).days
        findings.append(Finding(
            layer=Layer.OCI_IAM,
            identifier=f"api_key {ak.fingerprint} (user {user.name})",
            last_rotated=last,
            age_days=age,
            severity=_grade(age, cfg),
            rotation_command="",
            notes=f"Fingerprint: {ak.fingerprint}",
        ))

    return findings
