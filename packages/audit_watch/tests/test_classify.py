"""Tests for the event classifier."""

import pytest

from audit_watch.classify import DENIED, UNEXPECTED_WRITE, flags, is_read

KNOWN = frozenset({"UpdateNetworkLoadBalancer"})


@pytest.mark.parametrize("name", ["GetVnic", "ListInstances", "SummarizeMetricsData", "SearchResources", "InspectInstance"])
def test_reads(name):
    assert is_read(name)
    assert flags(name, "200", KNOWN) == []


def test_denied_statuses():
    for status in ("401", "403", "404"):
        assert flags("ListInstances", status, KNOWN) == [DENIED]


def test_server_errors_are_not_denials():
    assert flags("ListInstances", "500", KNOWN) == []


def test_unexpected_write():
    assert flags("DeleteBucket", "200", KNOWN) == [UNEXPECTED_WRITE]


def test_known_write_is_quiet():
    assert flags("UpdateNetworkLoadBalancer", "202", KNOWN) == []


def test_denied_write_has_both_flags():
    assert flags("DeleteBucket", "404", KNOWN) == [DENIED, UNEXPECTED_WRITE]


def test_control_plane_writes_are_never_unexpected():
    assert flags("AttachVolume", "200", KNOWN, control_plane=True) == []
    assert flags("SomethingNobodyListed", "202", KNOWN, control_plane=True) == []


def test_control_plane_refusals_still_count():
    assert flags("AttachVolume", "404", KNOWN, control_plane=True) == [DENIED]


def test_same_write_from_elsewhere_is_still_unexpected():
    assert flags("AttachVolume", "200", KNOWN, control_plane=False) == [UNEXPECTED_WRITE]
