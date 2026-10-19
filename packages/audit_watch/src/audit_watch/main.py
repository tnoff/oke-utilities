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

from .classify import flags
from .config import ConfigError, WatchConfig

logger = getLogger(__name__)

RESOURCE_AUTH = "resource"


def compartments(identity, tenancy_id: str) -> list[str]:
    """The tenancy and every active compartment under it that the principal can see."""
    found = oci.pagination.list_call_get_all_results(
        identity.list_compartments,
        tenancy_id,
        compartment_id_in_subtree=True,
        lifecycle_state="ACTIVE",
    ).data
    return [tenancy_id] + [c.id for c in found]


def resource_events(audit, compartment_id: str, start: datetime, end: datetime):
    """Yield (event_name, status, source_ip, event_type) for resource-principal events."""
    events = oci.pagination.list_call_get_all_results_generator(
        audit.list_events, "record", compartment_id, start, end, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
    )
    for event in events:
        data = event.data
        # Only auth_type is read from the identity: never its credentials.
        if getattr(data.identity, "auth_type", None) != RESOURCE_AUTH:
            continue
        yield (
            data.event_name or "unknown",
            str(getattr(data.response, "status", None) or "unknown"),
            getattr(data.identity, "ip_address", None) or "unknown",
            event.event_type or "unknown",
        )


def run(cfg: WatchConfig, audit, identity, tenancy_id: str, now: Optional[datetime] = None) -> int:
    """Audit one span. Returns 0 when it was audited, 1 when it could not be."""
    end = (now or datetime.now(timezone.utc)) - timedelta(minutes=cfg.lag_minutes)
    start = end - timedelta(minutes=cfg.window_minutes)

    try:
        comps = compartments(identity, tenancy_id)
    except oci.exceptions.ServiceError as exc:
        logger.error("Cannot list compartments: %s %s", exc.status, exc.message)
        return 1

    groups = collections.Counter()
    sources = collections.defaultdict(set)
    total = 0
    for comp in comps:
        try:
            for name, status, ip, event_type in resource_events(audit, comp, start, end):
                total += 1
                groups[(name, status, event_type)] += 1
                sources[(name, status, event_type)].add(ip)
        except oci.exceptions.ServiceError as exc:
            logger.error("Cannot list audit events for a compartment: %s %s", exc.status, exc.message)
            return 1

    flagged = 0
    for (name, status, event_type), count in sorted(groups.items()):
        ips = ",".join(sorted(sources[(name, status, event_type)]))
        reasons = flags(name, status, cfg.known_writes)
        if reasons:
            flagged += count
            logger.warning(
                "audit-watch %s event=%s status=%s count=%d ips=%s type=%s",
                "+".join(reasons), name, status, count, ips, event_type,
            )
        else:
            logger.info("audit-watch seen event=%s status=%s count=%d ips=%s", name, status, count, ips)

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
    client_config = {"region": signer.region}
    audit = oci.audit.AuditClient(client_config, signer=signer)
    identity = oci.identity.IdentityClient(client_config, signer=signer)
    return run(cfg, audit, identity, signer.tenancy_id)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
