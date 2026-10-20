"""Tests for registry_client module."""

import json
import pytest
import requests
from unittest.mock import Mock, patch, mock_open
from datetime import datetime, timezone, timedelta
from oci.exceptions import ServiceError
from ocir_cleanup.registry_client import RegistryClient, CleanupRecommendation
from oke_scanner_core.image import Image


class TestRegistryClient:
    """Tests for RegistryClient class."""

    @pytest.fixture
    def config(self, base_config):
        """Use the shared base_config fixture."""
        return base_config

    @patch('ocir_cleanup.registry_client.oci')
    def test_init_with_oci_sdk(self, mock_oci, config):
        """Test RegistryClient initialization with OCI SDK."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_artifacts_client = Mock()
        mock_identity_client = Mock()
        mock_object_client = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts_client
        mock_oci.identity.IdentityClient.return_value = mock_identity_client
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)

        assert client.artifacts_client == mock_artifacts_client
        assert client.identity_client == mock_identity_client
        assert client.object_client == mock_object_client
        assert client.oci_config == mock_config
        mock_oci.config.from_file.assert_called_once()

    @patch('ocir_cleanup.registry_client.oci')
    def test_init_as_resource_principal(self, mock_oci, config, monkeypatch):
        """With the rpst sidecar's env set, every client is built with the signer, not the config file."""
        monkeypatch.setenv('OCI_RESOURCE_PRINCIPAL_VERSION', '2.2')
        signer = Mock(region='us-ashburn-1', tenancy_id='ocid1.tenancy.rp')
        mock_oci.auth.signers.get_resource_principals_signer.return_value = signer

        client = RegistryClient(config)

        assert client.oci_config == {'region': 'us-ashburn-1', 'tenancy': 'ocid1.tenancy.rp'}
        mock_oci.config.from_file.assert_not_called()
        for builder in (mock_oci.artifacts.ArtifactsClient, mock_oci.identity.IdentityClient,
                        mock_oci.object_storage.ObjectStorageClient):
            builder.assert_called_once_with(client.oci_config, signer=signer)
        assert client.oci_registry == 'iad.ocir.io'

    @patch('ocir_cleanup.registry_client.oci')
    def test_init_resource_principal_failure_leaves_clients_unset(self, mock_oci, config, monkeypatch):
        """A signer that cannot be built degrades like a missing config file."""
        monkeypatch.setenv('OCI_RESOURCE_PRINCIPAL_VERSION', '2.2')
        mock_oci.auth.signers.get_resource_principals_signer.side_effect = Exception('no rpst')

        client = RegistryClient(config)

        assert client.artifacts_client is None
        assert client.oci_config is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_list_all_compartments_uses_configured_ids(self, mock_oci, config):
        """A configured compartment list skips the Identity search (and its per-compartment 404s)."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        config.ocir_compartment_ids = ['ocid1.compartment.apps']

        client = RegistryClient(config)

        assert client._list_all_compartments() == ['ocid1.compartment.apps']
        client.identity_client.list_compartments.assert_not_called()

    @patch('ocir_cleanup.registry_client.oci')
    def test_init_without_oci_sdk(self, mock_oci, config):
        """Test RegistryClient initialization when OCI SDK fails."""
        mock_oci.config.from_file.side_effect = Exception("No config file")

        client = RegistryClient(config)

        assert client.artifacts_client is None
        assert client.identity_client is None
        assert client.object_client is None
        assert client.oci_config is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_namespace_property_fetches_from_object_storage(self, mock_oci, config):
        """Test oci_namespace property fetches namespace from Object Storage API."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        namespace = client.oci_namespace

        assert namespace == 'testnamespace'
        mock_object_client.get_namespace.assert_called_once()

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_namespace_property_caches_result(self, mock_oci, config):
        """Test oci_namespace property caches the result after first fetch."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)

        # First call
        namespace1 = client.oci_namespace
        # Second call
        namespace2 = client.oci_namespace

        assert namespace1 == 'testnamespace'
        assert namespace2 == 'testnamespace'
        # Should only call API once
        assert mock_object_client.get_namespace.call_count == 1

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_namespace_property_returns_none_without_client(self, mock_oci, config):
        """Test oci_namespace property returns None when object client unavailable."""
        mock_oci.config.from_file.side_effect = Exception("No config")

        client = RegistryClient(config)
        namespace = client.oci_namespace

        assert namespace is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_registry_property_caches_result(self, mock_oci, config):
        """Test oci_registry property caches the result after first derivation."""
        mock_config = {'tenancy': 'ocid1.tenancy.test', 'region': 'us-ashburn-1'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        # Pre-populate cache
        client._oci_registry = 'iad.ocir.io'

        # First call
        registry1 = client.oci_registry
        # Second call
        registry2 = client.oci_registry

        assert registry1 == 'iad.ocir.io'
        assert registry2 == 'iad.ocir.io'

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_registry_property_returns_none_without_config(self, mock_oci, config):
        """Test oci_registry property returns None when OCI config unavailable."""
        mock_oci.config.from_file.side_effect = Exception("No config")

        client = RegistryClient(config)
        registry = client.oci_registry

        assert registry is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_strip_namespace_prefix_with_namespace(self, mock_oci, config):
        """Test _strip_namespace_prefix strips namespace prefix."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)

        # Should strip namespace prefix
        assert client._strip_namespace_prefix('testnamespace/myapp') == 'myapp'
        assert client._strip_namespace_prefix('testnamespace/my-app') == 'my-app'

    @patch('ocir_cleanup.registry_client.oci')
    def test_strip_namespace_prefix_without_namespace(self, mock_oci, config):
        """Test _strip_namespace_prefix leaves repo unchanged if no namespace prefix."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)

        # Should not change if no namespace prefix
        assert client._strip_namespace_prefix('myapp') == 'myapp'
        assert client._strip_namespace_prefix('my-app') == 'my-app'

    @patch('ocir_cleanup.registry_client.oci')
    def test_strip_namespace_prefix_different_namespace(self, mock_oci, config):
        """Test _strip_namespace_prefix leaves repo unchanged if different namespace."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)

        # Should not change if different namespace prefix
        assert client._strip_namespace_prefix('othernamespace/myapp') == 'othernamespace/myapp'

    @patch('ocir_cleanup.registry_client.oci')
    def test_strip_namespace_prefix_fallback_without_namespace(self, mock_oci, config):
        """Test _strip_namespace_prefix fallback when namespace unavailable."""
        mock_oci.config.from_file.side_effect = Exception("No config")

        client = RegistryClient(config)

        # Should fall back to taking last part after '/'
        assert client._strip_namespace_prefix('anyprefix/myapp') == 'myapp'
        assert client._strip_namespace_prefix('myapp') == 'myapp'

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_tenancy_id(self, mock_oci, config):
        """Test _get_tenancy_id method."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        tenancy_id = client._get_tenancy_id()

        assert tenancy_id == 'ocid1.tenancy.test'

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_tenancy_id_no_config(self, mock_oci, config):
        """Test _get_tenancy_id when no config available."""
        mock_oci.config.from_file.side_effect = Exception("No config")

        client = RegistryClient(config)
        tenancy_id = client._get_tenancy_id()

        assert tenancy_id is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_list_all_compartments(self, mock_oci, config):
        """Test _list_all_compartments method."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config

        mock_identity_client = Mock()
        mock_oci.identity.IdentityClient.return_value = mock_identity_client
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        # Mock compartment list response
        mock_compartment1 = Mock()
        mock_compartment1.id = 'ocid1.compartment.1'
        mock_compartment1.lifecycle_state = 'ACTIVE'

        mock_compartment2 = Mock()
        mock_compartment2.id = 'ocid1.compartment.2'
        mock_compartment2.lifecycle_state = 'ACTIVE'

        mock_compartment3 = Mock()
        mock_compartment3.id = 'ocid1.compartment.3'
        mock_compartment3.lifecycle_state = 'DELETED'

        mock_response = Mock()
        mock_response.data = [mock_compartment1, mock_compartment2, mock_compartment3]
        mock_identity_client.list_compartments.return_value = mock_response

        client = RegistryClient(config)
        compartments = client._list_all_compartments()

        # Should include tenancy + 2 active compartments
        assert len(compartments) == 3
        assert 'ocid1.tenancy.test' in compartments
        assert 'ocid1.compartment.1' in compartments
        assert 'ocid1.compartment.2' in compartments
        assert 'ocid1.compartment.3' not in compartments

    @patch('ocir_cleanup.registry_client.oci')
    def test_find_repository_compartment_cached(self, mock_oci, config):
        """Test _find_repository_compartment with cached result."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        # Pre-populate cache
        client._repository_compartment_cache['test-repo'] = 'ocid1.compartment.cached'

        compartment_id = client._find_repository_compartment('test-repo')

        assert compartment_id == 'ocid1.compartment.cached'

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_ocir_images_via_sdk(self, mock_oci, config):
        """Test _get_ocir_images_via_sdk method."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config

        mock_artifacts_client = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts_client
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        # Pre-populate compartment cache
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        # Mock image list response
        mock_image1 = Mock()
        mock_image1.version = 'v1.0.0'
        mock_image1.time_created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_image1.id = 'ocid1.image.1'
        mock_image1.digest = 'sha256:aaa111'

        mock_image2 = Mock()
        mock_image2.version = 'v1.1.0'
        mock_image2.time_created = datetime(2024, 2, 1, tzinfo=timezone.utc)
        mock_image2.id = 'ocid1.image.2'
        mock_image2.digest = 'sha256:bbb222'

        mock_image3 = Mock()
        mock_image3.version = None
        mock_image3.digest = 'sha256:abc123def456'
        mock_image3.time_created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_image3.id = 'ocid1.image.3'

        mock_list_response = Mock()
        mock_list_response.data = [mock_image1, mock_image2, mock_image3]
        mock_oci.pagination.list_call_get_all_results.return_value = mock_list_response

        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0')
        images = client._get_ocir_images_via_sdk(image)

        assert len(images) == 3
        assert images[0].tag == 'v1.0.0'
        assert images[1].tag == 'v1.1.0'
        # Untagged platform manifest uses digest as synthetic tag
        assert 'sha256:abc123def456' in images[2].full_name
        assert images[0].digest == 'sha256:aaa111'
        assert images[1].digest == 'sha256:bbb222'

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_ocir_images_via_sdk_cached(self, mock_oci, config):
        """Test _get_ocir_images_via_sdk with cached data."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_artifacts_client = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts_client
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)

        # Pre-populate cache
        cached_image = Image('test.ocir.io/testnamespace/myapp:cached')
        client._ocir_image_cache['testnamespace/myapp'] = [cached_image]

        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0')
        images = client._get_ocir_images_via_sdk(image)

        assert images == [cached_image]
        # Should not call API when cached
        mock_artifacts_client.list_container_images.assert_not_called()

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_ocir_images_via_sdk_no_client(self, mock_oci, config):
        """Test _get_ocir_images_via_sdk when OCI client unavailable."""
        mock_oci.config.from_file.side_effect = Exception("No config")

        client = RegistryClient(config)
        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0')
        images = client._get_ocir_images_via_sdk(image)

        assert images == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_returns_cleanup_recommendations(self, mock_oci, config):
        """Test get_old_ocir_images returns CleanupRecommendation for old images."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        # Create old githash images (7-character alphanumeric tags)
        now = datetime.now(timezone.utc)
        cached_images = []
        for i in range(10):
            img = Image(
                f'test.ocir.io/testnamespace/myapp:abc{i:04d}',
                ocid=f'ocid1.image.{i}',
                created_at=now - timedelta(days=i),
                digest=f'sha256:abc{i:04d}',
            )
            cached_images.append(img)

        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # Current image in use
        images = [Image('test.ocir.io/testnamespace/myapp:abc0000')]

        recommendations = client.get_old_ocir_images(images, keep_count=5)

        assert len(recommendations) == 1
        rec = recommendations[0]
        assert isinstance(rec, CleanupRecommendation)
        assert rec.repository == 'testnamespace/myapp'
        # Should have 4 images to delete (10 - 1 current - 5 keep = 4)
        assert len(rec.tags_to_delete) == 4

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_includes_all_tag_types(self, mock_oci, config):
        """Test get_old_ocir_images includes semver, githash, and arbitrary tags in cleanup."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:abc1234', ocid='ocid1.image.1', created_at=now - timedelta(days=10), digest='sha256:fixture022'),
            Image('test.ocir.io/testnamespace/myapp:v1.0.0', ocid='ocid1.image.2', created_at=now - timedelta(days=9), digest='sha256:fixture023'),
            Image('test.ocir.io/testnamespace/myapp:my-custom-tag', ocid='ocid1.image.3', created_at=now - timedelta(days=8), digest='sha256:fixture024'),
            Image('test.ocir.io/testnamespace/myapp:dev-build-42', ocid='ocid1.image.4', created_at=now - timedelta(days=7), digest='sha256:fixture025'),
            Image('test.ocir.io/testnamespace/myapp:deployed', ocid='ocid1.image.5', created_at=now - timedelta(days=1), digest='sha256:fixture026'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # 'deployed' is the currently running image
        images = [Image('test.ocir.io/testnamespace/myapp:deployed')]

        recommendations = client.get_old_ocir_images(images, keep_count=1)

        assert len(recommendations) == 1
        rec = recommendations[0]
        deleted_tags = {img.tag for img in rec.tags_to_delete}
        # 5 total - 1 deployed - 1 keep = 3 to delete (the 3 oldest)
        assert len(rec.tags_to_delete) == 3
        assert 'abc1234' in deleted_tags
        assert 'v1.0.0' in deleted_tags
        assert 'my-custom-tag' in deleted_tags
        # The kept image (newest non-deployed) and deployed should not be deleted
        assert 'dev-build-42' not in deleted_tags
        assert 'deployed' not in deleted_tags

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_excludes_latest_and_deployed(self, mock_oci, config):
        """Test get_old_ocir_images always excludes 'latest' tag and currently deployed image."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:latest', ocid='ocid1.image.0', created_at=now, digest='sha256:fixture018'),
            Image('test.ocir.io/testnamespace/myapp:abc1234', ocid='ocid1.image.1', created_at=now - timedelta(days=10), digest='sha256:fixture019'),
            Image('test.ocir.io/testnamespace/myapp:old-tag', ocid='ocid1.image.2', created_at=now - timedelta(days=9), digest='sha256:fixture020'),
            Image('test.ocir.io/testnamespace/myapp:deployed', ocid='ocid1.image.3', created_at=now - timedelta(days=1), digest='sha256:fixture021'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        images = [Image('test.ocir.io/testnamespace/myapp:deployed')]

        recommendations = client.get_old_ocir_images(images, keep_count=0)

        assert len(recommendations) == 1
        rec = recommendations[0]
        deleted_tags = {img.tag for img in rec.tags_to_delete}
        # 'latest' and 'deployed' must never be deleted
        assert 'latest' not in deleted_tags
        assert 'deployed' not in deleted_tags
        # The remaining images should be marked for deletion
        assert 'abc1234' in deleted_tags
        assert 'old-tag' in deleted_tags

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protects_digest_shared_with_kept_tag(self, mock_oci, config):
        """A byte-identical rebuild makes a commit-hash tag share :latest's
        manifest digest. OCIR dates a ContainerImage by when the digest first
        appeared, so the fresh tag sorts 'old' and lands in the delete pool —
        but deleting it by OCID would destroy the shared manifest and break
        :latest. Any tag sharing a kept tag's digest must be protected."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'
        # Single-arch image: the top digest is not a manifest list, so no
        # sub-manifests to enumerate. Avoids a live registry HTTP call.
        client._get_manifest_list_sub_digests = Mock(return_value=set())

        now = datetime.now(timezone.utc)
        shared_digest = 'sha256:bbe58f25651c93c4a743abbb52a070318a9f34168d42f96b94e9f4dc869e9bce'
        cached_images = [
            # :latest and the freshly-rebuilt commit-hash tag resolve to ONE
            # digest and both carry the digest's original (old) creation date.
            Image('test.ocir.io/testnamespace/myapp:latest', ocid='ocid1.image.latest',
                  created_at=now - timedelta(days=30), digest=shared_digest),
            Image('test.ocir.io/testnamespace/myapp:0d19f5ee', ocid='ocid1.image.sha',
                  created_at=now - timedelta(days=30), digest=shared_digest),
            # A genuinely stale, distinct-digest tag that SHOULD still be pruned.
            Image('test.ocir.io/testnamespace/myapp:reallyold', ocid='ocid1.image.stale',
                  created_at=now - timedelta(days=40), digest='sha256:stale'),
        ]
        # Newer, distinct-digest tags that fill the keep window.
        for i in range(5):
            cached_images.append(Image(
                f'test.ocir.io/testnamespace/myapp:new{i:04d}', ocid=f'ocid1.image.new{i}',
                created_at=now - timedelta(days=i), digest=f'sha256:new{i}'))
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # The cleanup scans the repo's :latest tag (CLEANUP_REPO flow).
        images = [Image('test.ocir.io/testnamespace/myapp:latest')]

        recommendations = client.get_old_ocir_images(images, keep_count=5)

        deleted_tags = ({img.tag for img in recommendations[0].tags_to_delete}
                        if recommendations else set())
        # The sha tag sharing :latest's digest must NOT be deleted (would break :latest).
        assert '0d19f5ee' not in deleted_tags
        assert 'latest' not in deleted_tags
        # The distinct-digest stale tag is still pruned — cleanup keeps working.
        assert deleted_tags == {'reallyold'}

    def _mock_oci_clients(self, mock_oci):
        """Wire the OCI SDK mocks every get_old_ocir_images test needs."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_skips_repo_when_a_surviving_tag_has_no_digest(self, mock_oci, config):
        """Protection is digest equality, so a surviving tag with no digest cannot
        be protected — a candidate sharing its manifest would be deleted by OCID
        and break it. Bail on the repo instead of pruning blind. One of the
        unresolved tags is tagless (real OCIR listings contain them) to prove the
        log line doesn't blow up sorting None against str."""
        self._mock_oci_clients(mock_oci)
        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'
        client._get_manifest_list_sub_digests = Mock(return_value=set())

        now = datetime.now(timezone.utc)
        # :latest survives but its digest never resolved.
        no_digest_latest = Image('test.ocir.io/testnamespace/myapp:latest',
                                 ocid='ocid1.image.latest', created_at=now)
        tagless = Image('test.ocir.io/testnamespace/myapp:placeholder',
                        ocid='ocid1.image.tagless', created_at=now)
        tagless.tag = None
        cached_images = [no_digest_latest, tagless]
        for i in range(8):
            cached_images.append(Image(
                f'test.ocir.io/testnamespace/myapp:sha{i:04d}', ocid=f'ocid1.image.{i}',
                created_at=now - timedelta(days=20 - i), digest=f'sha256:sha{i:04d}'))
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        images = [Image('test.ocir.io/testnamespace/myapp:latest')]
        recommendations = client.get_old_ocir_images(images, keep_count=5)

        # Without the guard, 3 of the 8 sha tags would be pruned here.
        assert recommendations == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protects_candidate_with_no_digest(self, mock_oci, config):
        """A candidate whose own digest is unresolved is never deleted: the delete
        is by OCID, so an unidentified manifest is exactly what a shared digest
        looks like from here. Candidates that DID resolve are still pruned."""
        self._mock_oci_clients(mock_oci)
        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'
        client._get_manifest_list_sub_digests = Mock(return_value=set())

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:latest', ocid='ocid1.image.latest',
                  created_at=now, digest='sha256:latestdigest'),
            # Oldest two are candidates; one never resolved a digest.
            Image('test.ocir.io/testnamespace/myapp:nodigest', ocid='ocid1.image.nodigest',
                  created_at=now - timedelta(days=40)),
            Image('test.ocir.io/testnamespace/myapp:resolved', ocid='ocid1.image.resolved',
                  created_at=now - timedelta(days=39), digest='sha256:resolved'),
        ]
        for i in range(5):
            cached_images.append(Image(
                f'test.ocir.io/testnamespace/myapp:new{i:04d}', ocid=f'ocid1.image.new{i}',
                created_at=now - timedelta(days=i), digest=f'sha256:new{i}'))
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        images = [Image('test.ocir.io/testnamespace/myapp:latest')]
        recommendations = client.get_old_ocir_images(images, keep_count=5)

        deleted_tags = {img.tag for img in recommendations[0].tags_to_delete}
        assert 'nodigest' not in deleted_tags
        assert deleted_tags == {'resolved'}

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_prunes_when_nothing_is_protected(self, mock_oci, config):
        """A repo with no :latest and a deployed tag absent from the listing leaves
        the protected set empty. Guarding the filter on a non-empty set used to
        skip it entirely; it now always runs, and resolvable candidates still go."""
        self._mock_oci_clients(mock_oci)
        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'
        client._get_manifest_list_sub_digests = Mock(return_value=set())

        now = datetime.now(timezone.utc)
        cached_images = [
            Image(f'test.ocir.io/testnamespace/myapp:sha{i:04d}', ocid=f'ocid1.image.{i}',
                  created_at=now - timedelta(days=20 - i), digest=f'sha256:sha{i:04d}')
            for i in range(7)
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # Deployed tag is not in the listing, so it resolves to the synthetic
        # fallback: no ocid, no digest, nothing to protect.
        images = [Image('test.ocir.io/testnamespace/myapp:deployed-elsewhere')]
        recommendations = client.get_old_ocir_images(images, keep_count=5)

        deleted_tags = {img.tag for img in recommendations[0].tags_to_delete}
        assert deleted_tags == {'sha0000', 'sha0001'}

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protect_tags_regex(self, mock_oci, base_config):
        """CLEANUP_PROTECT_TAGS_REGEX excludes matching tags from the deletion pool."""
        from dataclasses import replace
        cfg = replace(base_config, cleanup_protect_tags_regex=r'^\d+\.\d+$')
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(cfg)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        # ci-base-images-shaped tags: mutable :3.X channels that should be
        # protected even though they're "old" by created_at, plus several
        # :3.X-<sha> immutables.
        now = datetime.now(timezone.utc)
        cached_images = [
            # Mutable channels — old created_at but must be protected
            Image('test.ocir.io/testnamespace/myapp:3.11', ocid='m1', created_at=now - timedelta(days=100), digest='sha256:fixture012'),
            Image('test.ocir.io/testnamespace/myapp:3.12', ocid='m2', created_at=now - timedelta(days=100), digest='sha256:fixture013'),
            # Immutables — should be eligible for deletion under keep_count
            Image('test.ocir.io/testnamespace/myapp:3.11-aaa', ocid='i1', created_at=now - timedelta(days=10), digest='sha256:fixture014'),
            Image('test.ocir.io/testnamespace/myapp:3.11-bbb', ocid='i2', created_at=now - timedelta(days=9), digest='sha256:fixture015'),
            Image('test.ocir.io/testnamespace/myapp:3.11-ccc', ocid='i3', created_at=now - timedelta(days=8), digest='sha256:fixture016'),
            Image('test.ocir.io/testnamespace/myapp:3.11-ddd', ocid='i4', created_at=now - timedelta(days=1), digest='sha256:fixture017'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images
        images = [Image('test.ocir.io/testnamespace/myapp:latest')]

        recommendations = client.get_old_ocir_images(images, keep_count=2)

        assert len(recommendations) == 1
        deleted_tags = {im.tag for im in recommendations[0].tags_to_delete}
        # Mutable channels are protected by the regex
        assert '3.11' not in deleted_tags
        assert '3.12' not in deleted_tags
        # 4 immutables, keep_count=2 → 2 oldest get deleted
        assert deleted_tags == {'3.11-aaa', '3.11-bbb'}

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protects_digest_shared_with_protected_channel_tag(self, mock_oci, base_config):
        """A byte-identical :3.X-<sha> rebuild shares the mutable :3.X channel
        tag's manifest digest. The channel tag is pulled from the delete pool by
        CLEANUP_PROTECT_TAGS_REGEX, but OCIR dates the immutable by when that
        digest first appeared, so it sorts 'old' and lands in the delete pool.
        Pruning it by OCID would destroy the manifest :3.X still points at
        ('manifest unknown'). Any candidate sharing a surviving tag's digest —
        including a protect-regex channel tag — must be spared."""
        from dataclasses import replace
        cfg = replace(base_config, cleanup_protect_tags_regex=r'^\d+\.\d+$')
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(cfg)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'
        # Single-arch images: no manifest list to enumerate. Avoids a live
        # registry HTTP call for every surviving tag.
        client._get_manifest_list_sub_digests = Mock(return_value=set())

        now = datetime.now(timezone.utc)
        channel_digest = 'sha256:bbe58f25651c93c4a743abbb52a070318a9f34168d42f96b94e9f4dc869e9bce'
        cached_images = [
            # Mutable :3.13 channel and its byte-identical immutable rebuild
            # resolve to ONE digest and share the digest's original (old) date.
            Image('test.ocir.io/testnamespace/myapp:3.13', ocid='ch',
                  created_at=now - timedelta(days=30), digest=channel_digest),
            Image('test.ocir.io/testnamespace/myapp:3.13-oldsha', ocid='oldsha',
                  created_at=now - timedelta(days=30), digest=channel_digest),
            # A genuinely stale, distinct-digest immutable that SHOULD be pruned.
            Image('test.ocir.io/testnamespace/myapp:3.13-stale', ocid='stale',
                  created_at=now - timedelta(days=40), digest='sha256:stale'),
        ]
        # Newer, distinct-digest immutables that fill the keep window.
        for i in range(2):
            cached_images.append(Image(
                f'test.ocir.io/testnamespace/myapp:3.13-new{i}', ocid=f'new{i}',
                created_at=now - timedelta(days=i + 1), digest=f'sha256:new{i}'))
        client._ocir_image_cache['testnamespace/myapp'] = cached_images
        images = [Image('test.ocir.io/testnamespace/myapp:latest')]

        recommendations = client.get_old_ocir_images(images, keep_count=2)

        deleted_tags = ({im.tag for im in recommendations[0].tags_to_delete}
                        if recommendations else set())
        # The immutable sharing :3.13's digest must NOT be deleted.
        assert '3.13-oldsha' not in deleted_tags
        assert '3.13' not in deleted_tags
        # The distinct-digest stale immutable is still pruned — cleanup works.
        assert deleted_tags == {'3.13-stale'}

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_group_by_regex_tagless_image_goes_to_ungrouped(self, mock_oci, base_config):
        """Tagless images (e.g. digest-only manifests) fall into the _ungrouped bucket.

        Real OCIR data occasionally has images without a tag. The grouping
        branch must handle them without throwing.
        """
        from dataclasses import replace
        cfg = replace(
            base_config,
            cleanup_group_by_regex=r'^(\d+\.\d+)',
        )
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(cfg)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:3.11-aaa', ocid='g1', created_at=now - timedelta(days=5), digest='sha256:fixture009'),
            Image('test.ocir.io/testnamespace/myapp:3.11-bbb', ocid='g2', created_at=now - timedelta(days=1), digest='sha256:fixture010'),
        ]
        # Three digest-only siblings — overwrite their .tag after construction
        # so they survive the "must be present" check in __init__ but reach
        # the grouping branch with tag=None.
        for i in range(3):
            tagless = Image(f'test.ocir.io/testnamespace/myapp:placeholder{i}',
                            ocid=f'tl{i}', created_at=now - timedelta(days=40 - i),
                            digest=f'sha256:tagless{i}')
            tagless.tag = None
            cached_images.append(tagless)
        client._ocir_image_cache['testnamespace/myapp'] = cached_images
        images = [Image('test.ocir.io/testnamespace/myapp:latest')]

        recommendations = client.get_old_ocir_images(images, keep_count=1)

        # _ungrouped bucket: 3 tagless, keep 1 → 2 deleted.
        # 3.11 group: 2 tagged, keep 1 → 1 deleted.
        # Total: 3 deletion candidates.
        assert len(recommendations) == 1
        assert len(recommendations[0].tags_to_delete) == 3

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_group_by_regex_no_deletions(self, mock_oci, base_config):
        """When every group is at or under keep_count, no recommendation is emitted."""
        from dataclasses import replace
        cfg = replace(
            base_config,
            cleanup_group_by_regex=r'^(\d+\.\d+)',
        )
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(cfg)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:3.11-aaa', ocid='g1', created_at=now - timedelta(days=2)),
            Image('test.ocir.io/testnamespace/myapp:3.11-bbb', ocid='g2', created_at=now - timedelta(days=1)),
            Image('test.ocir.io/testnamespace/myapp:3.12-aaa', ocid='g3', created_at=now - timedelta(days=2)),
            Image('test.ocir.io/testnamespace/myapp:3.12-bbb', ocid='g4', created_at=now - timedelta(days=1)),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images
        images = [Image('test.ocir.io/testnamespace/myapp:latest')]

        # keep_count=5; both groups have only 2 entries → nothing to delete
        recommendations = client.get_old_ocir_images(images, keep_count=5)

        assert recommendations == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_group_by_regex_isolates_groups(self, mock_oci, base_config):
        """CLEANUP_GROUP_BY_REGEX applies keep_count per capture group.

        Heavy churn in one group must not push another group's tags out of
        the keep window — this is the bug the per-group logic exists to
        prevent.
        """
        from dataclasses import replace
        cfg = replace(
            base_config,
            cleanup_protect_tags_regex=r'^\d+\.\d+$',
            cleanup_group_by_regex=r'^(\d+\.\d+)',
        )
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(cfg)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        # 3.11 had a flurry of recent builds; 3.12 had a single older build.
        # Without per-group logic, keep_count=2 globally would keep the
        # two newest 3.11 tags and prune 3.12-only. The per-group logic
        # must keep 3.12-only because it's the only tag in its group.
        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:3.11', ocid='m1', created_at=now - timedelta(days=1), digest='sha256:fixture001'),
            Image('test.ocir.io/testnamespace/myapp:3.12', ocid='m2', created_at=now - timedelta(days=1), digest='sha256:fixture002'),
            # 3.12: one older build, should be kept (sole occupant of its group)
            Image('test.ocir.io/testnamespace/myapp:3.12-only', ocid='g1', created_at=now - timedelta(days=30), digest='sha256:fixture003'),
            # 3.11: five recent builds, keep_count=2 → keep 2 newest, delete 3 oldest
            Image('test.ocir.io/testnamespace/myapp:3.11-aaa', ocid='g2', created_at=now - timedelta(days=10), digest='sha256:fixture004'),
            Image('test.ocir.io/testnamespace/myapp:3.11-bbb', ocid='g3', created_at=now - timedelta(days=9), digest='sha256:fixture005'),
            Image('test.ocir.io/testnamespace/myapp:3.11-ccc', ocid='g4', created_at=now - timedelta(days=8), digest='sha256:fixture006'),
            Image('test.ocir.io/testnamespace/myapp:3.11-ddd', ocid='g5', created_at=now - timedelta(days=2), digest='sha256:fixture007'),
            Image('test.ocir.io/testnamespace/myapp:3.11-eee', ocid='g6', created_at=now - timedelta(days=1), digest='sha256:fixture008'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images
        images = [Image('test.ocir.io/testnamespace/myapp:latest')]

        recommendations = client.get_old_ocir_images(images, keep_count=2)

        assert len(recommendations) == 1
        deleted_tags = {im.tag for im in recommendations[0].tags_to_delete}
        # 3.12 group: only 1 immutable, under keep_count, not deleted
        assert '3.12-only' not in deleted_tags
        # 3.11 group: 5 immutables, keep 2 newest (ddd, eee), delete 3 oldest
        assert deleted_tags == {'3.11-aaa', '3.11-bbb', '3.11-ccc'}

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_basic(self, mock_oci, config):
        """Test orphan detection: platform manifests not referenced by any normal tag's manifest list are orphans."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        build1_time = now - timedelta(days=5)
        build2_time = now - timedelta(days=10)

        cached_images = [
            # Normal tags
            Image('test.ocir.io/testnamespace/myapp:v2.5.1', ocid='ocid1.image.1', created_at=build1_time, digest='sha256:tag1digest'),
            Image('test.ocir.io/testnamespace/myapp:832992d', ocid='ocid1.image.2', created_at=build1_time, digest='sha256:tag2digest'),
            Image('test.ocir.io/testnamespace/myapp:latest', ocid='ocid1.image.3', created_at=build1_time, digest='sha256:latestdigest'),
            # Platform manifests for build1 (NOT orphans - referenced by normal tags)
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.4', created_at=build1_time, digest='sha256:aaa111'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:bbb222', ocid='ocid1.image.5', created_at=build1_time, digest='sha256:bbb222'),
            # Platform manifests for build2 (ORPHANS - not referenced by any normal tag)
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ccc333', ocid='ocid1.image.6', created_at=build2_time, digest='sha256:ccc333'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ddd444', ocid='ocid1.image.7', created_at=build2_time, digest='sha256:ddd444'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # Mock manifest list resolution: normal tags reference aaa111 and bbb222
        def mock_sub_digests(image):
            if image.digest in ('sha256:tag1digest', 'sha256:tag2digest', 'sha256:latestdigest'):
                return {'sha256:aaa111', 'sha256:bbb222'}
            return set()

        with patch.object(client, '_get_manifest_list_sub_digests', side_effect=mock_sub_digests):
            images = {Image('test.ocir.io/testnamespace/myapp:v2.5.1')}
            recommendations = client.get_orphaned_manifests(images)

        assert len(recommendations) == 1
        rec = recommendations[0]
        assert rec.repository == 'testnamespace/myapp'
        assert len(rec.tags_to_delete) == 2
        orphan_ocids = {img.ocid for img in rec.tags_to_delete}
        assert 'ocid1.image.6' in orphan_ocids
        assert 'ocid1.image.7' in orphan_ocids

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_no_orphans(self, mock_oci, config):
        """Test no orphans when all platform manifests are referenced by normal tags."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:v1.0.0', ocid='ocid1.image.1', created_at=now, digest='sha256:tagdigest'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.2', created_at=now, digest='sha256:aaa111'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:bbb222', ocid='ocid1.image.3', created_at=now, digest='sha256:bbb222'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        with patch.object(client, '_get_manifest_list_sub_digests', return_value={'sha256:aaa111', 'sha256:bbb222'}):
            images = {Image('test.ocir.io/testnamespace/myapp:v1.0.0')}
            recommendations = client.get_orphaned_manifests(images)

        assert len(recommendations) == 0

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_all_orphans(self, mock_oci, config):
        """Test all platform manifests are orphans when no normal tags reference them."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        old_time = datetime.now(timezone.utc) - timedelta(days=30)
        cached_images = [
            # A normal tag that doesn't reference any of the platform manifests
            Image('test.ocir.io/testnamespace/myapp:latest', ocid='ocid1.image.0', created_at=old_time, digest='sha256:latestdigest'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.1', created_at=old_time, digest='sha256:aaa111'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:bbb222', ocid='ocid1.image.2', created_at=old_time, digest='sha256:bbb222'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ccc333', ocid='ocid1.image.3', created_at=old_time + timedelta(days=1), digest='sha256:ccc333'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # latest tag references different sub-manifests (not aaa111/bbb222/ccc333)
        with patch.object(client, '_get_manifest_list_sub_digests', return_value={'sha256:other1', 'sha256:other2'}):
            images = {Image('test.ocir.io/testnamespace/myapp:latest')}
            recommendations = client.get_orphaned_manifests(images)

        assert len(recommendations) == 1
        assert len(recommendations[0].tags_to_delete) == 3

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_latest_protected(self, mock_oci, config):
        """Test platform manifests referenced by latest's manifest list are NOT orphans."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=30)

        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:latest', ocid='ocid1.image.1', created_at=now, digest='sha256:latestdigest'),
            # Platform manifests referenced by latest -> NOT orphans
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.2', created_at=now, digest='sha256:aaa111'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:bbb222', ocid='ocid1.image.3', created_at=now, digest='sha256:bbb222'),
            # Platform manifest NOT referenced by any tag -> orphan
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ccc333', ocid='ocid1.image.4', created_at=old_time, digest='sha256:ccc333'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # latest references aaa111 and bbb222 but not ccc333
        with patch.object(client, '_get_manifest_list_sub_digests', return_value={'sha256:aaa111', 'sha256:bbb222'}):
            images = {Image('test.ocir.io/testnamespace/myapp:latest')}
            recommendations = client.get_orphaned_manifests(images)

        assert len(recommendations) == 1
        assert len(recommendations[0].tags_to_delete) == 1
        assert recommendations[0].tags_to_delete[0].ocid == 'ocid1.image.4'

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_deployed_image_protected(self, mock_oci, config):
        """Test platform manifests referenced by deployed image's manifest list are NOT orphans."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        deployed_time = now - timedelta(days=2)
        orphan_time = now - timedelta(days=20)

        cached_images = [
            # Currently deployed githash image
            Image('test.ocir.io/testnamespace/myapp:a1b2c3d', ocid='ocid1.image.1', created_at=deployed_time, digest='sha256:deployeddigest'),
            # Platform manifests referenced by deployed image -> NOT orphans
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.2', created_at=deployed_time, digest='sha256:aaa111'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:bbb222', ocid='ocid1.image.3', created_at=deployed_time, digest='sha256:bbb222'),
            # Orphaned platform manifests from old deleted build
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ccc333', ocid='ocid1.image.4', created_at=orphan_time, digest='sha256:ccc333'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ddd444', ocid='ocid1.image.5', created_at=orphan_time, digest='sha256:ddd444'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # Deployed image references aaa111 and bbb222
        with patch.object(client, '_get_manifest_list_sub_digests', return_value={'sha256:aaa111', 'sha256:bbb222'}):
            images = {Image('test.ocir.io/testnamespace/myapp:a1b2c3d')}
            recommendations = client.get_orphaned_manifests(images)

        assert len(recommendations) == 1
        assert len(recommendations[0].tags_to_delete) == 2
        orphan_ocids = {img.ocid for img in recommendations[0].tags_to_delete}
        assert 'ocid1.image.4' in orphan_ocids
        assert 'ocid1.image.5' in orphan_ocids

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_skips_when_no_digests_resolved(self, mock_oci, config):
        """Test orphan detection skips repo when manifest list resolution fails entirely."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:v1.0.0', ocid='ocid1.image.1', created_at=now, digest='sha256:tagdigest'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.2', created_at=now, digest='sha256:aaa111'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # Simulate manifest list resolution failure (returns empty set)
        with patch.object(client, '_get_manifest_list_sub_digests', return_value=set()):
            images = {Image('test.ocir.io/testnamespace/myapp:v1.0.0')}
            recommendations = client.get_orphaned_manifests(images)

        # Should skip - not delete anything when we can't resolve manifests
        assert len(recommendations) == 0

    @patch('ocir_cleanup.registry_client.oci')
    def test_delete_ocir_images(self, mock_oci, config):
        """Test delete_ocir_images deletes images via SDK."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config

        mock_artifacts_client = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts_client
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)

        # Create cleanup recommendations
        img1 = Image('test.ocir.io/testnamespace/myapp:old1', ocid='ocid1.image.1')
        img2 = Image('test.ocir.io/testnamespace/myapp:old2', ocid='ocid1.image.2')
        recommendations = [
            CleanupRecommendation(
                registry='test.ocir.io',
                repository='testnamespace/myapp',
                tags_to_delete=[img1, img2]
            )
        ]

        deleted = client.delete_ocir_images(recommendations)

        assert len(deleted) == 2
        assert mock_artifacts_client.delete_container_image.call_count == 2
        mock_artifacts_client.delete_container_image.assert_any_call('ocid1.image.1')
        mock_artifacts_client.delete_container_image.assert_any_call('ocid1.image.2')

    @patch('ocir_cleanup.registry_client.oci')
    def test_delete_ocir_images_skips_already_deleted(self, mock_oci, config):
        """Test delete_ocir_images handles 404 for already-deleted images."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config

        mock_artifacts_client = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts_client
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        # Second delete raises 404 (same underlying image already deleted)
        not_found_error = mock_oci.exceptions.ServiceError(
            status=404, code='REPO_ID_UNKNOWN', headers={}, message='Image Id Unknown'
        )
        mock_artifacts_client.delete_container_image.side_effect = [
            None,  # first delete succeeds
            not_found_error,  # second delete 404s
        ]

        client = RegistryClient(config)

        img1 = Image('test.ocir.io/testnamespace/myapp:old1', ocid='ocid1.image.1')
        img2 = Image('test.ocir.io/testnamespace/myapp:old2', ocid='ocid1.image.1')  # same OCID
        recommendations = [
            CleanupRecommendation(
                registry='test.ocir.io',
                repository='testnamespace/myapp',
                tags_to_delete=[img1, img2]
            )
        ]

        deleted = client.delete_ocir_images(recommendations)

        assert len(deleted) == 2
        assert mock_artifacts_client.delete_container_image.call_count == 2

    @patch('ocir_cleanup.registry_client.oci')
    def test_delete_ocir_images_no_client(self, mock_oci, config):
        """Test delete_ocir_images returns empty when no SDK client."""
        mock_oci.config.from_file.side_effect = Exception("No config")

        client = RegistryClient(config)
        recommendations = []

        result = client.delete_ocir_images(recommendations)

        assert result == []

    @patch('ocir_cleanup.registry_client.requests.get')
    @patch('builtins.open', new_callable=mock_open,
           read_data=json.dumps({'auths': {'iad.ocir.io': {'auth': 'dXNlcjpwYXNz'}}}))
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_docker_auth_basic_accepted(self, mock_oci, mock_file, mock_get, config):
        """Test _get_docker_auth returns Basic header when registry accepts it directly."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        # Registry accepts Basic auth directly (200)
        mock_v2_resp = Mock()
        mock_v2_resp.status_code = 200
        mock_get.return_value = mock_v2_resp

        client = RegistryClient(config)
        image = Image('iad.ocir.io/tnoff/myapp:v1.0.0')
        result = client._get_docker_auth(image)

        assert result == {'Authorization': 'Basic dXNlcjpwYXNz'}

    @patch('ocir_cleanup.registry_client.requests.get')
    @patch('builtins.open', new_callable=mock_open,
           read_data=json.dumps({'auths': {'iad.ocir.io': {'auth': 'dXNlcjpwYXNz'}}}))
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_docker_auth_token_exchange(self, mock_oci, mock_file, mock_get, config):
        """Test _get_docker_auth does token exchange when registry requires Bearer auth."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        # Registry returns 401 with WWW-Authenticate challenge
        mock_v2_resp = Mock()
        mock_v2_resp.status_code = 401
        mock_v2_resp.headers = {
            'WWW-Authenticate': 'Bearer realm="https://iad.ocir.io/20180419/docker/token",service="iad.ocir.io",scope=""'
        }

        # Token endpoint returns a token
        mock_token_resp = Mock()
        mock_token_resp.ok = True
        mock_token_resp.json.return_value = {'token': 'my-bearer-token'}

        mock_get.side_effect = [mock_v2_resp, mock_token_resp]

        client = RegistryClient(config)
        image = Image('iad.ocir.io/tnoff/myapp:v1.0.0')
        result = client._get_docker_auth(image)

        assert result == {'Authorization': 'Bearer my-bearer-token'}
        # Verify token request included correct scope
        token_call = mock_get.call_args_list[1]
        assert token_call[1]['params']['scope'] == 'repository:tnoff/myapp:pull'

    @patch('builtins.open', side_effect=FileNotFoundError)
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_docker_auth_missing_config(self, mock_oci, mock_file, config):
        """Test _get_docker_auth returns None when config file is missing."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        image = Image('iad.ocir.io/tnoff/myapp:v1.0.0')
        result = client._get_docker_auth(image)

        assert result is None

    @patch('builtins.open', new_callable=mock_open,
           read_data=json.dumps({'auths': {'other.registry.io': {'auth': 'dXNlcjpwYXNz'}}}))
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_docker_auth_no_entry_for_registry(self, mock_oci, mock_file, config):
        """Test _get_docker_auth returns None when no entry for the registry."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        image = Image('iad.ocir.io/tnoff/myapp:v1.0.0')
        result = client._get_docker_auth(image)

        assert result is None

    @patch('ocir_cleanup.registry_client.requests.get')
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_manifest_list_sub_digests_manifest_list(self, mock_oci, mock_get, config):
        """Test _get_manifest_list_sub_digests returns sub-digests for manifest lists."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)

        manifest_list_response = {
            'mediaType': 'application/vnd.docker.distribution.manifest.list.v2+json',
            'manifests': [
                {'digest': 'sha256:sub1', 'platform': {'architecture': 'amd64'}},
                {'digest': 'sha256:sub2', 'platform': {'architecture': 'arm64'}},
            ]
        }
        mock_resp = Mock()
        mock_resp.json.return_value = manifest_list_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp

        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0', digest='sha256:manifest_list')
        # Mock _get_docker_auth to return auth
        client._get_docker_auth = Mock(return_value={'Authorization': 'Basic dXNlcjpwYXNz'})

        result = client._get_manifest_list_sub_digests(image)

        assert result == {'sha256:sub1', 'sha256:sub2'}

    @patch('ocir_cleanup.registry_client.requests.get')
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_manifest_list_sub_digests_non_list(self, mock_oci, mock_get, config):
        """Test _get_manifest_list_sub_digests returns empty set for non-list manifests."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)

        single_manifest_response = {
            'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
            'config': {'digest': 'sha256:config123'},
        }
        mock_resp = Mock()
        mock_resp.json.return_value = single_manifest_response
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp

        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0', digest='sha256:single')
        client._get_docker_auth = Mock(return_value={'Authorization': 'Basic dXNlcjpwYXNz'})

        result = client._get_manifest_list_sub_digests(image)

        assert result == set()

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_manifest_list_sub_digests_no_digest(self, mock_oci, config):
        """Test _get_manifest_list_sub_digests returns empty set when image has no digest."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)

        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0')
        result = client._get_manifest_list_sub_digests(image)

        assert result == set()

    @patch('ocir_cleanup.registry_client.requests.get')
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_manifest_list_sub_digests_api_error(self, mock_oci, mock_get, config):
        """Test _get_manifest_list_sub_digests returns empty set on API error."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        mock_get.side_effect = requests.exceptions.ConnectionError("connection refused")

        image = Image('test.ocir.io/testnamespace/myapp:v1.0.0', digest='sha256:abc')
        client._get_docker_auth = Mock(return_value={'Authorization': 'Basic dXNlcjpwYXNz'})

        result = client._get_manifest_list_sub_digests(image)

        assert result == set()

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protects_sub_manifest_digests(self, mock_oci, config):
        """Test get_old_ocir_images protects sub-manifest digests from deletion."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:deployed', ocid='ocid1.image.0',
                  created_at=now, digest='sha256:deployed_digest'),
            Image('test.ocir.io/testnamespace/myapp:old1', ocid='ocid1.image.1',
                  created_at=now - timedelta(days=10), digest='sha256:sub1'),
            Image('test.ocir.io/testnamespace/myapp:old2', ocid='ocid1.image.2',
                  created_at=now - timedelta(days=9), digest='sha256:unrelated'),
            Image('test.ocir.io/testnamespace/myapp:old3', ocid='ocid1.image.3',
                  created_at=now - timedelta(days=8), digest='sha256:sub2'),
            Image('test.ocir.io/testnamespace/myapp:kept', ocid='ocid1.image.4',
                  created_at=now - timedelta(days=1), digest='sha256:kept_digest'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # Mock _get_manifest_list_sub_digests: deployed image is a manifest list
        def mock_sub_digests(image):
            if image.digest == 'sha256:deployed_digest':
                return {'sha256:sub1', 'sha256:sub2'}
            return set()
        client._get_manifest_list_sub_digests = Mock(side_effect=mock_sub_digests)

        images = [Image('test.ocir.io/testnamespace/myapp:deployed')]
        recommendations = client.get_old_ocir_images(images, keep_count=1)

        assert len(recommendations) == 1
        rec = recommendations[0]
        deleted_tags = {img.tag for img in rec.tags_to_delete}
        # old1 (sha256:sub1) and old3 (sha256:sub2) are sub-manifests and should be protected
        assert 'old1' not in deleted_tags
        assert 'old3' not in deleted_tags
        # old2 (sha256:unrelated) should still be deleted
        assert 'old2' in deleted_tags

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_no_manifest_lists_no_regression(self, mock_oci, config):
        """Test get_old_ocir_images behaves unchanged when no manifest lists exist."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = []
        for i in range(8):
            img = Image(
                f'test.ocir.io/testnamespace/myapp:tag{i:02d}',
                ocid=f'ocid1.image.{i}',
                created_at=now - timedelta(days=i),
                digest=f'sha256:digest{i}',
            )
            cached_images.append(img)
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        # No manifest lists — _get_manifest_list_sub_digests always returns empty
        client._get_manifest_list_sub_digests = Mock(return_value=set())

        images = [Image('test.ocir.io/testnamespace/myapp:tag00')]
        recommendations = client.get_old_ocir_images(images, keep_count=3)

        assert len(recommendations) == 1
        rec = recommendations[0]
        # 8 total - 1 deployed - 3 kept = 4 to delete
        assert len(rec.tags_to_delete) == 4

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_excludes_platform_manifests(self, mock_oci, config):
        """Test get_old_ocir_images ignores platform manifests (unknown@sha256:...) entirely.

        Platform manifests should only be handled by get_orphaned_manifests().
        Previously, platform manifests were mixed into the keep_count logic, which
        could cause a kept tag's amd64 manifest to be deleted while the tag survived,
        resulting in 'no matching manifest for linux/amd64' errors on pull.
        """
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)
        cached_images = [
            Image('test.ocir.io/testnamespace/myapp:deployed', ocid='ocid1.image.0',
                  created_at=now, digest='sha256:deployed_digest'),
            Image('test.ocir.io/testnamespace/myapp:tag1', ocid='ocid1.image.1',
                  created_at=now - timedelta(days=5), digest='sha256:tag1_digest'),
            Image('test.ocir.io/testnamespace/myapp:tag2', ocid='ocid1.image.2',
                  created_at=now - timedelta(days=10), digest='sha256:tag2_digest'),
            # Platform manifests should NOT be included in keep_count or deletion
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:aaa111', ocid='ocid1.image.10',
                  created_at=now - timedelta(days=1), digest='sha256:aaa111'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:bbb222', ocid='ocid1.image.11',
                  created_at=now - timedelta(days=1), digest='sha256:bbb222'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ccc333', ocid='ocid1.image.12',
                  created_at=now - timedelta(days=5), digest='sha256:ccc333'),
            Image('test.ocir.io/testnamespace/myapp:unknown@sha256:ddd444', ocid='ocid1.image.13',
                  created_at=now - timedelta(days=5), digest='sha256:ddd444'),
        ]
        client._ocir_image_cache['testnamespace/myapp'] = cached_images

        client._get_manifest_list_sub_digests = Mock(return_value=set())

        images = [Image('test.ocir.io/testnamespace/myapp:deployed')]
        # keep_count=1 means keep deployed + 1 newest tag = tag1 kept, tag2 deleted
        recommendations = client.get_old_ocir_images(images, keep_count=1)

        assert len(recommendations) == 1
        rec = recommendations[0]
        deleted_tags = {img.tag for img in rec.tags_to_delete}
        # Only tag2 should be deleted — platform manifests must NOT appear
        assert deleted_tags == {'tag2'}
        # Verify no platform manifest was included
        for img in rec.tags_to_delete:
            assert 'unknown@sha256:' not in img.full_name

    @patch('ocir_cleanup.registry_client.oci')
    def test_delete_ocir_images_invalidates_cache(self, mock_oci, config):
        """Test that delete_ocir_images invalidates the cache for modified repos."""
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config

        mock_artifacts = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        # Pre-populate cache
        client._ocir_image_cache['testnamespace/myapp'] = [Image('test.ocir.io/testnamespace/myapp:old')]
        client._ocir_image_cache['testnamespace/other'] = [Image('test.ocir.io/testnamespace/other:old')]

        image_to_delete = Image('test.ocir.io/testnamespace/myapp:old', ocid='ocid1.image.1')
        recommendation = CleanupRecommendation(
            registry='test.ocir.io',
            repository='testnamespace/myapp',
            tags_to_delete=[image_to_delete],
        )

        client.delete_ocir_images([recommendation])

        # Cache for the modified repo should be invalidated
        assert 'testnamespace/myapp' not in client._ocir_image_cache
        # Cache for other repos should remain
        assert 'testnamespace/other' in client._ocir_image_cache

    @patch('ocir_cleanup.registry_client.oci')
    def test_old_image_cleanup_then_orphan_detection_deletes_intermediate_tags(self, mock_oci, config):
        """End-to-end: after old tags are deleted, orphan detection finds and removes
        their platform manifests while keeping platform manifests of surviving tags.

        Scenario: 7 tagged images with 2 platform manifests each (amd64 + arm64).
        keep_count=5, so the 2 oldest tags are deleted. Their 4 platform manifests
        should then be detected as orphans, while the 10 platform manifests belonging
        to the 5 kept tags + deployed remain referenced.
        """
        mock_config = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.config.from_file.return_value = mock_config

        mock_artifacts = Mock()
        mock_oci.artifacts.ArtifactsClient.return_value = mock_artifacts
        mock_oci.identity.IdentityClient.return_value = Mock()

        mock_object_client = Mock()
        mock_response = Mock()
        mock_response.data = 'testnamespace'
        mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        client._repository_compartment_cache['myapp'] = 'ocid1.compartment.apps'

        now = datetime.now(timezone.utc)

        # Build the full image list: 1 deployed + 7 other tags, each with 2 platform manifests
        # Tag layout (newest first):
        #   deployed (day 0)  -> plat sha256:deployed_amd64, sha256:deployed_arm64
        #   tag6 (day 1)      -> plat sha256:tag6_amd64, sha256:tag6_arm64
        #   tag5 (day 2)      -> plat sha256:tag5_amd64, sha256:tag5_arm64
        #   tag4 (day 3)      -> plat sha256:tag4_amd64, sha256:tag4_arm64
        #   tag3 (day 4)      -> plat sha256:tag3_amd64, sha256:tag3_arm64
        #   tag2 (day 5)      -> plat sha256:tag2_amd64, sha256:tag2_arm64
        #   --- keep_count=5 boundary (tag2-tag6 kept) ---
        #   tag1 (day 6)      -> plat sha256:tag1_amd64, sha256:tag1_arm64  <-- DELETE
        #   tag0 (day 7)      -> plat sha256:tag0_amd64, sha256:tag0_arm64  <-- DELETE

        all_images = []
        tag_names = ['deployed'] + [f'tag{i}' for i in range(7)]
        for idx, tag_name in enumerate(tag_names):
            day_offset = idx  # deployed=0, tag6=1, ..., tag0=7
            # Reverse so tag6 is newest non-deployed
            if tag_name != 'deployed':
                day_offset = 8 - int(tag_name.replace('tag', ''))
            else:
                day_offset = 0
            created = now - timedelta(days=day_offset)
            digest = f'sha256:{tag_name}_digest'

            all_images.append(Image(
                f'test.ocir.io/testnamespace/myapp:{tag_name}',
                ocid=f'ocid1.image.{tag_name}',
                created_at=created,
                digest=digest,
            ))
            # Two platform manifests per tag
            for arch in ['amd64', 'arm64']:
                plat_digest = f'sha256:{tag_name}_{arch}'
                all_images.append(Image(
                    f'test.ocir.io/testnamespace/myapp:unknown@{plat_digest}',
                    ocid=f'ocid1.image.{tag_name}_{arch}',
                    created_at=created,
                    digest=plat_digest,
                ))

        # Manifest list sub-digest mapping: each tag references its two platform digests
        manifest_map = {}
        for tag_name in tag_names:
            manifest_map[f'sha256:{tag_name}_digest'] = {
                f'sha256:{tag_name}_amd64', f'sha256:{tag_name}_arm64',
            }

        def mock_sub_digests(image):
            return manifest_map.get(image.digest, set())
        client._get_manifest_list_sub_digests = Mock(side_effect=mock_sub_digests)

        # --- Step 1: get_old_ocir_images ---
        client._ocir_image_cache['testnamespace/myapp'] = list(all_images)
        images = {Image('test.ocir.io/testnamespace/myapp:deployed')}
        old_recommendations = client.get_old_ocir_images(images, keep_count=5)

        assert len(old_recommendations) == 1
        old_rec = old_recommendations[0]
        old_deleted_tags = {img.tag for img in old_rec.tags_to_delete}
        # Only the 2 oldest normal tags should be marked for deletion
        assert old_deleted_tags == {'tag0', 'tag1'}
        # No platform manifests should appear
        for img in old_rec.tags_to_delete:
            assert 'unknown@sha256:' not in img.full_name

        # --- Step 2: simulate deletion + cache invalidation ---
        client.delete_ocir_images(old_recommendations)
        assert 'testnamespace/myapp' not in client._ocir_image_cache

        # --- Step 3: get_orphaned_manifests with fresh data (tags deleted) ---
        # Simulate what OCIR returns after the old tags were deleted:
        # tag0 and tag1 are gone, their platform manifests remain as orphans
        post_deletion_images = [im for im in all_images
                                if im.tag not in ('tag0', 'tag1')]
        client._ocir_image_cache['testnamespace/myapp'] = post_deletion_images

        orphan_recommendations = client.get_orphaned_manifests(
            {Image('test.ocir.io/testnamespace/myapp:deployed')},
        )

        assert len(orphan_recommendations) == 1
        orphan_rec = orphan_recommendations[0]
        orphan_digests = {img.digest for img in orphan_rec.tags_to_delete}
        # The 4 platform manifests from tag0 and tag1 should be orphans
        assert orphan_digests == {
            'sha256:tag0_amd64', 'sha256:tag0_arm64',
            'sha256:tag1_amd64', 'sha256:tag1_arm64',
        }
        # Platform manifests for kept tags must NOT be in orphans
        for kept_tag in ['deployed', 'tag2', 'tag3', 'tag4', 'tag5', 'tag6']:
            assert f'sha256:{kept_tag}_amd64' not in orphan_digests
            assert f'sha256:{kept_tag}_arm64' not in orphan_digests


