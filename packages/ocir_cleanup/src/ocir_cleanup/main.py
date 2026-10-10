"""Main entry point for the OCIR cleanup sweep.

Split out of the combined oke-security-scanner image
(docs/projects/oke-security-scanner-package-split.md). No deployed
CronJob ever ran scan+cleanup combined in one process (cronjob.yaml sets
ENABLE_CLEANUP=false, ocir-cleanup-cronjob.yaml sets ENABLE_SCAN=false), so there is
no discovered_images hand-off to preserve -- this always discovers images
itself.
"""

import sys
import logging
from logging import getLogger
from typing import Optional

from oke_scanner_core.k8s_client import KubernetesClient
from oke_scanner_core.telemetry import setup_telemetry, shutdown_telemetry

from .config import CleanupConfig
from .discord_notifier import DiscordNotifier
from .registry_client import RegistryClient

logger = getLogger(__name__)


def run_cleanup(
    config: CleanupConfig,
    notifier: Optional[DiscordNotifier],
):
    """Run the OCIR tag + orphan-manifest cleanup phase.

    If ``CLEANUP_REPO`` is set, the cleanup is scoped to that single
    OCIR repo (used by producer pipelines that fire a one-off Job after
    pushing). Otherwise it sweeps every deployed image.
    """
    k8s_client = KubernetesClient(config.namespaces, config.exclude_namespaces)
    discovered_images = k8s_client.get_all_images()

    if config.cleanup_repo:
        logger.info(f"Cleanup scoped to repo: {config.cleanup_repo}")
        images = {
            im for im in discovered_images
            if im.is_ocir_image and im.repo_name == config.cleanup_repo
        }
        # Always include the target repo in extras so cleanup runs even if
        # nothing is currently deployed (e.g. first push of a new repo).
        extras = [config.cleanup_repo]
    else:
        images = discovered_images
        extras = config.ocir_extra_repositories

    registry_client = RegistryClient(config)

    logger.info("Checking for OCIR cleanup recommendations...")
    cleanup_recommendations = registry_client.get_old_ocir_images(
        images, keep_count=config.ocir_cleanup_keep_count,
        extra_repositories=extras,
    )
    # The scan augments `images` in place with the configured extras; union the
    # extras explicitly too so the per-repo "nothing deleted" report stays
    # complete regardless of that in-place augmentation.
    scanned_repos = {f'{im.registry}/{im.repo_name}' for im in images if im.is_ocir_image}
    scanned_repos.update(f'{registry_client.oci_registry}/{extra}' for extra in extras)
    scanned_repos = sorted(scanned_repos)

    if config.ocir_cleanup_enabled:
        deletion_results = registry_client.delete_ocir_images(cleanup_recommendations)
        if notifier:
            logger.debug("Sending Discord webhook notification...")
            notifier.send_deletion_results(deletion_results, scanned_repos)
    elif notifier:
        logger.debug("Sending Discord webhook notification...")
        notifier.send_cleanup_recommendations(cleanup_recommendations)

    logger.info("Checking for orphaned platform manifests...")
    orphan_recommendations = registry_client.get_orphaned_manifests(
        images, extra_repositories=extras,
    )
    for rec in orphan_recommendations:
        logger.info(f"Found {len(rec.tags_to_delete)} orphaned manifests in {rec.repository}")

    if config.ocir_cleanup_enabled:
        orphans_deleted = registry_client.delete_ocir_images(orphan_recommendations)
        if orphans_deleted:
            logger.info(f"Deleted {len(orphans_deleted)} orphaned platform manifests")
        if notifier:
            logger.debug("Sending Discord webhook notification...")
            notifier.send_deletion_results(orphans_deleted, scanned_repos, is_orphaned=True)


def main() -> int:
    """Run the OCIR cleanup sweep."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger.info("Initializing OCIR cleanup sweep")

    meter_provider = None
    logger_provider = None

    try:
        logger.debug("Loading configuration from environment variables")
        config = CleanupConfig.from_env()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    try:
        meter_provider, logger_provider = setup_telemetry(config)
        notifier = DiscordNotifier(config.discord_webhook_url) if config.discord_webhook_url else None

        run_cleanup(config, notifier)

        logger.info("Run completed successfully")

    finally:
        shutdown_telemetry(meter_provider, logger_provider, logger)

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
