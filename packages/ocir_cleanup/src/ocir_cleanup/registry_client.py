"""OCIR registry client for fetching available image tags."""

import json
import os
import re
from collections import defaultdict
from logging import getLogger
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

import requests
import oci
from oci.regions import REGIONS_SHORT_NAMES
from oke_scanner_core.image import Image

from .config import CleanupConfig

logger = getLogger(__name__)

@dataclass
class CleanupRecommendation:
    """Cleanup recommendation for an OCIR repository."""

    registry: str
    repository: str
    tags_to_delete: list[Image]

class RegistryClient:
    """Client for interacting with container registry APIs."""

    def __init__(self, cfg: CleanupConfig):
        """Initialize registry client."""
        self.cfg = cfg

        # Cache for OCIR image data (repository -> list of image dicts)
        self._ocir_image_cache = {}

        # Cache for repository -> compartment_id mapping
        self._repository_compartment_cache = {}

        # OCI namespace (fetched from Object Storage API)
        self._oci_namespace: Optional[str] = None

        # OCI registry (derived from OCI config region)
        self._oci_registry: Optional[str] = None

        # Initialize OCI clients: as a resource principal when the pod carries an rpst sidecar
        # (OCI_RESOURCE_PRINCIPAL_VERSION set), otherwise from the config file (~/.oci/config)
        self.artifacts_client = None
        self.identity_client = None
        self.object_client = None
        self.oci_config = None
        try:
            client_kwargs = {}
            if os.getenv("OCI_RESOURCE_PRINCIPAL_VERSION"):
                signer = oci.auth.signers.get_resource_principals_signer()
                self.oci_config = {"region": signer.region, "tenancy": signer.tenancy_id}
                client_kwargs["signer"] = signer
            else:
                self.oci_config = oci.config.from_file()
            self.artifacts_client = oci.artifacts.ArtifactsClient(self.oci_config, **client_kwargs)
            self.identity_client = oci.identity.IdentityClient(self.oci_config, **client_kwargs)
            self.object_client = oci.object_storage.ObjectStorageClient(self.oci_config, **client_kwargs)
            logger.info(f"RegistryClient initialized with OCI SDK for OCIR: {self.oci_registry}")
        except Exception as e:
            logger.warning(f"Failed to initialize OCI SDK client: {e}")
            logger.warning("OCIR image version checking will not work without OCI credentials")

        logger.info(f"RegistryClient initialized for registry: {self.oci_registry}")

    @property
    def oci_namespace(self) -> Optional[str]:
        """Get OCI namespace from Object Storage API.

        The namespace is cached after the first retrieval.

        Returns:
            OCI namespace string, or None if unavailable
        """
        if self._oci_namespace is not None:
            return self._oci_namespace

        if not self.object_client:
            logger.info("Object Storage client not available, cannot fetch OCI namespace")
            return None

        try:
            self._oci_namespace = self.object_client.get_namespace().data
            logger.debug(f"Retrieved OCI namespace: {self._oci_namespace}")
            return self._oci_namespace
        except Exception as e:
            logger.info(f"Failed to get OCI namespace from Object Storage API: {e}")
            return None

    @property
    def oci_registry(self) -> Optional[str]:
        """Get OCI registry URL from OCI config region.

        Derives the OCIR URL from the region in the OCI config.
        Format: <region-key>.ocir.io

        The registry is cached after the first retrieval.

        Returns:
            OCIR registry URL (e.g., 'iad.ocir.io'), or None if unavailable
        """
        if self._oci_registry is not None:
            return self._oci_registry

        if not self.oci_config:
            logger.info("OCI config not available, cannot derive registry URL")
            return None

        try:
            region = self.oci_config.get('region')
            if not region:
                logger.info("No region found in OCI config")
                return None

            # Find the region key by looking up the region identifier
            region_key = None
            for key, identifier in REGIONS_SHORT_NAMES.items():
                if identifier == region:
                    region_key = key
                    break

            if region_key:
                self._oci_registry = f"{region_key}.ocir.io"
                logger.debug(f"Derived OCI registry from region {region}: {self._oci_registry}")
                return self._oci_registry

            logger.info(f"Could not find region key for region: {region}")
            return None
        except Exception as e:
            logger.info(f"Failed to derive OCI registry from config: {e}")
            return None

    def _strip_namespace_prefix(self, repository: str) -> str:
        """Strip namespace prefix from OCIR repository name.

        OCIR image references include the namespace in the path (e.g., 'namespace/discord_bot'),
        but the OCI API expects just the repository name without the namespace prefix.

        Args:
            repository: Full repository path (e.g., 'namespace/discord_bot')

        Returns:
            Repository name without namespace prefix (e.g., 'discord_bot')
        """
        if not self.oci_namespace:
            # Fallback: just take the last part after splitting on '/'
            if '/' in repository:
                return repository.split('/')[-1]
            return repository

        namespace_prefix = f"{self.oci_namespace}/"
        if repository.startswith(namespace_prefix):
            normalized = repository[len(namespace_prefix):]
            logger.debug(f"Stripped namespace from repository: {repository} -> {normalized}")
            return normalized

        return repository


    def _get_tenancy_id(self) -> Optional[str]:
        """Get tenancy OCID from OCI config."""
        if not self.oci_config:
            return None
        return self.oci_config.get('tenancy')

    def _list_all_compartments(self) -> list[str]:
        """List all compartments in the tenancy (including tenancy root).

        Returns:
            List of compartment OCIDs to search
        """
        # A configured list skips the search: probing every compartment for each repository
        # means a 404 per compartment that does not hold it, which an audit of resource-principal
        # calls reads as a refused request.
        if self.cfg.ocir_compartment_ids:
            return list(self.cfg.ocir_compartment_ids)

        if not self.identity_client:
            return []

        tenancy_id = self._get_tenancy_id()
        if not tenancy_id:
            return []

        compartment_ids = [tenancy_id]  # Start with root tenancy

        # List all compartments in the tenancy
        response = self.identity_client.list_compartments(
            compartment_id=tenancy_id,
            compartment_id_in_subtree=True,  # Include nested compartments
            access_level="ACCESSIBLE"  # Only compartments we can access
        )

        for compartment in response.data:
            if compartment.lifecycle_state == "ACTIVE":
                compartment_ids.append(compartment.id)

        logger.debug(f"Found {len(compartment_ids)} accessible compartments")
        return compartment_ids


    def _find_repository_compartment(self, repository: str) -> Optional[str]:
        """Find which compartment contains the given repository.

        Args:
            repository: Repository name (e.g., 'discord-bot')

        Returns:
            Compartment OCID where repository exists, or None if not found
        """
        # Make sure we take the namespace prefix out of the repo name
        if repository in self._repository_compartment_cache:
            logger.debug(f"Using cached compartment for repository {repository}")
            return self._repository_compartment_cache[repository]

        if not self.artifacts_client:
            return None

        # Get all accessible compartments
        compartments = self._list_all_compartments()

        # Search each compartment for the repository
        for compartment_id in compartments:
            try:
                response = self.artifacts_client.list_container_images(
                    compartment_id=compartment_id,
                    repository_name=repository,
                    limit=1  # Just check if it exists
                )

                # If we got any results, this compartment has the repository
                if response.data.items:
                    logger.info(f"Found repository {repository} in compartment {compartment_id}")
                    # Cache the result
                    self._repository_compartment_cache[repository] = compartment_id
                    return compartment_id

            except oci.exceptions.ServiceError as e:
                # 404 means repository doesn't exist in this compartment, continue searching
                if e.status == 404:
                    continue
                # Other errors - log and continue
                logger.debug(f"Error checking compartment {compartment_id}: {e.message}")
                continue

        logger.info(f"Repository {repository} not found in any accessible compartment")
        return None

    def _get_ocir_images_via_sdk(self, image: Image) -> list[Image]:
        """Get OCIR images using OCI SDK.

        Searches across all accessible compartments to find the repository.

        Args:
            repository: Repository name (e.g., 'discord-bot', with or without namespace)

        Returns:
            List of image dictionaries with version and created_at
        """
        # Check cache first (use normalized name)
        if image.repo_name in self._ocir_image_cache:
            logger.debug(f"Using cached OCIR image data for {image.repo_name}")
            return self._ocir_image_cache[image.repo_name]

        if not self.artifacts_client:
            logger.debug("OCI SDK client not available")
            return []

        # Strip namespace prefix from repository name for OCI API calls
        repository = self._strip_namespace_prefix(image.repo_name)

        # Find which compartment contains this repository
        compartment_id = self._find_repository_compartment(repository)
        if not compartment_id:
            logger.info(f"Could not find compartment for repository {image.repo_name}")
            return []
        # List all container images in the repository (with pagination)
        response = oci.pagination.list_call_get_all_results(
            self.artifacts_client.list_container_images,
            compartment_id,
            repository_name=repository,
        )

        images = []
        for item in response.data:
            # Tagged images have a version; untagged platform manifests don't
            if item.version:
                tag = item.version
            elif hasattr(item, 'digest') and item.digest:
                tag = f'unknown@{item.digest}'
            else:
                continue
            new_image = Image(f'{image.registry}/{image.repo_name}:{tag}',
                            ocid=item.id,
                            created_at=item.time_created,
                            digest=item.digest)
            images.append(new_image)

        # Cache the results
        self._ocir_image_cache[image.repo_name] = images
        logger.debug(f"Found {len(images)} OCIR images for {image.repo_name}")
        return images

    def _read_basic_auth(self, registry: str) -> Optional[str]:
        """Read the Basic credential for a registry from ~/.docker/config.json.

        Args:
            registry: Registry hostname

        Returns:
            The base64 ``auth`` value, or None when the config or the entry is absent
        """
        try:
            config_path = os.path.expanduser('~/.docker/config.json')
            with open(config_path, encoding='utf-8') as f:
                docker_config = json.load(f)
            entry = docker_config.get('auths', {}).get(registry)
            if not entry or 'auth' not in entry:
                return None
            return entry['auth']
        except Exception as e:
            logger.debug(f"Could not read Docker auth for {registry}: {e}")
        return None

    def _get_docker_auth(self, image: Image) -> Optional[dict]:
        """Get Docker V2 API auth headers for an image.

        Reads Basic credentials from ~/.docker/config.json when present, then
        exchanges them for a Bearer token via the registry's token endpoint
        (required by OCIR and other registries using token-based auth). With
        no credentials it asks for an anonymous token instead, which public
        repositories grant.

        Args:
            image: Image to authenticate for (needs registry and repo_name)

        Returns:
            Dict of auth headers (empty when the registry needs none), or None
            if no access could be obtained
        """
        basic_auth = self._read_basic_auth(image.registry)
        basic_headers = {'Authorization': f'Basic {basic_auth}'} if basic_auth else {}

        try:
            v2_resp = requests.get(f'https://{image.registry}/v2/',
                                   headers=basic_headers,
                                   timeout=10)
            if v2_resp.status_code == 200:
                # Registry accepted the request as-is (Basic, or nothing)
                return basic_headers

            if v2_resp.status_code == 401:
                www_auth = v2_resp.headers.get('WWW-Authenticate', '')
                # Parse Bearer realm and service from WWW-Authenticate header
                realm = service = None
                for part in www_auth.replace('Bearer ', '').split(','):
                    key, _, val = part.strip().partition('=')
                    val = val.strip('"')
                    if key == 'realm':
                        realm = val
                    elif key == 'service':
                        service = val

                if realm and service:
                    token_resp = requests.get(
                        realm,
                        headers=basic_headers,
                        params={
                            'service': service,
                            'scope': f'repository:{image.repo_name}:pull',
                        },
                        timeout=10,
                    )
                    if token_resp.ok:
                        token = token_resp.json().get('token')
                        if token:
                            return {'Authorization': f'Bearer {token}'}
        except requests.RequestException as e:
            logger.debug(f"Token exchange failed for {image.registry}: {e}")
        return None

    def _get_manifest_list_sub_digests(self, image: Image) -> set[str]:
        """Fetch sub-manifest digests if the image is a manifest list.

        Args:
            image: Image with digest to check

        Returns:
            Set of sub-manifest digest strings, or empty set on any error
        """
        if not image.digest:
            return set()

        auth_headers = self._get_docker_auth(image)
        if auth_headers is None:
            return set()

        url = f'https://{image.registry}/v2/{image.repo_name}/manifests/{image.digest}'
        headers = {
            **auth_headers,
            'Accept': ', '.join([
                'application/vnd.docker.distribution.manifest.list.v2+json',
                'application/vnd.oci.image.index.v1+json',
                'application/vnd.docker.distribution.manifest.v2+json',
                'application/vnd.oci.image.manifest.v1+json',
            ]),
        }

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            media_type = data.get('mediaType', '')
            if media_type in (
                'application/vnd.docker.distribution.manifest.list.v2+json',
                'application/vnd.oci.image.index.v1+json',
            ):
                manifests = data.get('manifests', [])
                return {m['digest'] for m in manifests if 'digest' in m}
        except Exception as e:
            logger.info(f"Could not fetch manifest list for {image.full_name}: {e}")

        return set()

    def get_image_creation_date(self, image: Image) -> Optional[datetime]:
        """Get image creation date from manifest.

        Args:
            registry: Registry hostname
            repository: Image repository
            tag: Image tag

        Returns:
            Creation datetime or None
        """
        if image.is_ocir_image:
            images = self._get_ocir_images_via_sdk(image)
            for img in images:
                if img.tag == image.tag and img.created_at:
                    logger.debug(f"Using cached creation date for OCIR image {image.repo_name}:{image.tag}")
                    return img.created_at
            logger.debug(f"No cached creation date found for OCIR image {image.repo_name}:{image.tag}")
            return None
        return None

    def get_old_ocir_images(self, images: list[Image], keep_count: int = 5,
                       extra_repositories: list[str] = None) -> list[CleanupRecommendation]:
        '''Return report of images that can be deleted'''
        repo_names_processed = []
        extra_repositories = extra_repositories or []
        recommendations = []

        # Optional per-repo knobs sourced from config. Compiled once outside
        # the loop. Empty string means the corresponding feature is off.
        protect_re = (re.compile(self.cfg.cleanup_protect_tags_regex)
                      if self.cfg.cleanup_protect_tags_regex else None)
        group_re = (re.compile(self.cfg.cleanup_group_by_regex)
                    if self.cfg.cleanup_group_by_regex else None)

        # Skip extras for repos already represented by a real discovered image —
        # the synthetic :latest entry would race the real one on set iteration
        # order and, if visited first, marks the repo processed without ever
        # protecting the deployed tag (it doesn't match :latest), so the
        # deployed image can land in the deletion list.
        existing_ocir_repos = {im.repo_name for im in images if im.is_ocir_image}
        # Every tag some pod in the cluster names, per repo. A repo can have
        # several (a rollout in flight, or finished CronJob pods that linger in
        # Job history on an older tag); all of them must survive, not just
        # whichever image happens to be visited first.
        deployed_by_repo = defaultdict(set)
        for im in images:
            if im.is_ocir_image:
                deployed_by_repo[f'{im.registry}/{im.repo_name}'].add(im.full_name)
        for extra_repo in extra_repositories:
            if extra_repo in existing_ocir_repos:
                continue
            logger.info(f'Scanning extra repo {extra_repo} in old image scan')
            images.add(Image(f'{self.oci_registry}/{extra_repo}:latest'))
        for image in images:
            if f'{image.registry}/{image.repo_name}' in repo_names_processed:
                continue
            logger.info(f'Scanning for versions of {image} that can be deleted')
            if not image.is_ocir_image:
                continue
            # The pass below covers the whole repo (every deployed tag is
            # protected), so it never needs repeating for another image of it.
            repo_names_processed.append(f'{image.registry}/{image.repo_name}')
            deployed_names = deployed_by_repo.get(f'{image.registry}/{image.repo_name}', set()) | {image.full_name}
            all_images = self._get_ocir_images_via_sdk(image)
            # Exclude platform manifests - they are handled by get_orphaned_manifests()
            normal_images = [im for im in all_images if 'unknown@sha256:' not in im.full_name]
            # Skip the 'latest' tag and every currently deployed image
            filtered_images = [im for im in normal_images
                               if im.tag != 'latest' and im.full_name not in deployed_names]

            # CLEANUP_PROTECT_TAGS_REGEX: tags whose name fully matches are
            # excluded from the deletion candidate pool entirely. Used to
            # protect mutable "channel" tags that get overwritten on every
            # build (e.g. ci-base-images' :3.11/:3.12/:3.13/:3.14).
            if protect_re is not None:
                protected_count = sum(1 for im in filtered_images
                                      if im.tag and protect_re.fullmatch(im.tag))
                if protected_count:
                    filtered_images = [im for im in filtered_images
                                       if not (im.tag and protect_re.fullmatch(im.tag))]
                    logger.info(
                        f'Protected {protected_count} tag(s) matching '
                        f'CLEANUP_PROTECT_TAGS_REGEX in {image.repo_name}'
                    )

            # Reduce to the delete candidates (all-but-newest keep_count,
            # per group when CLEANUP_GROUP_BY_REGEX is set). Each deployed tag
            # is resolved to its registry entry so we can protect its digest
            # even in the fallback case where it isn't among the scanned tags.
            registry_by_name = {im.full_name: im for im in all_images}
            deployed_with_digest = [registry_by_name.get(name) or Image(name) for name in sorted(deployed_names)]
            if group_re is not None:
                # CLEANUP_GROUP_BY_REGEX: group remaining tags by the regex's
                # first capture group and apply keep_count per-group, so heavy
                # churn in one group can't push other groups' tags out of the
                # keep window. Tags whose name doesn't match are treated as a
                # single "_ungrouped" bucket and trimmed together.
                groups = defaultdict(list)
                for im in filtered_images:
                    if not im.tag:
                        groups['_ungrouped'].append(im)
                        continue
                    m = group_re.match(im.tag)
                    key = m.group(1) if m else '_ungrouped'
                    groups[key].append(im)
                to_delete = []
                for key, group_images in groups.items():
                    group_images.sort(key=lambda im: im.created_at)
                    if len(group_images) <= keep_count:
                        continue
                    to_delete.extend(group_images[0:len(group_images) - keep_count])
                if not to_delete:
                    continue
                filtered_images = to_delete
            else:
                # Sort so we can check against the keep count.
                filtered_images.sort(key=lambda im: im.created_at)
                if len(filtered_images) <= keep_count:
                    continue
                filtered_images = filtered_images[0:len(filtered_images) - keep_count]

            # Collect digests we must never delete. Deleting a ContainerImage by
            # OCID removes the underlying manifest, so a delete candidate that
            # shares its digest with ANY tag that outlives this run would break
            # that surviving tag ("manifest unknown"). The tags that survive are
            # every tag in the repo EXCEPT the current delete candidates — this
            # covers :latest, the deployed tag, the keep-window tags, AND the
            # CLEANUP_PROTECT_TAGS_REGEX channel tags (e.g. ci-base-images'
            # :3.13) that were pulled out of the candidate pool above. Protecting
            # only the keep-window tags missed the channel-tag case: a
            # byte-identical rebuild gives a fresh :3.13-<sha> the channel
            # digest, OCIR dates it by when that digest first appeared so it
            # sorts "old" and lands in the delete pool, and pruning it by OCID
            # destroyed the manifest :3.13 still points at. Protect every
            # surviving tag's own digest plus any sub-manifests it references.
            delete_ocids = {im.ocid for im in filtered_images}
            surviving_images = [im for im in normal_images if im.ocid not in delete_ocids]
            for deployed in deployed_with_digest:
                if deployed not in surviving_images:
                    surviving_images.append(deployed)
            # Protection is digest equality, so a surviving tag whose digest
            # did not resolve is a tag we cannot protect: any candidate sharing
            # its manifest would still be deleted by OCID and break it. Bail on
            # the whole repo instead — the same posture get_orphaned_manifests
            # takes when no sub-manifests resolve. Only registry entries are
            # checked: the synthetic deployed-tag fallback carries no ocid
            # precisely because it was never in the listing, so there is nothing
            # under that name in the registry for a prune to break.
            # str() because real OCIR listings include tagless images (tag is None)
            unresolved = sorted(str(im.tag) for im in surviving_images if im.ocid and not im.digest)
            if unresolved:
                logger.info(f'Skipping old-image cleanup for {image.repo_name}: surviving '
                            f'tag(s) {", ".join(unresolved)} have no digest, so a candidate '
                            f'sharing their manifest could not be protected')
                continue
            protected_digests = set()
            for surviving in surviving_images:
                if surviving.digest:
                    protected_digests.add(surviving.digest)
                protected_digests.update(self._get_manifest_list_sub_digests(surviving))
            # Drop candidates that share a digest with (or are a sub-manifest of)
            # any surviving tag — unless that same tag is itself being deleted.
            # A candidate with no digest of its own goes too: deleting it means
            # calling delete_container_image() on a manifest we could not
            # identify, and an unidentified manifest is exactly what a shared
            # digest looks like from here. Unconditional — guarding this on a
            # non-empty protected set used to let a repo with nothing to protect
            # delete every candidate unchecked.
            before_count = len(filtered_images)
            filtered_images = [im for im in filtered_images
                               if im.digest and im.digest not in protected_digests]
            protected_count = before_count - len(filtered_images)
            if protected_count:
                logger.info(f'Protected {protected_count} image(s) with an unresolved digest, or '
                            f'sharing a digest with a surviving tag, in {image.repo_name}')
            if not filtered_images:
                continue
            recommendations.append(CleanupRecommendation(image.registry, image.repo_name, filtered_images))

        return recommendations

    def get_orphaned_manifests(self, images: list[Image],
                               extra_repositories: list[str] = None) -> list[CleanupRecommendation]:
        """Detect orphaned platform manifests in OCIR repositories.

        Multi-arch Docker builds create unknown@sha256:... platform manifests for each
        tagged image. When the parent tag is deleted, these platform manifests remain as
        orphans. This method identifies them by resolving each normal tag's manifest list
        to find its referenced sub-manifest digests. Platform manifests whose digest is
        not referenced by any normal tag's manifest list are orphans.

        Args:
            images: Set of currently deployed images
            extra_repositories: Additional repositories to scan

        Returns:
            List of CleanupRecommendation for orphaned manifests
        """
        repo_names_processed = []
        extra_repositories = extra_repositories or []
        recommendations = []
        # See get_old_ocir_images: skip extras for repos already covered by a
        # real discovered image to avoid set-order races.
        existing_ocir_repos = {im.repo_name for im in images if im.is_ocir_image}
        for extra_repo in extra_repositories:
            if extra_repo in existing_ocir_repos:
                continue
            logger.info(f'Scanning extra repo {extra_repo} for orphaned manifests')
            images.add(Image(f'{self.oci_registry}/{extra_repo}:latest'))
        for image in images:
            if f'{image.registry}/{image.repo_name}' in repo_names_processed:
                continue
            if not image.is_ocir_image:
                continue
            logger.info(f'Scanning {image.repo_name} for orphaned platform manifests')
            all_images = self._get_ocir_images_via_sdk(image)

            # Separate normal tags from platform manifests
            # Note: Image.__post_init__ strips @sha256: from the tag, so check full_name
            normal_tags = [im for im in all_images if 'unknown@sha256:' not in im.full_name]
            platform_manifests = [im for im in all_images if 'unknown@sha256:' in im.full_name]

            if not platform_manifests:
                repo_names_processed.append(f'{image.registry}/{image.repo_name}')
                continue

            # Collect all sub-manifest digests referenced by normal tags
            # This is the reliable way to determine which platform manifests are still needed,
            # rather than timestamp matching which can fail when push times differ slightly
            referenced_digests = set()
            for tag in normal_tags:
                sub_digests = self._get_manifest_list_sub_digests(tag)
                referenced_digests.update(sub_digests)

            if not referenced_digests:
                # No normal tag yielded sub-manifest digests — either every tag is a
                # single-platform image (no manifest list to enumerate) or all the
                # manifest fetches failed. Either way, we can't safely tell which
                # platform manifests are still referenced, so skip rather than risk
                # deleting needed ones. Real fetch failures surface via the per-tag
                # 'Could not fetch manifest list' log above.
                logger.info(f'No referenced sub-manifests found for {image.repo_name} '
                            f'across {len(normal_tags)} normal tags '
                            f'(likely all single-platform); skipping orphan detection')
                repo_names_processed.append(f'{image.registry}/{image.repo_name}')
                continue

            # Orphans are platform manifests whose digest is not referenced by any normal tag
            orphans = [im for im in platform_manifests
                       if im.digest and im.digest not in referenced_digests]

            if orphans:
                logger.info(f'Found {len(orphans)} orphaned manifests in {image.repo_name} '
                            f'(out of {len(platform_manifests)} platform manifests)')
                recommendations.append(CleanupRecommendation(
                    image.registry, image.repo_name, orphans))

            repo_names_processed.append(f'{image.registry}/{image.repo_name}')

        return recommendations

    def delete_ocir_images(self, cleanup_recommendations: list[CleanupRecommendation]) -> list[Image]:
        """Delete old OCIR images based on cleanup recommendations.

        Args:
            cleanup_recommendations: Dictionary of CleanupRecommendation from get_cleanup_recommendations()

        Returns:
            Dictionary mapping repository names to DeletionResult dataclasses
        """
        if not self.artifacts_client:
            logger.info("OCI SDK not available, cannot delete OCIR images")
            return []

        images_deleted = []
        repos_modified = set()

        for item in cleanup_recommendations:
            for image in item.tags_to_delete:
                tag_label = image.tag if image.tag != 'unknown' else image.digest
                try:
                    self.artifacts_client.delete_container_image(image.ocid)
                    logger.info(f'Deleted {image.repo_name}:{tag_label}')
                except oci.exceptions.ServiceError as e:
                    if e.status == 404:
                        logger.info(f'Already absent {image.repo_name}:{tag_label}, skipping')
                    else:
                        raise
                images_deleted.append(image)
                repos_modified.add(image.repo_name)

        # Invalidate cache for modified repos so subsequent calls get fresh data
        for repo in repos_modified:
            self._ocir_image_cache.pop(repo, None)

        return images_deleted
