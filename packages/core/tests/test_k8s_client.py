"""Tests for k8s_client module."""

from unittest.mock import Mock, patch

import pytest
from kubernetes.client.rest import ApiException

from oke_scanner_core.k8s_client import KubernetesClient


class TestKubernetesClient:
    """Tests for KubernetesClient class."""

    @pytest.fixture
    def k8s_client(self):
        """Create a KubernetesClient instance with configured namespaces."""
        with patch('oke_scanner_core.k8s_client.load_k8s_config'), \
             patch('oke_scanner_core.k8s_client.client.CoreV1Api'):
            return KubernetesClient(["default", "production"], [])

    def test_init_loads_k8s_config(self):
        """__init__ delegates to the shared auth bootstrap.

        The incluster/kubeconfig-fallback behavior itself is
        oke_scanner_core.k8s_auth's own responsibility and coverage.
        """
        with patch('oke_scanner_core.k8s_client.load_k8s_config') as load_config, \
             patch('oke_scanner_core.k8s_client.client.CoreV1Api'):
            KubernetesClient([], [])
        load_config.assert_called_once()

    @patch('oke_scanner_core.k8s_client.client.CoreV1Api')
    @patch('oke_scanner_core.k8s_client.load_k8s_config')
    def test_init_mirrors_authorization_to_bearertoken(self, mock_load_config, _mock_core_api):
        """kubernetes==36 stores the bearer token under api_key['authorization'] but the
        generated API methods look it up under 'BearerToken'. KubernetesClient.__init__
        must mirror the value across so outgoing requests carry an Authorization header."""
        from kubernetes import client as k8s_client_mod

        original = k8s_client_mod.Configuration.get_default_copy()
        try:
            def populate_auth_like_v36():
                cfg = k8s_client_mod.Configuration.get_default_copy()
                cfg.api_key = {'authorization': 'bearer fake-token'}
                k8s_client_mod.Configuration.set_default(cfg)
            mock_load_config.side_effect = populate_auth_like_v36

            KubernetesClient([], [])

            final = k8s_client_mod.Configuration.get_default_copy()
            assert final.api_key.get('BearerToken') == 'bearer fake-token'
            assert final.api_key.get('authorization') == 'bearer fake-token'
        finally:
            k8s_client_mod.Configuration.set_default(original)

    @patch('oke_scanner_core.k8s_client.client.CoreV1Api')
    @patch('oke_scanner_core.k8s_client.load_k8s_config')
    def test_init_does_not_overwrite_existing_bearertoken(self, mock_load_config, _mock_core_api):
        """If the loader already populated 'BearerToken' (e.g. on a future fixed client),
        the mirror step must leave it alone."""
        from kubernetes import client as k8s_client_mod

        original = k8s_client_mod.Configuration.get_default_copy()
        try:
            def populate_both():
                cfg = k8s_client_mod.Configuration.get_default_copy()
                cfg.api_key = {'authorization': 'bearer old', 'BearerToken': 'bearer new'}
                k8s_client_mod.Configuration.set_default(cfg)
            mock_load_config.side_effect = populate_both

            KubernetesClient([], [])

            assert k8s_client_mod.Configuration.get_default_copy().api_key['BearerToken'] == 'bearer new'
        finally:
            k8s_client_mod.Configuration.set_default(original)

    def test_get_namespaces_uses_configured_namespaces(self, k8s_client):
        """Test _get_namespaces returns configured namespaces."""
        namespaces = k8s_client._get_namespaces()
        assert namespaces == ["default", "production"]

    def test_get_namespaces_without_config_discovers_all(self):
        """Test _get_namespaces discovers namespaces when none configured."""
        with patch('oke_scanner_core.k8s_client.load_k8s_config'), \
             patch('oke_scanner_core.k8s_client.client.CoreV1Api'):

            # Mock namespace list
            ns1 = Mock()
            ns1.metadata.name = "default"
            ns2 = Mock()
            ns2.metadata.name = "kube-system"
            ns3 = Mock()
            ns3.metadata.name = "production"

            mock_list_result = Mock()
            mock_list_result.items = [ns1, ns2, ns3]

            k8s = KubernetesClient([], ["kube-system"])
            k8s.core_v1.list_namespace.return_value = mock_list_result

            namespaces = k8s._get_namespaces()

            # kube-system should be excluded
            assert "default" in namespaces
            assert "production" in namespaces
            assert "kube-system" not in namespaces

    def _make_pod(self, containers=(), init_containers=()):
        """Build a pod mock with the given container and init-container images."""
        pod = Mock()
        pod.spec.containers = [Mock(image=img) for img in containers] if containers else None
        pod.spec.init_containers = [Mock(image=img) for img in init_containers] if init_containers else None
        return pod

    def test_get_namespace_images_collects_regular_and_init_containers(self, k8s_client):
        """_get_namespace_images extracts images from both regular and init containers."""
        pod_a = self._make_pod(
            containers=["docker.io/library/nginx:1.27"],
            init_containers=["docker.io/library/busybox:1.36"],
        )
        pod_b = self._make_pod(containers=["iad.ocir.io/ns/app:v1.0.0"])

        list_result = Mock()
        list_result.items = [pod_a, pod_b]
        k8s_client.core_v1.list_namespaced_pod.return_value = list_result

        images = k8s_client._get_namespace_images("default")

        full_names = {img.full_name for img in images}
        assert full_names == {
            "docker.io/library/nginx:1.27",
            "docker.io/library/busybox:1.36",
            "iad.ocir.io/ns/app:v1.0.0",
        }

    def test_get_namespace_images_returns_empty_on_api_exception(self, k8s_client):
        """_get_namespace_images logs and returns an empty set when the pod list fails."""
        k8s_client.core_v1.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")

        images = k8s_client._get_namespace_images("restricted")

        assert images == set()

    def test_get_all_images_aggregates_across_namespaces(self, k8s_client):
        """get_all_images merges images from each configured namespace."""
        def list_pods(namespace):
            pod = Mock()
            if namespace == "default":
                pod.spec.containers = [Mock(image="docker.io/library/nginx:1.27")]
                pod.spec.init_containers = None
            else:
                pod.spec.containers = [Mock(image="iad.ocir.io/ns/app:v1.0.0")]
                pod.spec.init_containers = None
            result = Mock()
            result.items = [pod]
            return result

        k8s_client.core_v1.list_namespaced_pod.side_effect = list_pods

        images = k8s_client.get_all_images()

        full_names = {img.full_name for img in images}
        assert full_names == {
            "docker.io/library/nginx:1.27",
            "iad.ocir.io/ns/app:v1.0.0",
        }

    def test_get_all_images_reraises_namespace_list_failure(self):
        """get_all_images propagates an ApiException raised by namespace discovery."""
        with patch('oke_scanner_core.k8s_client.load_k8s_config'), \
             patch('oke_scanner_core.k8s_client.client.CoreV1Api'):
            k8s = KubernetesClient([], [])

        k8s.core_v1.list_namespace.side_effect = ApiException(status=500, reason="Boom")

        with pytest.raises(ApiException):
            k8s.get_all_images()
