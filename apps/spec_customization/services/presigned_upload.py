"""
Spec Customization — Direct-to-S3 Presigned Upload helper.

Why
───
Railway's edge proxy enforces a request-body size limit (~100 MB on most
plans), and even when it doesn't, routing a 1 GB multipart through gunicorn
ties up a worker for the entire transfer. Direct-to-S3 presigned PUT lets the
browser upload straight to AWS, then the backend just records metadata and
streams the object into the existing extraction pipeline.

Soft-coding
───────────
Every knob is overridable via env vars — no code change needed to retune:

    SPEC_PRESIGNED_UPLOAD_ENABLED   "True"/"False"   master switch
    SPEC_PRESIGNED_URL_EXPIRY       seconds (int)    default 3600 (1 h)
    SPEC_PRESIGNED_MAX_BYTES        bytes  (int)     default 2 GiB
    SPEC_PRESIGNED_KEY_PREFIX       string           default 'spec-customization/uploads/'
    SPEC_PRESIGNED_DELETE_AFTER_INGEST "True"/"False" default True
    SPEC_PRESIGNED_DOWNLOAD_CHUNK   bytes  (int)     default 8 MiB

Falls back to ``enabled=False`` when S3 isn't ready, when boto3 raises, or
when the master switch is off — callers should then use the legacy multipart
upload path.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from decouple import config as env_config
from django.conf import settings
from django.core.files.uploadedfile import TemporaryUploadedFile

logger = logging.getLogger(__name__)


# ─── Soft-coded config (read once at import) ───────────────────────────────
def _b(name: str, default: str) -> bool:
    return str(env_config(name, default=default)).strip().lower() in ('1', 'true', 'yes', 'on')


def _i(name: str, default: int) -> int:
    try:
        return int(env_config(name, default=str(default)))
    except (TypeError, ValueError):
        return default


PRESIGNED_UPLOAD_CONFIG = {
    'enabled':              _b('SPEC_PRESIGNED_UPLOAD_ENABLED', 'True'),
    'url_expiry_seconds':   _i('SPEC_PRESIGNED_URL_EXPIRY', 3600),
    'max_bytes':            _i('SPEC_PRESIGNED_MAX_BYTES', 2 * 1024 * 1024 * 1024),  # 2 GiB
    'key_prefix':           env_config('SPEC_PRESIGNED_KEY_PREFIX',
                                       default='spec-customization/uploads/'),
    'delete_after_ingest':  _b('SPEC_PRESIGNED_DELETE_AFTER_INGEST', 'True'),
    'download_chunk_bytes': _i('SPEC_PRESIGNED_DOWNLOAD_CHUNK', 8 * 1024 * 1024),    # 8 MiB
}


# ─── Result shapes ─────────────────────────────────────────────────────────
@dataclass
class PresignResult:
    enabled: bool
    upload_url: Optional[str] = None
    s3_key: Optional[str] = None
    method: str = 'PUT'
    headers: Optional[dict] = None
    expires_in: int = 0
    bucket: Optional[str] = None
    reason: str = ''        # populated when enabled=False, for client-side logging


# ─── Helpers ───────────────────────────────────────────────────────────────
def is_presigned_upload_available() -> bool:
    """Quick check the view layer can call without touching boto3."""
    if not PRESIGNED_UPLOAD_CONFIG['enabled']:
        return False
    if not getattr(settings, 'USE_S3', False):
        return False
    if not getattr(settings, 'AWS_STORAGE_BUCKET_NAME', ''):
        return False
    return True


def _build_s3_client():
    """
    Build a boto3 S3 client using the same credentials Django/Storages uses.
    Kept local so the rest of the codebase doesn't import boto3 unless this
    feature is actually invoked.
    """
    import boto3  # noqa: WPS433  (deliberate local import)

    region = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
    endpoint = getattr(settings, 'AWS_S3_ENDPOINT_URL', None)
    return boto3.client(
        's3',
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=env_config('AWS_ACCESS_KEY_ID', default=None),
        aws_secret_access_key=env_config('AWS_SECRET_ACCESS_KEY', default=None),
        config=__import__('botocore.config', fromlist=['Config']).Config(
            signature_version='s3v4',
            s3={'addressing_style': 'virtual'},
        ),
    )


def _sanitize_filename(name: str) -> str:
    """Strip path separators, keep extension. Never trust the client name."""
    import os
    import re

    base = os.path.basename(name or 'upload')
    # Replace anything that isn't a safe S3 key character.
    base = re.sub(r'[^A-Za-z0-9._\- ]+', '_', base).strip(' .') or 'upload'
    return base[:200]


# ─── Public API ────────────────────────────────────────────────────────────
def generate_presigned_put(
    *,
    filename: str,
    content_type: str = 'application/octet-stream',
    size_bytes: int = 0,
) -> PresignResult:
    """
    Generate a presigned PUT URL the browser can hit directly.

    Returns ``PresignResult(enabled=False, reason=...)`` if the feature is
    disabled or any precondition fails — callers should fall back to the
    legacy multipart upload in that case.
    """
    if not is_presigned_upload_available():
        return PresignResult(enabled=False, reason='presigned uploads disabled or S3 not configured')

    if size_bytes and size_bytes > PRESIGNED_UPLOAD_CONFIG['max_bytes']:
        return PresignResult(
            enabled=False,
            reason=f"size {size_bytes} exceeds cap {PRESIGNED_UPLOAD_CONFIG['max_bytes']}",
        )

    safe_name = _sanitize_filename(filename)
    key = f"{PRESIGNED_UPLOAD_CONFIG['key_prefix']}{uuid.uuid4()}/{safe_name}"
    bucket = settings.AWS_STORAGE_BUCKET_NAME

    try:
        client = _build_s3_client()
        url = client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket':      bucket,
                'Key':         key,
                'ContentType': content_type or 'application/octet-stream',
            },
            ExpiresIn=PRESIGNED_UPLOAD_CONFIG['url_expiry_seconds'],
        )
    except Exception as exc:  # noqa: BLE001 — surface as graceful fallback
        logger.warning('[SpecPresign] presign failed: %s', exc)
        return PresignResult(enabled=False, reason=f'presign error: {exc}')

    return PresignResult(
        enabled=True,
        upload_url=url,
        s3_key=key,
        method='PUT',
        headers={'Content-Type': content_type or 'application/octet-stream'},
        expires_in=PRESIGNED_UPLOAD_CONFIG['url_expiry_seconds'],
        bucket=bucket,
    )


def fetch_uploaded_to_temp_file(
    *,
    s3_key: str,
    original_filename: str,
) -> TemporaryUploadedFile:
    """
    Stream the S3 object into a Django ``TemporaryUploadedFile`` so the rest
    of the extraction pipeline (which expects a Django file-like) sees the
    exact same interface as a direct multipart upload.

    Raises ``RuntimeError`` if the object can't be fetched — caller should
    map this to HTTP 400/500 and surface a clear message.
    """
    if not is_presigned_upload_available():
        raise RuntimeError('presigned uploads are not available in this environment')
    if not s3_key:
        raise RuntimeError('missing s3_key')

    bucket = settings.AWS_STORAGE_BUCKET_NAME
    client = _build_s3_client()

    try:
        head = client.head_object(Bucket=bucket, Key=s3_key)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f'S3 object not found: {exc}') from exc

    size = int(head.get('ContentLength') or 0)
    content_type = head.get('ContentType') or 'application/octet-stream'

    if size > PRESIGNED_UPLOAD_CONFIG['max_bytes']:
        raise RuntimeError(
            f"uploaded size {size} exceeds cap {PRESIGNED_UPLOAD_CONFIG['max_bytes']}"
        )

    safe_name = _sanitize_filename(original_filename)
    tmp = TemporaryUploadedFile(
        name=safe_name,
        content_type=content_type,
        size=size,
        charset=None,
    )

    try:
        obj = client.get_object(Bucket=bucket, Key=s3_key)
        body = obj['Body']
        chunk = PRESIGNED_UPLOAD_CONFIG['download_chunk_bytes']
        while True:
            buf = body.read(chunk)
            if not buf:
                break
            tmp.write(buf)
    except Exception as exc:  # noqa: BLE001
        tmp.close()
        raise RuntimeError(f'S3 download failed: {exc}') from exc

    tmp.seek(0)
    return tmp


def best_effort_delete(s3_key: str) -> None:
    """Best-effort cleanup of the staged object — never raises."""
    if not (s3_key and PRESIGNED_UPLOAD_CONFIG['delete_after_ingest']):
        return
    if not is_presigned_upload_available():
        return
    try:
        client = _build_s3_client()
        client.delete_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=s3_key)
    except Exception as exc:  # noqa: BLE001
        logger.info('[SpecPresign] cleanup skipped for %s: %s', s3_key, exc)
