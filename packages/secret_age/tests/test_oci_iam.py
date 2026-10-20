"""Tests for readers.oci_iam — enumerates IAM users + their credentials."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from secret_age.finding import Layer, Severity
from secret_age.readers.oci_iam import read_findings, _to_date, _grade


def _make_token(time_created, **kwargs):
    return SimpleNamespace(time_created=time_created, **kwargs)


def _identity_mock(users, tokens=None, csk=None, api_keys=None):
    identity = Mock()

    list_users_resp = Mock()
    list_users_resp.data = users

    with patch("secret_age.readers.oci_iam.oci.pagination.list_call_get_all_results", return_value=list_users_resp):
        pass

    identity.list_auth_tokens.return_value = SimpleNamespace(data=tokens or [])
    identity.list_customer_secret_keys.return_value = SimpleNamespace(data=csk or [])
    identity.list_api_keys.return_value = SimpleNamespace(data=api_keys or [])
    return identity, list_users_resp


def test_grade_ok_warn_rotate(cfg):
    assert _grade(50, cfg) == Severity.OK
    assert _grade(100, cfg) == Severity.WARN
    assert _grade(200, cfg) == Severity.ROTATE


def test_to_date_handles_datetime_iso_and_fallback():
    dt = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    assert _to_date(dt) == dt.date()
    assert _to_date("2026-03-15T00:00:00Z").year == 2026
    # Unknown type falls back to today
    today = _to_date(12345)  # int input → fallback
    assert today is not None


def test_read_findings_skips_without_tenancy(cfg):
    cfg.oci_tenancy_ocid = ""
    assert read_findings(cfg) == []


def test_read_findings_emits_findings_per_credential(cfg):
    user = SimpleNamespace(id="ocid1.user.test", name="terraform-admin")
    auth_token = SimpleNamespace(
        id="ocid1.authtoken.test",
        description="ci-push",
        time_created=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    csk = SimpleNamespace(
        id="ocid1.csk.test",
        display_name="monitoring-os",
        time_created=datetime(2025, 9, 1, tzinfo=timezone.utc),
    )
    api_key = SimpleNamespace(
        fingerprint="ab:cd:ef",
        time_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    identity = Mock()
    identity.list_auth_tokens.return_value = SimpleNamespace(data=[auth_token])
    identity.list_customer_secret_keys.return_value = SimpleNamespace(data=[csk])
    identity.list_api_keys.return_value = SimpleNamespace(data=[api_key])

    list_users_resp = SimpleNamespace(data=[user])

    with patch("secret_age.readers.oci_iam.oci.config.from_file", return_value={}), \
         patch("secret_age.readers.oci_iam.oci.identity.IdentityClient", return_value=identity), \
         patch("secret_age.readers.oci_iam.oci.pagination.list_call_get_all_results",
               return_value=list_users_resp):
        findings = read_findings(cfg)

    assert len(findings) == 3
    layers = {f.layer for f in findings}
    assert layers == {Layer.OCI_IAM}
    # OCI findings carry no rotation_command — rotation steps live in the
    # runbook, and a fabricated command would risk terraform state drift.
    for f in findings:
        assert f.rotation_command == ""


def test_read_findings_zero_creds_user(cfg):
    user = SimpleNamespace(id="ocid1.user.empty", name="empty-bot")
    identity = Mock()
    identity.list_auth_tokens.return_value = SimpleNamespace(data=[])
    identity.list_customer_secret_keys.return_value = SimpleNamespace(data=[])
    identity.list_api_keys.return_value = SimpleNamespace(data=[])

    list_users_resp = SimpleNamespace(data=[user])

    with patch("secret_age.readers.oci_iam.oci.config.from_file", return_value={}), \
         patch("secret_age.readers.oci_iam.oci.identity.IdentityClient", return_value=identity), \
         patch("secret_age.readers.oci_iam.oci.pagination.list_call_get_all_results",
               return_value=list_users_resp):
        findings = read_findings(cfg)

    assert findings == []


def test_read_findings_as_resource_principal(cfg, monkeypatch):
    """With the rpst sidecar's env set, the Identity client is signed by the resource principal."""
    monkeypatch.setenv("OCI_RESOURCE_PRINCIPAL_VERSION", "2.2")
    signer = Mock(region="us-ashburn-1")
    identity = Mock()

    with patch("secret_age.readers.oci_iam.oci.auth.signers.get_resource_principals_signer",
               return_value=signer), \
         patch("secret_age.readers.oci_iam.oci.config.from_file") as from_file, \
         patch("secret_age.readers.oci_iam.oci.identity.IdentityClient", return_value=identity) as build, \
         patch("secret_age.readers.oci_iam.oci.pagination.list_call_get_all_results",
               return_value=SimpleNamespace(data=[])):
        assert read_findings(cfg) == []

    build.assert_called_once_with({"region": "us-ashburn-1"}, signer=signer)
    from_file.assert_not_called()
