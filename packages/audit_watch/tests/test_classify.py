"""Tests for the event classifier."""

import ipaddress

import pytest

from audit_watch.classify import DENIED, UNEXPECTED_WRITE, flags, in_networks, is_check, is_read

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


@pytest.mark.parametrize("method", ["GET", "HEAD", "get"])
def test_an_unnamed_read_is_quiet(method):
    # OCI's Block Volumes service logs its attachability check without an operation name.
    assert is_read("unknown", method)
    assert flags("unknown", "200", KNOWN, method=method) == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "unknown", ""])
def test_an_unnamed_call_that_is_not_a_read_is_still_unexpected(method):
    assert not is_read("unknown", method)
    assert flags("unknown", "200", KNOWN, method=method) == [UNEXPECTED_WRITE]


def test_an_unnamed_call_without_a_method_argument_is_still_unexpected():
    assert flags("unknown", "200", KNOWN) == [UNEXPECTED_WRITE]


def test_a_denied_unnamed_read_is_still_a_denial():
    assert flags("unknown", "404", KNOWN, method="GET") == [DENIED]


def test_the_method_never_excuses_a_named_write():
    assert flags("DeleteBucket", "200", KNOWN, method="GET") == [UNEXPECTED_WRITE]


@pytest.mark.parametrize(
    "path",
    ["/v1/preLaunchAttachability", "/v1/volumeAttachability", "/v1/volumeAttachability/ocid1.volume.x"],
)
def test_the_services_read_only_checks_are_recognised(path):
    assert is_check(path)


@pytest.mark.parametrize(
    "path", ["", "/v1/preLaunchAttachabilityX", "/v1/volumes", "/v1/other/preLaunchAttachability", "/v2/preLaunchAttachability"]
)
def test_other_paths_are_not_checks(path):
    assert not is_check(path)


@pytest.mark.parametrize("method", ["POST", "GET", "PUT", "unknown"])
def test_an_unnamed_check_is_quiet_whatever_its_method(method):
    assert is_read("unknown", method, check=True)
    assert flags("unknown", "200", KNOWN, method=method, check=True) == []


def test_a_denied_unnamed_check_is_still_a_denial():
    assert flags("unknown", "404", KNOWN, method="POST", check=True) == [DENIED]


def test_a_check_never_excuses_a_named_write():
    assert flags("DeleteBucket", "200", KNOWN, method="POST", check=True) == [UNEXPECTED_WRITE]

@pytest.mark.parametrize("name", ["LaunchInstance", "TerminateInstance"])
@pytest.mark.parametrize("status", ["200", "204", "404"])
def test_the_oke_service_replacing_a_node_is_quiet(name, status):
    # Its first LaunchInstance attempt is answered 404 and retried a second later.
    assert flags(name, status, KNOWN, method="POST", oke_service=True) == []


@pytest.mark.parametrize("name", ["LaunchInstance", "TerminateInstance"])
def test_the_same_call_from_anywhere_else_is_still_reported(name):
    assert flags(name, "200", KNOWN, oke_service=False) == [UNEXPECTED_WRITE]
    assert flags(name, "404", KNOWN, oke_service=False) == [DENIED, UNEXPECTED_WRITE]


@pytest.mark.parametrize("name", ["UpdateInstance", "DeleteBucket", "CreateVcn", "unknown"])
def test_the_oke_service_network_excuses_nothing_but_node_lifecycle(name):
    assert flags(name, "200", KNOWN, method="POST", oke_service=True) == [UNEXPECTED_WRITE]
    assert DENIED in flags(name, "404", KNOWN, method="POST", oke_service=True)


@pytest.mark.parametrize(
    "ip,expected",
    [("10.240.8.37", True), ("10.240.8.255", True), ("10.240.9.1", False), ("10.0.0.5", False), ("unknown", False), ("", False)],
)
def test_in_networks(ip, expected):
    assert in_networks(ip, [ipaddress.ip_network("10.240.8.0/24")]) is expected


def test_no_networks_matches_nothing():
    assert not in_networks("10.240.8.37", ())
