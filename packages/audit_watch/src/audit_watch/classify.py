"""Decide which Audit events from resource principals deserve attention."""

# OCI read operations start with one of these. Anything else changes state.
READ_PREFIXES = ("Get", "List", "Summarize", "Search", "Inspect", "Read", "Describe")

# What the watcher calls an event that carries no operation name. OCI's own services log some calls that way
# (Block Volumes checking that a volume can attach, when a pod with a volume moves node).
UNNAMED = "unknown"

# HTTP methods that cannot change anything. Only consulted for an unnamed event: a name is the better signal.
READ_METHODS = frozenset({"GET", "HEAD"})

# Requests an OCI service sends to ask whether something is possible, whatever HTTP method they use (Block
# Volumes asks with a GET before a volume attaches and with a POST before an instance launches). Matched on the
# request path, only for an unnamed event, so no other unnamed POST is excused.
READ_ONLY_PATHS = ("/v1/volumeAttachability", "/v1/preLaunchAttachability")

# OCI answers a call the principal may not make with 404 NotAuthorizedOrNotFound (it does not
# reveal whether the thing exists), or 401/403.
DENIED_STATUSES = frozenset({"401", "403", "404"})

DENIED = "DENIED"
UNEXPECTED_WRITE = "UNEXPECTED_WRITE"


def is_check(path: str) -> bool:
    """True for a request path that is one of the services' read-only checks (`READ_ONLY_PATHS`)."""
    return any(path == known or path.startswith(known + "/") for known in READ_ONLY_PATHS)


def is_read(event_name: str, method: str = "", check: bool = False) -> bool:
    """True for operations that only read.

    An unnamed event is judged by its HTTP method instead, so a GET is a read and an unnamed POST, PUT, PATCH
    or DELETE is still not, unless `check` says its path is one of the read-only checks (`is_check`).
    """
    if event_name.startswith(READ_PREFIXES):
        return True
    return event_name == UNNAMED and (check or method.upper() in READ_METHODS)


def flags(
    event_name: str, status: str, known_writes, control_plane: bool = False, method: str = "", check: bool = False
) -> list[str]:
    """Why this event is worth a look, or an empty list for ordinary traffic.

    `control_plane` is True for events from the cluster's own API server addresses: OKE operating the
    cluster (attaching a volume when a pod moves, say), whatever the operation is called, so its writes
    are never unexpected. A refusal still is: it means the control plane lost a permission.

    `method` is the request's HTTP method and `check` whether its path is a read-only check; both only matter
    for an event with no name (see `is_read`).
    """
    found = []
    if status in DENIED_STATUSES:
        found.append(DENIED)
    if not control_plane and not is_read(event_name, method, check) and event_name not in known_writes:
        found.append(UNEXPECTED_WRITE)
    return found
