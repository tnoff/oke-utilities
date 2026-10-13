"""Read and overwrite the hosted key set in Object Storage."""

import os
from logging import getLogger
from typing import Optional

import oci

from .config import JwksPublisherConfig

logger = getLogger(__name__)


class HostedJwks:
    """The one object this job is allowed to overwrite."""

    def __init__(self, cfg: JwksPublisherConfig, client=None):
        self.bucket = cfg.bucket_name
        self.object_name = cfg.object_name
        if client is None:
            oci_config = oci.config.from_file(
                file_location=os.path.expanduser(cfg.oci_config_file),
                profile_name=cfg.oci_profile,
            )
            client = oci.object_storage.ObjectStorageClient(oci_config)
        self._client = client
        self._namespace: Optional[str] = None

    @property
    def namespace(self) -> str:
        """Object Storage namespace, fetched once."""
        if self._namespace is None:
            self._namespace = self._client.get_namespace().data
        return self._namespace

    def read(self) -> Optional[tuple[str, str]]:
        """Return (body, etag) of the hosted object, or None if it does not exist."""
        try:
            response = self._client.get_object(self.namespace, self.bucket, self.object_name)
        except oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                return None
            raise
        return response.data.content.decode("utf-8"), response.headers.get("etag")

    def write(self, body: str, etag: Optional[str]) -> None:
        """Overwrite the object, but only if it is still the version we read.

        `if_match` makes a concurrent change fail with 412 instead of being
        silently clobbered. The policy ("use objects") allows an overwrite but not
        a create, so a missing object is an error, not something to repair here.
        """
        self._client.put_object(
            self.namespace,
            self.bucket,
            self.object_name,
            body.encode("utf-8"),
            content_type="application/json",
            cache_control="no-store",
            if_match=etag,
        )
