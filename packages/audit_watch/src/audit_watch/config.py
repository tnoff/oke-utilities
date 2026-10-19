"""Configuration for the audit watcher, loaded from env vars."""

import os
from dataclasses import dataclass

# Writes seen from resource principals in normal operation: the OKE cloud controller keeping
# load balancers and VNICs in step with Services. Measured over 12 hours; reads are not listed
# because anything that only reads is never flagged.
DEFAULT_KNOWN_WRITES = "UpdateInternalVnic,UpdateNetworkLoadBalancer"


class ConfigError(ValueError):
    """An environment variable holds a value the watcher cannot use."""


@dataclass(frozen=True)
class WatchConfig:
    """Watcher config; populated by `from_env`."""

    # The audited span is [now - lag - window, now - lag]. The lag covers the minutes the Audit
    # service takes to make an event listable; the window should match the CronJob schedule
    # (plus a little overlap so an event on a boundary is not missed).
    window_minutes: int = 12
    lag_minutes: int = 5

    # Event names a resource principal may perform that are not reads.
    known_writes: frozenset = frozenset(DEFAULT_KNOWN_WRITES.split(","))

    @classmethod
    def from_env(cls) -> "WatchConfig":
        """Read and validate the environment."""
        try:
            window = int(os.getenv("WINDOW_MINUTES", "12"))
            lag = int(os.getenv("LAG_MINUTES", "5"))
        except ValueError as exc:
            raise ConfigError(f"WINDOW_MINUTES and LAG_MINUTES must be integers: {exc}") from exc
        if window < 1 or lag < 0:
            raise ConfigError("WINDOW_MINUTES must be at least 1 and LAG_MINUTES at least 0")
        names = os.getenv("KNOWN_WRITE_EVENTS", DEFAULT_KNOWN_WRITES)
        return cls(
            window_minutes=window,
            lag_minutes=lag,
            known_writes=frozenset(n.strip() for n in names.split(",") if n.strip()),
        )
