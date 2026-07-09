"""Phase 1 — S3 helpers for ProjectDocument.

Wraps boto3's `generate_presigned_url` so the view layer never needs to know
whether the storage backend is S3 or local filesystem. For local filesystem
storage the function returns the regular `file.url` (served by Django).
"""
from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

from ..config import S3_PRESIGN_TTL_SEC

logger = logging.getLogger(__name__)


def presign_document_download(document, expires: Optional[int] = None) -> Optional[str]:
    """Return a fresh download URL for the document's file (presigned if S3)."""
    if not document or not getattr(document, 'file', None):
        return None

    ttl = int(expires or S3_PRESIGN_TTL_SEC)
    storage = document.file.storage

    # boto3-backed storage exposes a connection + bucket_name
    if hasattr(storage, 'connection') and hasattr(storage, 'bucket_name'):
        try:
            client = storage.connection.meta.client
            return client.generate_presigned_url(
                'get_object',
                Params={'Bucket': storage.bucket_name, 'Key': document.file.name},
                ExpiresIn=ttl,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning('presign_document_download: S3 presign failed (%s); falling back to .url', exc)

    # Local filesystem / whitenoise — `.url` is already directly servable.
    try:
        return document.file.url
    except Exception as exc:  # noqa: BLE001
        logger.warning('presign_document_download: .url failed (%s)', exc)
        return None
