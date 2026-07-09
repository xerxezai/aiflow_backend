"""
Workbook Storage Service
========================

Smart AWS S3 storage for massive workbook datasets with soft-coded configuration.
Automatically decides between database and S3 storage based on data size.

Features:
- Auto-save large workbooks to S3 (reduces database load)
- Batch save operations (performance optimization)
- Versioning and snapshot management
- Row-level delete with cascade cleanup
- Soft-coded thresholds and configuration
"""
import json
import logging
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.cache import cache

from .models import WorkbookCellOverride, PaperSpecExtractionJob

logger = logging.getLogger(__name__)


# ─── Soft-coded configuration ────────────────────────────────────────────────
WORKBOOK_STORAGE_CONFIG = {
    # Storage strategy thresholds
    'storage': {
        'use_s3_above_cells': 5000,           # Use S3 for workbooks > 5000 cells
        'use_s3_above_mb': 10,                # Use S3 for workbooks > 10MB JSON
        'batch_save_threshold': 100,          # Batch save if >= 100 cells
        's3_compression': True,               # Gzip before S3 upload
        'enable_versioning': True,            # Keep S3 version history
    },
    
    # S3 bucket configuration
    's3': {
        'bucket_name': settings.AWS_STORAGE_BUCKET_NAME,
        'prefix': 'spec-customization/workbooks/',
        'public_read': False,
        'server_side_encryption': 'AES256',
        'storage_class': 'STANDARD_IA',       # Infrequent Access (cost-optimized)
    },
    
    # Cache configuration
    'cache': {
        'ttl_seconds': 1800,                  # 30 minutes
        'key_prefix': 'workbook_snapshot:',
    },
    
    # Auto-save configuration
    'autosave': {
        'enabled': True,
        'debounce_ms': 500,                   # Wait 500ms before saving
        'max_retries': 3,
        'retry_delay_ms': 1000,
    },
    
    # Row operations
    'row_operations': {
        'enable_delete': True,
        'enable_bulk_delete': True,
        'max_bulk_delete': 1000,              # Max rows per bulk delete
        'soft_delete': False,                  # Hard delete (save storage)
    },
}


# ─── S3 Client ────────────────────────────────────────────────────────────────
def _get_s3_client():
    """Get configured S3 client."""
    return boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )


# ─── Storage decision logic ───────────────────────────────────────────────────
def should_use_s3(cell_count: int, data_size_bytes: int) -> bool:
    """Determine if workbook should be stored in S3 based on size."""
    cfg = WORKBOOK_STORAGE_CONFIG['storage']
    
    if cell_count >= cfg['use_s3_above_cells']:
        return True
    
    size_mb = data_size_bytes / (1024 * 1024)
    if size_mb >= cfg['use_s3_above_mb']:
        return True
    
    return False


# ─── S3 Storage Operations ───────────────────────────────────────────────────
def save_workbook_to_s3(
    job_id: str,
    workbook: str,
    data: dict,
    version: Optional[str] = None
) -> dict:
    """
    Save workbook snapshot to S3.
    
    Returns:
        {
            's3_key': 'spec-customization/workbooks/...',
            'version_id': '...',
            'size_bytes': 12345,
            'compressed': True
        }
    """
    cfg = WORKBOOK_STORAGE_CONFIG['s3']
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    version_suffix = f"_v{version}" if version else ""
    
    s3_key = f"{cfg['prefix']}{job_id}/{workbook}_{timestamp}{version_suffix}.json"
    
    # Serialize
    json_data = json.dumps(data, indent=None, ensure_ascii=False)
    data_bytes = json_data.encode('utf-8')
    
    # Optionally compress
    if WORKBOOK_STORAGE_CONFIG['storage']['s3_compression']:
        import gzip
        data_bytes = gzip.compress(data_bytes)
        content_encoding = 'gzip'
    else:
        content_encoding = None
    
    try:
        s3_client = _get_s3_client()
        
        extra_args = {
            'ContentType': 'application/json',
            'ServerSideEncryption': cfg['server_side_encryption'],
            'StorageClass': cfg['storage_class'],
        }
        
        if content_encoding:
            extra_args['ContentEncoding'] = content_encoding
        
        if not cfg['public_read']:
            extra_args['ACL'] = 'private'
        
        response = s3_client.put_object(
            Bucket=cfg['bucket_name'],
            Key=s3_key,
            Body=data_bytes,
            **extra_args
        )
        
        logger.info(
            f"[WorkbookStorage] Saved to S3: {s3_key} ({len(data_bytes)} bytes)"
        )
        
        return {
            's3_key': s3_key,
            'version_id': response.get('VersionId'),
            'size_bytes': len(data_bytes),
            'compressed': bool(content_encoding),
        }
        
    except ClientError as e:
        logger.exception(f"[WorkbookStorage] S3 upload failed: {e}")
        raise


def load_workbook_from_s3(s3_key: str) -> dict:
    """Load workbook snapshot from S3."""
    cfg = WORKBOOK_STORAGE_CONFIG['s3']
    
    try:
        s3_client = _get_s3_client()
        response = s3_client.get_object(
            Bucket=cfg['bucket_name'],
            Key=s3_key
        )
        
        data_bytes = response['Body'].read()
        
        # Decompress if needed
        if response.get('ContentEncoding') == 'gzip':
            import gzip
            data_bytes = gzip.decompress(data_bytes)
        
        json_data = data_bytes.decode('utf-8')
        return json.loads(json_data)
        
    except ClientError as e:
        logger.exception(f"[WorkbookStorage] S3 download failed: {e}")
        raise


