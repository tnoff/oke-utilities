"""Tests for main module."""

import pytest
from unittest.mock import Mock, patch
from oke_scanner_core.image import Image
from scan.main import main, setup_otel, send_scan_metrics, parse_extra_images, run_scan


class TestSetupOtel:
    """Tests for setup_otel function."""

    @patch('scan.main.setup_telemetry')
    @patch('scan.main.create_metrics')
    def test_setup_otel_with_all_providers(self, mock_create_metrics, mock_setup_telemetry, base_config):
        """Test setup_otel with all providers enabled."""
        mock_meter_provider = Mock()
        mock_logger_provider = Mock()
        mock_setup_telemetry.return_value = (mock_meter_provider, mock_logger_provider)

        mock_metrics = Mock()
        mock_create_metrics.return_value = mock_metrics

        meter_provider, logger_provider, metrics = setup_otel(base_config)

        assert meter_provider == mock_meter_provider
        assert logger_provider == mock_logger_provider
        assert metrics == mock_metrics

    @patch('scan.main.setup_telemetry')
    @patch('scan.main.create_metrics')
    def test_setup_otel_with_no_providers(self, mock_create_metrics, mock_setup_telemetry, base_config):
        """Test setup_otel when all providers are disabled."""
        mock_setup_telemetry.return_value = (None, None)
        mock_create_metrics.return_value = None

        meter_provider, logger_provider, metrics = setup_otel(base_config)

        assert meter_provider is None
        assert logger_provider is None
        assert metrics is None


