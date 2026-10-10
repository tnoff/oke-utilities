"""Trivy scanner wrapper for vulnerability scanning."""

from dataclasses import dataclass, field
from logging import getLogger
from json import JSONDecodeError
from json import loads as json_loads
from pathlib import Path
import shutil
import subprocess  # nosec B404
from typing import Optional

from oke_scanner_core.image import Image

from .config import Config

@dataclass
class CVEDetails:
    '''Parsed CVE Details'''
    severity: str
    title: str
    package: str
    installed: str
    fixed: str

@dataclass
class CVE:
    '''CVE Info'''
    cve_id: str
    details: list[CVEDetails] = field(default_factory=list)

@dataclass
class ScanResult:
    """Result from scanning a container image for vulnerabilities."""

    image: Image
    cves: list[CVE] = field(default_factory=list)
    critical_count: int = field(init=False)
    high_count: int = field(init=False)
    critical_fixed_count: int = field(init=False)
    high_fixed_count: int = field(init=False)

    def __post_init__(self):
        self.critical_count = 0
        self.high_count = 0
        self.critical_fixed_count = 0
        self.high_fixed_count = 0

    def add_details(self, cve_id: str, details: CVEDetails):
        '''Add cve details for id'''
        if details.severity == 'CRITICAL':
            self.critical_count +=1
            if details.fixed:
                self.critical_fixed_count += 1
        if details.severity == 'HIGH':
            self.high_count += 1
            if details.fixed:
                self.high_fixed_count += 1
        for item in self.cves:
            if cve_id == item.cve_id:
                item.details.append(details)
                return True
        self.cves.append(CVE(cve_id, details=[details]))
        return True

@dataclass
class CompleteScanResult():
    '''Complete scan'''
    total_critical: int = field(init=False)
    total_critical_fixed: int = field(init=False)
    total_high: int = field(init=False)
    total_high_fixed: int = field(init=False)
    failed_scans: int = field(init=False)
    failed_images: list[Image] = field(default_factory=list)
    scan_results: list[ScanResult] = field(default_factory=list)

    def __post_init__(self):
        self.total_critical = 0
        self.total_critical_fixed = 0
        self.total_high = 0
        self.total_high_fixed = 0
        self.failed_scans = 0

    def add_result(self, result: Optional[ScanResult], image: Optional[Image] = None) -> bool:
        if not result:
            self.failed_scans += 1
            if image:
                self.failed_images.append(image)
            return True
        self.total_critical += result.critical_count
        self.total_critical_fixed += result.critical_fixed_count
        self.total_high += result.high_count
        self.total_high_fixed += result.high_fixed_count
        self.scan_results.append(result)
        return True

logger = getLogger(__name__)

class TrivyScanner:
    """Wrapper for Trivy vulnerability scanner."""

    def __init__(self, cfg: Config):
        """Initialize Trivy scanner."""
        self.cfg = cfg
        self.db_updated = False
        self.cache_dir = Path.home() / ".cache" / "trivy"

    def _cleanup_image_cache(self) -> None:
        """Remove cached image layers while preserving the vulnerability database."""
        fanal_dir = self.cache_dir / "fanal"
        if fanal_dir.exists():
            shutil.rmtree(fanal_dir, ignore_errors=True)
            logger.debug("Cleaned up Trivy image cache")

    def update_database(self) -> bool:
        """Update Trivy vulnerability database."""
        try:
            subprocess.run(  # nosec B603 B607
                ["nice", "-n", "15", "trivy", "image", "--download-db-only"],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )

            logger.info("Trivy database updated successfully")
            self.db_updated = True
            return True

        except subprocess.TimeoutExpired:
            logger.warning("Trivy database update timed out, using cached database")
            return False

        except subprocess.CalledProcessError as e:
            logger.warning(f"Trivy database update failed, using cached database: {e.stderr}")
            return False

    def scan_image(self, image: Image) -> Optional[ScanResult]:
        """Scan a container image for vulnerabilities."""
        logger.info(f'Scanning image {image.full_name}')
        try:
            # Run Trivy scan
            cmd = [
                "nice", "-n", "15",
                "trivy",
                "image",
                "--format", "json",
                "--scanners", "vuln",
                "--severity", self.cfg.trivy_severity,
                "--timeout", f"{self.cfg.trivy_timeout}s",
                "--skip-db-update",
            ]
            if self.cfg.trivy_platform:
                cmd.extend(["--platform", self.cfg.trivy_platform])
            cmd.append(image.full_name)
            result = subprocess.run(  # nosec B603
                cmd,
                capture_output=True,
                text=True,
                timeout=self.cfg.trivy_timeout + 30,
                check=True,
            )
            return self._parse_vulnerabilities(image, json_loads(result.stdout))

        except subprocess.TimeoutExpired:
            logger.error(f"Image scan timed out: {image.full_name}")
            return None

        except subprocess.CalledProcessError as e:
            logger.error(f"Image scan failed: {image.full_name} - {e.stderr}")
            return None

        except JSONDecodeError as e:
            logger.error(f"Failed to parse Trivy output for {image.full_name}: {e}")
            return None

        finally:
            self._cleanup_image_cache()

    def _parse_vulnerabilities(self, image: Image, json_output: dict) -> Optional[ScanResult]:
        """Parse vulnerability counts and CVE details from Trivy results.

        Returns:
            VulnerabilityAnalysis with counts and CVE details
        """
        if not json_output or "Results" not in json_output:
            return None

        scan_result = ScanResult(image)
        for result in json_output.get("Results", []):
            for vuln in result.get("Vulnerabilities", []):
                severity = vuln.get("Severity", "UNKNOWN")
                cve_id = vuln.get("VulnerabilityID")

                # Store CVE details for webhook reporting
                if cve_id:
                    scan_result.add_details(cve_id, CVEDetails(
                        severity,
                        vuln.get('Title', None),
                        vuln.get('PkgName', None),
                        vuln.get("InstalledVersion", ""),
                        vuln.get("FixedVersion", ""),
                    ))

                # Log individual critical/high vulnerabilities
                if severity in ["CRITICAL", "HIGH"]:
                    logger.info(
                        f"{severity} vulnerability found: {cve_id} in "
                        f"{vuln.get('PkgName')} {vuln.get('InstalledVersion')} "
                        f"(fixed: {vuln.get('FixedVersion')}) - {vuln.get('Title', '')[:100]}"
                    )

        return scan_result