# ─── Batch save operations ───────────────────────────────────────────────────
def batch_save_cells(job: PaperSpecExtractionJob, cells: list[dict], user=None) -> dict:
    """
    Batch save multiple cell overrides.
    
    Args:
        cells: List of dicts with keys: workbook, sheet_name, row_key, column_name, value
    
    Returns:
        {
            'saved_count': 123,
            'created': 45,
            'updated': 78,
            's3_snapshot': {...} or None
        }
    """
    created_count = 0
    updated_count = 0
    
    for cell in cells:
        obj, created = WorkbookCellOverride.objects.update_or_create(
            job=job,
            workbook=cell['workbook'],
            sheet_name=cell['sheet_name'],
            row_key=cell['row_key'],
            column_name=cell['column_name'],
            defaults={
                'value': cell.get('value', ''),
                'edited_by': user,
            }
        )
        
        if created:
            created_count += 1
        else:
            updated_count += 1
    
    # Check if we should snapshot to S3
    total_overrides = WorkbookCellOverride.objects.filter(job=job).count()
    
    s3_snapshot = None
    if total_overrides >= WORKBOOK_STORAGE_CONFIG['storage']['use_s3_above_cells']:
        try:
            # Build snapshot
            snapshot_data = {
                'job_id': str(job.id),
                'total_overrides': total_overrides,
                'timestamp': datetime.utcnow().isoformat(),
                'overrides': list(
                    WorkbookCellOverride.objects.filter(job=job).values(
                        'workbook', 'sheet_name', 'row_key', 'column_name', 'value'
                    )
                )
            }
            
            s3_snapshot = save_workbook_to_s3(
                str(job.id),
                'all_overrides',
                snapshot_data
            )
        except Exception as e:
            logger.exception(f"[WorkbookStorage] Snapshot to S3 failed: {e}")
    
    return {
        'saved_count': len(cells),
        'created': created_count,
        'updated': updated_count,
        's3_snapshot': s3_snapshot,
    }


# ─── Row-level delete operations ─────────────────────────────────────────────
def delete_row(
    job: PaperSpecExtractionJob,
    workbook: str,
    sheet_name: str,
    row_key: str
) -> dict:
    """
    Delete all cell overrides for a specific row.
    
    Returns:
        {
            'deleted_count': 12,
            'row_key': '...',
            'columns_deleted': ['Col1', 'Col2', ...]
        }
    """
    if not WORKBOOK_STORAGE_CONFIG['row_operations']['enable_delete']:
        raise ValueError("Row delete is disabled in configuration")
    
    overrides = WorkbookCellOverride.objects.filter(
        job=job,
        workbook=workbook,
        sheet_name=sheet_name,
        row_key=row_key
    )
    
    columns_deleted = list(overrides.values_list('column_name', flat=True))
    deleted_count, _ = overrides.delete()
    
    logger.info(
        f"[WorkbookStorage] Deleted row: {workbook}/{sheet_name}/{row_key} "
        f"({deleted_count} cells)"
    )
    
    return {
        'deleted_count': deleted_count,
        'row_key': row_key,
        'columns_deleted': columns_deleted,
    }


def bulk_delete_rows(
    job: PaperSpecExtractionJob,
    workbook: str,
    sheet_name: str,
    row_keys: list[str]
) -> dict:
    """
    Delete multiple rows at once.
    
    Returns:
        {
            'deleted_rows': 45,
            'deleted_cells': 234,
            'row_keys': [...]
        }
    """
    cfg = WORKBOOK_STORAGE_CONFIG['row_operations']
    
    if not cfg['enable_bulk_delete']:
        raise ValueError("Bulk delete is disabled in configuration")
    
    if len(row_keys) > cfg['max_bulk_delete']:
        raise ValueError(
            f"Bulk delete limited to {cfg['max_bulk_delete']} rows "
            f"(requested: {len(row_keys)})"
        )
    
    deleted_count, _ = WorkbookCellOverride.objects.filter(
        job=job,
        workbook=workbook,
        sheet_name=sheet_name,
        row_key__in=row_keys
    ).delete()
    
    logger.info(
        f"[WorkbookStorage] Bulk deleted {len(row_keys)} rows "
        f"({deleted_count} cells) from {workbook}/{sheet_name}"
    )
    
    return {
        'deleted_rows': len(row_keys),
        'deleted_cells': deleted_count,
        'row_keys': row_keys,
    }


# ─── Cache helpers ────────────────────────────────────────────────────────────
def cache_workbook_snapshot(job_id: str, workbook: str, data: dict):
    """Cache workbook snapshot for faster repeated loads."""
    cfg = WORKBOOK_STORAGE_CONFIG['cache']
    cache_key = f"{cfg['key_prefix']}{job_id}:{workbook}"
    cache.set(cache_key, data, cfg['ttl_seconds'])


def get_cached_workbook_snapshot(job_id: str, workbook: str) -> Optional[dict]:
    """Retrieve cached workbook snapshot."""
    cfg = WORKBOOK_STORAGE_CONFIG['cache']
    cache_key = f"{cfg['key_prefix']}{job_id}:{workbook}"
    return cache.get(cache_key)


def invalidate_workbook_cache(job_id: str, workbook: str):
    """Invalidate cached workbook snapshot after edits."""
    cfg = WORKBOOK_STORAGE_CONFIG['cache']
    cache_key = f"{cfg['key_prefix']}{job_id}:{workbook}"
    cache.delete(cache_key)
