"""
Non-TEFF Metadata — Direct-to-S3 Presigned Upload helper.

Mirrors the proven `spec_customization.services.presigned_upload` pattern so
the Bulk Master Index flow can ingest very large files (≥ 2 GB) without ever
sending the payload through Railway's edge proxy.

Why
───
Railway's HTTP edge enforces a body-size limit (~100-500 MB depending on plan)
and a request-timeout (~5-10 min). A 2 GB multipart POST is therefore
architecturally impossible — the edge kills the upload mid-flight and the
browser surfaces the missing CORS headers as a "CORS policy" error.

Direct-to-S3 presigned PUT moves the bytes straight from the browser to AWS;
only the tiny "presign" and "complete" RPCs touch Django.

Soft-coding
───────────
Every knob is overridable via env vars — no code change to retune:

    NT_PRESIGNED_UPLOAD_ENABLED      "True"/"False"   master switch
    NT_PRESIGNED_URL_EXPIRY          seconds (int)    default 3600 (1 h)
    NT_PRESIGNED_MAX_BYTES           bytes  (int)     default 5 GiB
    NT_PRESIGNED_KEY_PREFIX          string           default 'non-teff/uploads/'
    NT_PRESIGNED_DELETE_AFTER_INGEST "True"/"False"   default True
    NT_PRESIGNED_DOWNLOAD_CHUNK      bytes  (int)     default 8 MiB
    NT_PRESIGNED_MIN_MB              int              default 50 (advisory; client honours)

Falls back to ``enabled=False`` when S3 isn't ready, when boto3 raises, or
when the master switch is off — callers should then use the legacy multipart
upload path.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional

from decouple import config as env_config
from django.conf import settings

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
    'enabled':              _b('NT_PRESIGNED_UPLOAD_ENABLED', 'True'),
    'url_expiry_seconds':   _i('NT_PRESIGNED_URL_EXPIRY', 3600),
    # 5 GiB — boto3 single PUT max. For larger files we'd need multipart S3.
    'max_bytes':            _i('NT_PRESIGNED_MAX_BYTES', 5 * 1024 * 1024 * 1024),
    'key_prefix':           env_config('NT_PRESIGNED_KEY_PREFIX',
                                       default='non-teff/uploads/'),
    'delete_after_ingest':  _b('NT_PRESIGNED_DELETE_AFTER_INGEST', 'True'),
    'download_chunk_bytes': _i('NT_PRESIGNED_DOWNLOAD_CHUNK', 8 * 1024 * 1024),
    'min_mb_advisory':      _i('NT_PRESIGNED_MIN_MB', 50),
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
    import re

    base = os.path.basename(name or 'upload')
    base = re.sub(r'[^A-Za-z0-9._\- ]+', '_', base).strip(' .') or 'upload'
    return base[:200]


# ─── Public API ────────────────────────────────────────────────────────────
def generate_presigned_put(
    *,
    filename: str,
    content_type: str = 'application/octet-stream',
    size_bytes: int = 0,
    batch_id: Optional[str] = None,
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
    # Namespace by batch so stray staged objects are easy to identify/clean.
    bid = (batch_id or 'unscoped').replace('/', '_')
    key = f"{PRESIGNED_UPLOAD_CONFIG['key_prefix']}{bid}/{uuid.uuid4()}/{safe_name}"
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
        logger.warning('[NonTeffPresign] presign failed: %s', exc)
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


def fetch_uploaded_to_path(
    *,
    s3_key: str,
    dest_path: str,
) -> int:
    """
    Stream the S3 object to ``dest_path`` (an absolute filesystem path).

    Returns the number of bytes written. Raises ``RuntimeError`` if the
    object can't be fetched or exceeds the configured size cap.
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
    if size > PRESIGNED_UPLOAD_CONFIG['max_bytes']:
        raise RuntimeError(
            f"uploaded size {size} exceeds cap {PRESIGNED_UPLOAD_CONFIG['max_bytes']}"
        )

    os.makedirs(os.path.dirname(dest_path) or '.', exist_ok=True)
    chunk = PRESIGNED_UPLOAD_CONFIG['download_chunk_bytes']
    # Stream straight into the final destination so we never hold the file
    # in memory — works for multi-GB inputs.
    tmp_path = dest_path + '.part'
    try:
        with open(tmp_path, 'wb') as fh:
            body = client.get_object(Bucket=bucket, Key=s3_key)['Body']
            while True:
                buf = body.read(chunk)
                if not buf:
                    break
                fh.write(buf)
        shutil.move(tmp_path, dest_path)
    except Exception:
        # Clean up the temp file on any failure.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise

    return size


def best_effort_delete(s3_key: str) -> None:
    """Delete the staged object — never raises."""
    if not s3_key or not is_presigned_upload_available():
        return
    if not PRESIGNED_UPLOAD_CONFIG['delete_after_ingest']:
        return
    try:
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        _build_s3_client().delete_object(Bucket=bucket, Key=s3_key)
    except Exception:  # noqa: BLE001
        logger.warning('[NonTeffPresign] best-effort delete failed for %s', s3_key, exc_info=True)
