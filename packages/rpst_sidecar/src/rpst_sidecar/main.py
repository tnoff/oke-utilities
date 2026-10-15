"""Entrypoint for the RPST sidecar.

Invoked as `python -m rpst_sidecar`. Runs forever beside the main container: trade the
projected ServiceAccount token for an OCI resource principal session token (RPST), write
it and its private key to a shared memory-backed volume, sleep, repeat
(tnoff/terraform#191). `python -m rpst_sidecar --check` is the exec probe.
"""

import argparse
import logging
import os
import random
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from typing import Callable, Optional

from cryptography.hazmat.primitives.asymmetric import rsa

from .config import ConfigError, RpstConfig
from .exchange import ExchangeError, exchange, generate_key, jwt_claims, private_pem

logger = getLogger(__name__)

RPST_FILE = "rpst"
PRIVATE_KEY_FILE = "private.pem"
FILE_MODE = 0o640

BACKOFF_START = 2.0
BACKOFF_CAP = 120.0

# --check fails when the token has less than this left.
CHECK_MIN_REMAINING = 60


def now() -> float:
    """Wall clock seconds; a seam for tests."""
    return time.time()


def default_jitter(delay: float) -> float:
    """Spread retries over the upper half of the backoff window."""
    return delay * random.uniform(0.5, 1.0)  # nosec B311 - scheduling jitter, not security


def write_atomic(path: Path, data: bytes, mode: int = FILE_MODE) -> None:
    """Write `data` so a reader sees the old file or the new one, never a partial one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def iso(timestamp: Optional[float]) -> str:
    """UTC ISO-8601, or "unknown"."""
    if timestamp is None:
        return "unknown"
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def lifetime_seconds(claims: dict, fallback_minutes: int) -> float:
    """Lifetime from the RPST's own exp - iat; the requested lifetime when they are unusable."""
    iat, exp = _number(claims.get("iat")), _number(claims.get("exp"))
    if iat is not None and exp is not None and exp > iat:
        return exp - iat
    return fallback_minutes * 60.0


def refresh_delay(claims: dict, cfg: RpstConfig) -> float:
    """Seconds to wait after a successful exchange before the next one."""
    return lifetime_seconds(claims, cfg.exp_minutes) * cfg.refresh_fraction


class Sidecar:
    """One process's worth of state: the config, the one key, and whether it is on disk yet."""

    def __init__(self, cfg: RpstConfig, key: Optional[rsa.RSAPrivateKey] = None) -> None:
        self.cfg = cfg
        self.key = key or generate_key()
        self.key_written = False

    def refresh_once(self) -> float:
        """One exchange and write. Returns seconds until the next refresh.

        Raises ExchangeError on an exchange failure and OSError if the volume is not writable.
        """
        out = Path(self.cfg.output_dir)
        rpst, sa_token = exchange(self.cfg, self.key)
        if not self.key_written:
            # Once, and before the first rpst, so a reader never sees a token without its key.
            write_atomic(out / PRIVATE_KEY_FILE, private_pem(self.key))
            self.key_written = True
        # No trailing newline: the OCI SDK fails with "Invalid crypto padding" otherwise.
        write_atomic(out / RPST_FILE, rpst.encode("ascii"))
        claims = jwt_claims(rpst)
        self.audit(claims, jwt_claims(sa_token))
        return refresh_delay(claims, self.cfg)

    def audit(self, rpst_claims: dict, sa_claims: dict) -> None:
        """One INFO line per successful exchange: which workload got which token. No secrets."""
        iat, exp = _number(rpst_claims.get("iat")), _number(rpst_claims.get("exp"))
        lifetime = f"{(exp - iat) / 60:.1f}" if iat is not None and exp is not None else "unknown"
        logger.info(
            "rpst issued jti=%s sub=%s namespace=%s pod=%s service_account=%s issued=%s expires=%s lifetime_minutes=%s",
            rpst_claims.get("jti", "unknown"),
            sa_claims.get("sub", "unknown"),
            self.cfg.pod_namespace,
            self.cfg.pod_name,
            self.cfg.service_account_name,
            iso(iat),
            iso(exp),
            lifetime,
        )

    def run(
        self,
        stop: threading.Event,
        jitter: Callable[[float], float] = default_jitter,
    ) -> int:
        """Refresh until `stop` is set. Exchange failures are retried forever with backoff."""
        failures = 0
        while not stop.is_set():
            try:
                delay = self.refresh_once()
            except (ExchangeError, OSError) as exc:
                failures += 1
                delay = jitter(min(BACKOFF_CAP, BACKOFF_START * 2 ** (failures - 1)))
                logger.warning("RPST exchange failed (attempt %d), retrying in %.1fs: %s", failures, delay, exc)
            else:
                failures = 0
                logger.info("Next refresh in %.0fs", delay)
            stop.wait(delay)
        logger.info("Stopping")
        return 0


def check(cfg: RpstConfig) -> Optional[str]:
    """Why the output is not healthy, or None when it is."""
    out = Path(cfg.output_dir)
    try:
        rpst = (out / RPST_FILE).read_text(encoding="ascii").strip()
    except (OSError, ValueError) as exc:
        return f"cannot read {out / RPST_FILE}: {exc}"
    if not (out / PRIVATE_KEY_FILE).is_file():
        return f"{out / PRIVATE_KEY_FILE} does not exist"
    exp = _number(jwt_claims(rpst).get("exp"))
    if exp is None:
        return "rpst has no usable exp claim"
    remaining = exp - now()
    if remaining <= CHECK_MIN_REMAINING:
        return f"rpst expires in {remaining:.0f}s (needs more than {CHECK_MIN_REMAINING}s)"
    return None


def main(argv: Optional[list[str]] = None) -> int:
    """Run the sidecar, or with --check, probe the files it wrote."""
    parser = argparse.ArgumentParser(prog="rpst_sidecar")
    parser.add_argument("--check", action="store_true", help="exit 0 only if a fresh RPST is on disk")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    try:
        cfg = RpstConfig.from_env()
    except ConfigError as exc:
        logger.error("Bad configuration: %s", exc)
        return 1

    if args.check:
        problem = check(cfg)
        if problem:
            print(f"unhealthy: {problem}")
            return 1
        return 0

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    logger.info("Initializing RPST sidecar for %s/%s", cfg.pod_namespace, cfg.pod_name)
    return Sidecar(cfg).run(stop)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
