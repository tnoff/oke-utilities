"""Shared fixtures for audit_watch tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from audit_watch.config import WatchConfig

NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
TENANCY = "ocid1.tenancy.oc1..t"


def event(name, status="200", auth_type="resource", ip="10.0.0.5", event_type="com.oraclecloud.x", credentials="SECRET-TOKEN-VALUE"):
    """An Audit event shaped like the SDK's AuditEvent."""
    identity = SimpleNamespace(auth_type=auth_type, ip_address=ip, credentials=credentials)
    data = SimpleNamespace(event_name=name, identity=identity, response=SimpleNamespace(status=status))
    return SimpleNamespace(event_type=event_type, data=data)


class Page:
    """One page of an SDK list response."""

    def __init__(self, data, next_page=None):
        self.data = data
        self.next_page = next_page
        self.has_next_page = next_page is not None
        self.headers = {}
        self.status = 200
        self.request = None


class FakeAudit:
    """list_events keyed by compartment id, one page per compartment."""

    def __init__(self, by_compartment):
        self.by_compartment = by_compartment
        self.calls = []

    def list_events(self, compartment_id, start, end, page=None, **_):
        """Record the call and return that compartment's events."""
        self.calls.append((compartment_id, start, end))
        return Page(self.by_compartment.get(compartment_id, []))


class FakeIdentity:
    """list_compartments returning fixed ids."""

    def __init__(self, ids):
        self.ids = ids

    def list_compartments(self, tenancy_id, page=None, **_):
        """Return the compartments under the tenancy."""
        return Page([SimpleNamespace(id=i) for i in self.ids])


@pytest.fixture
def cfg():
    """Default watcher config."""
    return WatchConfig()


class Failing:
    """A client whose list calls raise the given error."""

    def __init__(self, error):
        self.error = error

    def list_events(self, *_, **__):
        """Always fails."""
        raise self.error

    def list_compartments(self, *_, **__):
        """Always fails."""
        raise self.error