class TestMain:
    """Tests for main function."""

    @patch('scan.main.DiscordNotifier')
    @patch('scan.main.logging')
    @patch('scan.main.Config')
    @patch('scan.main.setup_telemetry')
    @patch('scan.main.create_metrics')
    @patch('scan.main.TrivyScanner')
    @patch('scan.main.KubernetesClient')
    def test_main_successful_run(
        self,
        mock_k8s_client,
        mock_scanner,
        mock_create_metrics,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """Test main successful run."""
        mock_config = Mock()
        mock_config.extra_images = []
        mock_config.discord_webhook_url = ""
        mock_config_class.from_env.return_value = mock_config

        mock_meter_provider = Mock()
        mock_logger_provider = Mock()
        mock_setup_telemetry.return_value = (mock_meter_provider, mock_logger_provider)
        mock_create_metrics.return_value = None

        mock_scanner_instance = Mock()
        mock_scanner_instance.update_database.return_value = True
        mock_scanner_instance.scan_image.return_value = None
        mock_scanner.return_value = mock_scanner_instance

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        main()

        # Should flush telemetry
        mock_meter_provider.force_flush.assert_called_once()
        mock_logger_provider.force_flush.assert_called_once()

    @patch('scan.main.DiscordNotifier')
    @patch('scan.main.logging')
    @patch('scan.main.Config')
    @patch('scan.main.setup_telemetry')
    @patch('scan.main.create_metrics')
    @patch('scan.main.TrivyScanner')
    @patch('scan.main.KubernetesClient')
    def test_main_with_discord_notification(
        self,
        mock_k8s_client,
        mock_scanner,
        mock_create_metrics,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """Test main sends Discord notification when URL is configured."""
        mock_config = Mock()
        mock_config.extra_images = []
        mock_config.discord_webhook_url = "https://discord.com/webhook"
        mock_config_class.from_env.return_value = mock_config

        mock_setup_telemetry.return_value = (None, None)
        mock_create_metrics.return_value = None

        mock_scanner_instance = Mock()
        mock_scanner_instance.update_database.return_value = True
        mock_scanner_instance.scan_image.return_value = None
        mock_scanner.return_value = mock_scanner_instance

        mock_k8s_instance = Mock()
        from oke_scanner_core.image import Image
        mock_k8s_instance.get_all_images.return_value = {Image("test.ocir.io/ns/app:v1")}
        mock_k8s_client.return_value = mock_k8s_instance

        main()

        mock_discord.assert_called()

    @patch('scan.main.DiscordNotifier')
    @patch('scan.main.logging')
    @patch('scan.main.Config')
    @patch('scan.main.setup_telemetry')
    @patch('scan.main.create_metrics')
    @patch('scan.main.TrivyScanner')
    @patch('scan.main.KubernetesClient')
    def test_main_exception_still_flushes_telemetry(
        self,
        mock_k8s_client,
        mock_scanner,
        mock_create_metrics,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """Test that exceptions don't prevent telemetry flush."""
        mock_config = Mock()
        mock_config.extra_images = []
        mock_config.discord_webhook_url = ""
        mock_config_class.from_env.return_value = mock_config

        mock_meter_provider = Mock()
        mock_setup_telemetry.return_value = (mock_meter_provider, None)
        mock_create_metrics.return_value = None

        mock_scanner_instance = Mock()
        mock_scanner_instance.update_database.side_effect = RuntimeError("Test error")
        mock_scanner.return_value = mock_scanner_instance

        with pytest.raises(RuntimeError):
            main()

        mock_meter_provider.force_flush.assert_called_once()
        mock_meter_provider.shutdown.assert_called_once()

    @patch('scan.main.DiscordNotifier')
    @patch('scan.main.logging')
    @patch('scan.main.Config')
    @patch('scan.main.setup_telemetry')
    @patch('scan.main.create_metrics')
    @patch('scan.main.TrivyScanner')
    @patch('scan.main.KubernetesClient')
    @patch('scan.main.send_scan_metrics')
    def test_main_logs_warning_when_db_update_fails_and_emits_metrics(
        self,
        mock_send_metrics,
        mock_k8s_client,
        mock_scanner,
        mock_create_metrics,
        mock_setup_telemetry,
        mock_config_class,
        _mock_logging,
        _mock_discord,
    ):
        """Covers: db_update=False branch and the scanner_metrics branch."""
        mock_config = Mock()
        mock_config.extra_images = []
        mock_config.discord_webhook_url = ""
        mock_config_class.from_env.return_value = mock_config

        mock_setup_telemetry.return_value = (None, None)
        mock_metrics = Mock()
        mock_create_metrics.return_value = mock_metrics

        mock_scanner_instance = Mock()
        mock_scanner_instance.update_database.return_value = False  # exercise the warning branch
        mock_scanner_instance.scan_image.return_value = None
        mock_scanner.return_value = mock_scanner_instance

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        main()

        mock_send_metrics.assert_called_once()


class TestParseExtraImages:
    """Tests for parse_extra_images."""

    def test_valid_references(self):
        images, invalid = parse_extra_images([
            "iad.ocir.io/tnoff/playball:latest",
            "iad.ocir.io/tnoff/other@sha256:abc123",
        ])
        assert invalid == 0
        assert {i.full_name for i in images} == {
            "iad.ocir.io/tnoff/playball:latest",
            "iad.ocir.io/tnoff/other@sha256:abc123",
        }

    def test_reference_without_tag_is_rejected(self):
        images, invalid = parse_extra_images(["iad.ocir.io/tnoff/playball"])
        assert images == set()
        assert invalid == 1

    def test_unparseable_reference_is_rejected(self):
        # has '@' so passes the tag check, but Image cannot split it
        images, invalid = parse_extra_images(["iad.ocir.io/tnoff/playball@nocolon"])
        assert images == set()
        assert invalid == 1

    def test_bad_entry_does_not_drop_good_ones(self):
        images, invalid = parse_extra_images(["notag", "iad.ocir.io/tnoff/playball:latest"])
        assert [i.full_name for i in images] == ["iad.ocir.io/tnoff/playball:latest"]
        assert invalid == 1

    def test_empty_list(self):
        assert parse_extra_images([]) == (set(), 0)


class TestRunScanExtraImages:
    """Tests for extra image handling in run_scan."""

    @pytest.fixture
    def scanner_and_k8s(self):
        with patch('scan.main.TrivyScanner') as mock_scanner, patch('scan.main.KubernetesClient') as mock_k8s:
            mock_scanner.return_value.update_database.return_value = True
            mock_scanner.return_value.scan_image.return_value = None
            yield mock_scanner.return_value, mock_k8s.return_value

    def test_extras_scanned_in_addition_to_deployed(self, base_config, scanner_and_k8s):
        scanner, k8s = scanner_and_k8s
        k8s.get_all_images.return_value = {Image("iad.ocir.io/tnoff/app:1")}
        base_config.extra_images = ["iad.ocir.io/tnoff/playball:latest"]

        images = run_scan(base_config, None, None, None)

        scanned = {call.args[0].full_name for call in scanner.scan_image.call_args_list}
        assert scanned == {"iad.ocir.io/tnoff/app:1", "iad.ocir.io/tnoff/playball:latest"}
        assert len(images) == 2

    def test_extra_also_deployed_is_scanned_once(self, base_config, scanner_and_k8s):
        scanner, k8s = scanner_and_k8s
        k8s.get_all_images.return_value = {Image("iad.ocir.io/tnoff/playball:latest")}
        base_config.extra_images = ["iad.ocir.io/tnoff/playball:latest"]

        run_scan(base_config, None, None, None)

        assert scanner.scan_image.call_count == 1

    def test_bad_reference_does_not_abort_and_counts_as_failed(self, base_config, scanner_and_k8s):
        scanner, k8s = scanner_and_k8s
        k8s.get_all_images.return_value = set()
        base_config.extra_images = ["notag", "iad.ocir.io/tnoff/playball:latest"]
        notifier = Mock()

        run_scan(base_config, None, None, notifier)

        assert scanner.scan_image.call_count == 1
        report = notifier.send_image_scan_report.call_args.args[0]
        # one bad reference plus the good one whose (mocked) scan returned None
        assert report.failed_scans == 2

    def test_no_extras_scans_only_deployed(self, base_config, scanner_and_k8s):
        scanner, k8s = scanner_and_k8s
        k8s.get_all_images.return_value = {Image("iad.ocir.io/tnoff/app:1")}

        run_scan(base_config, None, None, None)

        assert scanner.scan_image.call_count == 1


class TestSendScanMetrics:
    """Tests for send_scan_metrics helper."""

    def test_sets_critical_and_high_gauges_per_scan_result(self):
        """send_scan_metrics emits one critical + one high gauge call per scan result."""
        from scan.scanner import CompleteScanResult, ScanResult
        from oke_scanner_core.image import Image

        complete = CompleteScanResult()
        scan = ScanResult(Image("test.ocir.io/ns/app:v1.0.0"))
        scan.critical_count = 2
        scan.high_count = 3
        complete.add_result(scan, scan.image)

        metrics = Mock()
        send_scan_metrics(metrics, complete)

        assert metrics.scan_total.set.call_count == 2
        call_args = [call.args for call in metrics.scan_total.set.call_args_list]
        assert (2, {'image': 'ns/app', 'severity': 'critical'}) in call_args
        assert (3, {'image': 'ns/app', 'severity': 'high'}) in call_args
