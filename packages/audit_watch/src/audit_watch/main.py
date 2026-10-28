"""Entrypoint for the audit watcher CronJob.

Invoked as `python -m audit_watch`. Each run lists the Audit events of the last few minutes
in every compartment, keeps those made by resource principals (`auth_type == "resource"`,
which covers the federated workloads and OCI's own services), and logs a summary line, one
line per (event, status) group, and a WARNING for every denied call or unexpected write.

What it can and cannot tell you (measured, tnoff/terraform#191): events from a resource
principal carry no principal name and a shared principal id, so a call cannot be tied to a
ServiceAccount, only to its source IP. It CAN see a federated principal being refused (the
call is logged with status 404) and any write nobody expects. It never reads
`identity.credentials`: write events carry a live token there, and nothing about the credential
is needed or logged.

Exit 0 when the span was audited (flags are alerts, not failures); 1 when it could not be, so a
broken watcher fails its Job.
"""

import collections
import logging
import sys
from datetime import datetime, timedelta, timezone
from logging import getLogger
from typing import Optional

import oci

from .classify import UNNAMED, flags, is_check
from .config import ConfigError, WatchConfig
from .control_plane import ControlPlaneError, api_server_ips

logger = getLogger(__name__)

RESOURCE_AUTH = "resource"
# At most this many distinct samples of the request detail are kept per group: enough to name the caller,
# few enough that a noisy group cannot make a long log line.
DETAIL_SAMPLES = 3


def compartments(identity, tenancy_id: str) -> list[str]:
    """The tenancy and every active compartment under it that the principal can see."""
    found = oci.pagination.list_call_get_all_results(
        identity.list_compartments,
        tenancy_id,
        compartment_id_in_subtree=True,
        lifecycle_state="ACTIVE",
    ).data
    return [tenancy_id] + [c.id for c in found]


def describe(event) -> str:
    """Who and what an event was, from fields that name the caller without carrying a credential.

    The warning for an event with no name (`event=unknown`) is otherwise just an IP address. These are the
    fields that tell a reader what made the call: the service that logged it, the client's user agent and
    the request action and path. Never the request headers or parameters.
    """
    data = event.data
    request = getattr(data, "request", None)
    parts = (
        ("source", getattr(event, "source", None)),
        ("agent", getattr(data.identity, "user_agent", None)),
        ("request", " ".join(p for p in (getattr(request, "action", None), getattr(request, "path", None)) if p)),
        ("resource", getattr(data, "resource_name", None)),
    )
    return " ".join(f"{label}={value.replace(' ', '_')}" for label, value in parts if value)


def method_of(event) -> str:
    """The request's HTTP method in upper case, or "unknown"."""
    action = getattr(getattr(event.data, "request", None), "action", None)
    return action.upper() if action else "unknown"


def path_of(event) -> str:
    """The request path, or "" when the event has none."""
    return getattr(getattr(event.data, "request", None), "path", None) or ""


def resource_events(audit, compartment_id: str, start: datetime, end: datetime):
    """Yield (event_name, status, source_ip, event_type, method, check, detail) for resource-principal events.

    `check` is True when the request path is one of the services' read-only checks (see `classify.is_check`).
    """
    events = oci.pagination.list_call_get_all_results_generator(
        audit.list_events, "record", compartment_id, start, end, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
    )
    for event in events:
        data = event.data
        # Only auth_type is read from the identity: never its credentials.
        if getattr(data.identity, "auth_type", None) != RESOURCE_AUTH:
            continue
        yield (
            data.event_name or UNNAMED,
            str(getattr(data.response, "status", None) or "unknown"),
            getattr(data.identity, "ip_address", None) or "unknown",
            event.event_type or "unknown",
            method_of(event),
            is_check(path_of(event)),
            describe(event),
        )


def run(
    cfg: WatchConfig, audit, identity, tenancy_id: str, now: Optional[datetime] = None, control_plane: frozenset = frozenset()
) -> int:
    """Audit one span. Returns 0 when it was audited, 1 when it could not be.

    `control_plane` is the set of API server addresses: events from them are the cluster operating
    itself, so they are grouped apart and their writes are not flagged (their refusals still are).
    """
    end = (now or datetime.now(timezone.utc)) - timedelta(minutes=cfg.lag_minutes)
    start = end - timedelta(minutes=cfg.window_minutes)

    try:
        comps = compartments(identity, tenancy_id)
    except oci.exceptions.ServiceError as exc:
        logger.error("Cannot list compartments: %s %s", exc.status, exc.message)
        return 1

    groups = collections.Counter()
    sources = collections.defaultdict(set)
    details = collections.defaultdict(set)
    total = 0
    for comp in comps:
        try:
            for name, status, ip, event_type, method, check, detail in resource_events(audit, comp, start, end):
                total += 1
                # The method and the check are part of the group: an unnamed POST must not hide inside a group of
                # unnamed GETs, nor behind a read-only check.
                key = (name, status, event_type, ip in control_plane, method, check)
                groups[key] += 1
                sources[key].add(ip)
                if detail and len(details[key]) < DETAIL_SAMPLES:
                    details[key].add(detail)
        except oci.exceptions.ServiceError as exc:
            logger.error("Cannot list audit events for a compartment: %s %s", exc.status, exc.message)
            return 1

    flagged = 0
    for key, count in sorted(groups.items()):
        name, status, event_type, from_control_plane, method, check = key
        ips = ",".join(sorted(sources[key]))
        reasons = flags(name, status, cfg.known_writes, from_control_plane, method, check)
        if reasons:
            flagged += count
            logger.warning(
                "audit-watch %s event=%s status=%s count=%d ips=%s type=%s method=%s",
                "+".join(reasons), name, status, count, ips, event_type, method,
            )
            # One line per sample, so a reader can tell what an unnamed event was without the console.
            for detail in sorted(details[key]):
                logger.warning("audit-watch detail event=%s ips=%s %s", name, ips, detail)
        else:
            logger.info(
                "audit-watch seen event=%s status=%s count=%d ips=%s method=%s%s",
                name, status, count, ips, method, " check=yes" if check else "",
            )

    logger.info(
        "audit-watch summary start=%s end=%s compartments=%d resource_events=%d flagged=%d",
        start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"), len(comps), total, flagged,
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Build clients from the pod's resource principal and audit one span."""
    del argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    try:
        cfg = WatchConfig.from_env()
    except ConfigError as exc:
        logger.error("Bad configuration: %s", exc)
        return 1
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Cannot build the resource principal signer: %s", exc)
        return 1
    try:
        control_plane = api_server_ips()
        logger.info("audit-watch control plane ips=%s", ",".join(sorted(control_plane)))
    except ControlPlaneError as exc:
        # Not fatal: without the addresses the control plane's writes are flagged like any other, which
        # is noisy but never hides an event.
        logger.warning("Cannot read the API server addresses (%s); control plane writes will be flagged", exc)
        control_plane = frozenset()
    client_config = {"region": signer.region}
    audit = oci.audit.AuditClient(client_config, signer=signer)
    identity = oci.identity.IdentityClient(client_config, signer=signer)
    return run(cfg, audit, identity, signer.tenancy_id, control_plane=control_plane)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
