"""Decide which Audit events from resource principals deserve attention."""

# OCI read operations start with one of these. Anything else changes state.
READ_PREFIXES = ("Get", "List", "Summarize", "Search", "Inspect", "Read", "Describe")

# OCI answers a call the principal may not make with 404 NotAuthorizedOrNotFound (it does not
# reveal whether the thing exists), or 401/403.
DENIED_STATUSES = frozenset({"401", "403", "404"})

DENIED = "DENIED"
UNEXPECTED_WRITE = "UNEXPECTED_WRITE"


def is_read(event_name: str) -> bool:
    """True for operations that only read."""
    return event_name.startswith(READ_PREFIXES)


def flags(event_name: str, status: str, known_writes) -> list[str]:
    """Why this event is worth a look, or an empty list for ordinary traffic."""
    found = []
    if status in DENIED_STATUSES:
        found.append(DENIED)
    if not is_read(event_name) and event_name not in known_writes:
        found.append(UNEXPECTED_WRITE)
    return found
