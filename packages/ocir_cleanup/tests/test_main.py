"""Tests for ocir_cleanup.main module."""

import pytest
from unittest.mock import Mock, patch
from ocir_cleanup.main import main, run_cleanup


class TestMain:
    """Tests for main function."""

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    @patch('ocir_cleanup.main.setup_telemetry')
    @patch('ocir_cleanup.main.KubernetesClient')
    @patch('ocir_cleanup.main.RegistryClient')
    def test_main_successful_run(
        self,
        mock_registry_client,
        mock_k8s_client,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """Test main successful run."""
        mock_config = Mock()
        mock_config.discord_webhook_url = ""
        mock_config.ocir_cleanup_enabled = False
        mock_config.ocir_cleanup_keep_count = 5
        mock_config.ocir_extra_repositories = []
        mock_config.cleanup_repo = ""
        mock_config_class.from_env.return_value = mock_config

        mock_meter_provider = Mock()
        mock_logger_provider = Mock()
        mock_setup_telemetry.return_value = (mock_meter_provider, mock_logger_provider)

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        result = main()

        assert result == 0
        mock_meter_provider.force_flush.assert_called_once()
        mock_logger_provider.force_flush.assert_called_once()

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    @patch('ocir_cleanup.main.setup_telemetry')
    @patch('ocir_cleanup.main.KubernetesClient')
    @patch('ocir_cleanup.main.RegistryClient')
    def test_main_with_discord_notification(
        self,
        mock_registry_client,
        mock_k8s_client,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """Test main sends Discord notification when URL is configured."""
        mock_config = Mock()
        mock_config.discord_webhook_url = "https://discord.com/webhook"
        mock_config.ocir_cleanup_enabled = False
        mock_config.ocir_cleanup_keep_count = 5
        mock_config.ocir_extra_repositories = []
        mock_config.cleanup_repo = ""
        mock_config_class.from_env.return_value = mock_config

        mock_setup_telemetry.return_value = (None, None)

        mock_k8s_instance = Mock()
        from oke_scanner_core.image import Image
        mock_k8s_instance.get_all_images.return_value = {Image("test.ocir.io/ns/app:v1")}
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        main()

        mock_discord.assert_called()

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    @patch('ocir_cleanup.main.setup_telemetry')
    @patch('ocir_cleanup.main.KubernetesClient')
    @patch('ocir_cleanup.main.RegistryClient')
    def test_main_cleanup_enabled_skips_recommendations_sends_deletion(
        self,
        mock_registry_client,
        mock_k8s_client,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """When cleanup is enabled, send_cleanup_recommendations is skipped and send_deletion_results is sent."""
        mock_config = Mock()
        mock_config.discord_webhook_url = "https://discord.com/webhook"
        mock_config.ocir_cleanup_enabled = True
        mock_config.ocir_cleanup_keep_count = 5
        mock_config.ocir_extra_repositories = []
        mock_config.cleanup_repo = ""
        mock_config_class.from_env.return_value = mock_config

        mock_setup_telemetry.return_value = (None, None)

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.delete_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        mock_notifier = Mock()
        mock_discord.return_value = mock_notifier

        main()

        mock_notifier.send_cleanup_recommendations.assert_not_called()
        mock_notifier.send_deletion_results.assert_called()

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    @patch('ocir_cleanup.main.setup_telemetry')
    @patch('ocir_cleanup.main.KubernetesClient')
    @patch('ocir_cleanup.main.RegistryClient')
    def test_main_cleanup_disabled_sends_recommendations_skips_deletion(
        self,
        mock_registry_client,
        mock_k8s_client,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """When cleanup is disabled, send_cleanup_recommendations is sent and send_deletion_results is skipped."""
        mock_config = Mock()
        mock_config.discord_webhook_url = "https://discord.com/webhook"
        mock_config.ocir_cleanup_enabled = False
        mock_config.ocir_cleanup_keep_count = 5
        mock_config.ocir_extra_repositories = []
        mock_config.cleanup_repo = ""
        mock_config_class.from_env.return_value = mock_config

        mock_setup_telemetry.return_value = (None, None)

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        mock_notifier = Mock()
        mock_discord.return_value = mock_notifier

        main()

        mock_notifier.send_cleanup_recommendations.assert_called()
        mock_notifier.send_deletion_results.assert_not_called()

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    @patch('ocir_cleanup.main.setup_telemetry')
    @patch('ocir_cleanup.main.KubernetesClient')
    def test_main_exception_still_flushes_telemetry(
        self,
        mock_k8s_client,
        mock_setup_telemetry,
        mock_config_class,
        mock_logging,
        mock_discord,
    ):
        """Test that exceptions don't prevent telemetry flush."""
        mock_config = Mock()
        mock_config.discord_webhook_url = ""
        mock_config.cleanup_repo = ""
        mock_config_class.from_env.return_value = mock_config

        mock_meter_provider = Mock()
        mock_setup_telemetry.return_value = (mock_meter_provider, None)

        mock_k8s_client.side_effect = RuntimeError("Test error")

        with pytest.raises(RuntimeError):
            main()

        mock_meter_provider.force_flush.assert_called_once()
        mock_meter_provider.shutdown.assert_called_once()

    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    def test_main_returns_1_on_config_value_error(self, mock_config_class, _mock_logging):
        """A ValueError from CleanupConfig.from_env returns 1 rather than raising."""
        mock_config_class.from_env.side_effect = ValueError("bad config")

        assert main() == 1

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.logging')
    @patch('ocir_cleanup.main.CleanupConfig')
    @patch('ocir_cleanup.main.setup_telemetry')
    @patch('ocir_cleanup.main.KubernetesClient')
    @patch('ocir_cleanup.main.RegistryClient')
    def test_main_logs_orphan_recs_and_deletion(
        self,
        mock_registry_client,
        mock_k8s_client,
        mock_setup_telemetry,
        mock_config_class,
        _mock_logging,
        _mock_discord,
    ):
        """Covers the orphan-recs log loop and the orphans-deleted log line."""
        from oke_scanner_core.image import Image
        from ocir_cleanup.registry_client import CleanupRecommendation

        mock_config = Mock()
        mock_config.discord_webhook_url = ""
        mock_config.ocir_cleanup_enabled = True
        mock_config.ocir_cleanup_keep_count = 5
        mock_config.ocir_extra_repositories = []
        mock_config.cleanup_repo = ""
        mock_config_class.from_env.return_value = mock_config

        mock_setup_telemetry.return_value = (None, None)

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        deleted_image = Image("test.ocir.io/ns/app:unknown", digest="sha256:abc")
        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = [
            CleanupRecommendation("test.ocir.io", "ns/app", [deleted_image]),
        ]
        mock_registry_instance.delete_ocir_images.return_value = [deleted_image]
        mock_registry_client.return_value = mock_registry_instance

        main()

        assert mock_registry_instance.delete_ocir_images.call_count == 2


class TestRunCleanup:
    """Tests for run_cleanup's CLEANUP_REPO scoping."""

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.RegistryClient')
    @patch('ocir_cleanup.main.KubernetesClient')
    def test_run_cleanup_scoped_to_cleanup_repo(
        self, mock_k8s_client, mock_registry_client, _mock_discord, base_config
    ):
        """CLEANUP_REPO set: only images for that repo are forwarded, and the repo
        is always included via extra_repositories so cleanup runs even when nothing
        is deployed."""
        from dataclasses import replace
        from oke_scanner_core.image import Image

        cfg = replace(
            base_config,
            discord_webhook_url="https://discord.com/wh",
            ocir_extra_repositories=["unused/other"],  # must be overridden by cleanup_repo
            cleanup_repo="tnoff/discord_bot",
        )

        target = Image("iad.ocir.io/tnoff/discord_bot:abc123")
        other = Image("iad.ocir.io/tnoff/other_app:def456")
        non_ocir = Image("docker.io/library/postgres:16")
        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = {target, other, non_ocir}
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.delete_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        run_cleanup(cfg, None)

        call = mock_registry_instance.get_old_ocir_images.call_args
        assert call.args[0] == {target}
        assert call.kwargs["extra_repositories"] == ["tnoff/discord_bot"]
        assert call.kwargs["keep_count"] == 5

        orphan_call = mock_registry_instance.get_orphaned_manifests.call_args
        assert orphan_call.args[0] == {target}
        assert orphan_call.kwargs["extra_repositories"] == ["tnoff/discord_bot"]

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.RegistryClient')
    @patch('ocir_cleanup.main.KubernetesClient')
    def test_run_cleanup_works_with_nothing_deployed(
        self, mock_k8s_client, mock_registry_client, _mock_discord, base_config
    ):
        """First push of a brand-new repo: nothing deployed, cleanup still runs via
        the extra_repositories codepath."""
        from dataclasses import replace

        cfg = replace(
            base_config,
            ocir_cleanup_enabled=False,  # dry-run
            cleanup_repo="tnoff/new_repo",
        )

        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = set()
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        run_cleanup(cfg, None)

        mock_registry_instance.get_old_ocir_images.assert_called_once()
        mock_registry_instance.get_orphaned_manifests.assert_called_once()
        mock_registry_instance.delete_ocir_images.assert_not_called()

    @patch('ocir_cleanup.main.DiscordNotifier')
    @patch('ocir_cleanup.main.RegistryClient')
    @patch('ocir_cleanup.main.KubernetesClient')
    def test_run_cleanup_unscoped_uses_extra_repositories(
        self, mock_k8s_client, mock_registry_client, _mock_discord, base_config
    ):
        """CLEANUP_REPO unset: sweeps every discovered image plus config.ocir_extra_repositories."""
        from dataclasses import replace
        from oke_scanner_core.image import Image

        cfg = replace(base_config, ocir_extra_repositories=["tnoff/extra_repo"])

        discovered = {Image("iad.ocir.io/tnoff/a:v1")}
        mock_k8s_instance = Mock()
        mock_k8s_instance.get_all_images.return_value = discovered
        mock_k8s_client.return_value = mock_k8s_instance

        mock_registry_instance = Mock()
        mock_registry_instance.get_old_ocir_images.return_value = []
        mock_registry_instance.delete_ocir_images.return_value = []
        mock_registry_instance.get_orphaned_manifests.return_value = []
        mock_registry_client.return_value = mock_registry_instance

        run_cleanup(cfg, None)

        call = mock_registry_instance.get_old_ocir_images.call_args
        assert call.args[0] == discovered
        assert call.kwargs["extra_repositories"] == ["tnoff/extra_repo"]
