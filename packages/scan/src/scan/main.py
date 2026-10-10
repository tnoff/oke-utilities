"""Main entry point for the vulnerability scan."""

import sys
import logging
from logging import getLogger
from typing import Tuple, Optional

from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from oke_scanner_core.image import Image
from oke_scanner_core.k8s_client import KubernetesClient

from .config import Config
from .telemetry import setup_telemetry, shutdown_telemetry, create_metrics, Metrics
from .scanner import TrivyScanner, CompleteScanResult
from .discord_notifier import DiscordNotifier


logger = getLogger(__name__)

def setup_otel(config: Config) -> Tuple[Optional[MeterProvider], Optional[LoggerProvider], Optional[Metrics]]:
    logger.debug("Initializing OpenTelemetry")
    meter_provider, logger_provider = setup_telemetry(config)
    # The OTLP logging handler is attached once, to the root logger, in setup_telemetry

    # Create metrics (returns None if meter_provider is None)
    scanner_metrics = create_metrics(meter_provider)
    return meter_provider, logger_provider, scanner_metrics

def send_scan_metrics(metric_provider: Metrics, scan_results: CompleteScanResult):
    '''Send otel metrics from scan result'''
    for scan in scan_results.scan_results:
        metric_provider.scan_total.set(scan.critical_count, {
            'image': scan.image.repo_name,
            'severity': 'critical',
        })
        metric_provider.scan_total.set(scan.high_count, {
            'image': scan.image.repo_name,
            'severity': 'high',
        })
        metric_provider.scan_failed.set(0, {'image': scan.image.repo_name})
    # Set after the successes so an image with both a failed and a successful tag
    # (same repo_name) reads as failed
    for image in scan_results.failed_images:
        metric_provider.scan_failed.set(1, {'image': image.repo_name})

def parse_extra_images(references: list[str]) -> Tuple[set[Image], int]:
    '''Build Images from SCAN_EXTRA_IMAGES entries, returning the images and a count of bad entries.

    An entry must carry a tag (name:tag) or a digest (name@sha256:...); a bare name
    is rejected rather than silently scanned as :latest.
    '''
    images = set()
    invalid = 0
    for reference in references:
        last_segment = reference.rsplit('/', 1)[-1]
        if ':' not in last_segment and '@' not in last_segment:
            logger.error(f"Ignoring extra image without a tag or digest: {reference}")
            invalid += 1
            continue
        try:
            images.add(Image(reference))
        except (IndexError, ValueError):
            logger.error(f"Ignoring unparseable extra image: {reference}")
            invalid += 1
    return images, invalid

def run_scan(
    config: Config,
    scanner_metrics: Optional[Metrics],
    notifier: Optional[DiscordNotifier],
) -> CompleteScanResult:
    """Run the Trivy scan phase and return the results."""
    scanner = TrivyScanner(config)
    logger.info("Updating Trivy vulnerability database...")
    if not scanner.update_database():
        logger.warning("Trivy database update failed, using cached database")

    logger.debug("Initializing Kubernetes client")
    k8s_client = KubernetesClient(config.namespaces, config.exclude_namespaces)

    logger.info("Discovering deployed container images...")
    images = k8s_client.get_all_images()
    extra_images, invalid_extras = parse_extra_images(config.extra_images)
    if extra_images:
        logger.info(f"Adding {len(extra_images)} extra images to scan")
    # Set union dedupes an extra that is also deployed (Image equality is by full_name)
    images = images | extra_images
    logger.info(f"Beginning vulnerability scans ({len(images)} images)")
    scan_results = CompleteScanResult()
    # Unparseable extras have no Image to report, but still show up in the failed count
    for _ in range(invalid_extras):
        scan_results.add_result(None)

    for idx, image in enumerate(sorted(images), 1):
        logger.info(f"[{idx}/{len(images)}] Scanning: {image.full_name}")
        result = scanner.scan_image(image)
        scan_results.add_result(result, image)

    if notifier:
        logger.debug("Sending Discord webhook notification...")
        notifier.send_image_scan_report(scan_results)

    if scanner_metrics:
        logger.info('Sending out scan metrics')
        send_scan_metrics(scanner_metrics, scan_results)

    return scan_results

def main() -> int:
    """Run the security scanner."""
    # Configure logging to DEBUG level and output to stdout
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger.info("Initializing OKE Security Scanner")

    # Initialize providers as None so they're accessible in finally block
    meter_provider = None
    logger_provider = None

    logger.debug("Loading configuration from environment variables")
    config = Config.from_env()

    try:
        meter_provider, logger_provider, scanner_metrics = setup_otel(config)
        notifier = DiscordNotifier(config.discord_webhook_url) if config.discord_webhook_url else None

        scan_results = run_scan(config, scanner_metrics, notifier)

        if scan_results.failed_scans:
            logger.error(f"Run completed with {scan_results.failed_scans} failed scans")
        else:
            logger.info("Run completed successfully")

    finally:
        shutdown_telemetry(meter_provider, logger_provider, logger)

    # Exit non-zero on any failed scan so the Job/pod reads Failed, not Completed
    return 1 if scan_results.failed_scans else 0


if __name__ == "__main__":  # pragma: no cover
    main()