class TestRegistryClientCoverage:
    """Targeted tests filling in the remaining coverage gaps."""

    @pytest.fixture
    def config(self, base_config):
        return base_config

    def _make_client(self, mock_oci, *, with_namespace=True):
        """Build a RegistryClient with the common happy-path mocks."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        if with_namespace:
            mock_response = Mock()
            mock_response.data = 'testnamespace'
            mock_object_client.get_namespace.return_value = mock_response
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client
        return mock_object_client

    # --- oci_namespace / oci_registry error paths ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_namespace_returns_none_when_object_storage_raises(self, mock_oci, config):
        """oci_namespace returns None and logs when get_namespace() raises."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_object_client = Mock()
        mock_object_client.get_namespace.side_effect = Exception("API down")
        mock_oci.object_storage.ObjectStorageClient.return_value = mock_object_client

        client = RegistryClient(config)
        assert client.oci_namespace is None

    @patch('ocir_cleanup.registry_client.REGIONS_SHORT_NAMES', {'iad': 'us-ashburn-1'})
    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_registry_returns_none_when_region_not_found(self, mock_oci, config):
        """oci_registry returns None when the configured region has no short-name mapping."""
        mock_oci.config.from_file.return_value = {
            'tenancy': 'ocid1.tenancy.test',
            'region': 'mars-central-1',  # not in REGIONS_SHORT_NAMES
        }
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        assert client.oci_registry is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_oci_registry_returns_none_when_region_lookup_raises(self, mock_oci, config):
        """oci_registry returns None when something unexpected raises during region lookup."""
        mock_oci.config.from_file.return_value = {'tenancy': 'ocid1.tenancy.test', 'region': 'us-ashburn-1'}
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()

        client = RegistryClient(config)
        # Sabotage the config dict so .get('region') raises; clear any cached value
        broken = Mock()
        broken.get.side_effect = RuntimeError("boom")
        client.oci_config = broken
        client._oci_registry = None
        assert client.oci_registry is None

    # --- _list_all_compartments early-return paths ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_list_all_compartments_returns_empty_without_identity_client(self, mock_oci, config):
        mock_oci.config.from_file.side_effect = Exception("no config")
        client = RegistryClient(config)
        assert client._list_all_compartments() == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_list_all_compartments_returns_empty_without_tenancy_id(self, mock_oci, config):
        mock_oci.config.from_file.return_value = {}  # no 'tenancy' key
        mock_oci.artifacts.ArtifactsClient.return_value = Mock()
        mock_oci.identity.IdentityClient.return_value = Mock()
        mock_oci.object_storage.ObjectStorageClient.return_value = Mock()
        client = RegistryClient(config)
        assert client._list_all_compartments() == []

    # --- _find_repository_compartment search paths ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_find_repository_compartment_returns_none_without_artifacts_client(self, mock_oci, config):
        mock_oci.config.from_file.side_effect = Exception("no config")
        client = RegistryClient(config)
        assert client._find_repository_compartment('repo') is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_find_repository_compartment_finds_repo_after_404s(self, mock_oci, config):
        """_find_repository_compartment skips 404 errors and finds the repo in a later compartment."""
        mock_oci.exceptions.ServiceError = ServiceError
        self._make_client(mock_oci)

        client = RegistryClient(config)
        # Two compartments: tenancy root + one more
        compartment_response = Mock()
        comp = Mock()
        comp.id = 'ocid1.compartment.apps'
        comp.lifecycle_state = 'ACTIVE'
        compartment_response.data = [comp]
        client.identity_client.list_compartments.return_value = compartment_response

        # First compartment 404s, second has the repo, third would be a non-404 error
        # (but we won't reach it since second hits)
        not_found = ServiceError(status=404, code='NF', headers={}, message='not found')
        hit_response = Mock()
        hit_response.data.items = [Mock()]  # truthy
        client.artifacts_client.list_container_images.side_effect = [not_found, hit_response]

        compartment_id = client._find_repository_compartment('repo')

        assert compartment_id == 'ocid1.compartment.apps'
        # Second call to find_repo for same repo hits the cache (no new API call)
        assert client._find_repository_compartment('repo') == 'ocid1.compartment.apps'

    @patch('ocir_cleanup.registry_client.oci')
    def test_find_repository_compartment_logs_other_service_errors_and_continues(self, mock_oci, config):
        """Non-404 ServiceError is logged and the search continues."""
        mock_oci.exceptions.ServiceError = ServiceError
        self._make_client(mock_oci)

        client = RegistryClient(config)
        client.identity_client.list_compartments.return_value = Mock(data=[])
        # Only the tenancy compartment is searched; non-404 error -> continues -> not found
        client.artifacts_client.list_container_images.side_effect = ServiceError(
            status=500, code='X', headers={}, message='boom'
        )
        assert client._find_repository_compartment('repo') is None

    # --- _get_ocir_images_via_sdk edge cases ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_ocir_images_via_sdk_returns_empty_when_compartment_not_found(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)
        # Force compartment-not-found by short-circuiting the cache to nothing
        # and making compartment search return None
        with patch.object(client, '_find_repository_compartment', return_value=None):
            result = client._get_ocir_images_via_sdk(Image('iad.ocir.io/ns/app:v1'))
        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_ocir_images_via_sdk_skips_items_with_no_version_and_no_digest(self, mock_oci, config):
        """Items missing both version and digest are skipped (rare but defended)."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        # repo_name is 'ns/app' and doesn't start with 'testnamespace/', so _strip_namespace_prefix
        # returns 'ns/app' unchanged — that's what the compartment cache key must be.
        client._repository_compartment_cache['ns/app'] = 'ocid1.compartment.apps'

        # Two items: one with neither version nor digest (skip), one usable
        ghost = Mock(spec=['version', 'time_created', 'id'])
        ghost.version = None
        ghost.time_created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ghost.id = 'ocid1.image.ghost'
        # `hasattr(item, 'digest')` must be False for the skip branch — spec excludes digest

        usable = Mock()
        usable.version = 'v1.0.0'
        usable.time_created = datetime(2024, 2, 1, tzinfo=timezone.utc)
        usable.id = 'ocid1.image.usable'
        usable.digest = 'sha256:abc'

        list_response = Mock()
        list_response.data = [ghost, usable]
        mock_oci.pagination.list_call_get_all_results.return_value = list_response

        result = client._get_ocir_images_via_sdk(Image('iad.ocir.io/ns/app:v1.0.0'))
        # The ghost was skipped; only usable remains
        assert len(result) == 1
        assert result[0].tag == 'v1.0.0'

    # --- _get_docker_auth / _get_manifest_list_sub_digests ---

    @patch('ocir_cleanup.registry_client.requests.get')
    @patch('builtins.open', new_callable=mock_open,
           read_data=json.dumps({'auths': {'iad.ocir.io': {'auth': 'dXNlcjpwYXNz'}}}))
    @patch('ocir_cleanup.registry_client.oci')
    def test_get_docker_auth_returns_none_on_request_exception(self, mock_oci, _mock_file, mock_get, config):
        """When the /v2/ probe raises a RequestException, _get_docker_auth returns None."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        mock_get.side_effect = requests.RequestException("connection refused")

        result = client._get_docker_auth(Image('iad.ocir.io/ns/app:v1'))
        assert result is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_manifest_list_sub_digests_returns_empty_without_auth(self, mock_oci, config):
        """No Docker auth headers -> empty set of sub-digests."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        img = Image('iad.ocir.io/ns/app:v1', digest='sha256:abc')
        with patch.object(client, '_get_docker_auth', return_value=None):
            assert client._get_manifest_list_sub_digests(img) == set()

    # --- get_image_creation_date ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_image_creation_date_returns_cached_for_ocir(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)

        cached_image = Image(
            'iad.ocir.io/ns/app:v1.0.0',
            created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        )
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[cached_image]):
            result = client.get_image_creation_date(Image('iad.ocir.io/ns/app:v1.0.0'))
        assert result == datetime(2024, 6, 1, tzinfo=timezone.utc)

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_image_creation_date_returns_none_for_ocir_without_match(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[]):
            result = client.get_image_creation_date(Image('iad.ocir.io/ns/app:v1.0.0'))
        assert result is None

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_image_creation_date_returns_none_for_non_ocir(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)
        assert client.get_image_creation_date(Image('docker.io/library/nginx:latest')) is None

    # --- get_old_ocir_images skip/early-return branches ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_adds_extra_repositories(self, mock_oci, config):
        """extra_repositories synthesizes ':latest' Image entries into the scan set."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        client._oci_registry = 'iad.ocir.io'

        # _get_ocir_images_via_sdk returns nothing → nothing to recommend, but the
        # extra-repo loop still runs (covering lines 433-434).
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[]):
            result = client.get_old_ocir_images(set(), extra_repositories=['ns/extra'])
        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_extras_skip_repo_already_in_images(self, mock_oci, config):
        """Regression: when CLEANUP_REPO matches a real deployed image, the synthetic
        `:latest` extras entry must not be added — otherwise set iteration order
        decides whether the deployed tag gets protected, and on the unlucky order
        the deployed tag lands in the deletion list.
        """
        self._make_client(mock_oci)
        client = RegistryClient(config)
        client._oci_registry = 'iad.ocir.io'

        deployed = Image('iad.ocir.io/ns/app:deployed-tag', digest='sha256:dep')
        # 6 newer images so the deployed tag would be the 7th-oldest and, if it
        # ever landed in `filtered_images`, would be selected for deletion under
        # keep_count=5.
        newer = [
            Image(f'iad.ocir.io/ns/app:v{i}',
                  created_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
                  digest=f'sha256:new{i}',
                  ocid=f'ocid1.image.new{i}')
            for i in range(6)
        ]
        deployed_in_registry = Image(
            'iad.ocir.io/ns/app:deployed-tag',
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            digest='sha256:dep',
            ocid='ocid1.image.deployed',
        )
        sdk_response = [deployed_in_registry, *newer]
        sdk_calls = []

        def fake_sdk(img):
            sdk_calls.append(img.full_name)
            return sdk_response

        images = {deployed}
        with patch.object(client, '_get_ocir_images_via_sdk', side_effect=fake_sdk), \
             patch.object(client, '_get_manifest_list_sub_digests', return_value=set()):
            result = client.get_old_ocir_images(images, keep_count=5,
                                                extra_repositories=['ns/app'])

        # The deployed tag must never be in the deletion list, regardless of
        # set iteration order.
        deleted_tags = {im.tag for rec in result for im in rec.tags_to_delete}
        assert 'deployed-tag' not in deleted_tags
        # And the SDK should only ever be called once for the repo — the
        # synthetic :latest entry must not get added when a real image already
        # covers the repo.
        assert len(sdk_calls) == 1
        # `images` was not polluted with a synthetic `:latest` Image.
        assert not any(im.tag == 'latest' for im in images)

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_skips_already_processed_repo(self, mock_oci, config):
        """Two images for the same repo: the second is skipped via repo_names_processed.

        The duplicate-skip only kicks in once the first image has produced a recommendation
        (which is when the repo gets added to repo_names_processed), so we set up enough
        old tags here to actually generate one.
        """
        self._make_client(mock_oci)
        client = RegistryClient(config)

        deployed = Image('iad.ocir.io/ns/app:v9.0.0', digest='sha256:dep')
        # 6 old images so we exceed keep_count=5 and produce one deletion candidate
        old_images = [
            Image(f'iad.ocir.io/ns/app:v0.{i}.0',
                  created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                  digest=f'sha256:old{i}',
                  ocid=f'ocid1.image.old{i}')
            for i in range(6)
        ]
        sdk_response = [deployed, *old_images]
        sdk_calls = {'count': 0}

        def fake_sdk(_img):
            sdk_calls['count'] += 1
            return sdk_response

        with patch.object(client, '_get_ocir_images_via_sdk', side_effect=fake_sdk), \
             patch.object(client, '_get_manifest_list_sub_digests', return_value=set()):
            # Two different deployed tags for the same repo
            img1 = deployed
            img2 = Image('iad.ocir.io/ns/app:v9.0.1', digest='sha256:dep2')
            result = client.get_old_ocir_images([img1, img2], keep_count=5)

        assert len(result) == 1  # only one recommendation
        # The SDK was called exactly once -> second image hit the duplicate-skip branch
        assert sdk_calls['count'] == 1

    def _repo_with_old_tags(self, count=8):
        """Registry listing of `count` tags v0.0.0..v0.{count-1}.0, oldest first."""
        return [
            Image(f'iad.ocir.io/ns/app:v0.{i}.0',
                  created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                  digest=f'sha256:old{i}',
                  ocid=f'ocid1.image.old{i}')
            for i in range(count)
        ]

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protects_every_deployed_tag_in_a_repo(self, mock_oci, config):
        """Several pods can name different tags of one repo (a rollout in flight, or a
        finished CronJob pod still in Job history). Every one must survive, whichever
        image is visited first, even when both are far outside the keep window."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        listing = self._repo_with_old_tags()
        # v0.0.0 and v0.1.0 are the two oldest tags: normally the first to be deleted
        deployed = [Image('iad.ocir.io/ns/app:v0.0.0'), Image('iad.ocir.io/ns/app:v0.1.0')]

        for ordering in (deployed, list(reversed(deployed))):
            with patch.object(client, '_get_ocir_images_via_sdk', return_value=listing), \
                 patch.object(client, '_get_manifest_list_sub_digests', return_value=set()):
                result = client.get_old_ocir_images(list(ordering), keep_count=5)

            assert len(result) == 1
            deleted = {im.tag for im in result[0].tags_to_delete}
            assert deleted == {'v0.2.0'}  # 8 tags - 2 deployed = 6 candidates, keep newest 5

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_protects_digest_shared_with_second_deployed_tag(self, mock_oci, config):
        """A deployed tag that is not first must also protect its digest. v0.0.0 is the one
        delete candidate, but it is byte-identical to the (old, deployed) v0.1.0, so
        deleting it by OCID would break that deployed tag."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        listing = self._repo_with_old_tags()
        listing[0].digest = listing[1].digest
        deployed = [Image('iad.ocir.io/ns/app:v0.7.0'), Image('iad.ocir.io/ns/app:v0.1.0')]

        with patch.object(client, '_get_ocir_images_via_sdk', return_value=listing), \
             patch.object(client, '_get_manifest_list_sub_digests', return_value=set()):
            result = client.get_old_ocir_images(deployed, keep_count=5)

        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_tolerates_deployed_tag_missing_from_registry(self, mock_oci, config):
        """A deployed tag already gone from the registry has nothing to protect and must
        not break the pass or hide the real candidates."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        listing = self._repo_with_old_tags()
        deployed = [Image('iad.ocir.io/ns/app:v0.7.0'), Image('iad.ocir.io/ns/app:already-gone')]

        with patch.object(client, '_get_ocir_images_via_sdk', return_value=listing), \
             patch.object(client, '_get_manifest_list_sub_digests', return_value=set()):
            result = client.get_old_ocir_images(deployed, keep_count=5)

        deleted = {im.tag for im in result[0].tags_to_delete}
        # 8 tags - deployed v0.7.0 = 7 candidates, keep newest 5 -> delete the 2 oldest
        assert deleted == {'v0.0.0', 'v0.1.0'}

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_scans_a_repo_once_even_without_a_recommendation(self, mock_oci, config):
        """The pass is repo-wide, so a second image of a repo is never rescanned, even
        when the first produced nothing to delete."""
        self._make_client(mock_oci)
        client = RegistryClient(config)
        listing = self._repo_with_old_tags(count=3)  # under keep_count
        calls = []

        def fake_sdk(img):
            calls.append(img.full_name)
            return listing

        deployed = [Image('iad.ocir.io/ns/app:v0.0.0'), Image('iad.ocir.io/ns/app:v0.1.0')]
        with patch.object(client, '_get_ocir_images_via_sdk', side_effect=fake_sdk):
            assert client.get_old_ocir_images(deployed, keep_count=5) == []

        assert len(calls) == 1

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_skips_non_ocir_images(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)
        result = client.get_old_ocir_images([Image('docker.io/library/nginx:latest')])
        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_skips_when_under_keep_count(self, mock_oci, config):
        """When filtered tag count <= keep_count, no recommendation is produced."""
        self._make_client(mock_oci)
        client = RegistryClient(config)

        deployed = Image('iad.ocir.io/ns/app:v1.0.0')
        old_images = [
            Image('iad.ocir.io/ns/app:v0.9.0',
                  created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                  digest='sha256:a'),
            Image('iad.ocir.io/ns/app:v0.9.1',
                  created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
                  digest='sha256:b'),
        ]
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[deployed, *old_images]):
            result = client.get_old_ocir_images([deployed], keep_count=5)
        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_old_ocir_images_skips_when_all_candidates_protected(self, mock_oci, config):
        """If every old image's digest is referenced by a kept manifest list, nothing is recommended."""
        self._make_client(mock_oci)
        client = RegistryClient(config)

        deployed = Image('iad.ocir.io/ns/app:v9.0.0', digest='sha256:deployed')
        # 6 old tags so we exceed keep_count=5 and have one candidate-for-deletion
        old_images = [
            Image(f'iad.ocir.io/ns/app:v0.{i}.0',
                  created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                  digest=f'sha256:old{i}')
            for i in range(6)
        ]
        all_images = [deployed, *old_images]

        # Protected digest set covers the single candidate-for-deletion (oldest = old0)
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=all_images), \
             patch.object(client, '_get_manifest_list_sub_digests', return_value={'sha256:old0'}):
            result = client.get_old_ocir_images([deployed], keep_count=5)

        assert result == []  # filtered_images becomes empty after protection -> continue

    # --- get_orphaned_manifests skip/early-return branches ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_adds_extra_repositories(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)
        client._oci_registry = 'iad.ocir.io'

        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[]):
            result = client.get_orphaned_manifests(set(), extra_repositories=['ns/extra'])
        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_extras_skip_repo_already_in_images(self, mock_oci, config):
        """Mirror of the get_old_ocir_images regression: an extras entry whose repo
        is already covered by a real OCIR image in `images` must not add a synthetic
        `:latest`. Same set-iteration-order hazard, same fix.
        """
        self._make_client(mock_oci)
        client = RegistryClient(config)
        client._oci_registry = 'iad.ocir.io'

        deployed = Image('iad.ocir.io/ns/app:deployed-tag', digest='sha256:dep')
        images = {deployed}
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[]):
            client.get_orphaned_manifests(images, extra_repositories=['ns/app'])

        assert not any(im.tag == 'latest' for im in images)

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_skips_already_processed_repo(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)

        img1 = Image('iad.ocir.io/ns/app:v1.0.0')
        img2 = Image('iad.ocir.io/ns/app:v1.0.1')
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[]):
            client.get_orphaned_manifests([img1, img2])  # second skipped via repo_names_processed

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_skips_non_ocir(self, mock_oci, config):
        self._make_client(mock_oci)
        client = RegistryClient(config)
        result = client.get_orphaned_manifests([Image('docker.io/library/nginx:latest')])
        assert result == []

    @patch('ocir_cleanup.registry_client.oci')
    def test_get_orphaned_manifests_skips_when_no_platform_manifests_present(self, mock_oci, config):
        """Repo has tags but no platform manifests → skipped without errors."""
        self._make_client(mock_oci)
        client = RegistryClient(config)

        deployed = Image('iad.ocir.io/ns/app:v1.0.0', digest='sha256:a')
        # No 'unknown@sha256:' images present
        with patch.object(client, '_get_ocir_images_via_sdk', return_value=[deployed]):
            result = client.get_orphaned_manifests([deployed])
        assert result == []

    # --- delete_ocir_images ServiceError handling ---

    @patch('ocir_cleanup.registry_client.oci')
    def test_delete_ocir_images_skips_404_and_keeps_going(self, mock_oci, config):
        """A 404 on delete is treated as already-deleted; the image still appears in results."""
        mock_oci.exceptions.ServiceError = ServiceError
        self._make_client(mock_oci)
        client = RegistryClient(config)

        img = Image('iad.ocir.io/ns/app:old1', ocid='ocid1.image.deleted')
        client.artifacts_client.delete_container_image.side_effect = ServiceError(
            status=404, code='NF', headers={}, message='gone'
        )
        rec = CleanupRecommendation('iad.ocir.io', 'ns/app', [img])

        deleted = client.delete_ocir_images([rec])
        assert deleted == [img]

    @patch('ocir_cleanup.registry_client.oci')
    def test_delete_ocir_images_reraises_non_404_service_errors(self, mock_oci, config):
        """A non-404 ServiceError propagates out of delete_ocir_images."""
        mock_oci.exceptions.ServiceError = ServiceError
        self._make_client(mock_oci)
        client = RegistryClient(config)

        img = Image('iad.ocir.io/ns/app:bad', ocid='ocid1.image.bad')
        client.artifacts_client.delete_container_image.side_effect = ServiceError(
            status=500, code='X', headers={}, message='server boom'
        )
        rec = CleanupRecommendation('iad.ocir.io', 'ns/app', [img])

        with pytest.raises(ServiceError):
            client.delete_ocir_images([rec])
