"""Kubernetes client for discovering deployed images."""

from logging import getLogger

from kubernetes import client
from kubernetes.client.rest import ApiException

from .image import Image
from .k8s_auth import load_k8s_config

logger = getLogger(__name__)


class KubernetesClient:
    """Client for discovering container images deployed in the cluster."""

    def __init__(self, namespaces: list[str], exclude_namespaces: list[str]):
        """Initialize Kubernetes client.

        Args:
            namespaces: Namespaces to scan. Empty means "all namespaces
                minus exclude_namespaces".
            exclude_namespaces: Namespaces to skip when namespaces is empty.
        """
        self.namespaces = namespaces
        self.exclude_namespaces = exclude_namespaces
        load_k8s_config()

        # kubernetes==36.0.0 regression: load_*_config() stores the bearer
        # token under api_key['authorization'], but the generated API methods
        # look it up under the 'BearerToken' security-scheme key, so no
        # Authorization header gets sent and every call 401s. Mirror the
        # value across until upstream ships a fix.
        default_cfg = client.Configuration.get_default_copy()
        if default_cfg.api_key.get('authorization') and not default_cfg.api_key.get('BearerToken'):
            default_cfg.api_key['BearerToken'] = default_cfg.api_key['authorization']
            client.Configuration.set_default(default_cfg)

        self.core_v1 = client.CoreV1Api()

    def get_all_images(self) -> set[Image]:
        """Get all unique container images deployed in the cluster."""
        images = set()

        try:
            # Get all namespaces
            namespaces = self._get_namespaces()
            logger.info(f"Found {len(namespaces)} namespaces: {namespaces}")

            # Get images from pods in each namespace
            for namespace in namespaces:
                namespace_images = self._get_namespace_images(namespace)
                images.update(namespace_images)
                logger.info(f"Found {len(namespace_images)} images in namespace {namespace}")

        except ApiException as e:
            logger.error(f"Kubernetes API error (status {e.status}): {e}")
            raise

        logger.info(f"Total unique images discovered: {len(images)}")
        return images

    def _get_namespaces(self) -> list[str]:
        """Get list of namespaces to scan."""
        # If specific namespaces configured, use those
        if self.namespaces:
            return self.namespaces

        # Otherwise, get all namespaces and filter exclusions
        all_namespaces = self.core_v1.list_namespace()
        namespaces = [
            ns.metadata.name
            for ns in all_namespaces.items
            if ns.metadata.name not in self.exclude_namespaces
        ]
        return namespaces

    def _get_namespace_images(self, namespace: str) -> set[Image]:
        """Get all container images in a specific namespace."""
        images = set()

        try:
            # Get all pods in namespace
            pods = self.core_v1.list_namespaced_pod(namespace)

            # Extract container images
            for pod in pods.items:
                # Regular containers
                if pod.spec.containers:
                    for container in pod.spec.containers:
                        if container.image:
                            images.add(Image(container.image))

                # Init containers
                if pod.spec.init_containers:
                    for container in pod.spec.init_containers:
                        if container.image:
                            images.add(Image(container.image))

        except ApiException as e:
            logger.info(f"Failed to get pods in namespace {namespace}: {e}")
        return images
