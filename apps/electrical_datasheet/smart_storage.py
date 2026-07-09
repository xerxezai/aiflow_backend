"""
S3 + persistence helpers for the Smart Electrical Datasheet generator.

Centralises every "where do bytes go and where do rows live" decision so the
view layer stays thin. All thresholds, prefixes, and TTLs are soft-coded
constants below — never inline magic numbers.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from decouple import config
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─── Soft-coded constants ────────────────────────────────────────────────────
S3_BUCKET            = config('AWS_STORAGE_BUCKET_NAME', default='user-management-rejlers')
# Region default reflects the production bucket location (UAE Central). Override
# via env AWS_S3_REGION_NAME if the bucket is moved. SigV4 + region-specific
# endpoint are required for opt-in regions like me-central-1 — otherwise
# presigned URLs return IllegalLocationConstraintException.
S3_REGION            = config('AWS_S3_REGION_NAME',     default='me-central-1')
S3_ENDPOINT_URL      = config('AWS_S3_ENDPOINT_URL',    default=f'https://s3.{S3_REGION}.amazonaws.com')
S3_SIGNATURE_VERSION = config('AWS_S3_SIGNATURE_VERSION', default='s3v4')
S3_ADDRESSING_STYLE  = config('AWS_S3_ADDRESSING_STYLE',  default='virtual')
S3_PREFIX            = 'electrical-datasheets'
PRESIGN_TTL_SECONDS  = 3600                          # 1 hour
KEY_TEMPLATE         = '{prefix}/{user}/{datasheet}/{role}/{filename}'
ARTIFACT_TEMPLATE    = '{prefix}/{user}/{datasheet}/artifacts/{kind}_{ts}.{ext}'
EDITABLE_COLUMNS     = ('required_data', 'vendor_data', 'rev')  # server-enforced edit allowlist
EXCEL_REGEN_DELAY_S  = 60                            # coalescing window for Celery regen
SNAPSHOT_DEBOUNCE_M  = 30                            # auto-snapshot interval
SHARE_LINK_TTL_DAYS  = 30


# ─── S3 client ───────────────────────────────────────────────────────────────
class _SmartStorage:
    def __init__(self):
        try:
            self.client = boto3.client(
                's3',
                aws_access_key_id     = config('AWS_ACCESS_KEY_ID',     default=''),
                aws_secret_access_key = config('AWS_SECRET_ACCESS_KEY', default=''),
                region_name           = S3_REGION,
                endpoint_url          = S3_ENDPOINT_URL,
                config                = BotoConfig(
                    signature_version = S3_SIGNATURE_VERSION,
                    s3                = {'addressing_style': S3_ADDRESSING_STYLE},
                ),
            )
        except Exception as exc:
            logger.error(f"[smart_storage] boto3 init failed: {exc}")
            self.client = None

    def is_enabled(self) -> bool:
        return (
            self.client is not None
            and config('AWS_ACCESS_KEY_ID',     default='') != ''
            and config('AWS_SECRET_ACCESS_KEY', default='') != ''
        )

    # ── upload ──────────────────────────────────────────────────────────
    def upload_source_file(self, *, user_id, datasheet_id, role, file_obj, filename, content_type='application/octet-stream') -> Optional[str]:
        """Upload a user-uploaded source file. Returns the S3 key or None."""
        if not self.is_enabled():
            logger.warning("[smart_storage] S3 disabled — skipping source upload")
            return None
        key = KEY_TEMPLATE.format(prefix=S3_PREFIX, user=user_id, datasheet=datasheet_id, role=role, filename=filename)
        try:
            try:
                file_obj.seek(0)
            except Exception:
                pass
            self.client.upload_fileobj(
                file_obj, S3_BUCKET, key,
                ExtraArgs={
                    'ContentType': content_type,
                    'Metadata': {'role': role, 'uploaded-at': timezone.now().isoformat()},
                },
            )
            return key
        except ClientError as exc:
            logger.error(f"[smart_storage] upload_source_file failed: {exc}")
            return None

    def upload_artifact(self, *, user_id, datasheet_id, kind, content_bytes: bytes, ext='xlsx', content_type=None) -> Optional[str]:
        """Upload a generated artifact (excel/pdf). Returns the S3 key or None."""
        if not self.is_enabled():
            logger.warning("[smart_storage] S3 disabled — skipping artifact upload")
            return None
        ts  = timezone.now().strftime('%Y%m%dT%H%M%S')
        key = ARTIFACT_TEMPLATE.format(prefix=S3_PREFIX, user=user_id, datasheet=datasheet_id, kind=kind, ts=ts, ext=ext)
        ct  = content_type or {
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'pdf':  'application/pdf',
        }.get(ext, 'application/octet-stream')
        try:
            self.client.put_object(Bucket=S3_BUCKET, Key=key, Body=content_bytes, ContentType=ct)
            return key
        except ClientError as exc:
            logger.error(f"[smart_storage] upload_artifact failed: {exc}")
            return None

    # ── download ────────────────────────────────────────────────────────
    def download_to_bytes(self, s3_key) -> Optional[bytes]:
        if not self.is_enabled() or not s3_key:
            return None
        try:
            obj = self.client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            return obj['Body'].read()
        except ClientError as exc:
            logger.error(f"[smart_storage] download_to_bytes failed: {exc}")
            return None

    def download_to_tempfile(self, s3_key, suffix='') -> Optional[str]:
        data = self.download_to_bytes(s3_key)
        if data is None:
            return None
        fd, path = tempfile.mkstemp(suffix=suffix or os.path.splitext(s3_key)[1])
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        return path

    # ── presign ─────────────────────────────────────────────────────────
    def presigned_url(self, s3_key, expires=PRESIGN_TTL_SECONDS) -> Optional[str]:
        if not self.is_enabled() or not s3_key:
            return None
        try:
            return self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': s3_key},
                ExpiresIn=expires,
            )
        except ClientError as exc:
            logger.error(f"[smart_storage] presigned_url failed: {exc}")
            return None

    def delete(self, s3_key) -> bool:
        if not self.is_enabled() or not s3_key:
            return False
        try:
            self.client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
            return True
        except ClientError:
            return False


smart_storage = _SmartStorage()


# ─── Persistence orchestration ───────────────────────────────────────────────
def persist_generation(
    *,
    user,
    equipment_type: str,
    rows: List[Dict[str, Any]],
    summary: Dict[str, Any],
    metadata: Dict[str, Any],
    source_files: List[Dict[str, Any]],   # [{'role':..., 'file':<UploadedFile>}]
    excel_bytes: Optional[bytes] = None,
    title: str = '',
    variant: str = 'default',
):
    """Create a `GeneratedDatasheet` row, upload source files + Excel to S3.

    Best-effort: a failure in S3 does NOT block creation. Returns the saved
    instance plus a presigned `excel_url` (or None if S3 is disabled).
    """
    # local import to avoid circular at module load
    from .models import GeneratedDatasheet

    ds = GeneratedDatasheet.objects.create(
        user           = user,
        equipment_type = equipment_type,
        variant        = variant or 'default',
        title          = title,
        rows           = rows,
        summary        = summary,
        metadata       = metadata,
    )

    saved_sources: List[Dict[str, Any]] = []
    for entry in source_files:
        f = entry.get('file')
        role = entry.get('role') or 'source'
        if f is None:
            continue
        key = smart_storage.upload_source_file(
            user_id      = user.id,
            datasheet_id = ds.id,
            role         = role,
            file_obj     = f,
            filename     = f.name,
            content_type = getattr(f, 'content_type', '') or 'application/octet-stream',
        )
        saved_sources.append({
            'role':         role,
            'filename':     f.name,
            'size':         getattr(f, 'size', None),
            'content_type': getattr(f, 'content_type', '') or '',
            's3_key':       key,
        })

    excel_key = None
    if excel_bytes:
        excel_key = smart_storage.upload_artifact(
            user_id      = user.id,
            datasheet_id = ds.id,
            kind         = 'excel',
            content_bytes= excel_bytes,
            ext          = 'xlsx',
        )

    ds.source_files = saved_sources
    if excel_key:
        ds.excel_s3_key = excel_key
    ds.save(update_fields=['source_files', 'excel_s3_key'])

    return ds, smart_storage.presigned_url(excel_key) if excel_key else None


def serialize_summary(ds) -> Dict[str, Any]:
    """List-view shape (no rows)."""
    return {
        'id':              str(ds.id),
        'equipment_type':  ds.equipment_type,
        'variant':         ds.variant,
        'title':           ds.title,
        'revision':        ds.revision,
        'status':          ds.status,
        'is_archived':     ds.is_archived,
        'summary':         ds.summary or {},
        'created_at':      ds.created_at.isoformat() if ds.created_at else None,
        'updated_at':      ds.updated_at.isoformat() if ds.updated_at else None,
        'has_excel':       bool(ds.excel_s3_key),
        'has_pdf':         bool(ds.pdf_s3_key),
        'source_count':    len(ds.source_files or []),
        'original_filename': (ds.metadata or {}).get('original_filename', ''),
    }


def serialize_detail(ds) -> Dict[str, Any]:
    """Detail view — rows + presigned URLs."""
    base = serialize_summary(ds)
    base.update({
        'rows':         ds.rows or [],
        'metadata':     ds.metadata or {},
        'source_files': [
            {**sf, 'url': smart_storage.presigned_url(sf.get('s3_key'))}
            for sf in (ds.source_files or [])
        ],
        'excel_url':    smart_storage.presigned_url(ds.excel_s3_key),
        'pdf_url':      smart_storage.presigned_url(ds.pdf_s3_key) if ds.pdf_s3_key else None,
    })
    return base
