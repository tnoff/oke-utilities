"""Tests for the audit pass."""

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import oci
import pytest

from audit_watch import main as m
from audit_watch.config import WatchConfig

from conftest import NOW, TENANCY, FakeAudit, FakeIdentity, Failing, event


def run(cfg, by_compartment, comps=("c1",)):
    """Run one pass over fake clients and return (exit code, audit fake)."""
    audit = FakeAudit(by_compartment)
    return m.run(cfg, audit, FakeIdentity(comps), TENANCY, now=NOW), audit


def test_span_ends_after_the_lag_and_covers_the_window(cfg):
    _, audit = run(cfg, {})
    assert {c[0] for c in audit.calls} == {TENANCY, "c1"}
    _, start, end = audit.calls[0]
    assert end == NOW - timedelta(minutes=5)
    assert start == end - timedelta(minutes=12)


def test_ordinary_traffic_is_logged_unflagged(cfg, caplog):
    events = [event("SummarizeMetricsData")] * 3 + [event("GetVnic")]
    with caplog.at_level(logging.INFO):
        assert run(cfg, {"c1": events})[0] == 0
    assert "seen event=SummarizeMetricsData status=200 count=3" in caplog.text
    assert "flagged=0" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_non_resource_principals_are_ignored(cfg, caplog):
    with caplog.at_level(logging.INFO):
        run(cfg, {"c1": [event("DeleteBucket", auth_type="user"), event("GetVnic", auth_type="instance")]})
    assert "resource_events=0" in caplog.text


def test_denied_call_warns_with_source_ip(cfg, caplog):
    with caplog.at_level(logging.INFO):
        run(cfg, {"c1": [event("ListInstances", status="404", ip="98.1.2.3")]})
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["audit-watch DENIED event=ListInstances status=404 count=1 ips=98.1.2.3 type=com.oraclecloud.x"]
    assert "flagged=1" in caplog.text


def test_unexpected_write_warns_and_known_write_does_not(cfg, caplog):
    events = [event("DeleteBucket"), event("UpdateNetworkLoadBalancer", status="202")]
    with caplog.at_level(logging.INFO):
        run(cfg, {"c1": events})
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "UNEXPECTED_WRITE event=DeleteBucket" in warnings[0]


def test_ips_are_collected_per_group(cfg, caplog):
    with caplog.at_level(logging.INFO):
        run(cfg, {"c1": [event("GetVnic", ip="10.0.0.2"), event("GetVnic", ip="10.0.0.1")]})
    assert "ips=10.0.0.1,10.0.0.2" in caplog.text


def test_credentials_never_reach_the_log(cfg, caplog):
    with caplog.at_level(logging.DEBUG):
        run(cfg, {"c1": [event("CreateBucket", credentials="SECRET-TOKEN-VALUE")]})
    assert "SECRET-TOKEN-VALUE" not in caplog.text


def test_missing_fields_are_tolerated(cfg, caplog):
    bare = SimpleNamespace(
        event_type=None,
        data=SimpleNamespace(event_name=None, identity=SimpleNamespace(auth_type="resource"), response=None),
    )
    with caplog.at_level(logging.INFO):
        assert run(cfg, {"c1": [bare]})[0] == 0
    assert "event=unknown status=unknown" in caplog.text


def test_audit_failure_fails_the_run(cfg, caplog):
    audit = Failing(oci.exceptions.ServiceError(403, "NotAuthorized", {}, "boom"))
    assert m.run(cfg, audit, FakeIdentity([]), TENANCY, now=NOW) == 1
    assert "Cannot list audit events" in caplog.text


def test_compartment_listing_failure_fails_the_run(cfg, caplog):
    identity = Failing(oci.exceptions.ServiceError(404, "NotAuthorizedOrNotFound", {}, "no"))
    assert m.run(cfg, FakeAudit({}), identity, TENANCY, now=NOW) == 1
    assert "Cannot list compartments" in caplog.text


# ---- main -----------------------------------------------------------------------------

def test_main_bad_config(monkeypatch, caplog):
    monkeypatch.setenv("WINDOW_MINUTES", "nope")
    assert m.main([]) == 1
    assert "Bad configuration" in caplog.text


def test_main_signer_failure(monkeypatch, caplog):
    monkeypatch.delenv("WINDOW_MINUTES", raising=False)
    with mock.patch.object(m.oci.auth.signers, "get_resource_principals_signer", side_effect=OSError("no rpst")):
        assert m.main([]) == 1
    assert "resource principal signer" in caplog.text


def test_main_builds_clients_from_the_signer(monkeypatch):
    monkeypatch.delenv("WINDOW_MINUTES", raising=False)
    signer = SimpleNamespace(region="us-sanjose-1", tenancy_id=TENANCY)
    with mock.patch.object(m.oci.auth.signers, "get_resource_principals_signer", return_value=signer), \
            mock.patch.object(m.oci.audit, "AuditClient") as audit, \
            mock.patch.object(m.oci.identity, "IdentityClient") as identity, \
            mock.patch.object(m, "run", return_value=0) as run_:
        assert m.main([]) == 0
    audit.assert_called_once_with({"region": "us-sanjose-1"}, signer=signer)
    identity.assert_called_once_with({"region": "us-sanjose-1"}, signer=signer)
    assert run_.call_args.args[0] == WatchConfig() and run_.call_args.args[3] == TENANCY
