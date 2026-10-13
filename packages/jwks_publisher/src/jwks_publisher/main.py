"""Entrypoint for the JWKS publisher CronJob.

Invoked as `python -m jwks_publisher` from the CronJob's `command:`. Each run
mirrors the cluster's live ServiceAccount key set into the hosted object that OCI
Identity Domains fetches on every token exchange (tnoff/terraform#191).
"""

import logging
import sys
from logging import getLogger
from typing import Optional

import oci

from .config import JwksPublisherConfig
from .hosted import HostedJwks
from .jwks import JwksError, canonicalize, fetch_live_jwks

logger = getLogger(__name__)


def run(cfg: JwksPublisherConfig, hosted: Optional[HostedJwks] = None) -> int:
    """One mirror pass. Returns 0 when the hosted set is current, 1 on any failure."""
    if not cfg.bucket_name:
        logger.error("JWKS_BUCKET is not set")
        return 1

    try:
        live, live_kids = canonicalize(fetch_live_jwks(cfg.jwks_url, cfg.service_account_dir, cfg.request_timeout))
    except (JwksError, OSError) as exc:
        logger.error("Cannot read a publishable key set from the API server; hosted copy left alone: %s", exc)
        return 1

    try:
        hosted = hosted or HostedJwks(cfg)
        current = hosted.read()
    except oci.exceptions.ServiceError as exc:
        logger.error("Cannot read the hosted key set: %s %s", exc.status, exc.message)
        return 1
    if current is None:
        logger.error(
            "Hosted object %s/%s does not exist. terraform seeds it; the publisher cannot create it",
            cfg.bucket_name,
            cfg.object_name,
        )
        return 1
    current_body, etag = current

    try:
        current_canonical, current_kids = canonicalize(current_body)
    except JwksError as exc:
        # The terraform placeholder (an empty key set) lands here on the first run.
        logger.warning("Hosted key set is not a valid key set (%s); it will be replaced", exc)
        current_canonical, current_kids = None, []

    if current_canonical == live:
        logger.info("Hosted key set is current: kids=%s", live_kids)
        return 0

    logger.warning(
        "Hosted key set differs from the cluster's: added=%s removed=%s kept=%s",
        sorted(set(live_kids) - set(current_kids)),
        sorted(set(current_kids) - set(live_kids)),
        sorted(set(live_kids) & set(current_kids)),
    )
    if cfg.dry_run:
        logger.info("DRY_RUN is set; not writing")
        return 0

    try:
        hosted.write(live, etag)
    except oci.exceptions.ServiceError as exc:
        # 412 means the object changed between our read and write; the next run retries.
        logger.error("Could not overwrite the hosted key set: %s %s", exc.status, exc.message)
        return 1

    verify = hosted.read()
    if verify is None or verify[0] != live:
        logger.error("Hosted key set does not match what was just written")
        return 1
    logger.info("Published key set: kids=%s", live_kids)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger.info("Initializing JWKS publisher")
    return run(JwksPublisherConfig.from_env())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
