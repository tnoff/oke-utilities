"""Entrypoint for the secret-age tracker CronJob.

Invoked as `python -m secret_age` from the CronJob's `command:`.
"""

import logging
import sys
from logging import getLogger

from oke_scanner_core.log_redaction import install_log_redaction

from .aggregator import aggregate
from .config import SecretAgeConfig
from .discord_report import send_report
from .readers import k8s as k8s_reader
from .readers import layer1_ledger as layer1_reader
from .readers import oci_iam as oci_reader

logger = getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    install_log_redaction()
    logger.info("Initializing secret-age tracker")

    cfg = SecretAgeConfig.from_env()
    findings = []

    if cfg.enable_oci_reader:
        try:
            findings.extend(oci_reader.read_findings(cfg))
        except Exception:  # pylint: disable=broad-except
            logger.exception("OCI reader failed — continuing with other readers")

    if cfg.enable_k8s_reader:
        try:
            findings.extend(k8s_reader.read_findings(cfg))
        except Exception:  # pylint: disable=broad-except
            logger.exception("K8s reader failed — continuing with other readers")

    if cfg.enable_layer1_reader:
        try:
            findings.extend(layer1_reader.read_findings(cfg))
        except Exception:  # pylint: disable=broad-except
            logger.exception("Layer-1 ledger reader failed — continuing with other readers")

    report = aggregate(findings)
    send_report(cfg.discord_webhook_url, report)
    logger.info("Run complete: %d actionable / %d total", report.actionable, report.total)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
