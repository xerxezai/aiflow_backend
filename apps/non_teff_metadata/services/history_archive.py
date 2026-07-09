"""
Non-TEFF History Archive
------------------------

Additive service: persists every Non-TEFF extraction job to AWS S3 under a
role-based prefix so users (and admins) can re-open past extractions later.

Design notes
------------
* Best-effort & non-blocking — never raises into the request path.
* Works with or without S3: when AWS creds are absent, archival is a no-op
  and history simply falls back to the existing DB rows.
* Role resolution is soft-coded via ``ROLE_RESOLVERS`` so the same code
  works whether the user has an ``rbac_profile``, a Django group, or
  nothing at all.
* All S3 paths follow ``HISTORY_CONFIG['key_template']`` — change one
  string to re-shape the bucket layout.
* The DB ``NonTeffExtractionJob`` table is untouched; we just *also*
  upload the source file + result.json to S3.

Bucket layout (default)
-----------------------
    s3://<bucket>/non-teff/<role>/<user_id>/<job_id>/source.<ext>
    s3://<bucket>/non-teff/<role>/<user_id>/<job_id>/result.json
    s3://<bucket>/non-teff/<role>/<user_id>/<job_id>/manifest.json
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED CONFIG — every knob in one place
# ---------------------------------------------------------------------------
HISTORY_CONFIG: Dict[str, Any] = {
    # Master switch — flip to False to disable archival entirely without
    # ripping out the call sites.
    'enabled':         True,

    # Top-level prefix inside the bucket. Change to migrate a tenant.
    'root_prefix':     'non-teff',

    # Filenames inside each job folder. Tweak naming without touching code.
    'source_filename':   'source',          # extension is appended automatically
    'result_filename':   'result.json',
    'manifest_filename': 'manifest.json',

    # Role prefixes — soft-coded mapping from RBAC role code → folder name.
    # Anything not in here lands under 'staff'. Admins are merged for
    # cross-discipline lookup convenience.
    'role_folder_map': {
        'super_admin':  'admin',
        'admin':        'admin',
        'manager':      'manager',
        'engineer':     'engineer',
        'reviewer':     'reviewer',
        'viewer':       'viewer',
    },
    'fallback_role':   'staff',
    'guest_role':      'guest',

    # Whether non-admins can list/load other users' history. Default: NO.
    'cross_user_access_roles': {'super_admin', 'admin'},

    # Listing pagination & ordering.
    'list_default_limit': 50,
    'list_max_limit':     200,

    # Soft-coded mutation contract — which fields can be modified per kind.
    # Add/remove keys here to expose more attributes to the History UI.
    'mutable_fields': {
        'job':   ['file_name'],
        'batch': ['name', 'plant'],
    },
    # Map incoming JSON keys → DB column names per kind. Lets the frontend
    # use a uniform "name" alias regardless of the underlying entity.
    'field_aliases': {
        'job':   {'name': 'file_name', 'title': 'file_name'},
        'batch': {'title': 'name'},
    },
}


# ---------------------------------------------------------------------------
# Role resolution — soft-coded chain of resolvers
# ---------------------------------------------------------------------------
def _role_from_rbac(user) -> Optional[str]:
    """Try the RBAC profile (preferred — discipline-aware)."""
    try:
        prof = getattr(user, 'rbac_profile', None)
        if not prof:
            return None
        role = prof.roles.order_by('-level').first()
        return role.code if role else None
    except Exception:
        return None


def _role_from_groups(user) -> Optional[str]:
    """Fallback to Django groups."""
    try:
        g = user.groups.first()
        return g.name.lower().replace(' ', '_') if g else None
    except Exception:
        return None


def _role_from_flags(user) -> Optional[str]:
    """Last-resort flags (superuser/staff)."""
    if getattr(user, 'is_superuser', False):
        return 'super_admin'
    if getattr(user, 'is_staff', False):
        return 'admin'
    return None


# Order matters — first non-None wins.
ROLE_RESOLVERS = [_role_from_rbac, _role_from_groups, _role_from_flags]


def resolve_user_role(user) -> str:
    """Return the role-folder name for the given user (soft-coded mapping)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return HISTORY_CONFIG['guest_role']
    for resolver in ROLE_RESOLVERS:
        code = resolver(user)
        if code:
            return HISTORY_CONFIG['role_folder_map'].get(code, code)
    return HISTORY_CONFIG['fallback_role']


# ---------------------------------------------------------------------------
# S3 helpers — never raise on failure, just log and return falsy
# ---------------------------------------------------------------------------
def _get_s3():
    """Return (s3_client, bucket) or (None, None) if unavailable."""
    try:
        from django.conf import settings
        bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
        if not bucket:
            return None, None
        import boto3
        region = getattr(settings, 'AWS_S3_REGION_NAME', None) or os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
        return boto3.client('s3', region_name=region), bucket
    except Exception as exc:
        logger.warning('S3 client unavailable for Non-TEFF history: %s', exc)
        return None, None


def _job_prefix(role: str, user_id: Any, job_id: str) -> str:
    return f"{HISTORY_CONFIG['root_prefix']}/{role}/{user_id or 'anonymous'}/{job_id}"


def _put_object(s3, bucket: str, key: str, body, content_type: str = 'application/octet-stream',
                metadata: Optional[Dict[str, str]] = None) -> bool:
    try:
        extra = {'ContentType': content_type, 'ServerSideEncryption': 'AES256'}
        if metadata:
            extra['Metadata'] = {k: str(v)[:1000] for k, v in metadata.items()}
        s3.put_object(Bucket=bucket, Key=key, Body=body, **extra)
        return True
    except Exception as exc:
        logger.warning('S3 put failed for %s: %s', key, exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def archive_source(job, file_path: str, user) -> Optional[str]:
    """
    Upload the original source file to S3. Best-effort.
    Returns the S3 key on success, or None.
    """
    if not HISTORY_CONFIG['enabled']:
        return None
    s3, bucket = _get_s3()
    if not s3 or not bucket or not os.path.exists(file_path):
        return None

    role = resolve_user_role(user)
    ext  = os.path.splitext(file_path)[1].lower()
    key  = f"{_job_prefix(role, getattr(user, 'id', None), str(job.job_id))}/{HISTORY_CONFIG['source_filename']}{ext}"
    ctype, _ = mimetypes.guess_type(file_path)
    metadata = {
        'job_id':      str(job.job_id),
        'file_name':   job.file_name or '',
        'role':        role,
        'user_id':     str(getattr(user, 'id', '') or ''),
        'archived_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(file_path, 'rb') as fh:
            ok = _put_object(s3, bucket, key, fh, ctype or 'application/octet-stream', metadata)
        return key if ok else None
    except Exception as exc:
        logger.warning('Source archive failed for job %s: %s', job.job_id, exc)
        return None


def archive_result(job, user) -> Optional[str]:
    """
    Upload the extracted result_json + a small manifest. Best-effort.
    Returns the S3 key for result.json on success, or None.
    """
    if not HISTORY_CONFIG['enabled']:
        return None
    s3, bucket = _get_s3()
    if not s3 or not bucket:
        return None
    if not job.result_json:
        return None

    role        = resolve_user_role(user)
    base        = _job_prefix(role, getattr(user, 'id', None), str(job.job_id))
    result_key  = f"{base}/{HISTORY_CONFIG['result_filename']}"
    manifest_key = f"{base}/{HISTORY_CONFIG['manifest_filename']}"

    body = json.dumps(job.result_json, ensure_ascii=False, indent=2).encode('utf-8')
    ok = _put_object(s3, bucket, result_key, body, 'application/json', metadata={
        'job_id': str(job.job_id), 'role': role,
    })

    manifest = {
        'job_id':      str(job.job_id),
        'file_name':   job.file_name,
        'file_format': job.file_format,
        'status':      job.status,
        'role':        role,
        'user_id':     getattr(user, 'id', None),
        'created_at':  job.created_at.isoformat() if job.created_at else None,
        'archived_at': datetime.now(timezone.utc).isoformat(),
        'total_items': len((job.result_json or {}).get('items', [])),
    }
    _put_object(
        s3, bucket, manifest_key,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'),
        'application/json',
    )
    return result_key if ok else None


# ---------------------------------------------------------------------------
# Bulk-batch archival — mirrors archive_source/archive_result for batches.
# Uses the same path scheme so admins can grep one prefix for everything:
#
#   s3://<bucket>/non-teff/<role>/<user_id>/batch-<batch_id>/manifest.json
#   s3://<bucket>/non-teff/<role>/<user_id>/batch-<batch_id>/master_index.json
#   s3://<bucket>/non-teff/<role>/<user_id>/batch-<batch_id>/sources/<file>
#
# The 'batch-' prefix on the folder name lets list_history tell job vs batch
# entries apart without a separate listing call.
# ---------------------------------------------------------------------------
def _batch_prefix(role: str, user_id: Any, batch_id: str) -> str:
    return (f"{HISTORY_CONFIG['root_prefix']}/{role}/"
            f"{user_id or 'anonymous'}/batch-{batch_id}")


def archive_batch_source(batch, item, file_path: str) -> Optional[str]:
    """
    Upload ONE source file from a bulk batch item to S3. Best-effort.
    Returns the S3 key on success, or None.
    Called per-file during upload so failures don't accumulate.
    """
    if not HISTORY_CONFIG['enabled']:
        return None
    s3, bucket = _get_s3()
    if not s3 or not bucket or not file_path or not os.path.exists(file_path):
        return None

    user  = batch.created_by
    role  = resolve_user_role(user)
    # Preserve relative folder structure under sources/ so admins can browse.
    rel   = (item.relative_path or item.file_name or '').replace('\\', '/').lstrip('/')
    key   = f"{_batch_prefix(role, getattr(user, 'id', None), str(batch.batch_id))}/sources/{rel}"
    ctype, _ = mimetypes.guess_type(file_path)
    try:
        with open(file_path, 'rb') as fh:
            ok = _put_object(s3, bucket, key, fh,
                             ctype or 'application/octet-stream',
                             metadata={
                                 'batch_id':  str(batch.batch_id),
                                 'item_id':   str(item.item_id),
                                 'file_name': item.file_name or '',
                                 'role':      role,
                                 'user_id':   str(getattr(user, 'id', '') or ''),
                             })
        return key if ok else None
    except Exception as exc:
        logger.warning('Batch source archive failed (%s): %s',
                       item.file_name, exc)
        return None


def archive_batch_result(batch) -> Optional[str]:
    """
    Upload the aggregated master-index JSON + manifest for a completed
    batch. Best-effort. Returns the master_index.json key on success.
    Called once after extraction finishes.
    """
    if not HISTORY_CONFIG['enabled']:
        return None
    s3, bucket = _get_s3()
    if not s3 or not bucket:
        return None

    user  = batch.created_by
    role  = resolve_user_role(user)
    base  = _batch_prefix(role, getattr(user, 'id', None), str(batch.batch_id))

    # Aggregate every item.fields dict into the master-index payload. Same
    # shape the frontend canvas consumes — no extra core logic needed.
    items = list(batch.items.all().order_by('file_name'))
    payload = {
        'batch_id':    str(batch.batch_id),
        'name':        batch.name or '',
        'plant':       batch.plant or '',
        'status':      batch.status,
        'total_files': batch.total_files,
        'ready_files': batch.ready_files,
        'failed_files': batch.failed_files,
        'items':       [it.fields or {} for it in items],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    result_key = f"{base}/master_index.json"
    ok = _put_object(s3, bucket, result_key, body, 'application/json',
                     metadata={'batch_id': str(batch.batch_id), 'role': role})

    manifest = {
        'kind':         'batch',
        'batch_id':     str(batch.batch_id),
        'name':         batch.name or '',
        'plant':        batch.plant or '',
        'status':       batch.status,
        'role':         role,
        'user_id':      getattr(user, 'id', None),
        'total_files':  batch.total_files,
        'ready_files':  batch.ready_files,
        'failed_files': batch.failed_files,
        'created_at':   batch.created_at.isoformat() if batch.created_at else None,
        'archived_at':  datetime.now(timezone.utc).isoformat(),
    }
    _put_object(
        s3, bucket, f"{base}/manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'),
        'application/json',
    )
    return result_key if ok else None


# ---------------------------------------------------------------------------
# Diagnostics — lightweight read-only probe used by the History tab footer
# and the /history/diagnostics/ endpoint.
# ---------------------------------------------------------------------------
def get_archive_diagnostics() -> Dict[str, Any]:
    """Return a snapshot of S3 archival health (no writes)."""
    info: Dict[str, Any] = {
        'enabled':     bool(HISTORY_CONFIG.get('enabled')),
        'bucket':      '',
        'region':      '',
        'root_prefix': HISTORY_CONFIG.get('root_prefix', ''),
        'connected':   False,
        'reachable':   False,
        'error':       '',
    }
    try:
        from django.conf import settings
        info['bucket'] = (getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
                          or os.environ.get('AWS_STORAGE_BUCKET_NAME', ''))
        info['region'] = (getattr(settings, 'AWS_S3_REGION_NAME', '')
                          or os.environ.get('AWS_S3_REGION_NAME', ''))
    except Exception as exc:
        info['error'] = f'settings: {exc}'
        return info

    s3, bucket = _get_s3()
    info['connected'] = bool(s3 and bucket)
    if not info['connected']:
        info['error'] = info['error'] or 'S3 client not initialised'
        return info

    try:
        resp = s3.list_objects_v2(Bucket=bucket,
                                  Prefix=f"{HISTORY_CONFIG['root_prefix']}/",
                                  MaxKeys=1)
        info['reachable']    = True
        info['object_count'] = resp.get('KeyCount', 0)
    except Exception as exc:
        info['error'] = f'list: {exc}'
    return info


def list_history(user, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Return a list of past extractions visible to ``user`` — unified across
    both workflows:

      * Single-file extractions  → ``NonTeffExtractionJob``     (kind='job')
      * Bulk Master Index batches → ``NonTeffBatch`` + items    (kind='batch')

    Source of truth is the DB (fast). RBAC: non-admins only see their own.
    Results are merged & sorted by ``created_at`` (newest first).
    """
    from ..models import NonTeffExtractionJob, NonTeffBatch  # local — avoid cycles

    role = resolve_user_role(user)
    cross_ok = role in HISTORY_CONFIG['cross_user_access_roles']

    n = limit or HISTORY_CONFIG['list_default_limit']
    n = max(1, min(int(n), HISTORY_CONFIG['list_max_limit']))

    # ---- Single-file jobs ---------------------------------------------------
    job_qs = NonTeffExtractionJob.objects.all()
    if not cross_ok:
        job_qs = job_qs.filter(created_by=user)

    rows: List[Dict[str, Any]] = []
    for j in job_qs.order_by('-created_at')[:n]:
        j_role = resolve_user_role(j.created_by) if j.created_by else HISTORY_CONFIG['guest_role']
        rows.append({
            'kind':        'job',
            'job_id':      str(j.job_id),     # used by the frontend Re-open button
            'entry_id':    str(j.job_id),
            'file_name':   j.file_name,
            'file_format': j.file_format,
            'status':      j.status,
            'progress':    j.progress,
            'created_at':  j.created_at.isoformat() if j.created_at else None,
            'created_by':  getattr(j.created_by, 'username', '') if j.created_by_id else '',
            'total_items': len((j.result_json or {}).get('items', [])) if j.result_json else 0,
            'role_folder': j_role,
            's3_prefix':   _job_prefix(j_role, getattr(j.created_by, 'id', None), str(j.job_id)),
        })

    # ---- Bulk batches -------------------------------------------------------
    batch_qs = NonTeffBatch.objects.all()
    if not cross_ok:
        batch_qs = batch_qs.filter(created_by=user)

    for b in batch_qs.order_by('-created_at')[:n]:
        b_role = resolve_user_role(b.created_by) if b.created_by else HISTORY_CONFIG['guest_role']
        rows.append({
            'kind':        'batch',
            'job_id':      str(b.batch_id),   # frontend treats this opaquely
            'entry_id':    str(b.batch_id),
            'file_name':   b.name or f'Batch {str(b.batch_id)[:8]}',
            'file_format': 'batch',
            'status':      b.status,
            'progress':    100 if b.status == NonTeffBatch.BATCH_STATUS_READY else 0,
            'created_at':  b.created_at.isoformat() if b.created_at else None,
            'created_by':  getattr(b.created_by, 'username', '') if b.created_by_id else '',
            'total_items': b.ready_files or b.total_files or b.items.count(),
            'plant':       b.plant or '',
            'role_folder': b_role,
            's3_prefix':   _batch_prefix(b_role, getattr(b.created_by, 'id', None), str(b.batch_id)),
        })

    # Merge + sort + cap
    rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return rows[:n]


def _serialize_batch_items(batch) -> List[Dict[str, Any]]:
    """Flatten batch items into the same shape the Single-mode canvas
    expects: a list of ``fields`` dicts. ``file_name`` is injected so the
    user can tell rows apart in the table."""
    items = []
    for it in batch.items.all().order_by('file_name'):
        row = dict(it.fields or {})
        # Inject filename if not already present in the fields payload.
        row.setdefault('file_name', it.file_name)
        row.setdefault('document_file_name', it.file_name)
        row['__item_id'] = str(it.item_id)
        row['__status']  = it.status
        items.append(row)
    return items


def load_history(user, entry_id: str) -> Optional[Dict[str, Any]]:
    """
    Return the full payload for a past extraction so the user can re-open it
    without re-uploading. Looks up by either ``NonTeffExtractionJob.job_id``
    or ``NonTeffBatch.batch_id`` (transparent to the caller).
    """
    from ..models import NonTeffExtractionJob, NonTeffBatch

    role = resolve_user_role(user)
    cross_ok = role in HISTORY_CONFIG['cross_user_access_roles']

    # ── Try single-file job first ────────────────────────────────────────
    try:
        job = NonTeffExtractionJob.objects.get(pk=entry_id)
        if not cross_ok and job.created_by_id != getattr(user, 'id', None):
            return None
        return {
            'kind':        'job',
            'job_id':      str(job.job_id),
            'file_name':   job.file_name,
            'file_format': job.file_format,
            'status':      job.status,
            'created_at':  job.created_at.isoformat() if job.created_at else None,
            'result':      job.result_json or {},
            'source':      'db',
        }
    except (NonTeffExtractionJob.DoesNotExist, ValueError, Exception):
        pass

    # ── Then bulk batch ──────────────────────────────────────────────────
    try:
        batch = NonTeffBatch.objects.get(pk=entry_id)
        if not cross_ok and batch.created_by_id != getattr(user, 'id', None):
            return None
        items = _serialize_batch_items(batch)
        return {
            'kind':        'batch',
            'job_id':      str(batch.batch_id),
            'file_name':   batch.name,
            'file_format': 'batch',
            'status':      batch.status,
            'created_at':  batch.created_at.isoformat() if batch.created_at else None,
            'result':      {'items': items, 'total': len(items)},
            'source':      'db',
        }
    except (NonTeffBatch.DoesNotExist, ValueError, Exception):
        pass

    # ── S3 fallback (admins only) ────────────────────────────────────────
    if not cross_ok:
        return None
    s3, bucket = _get_s3()
    if not s3 or not bucket:
        return None
    try:
        prefix = f"{HISTORY_CONFIG['root_prefix']}/"
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
        for obj in resp.get('Contents', []) or []:
            if obj['Key'].endswith(f"/{entry_id}/{HISTORY_CONFIG['result_filename']}"):
                body = s3.get_object(Bucket=bucket, Key=obj['Key'])['Body'].read()
                return {
                    'kind':   'job',
                    'job_id': entry_id,
                    'result': json.loads(body),
                    'source': 's3',
                }
    except Exception as exc:
        logger.warning('S3 history fallback failed: %s', exc)
    return None


# ---------------------------------------------------------------------------
# Mutation helpers (Delete / Modify) — RBAC-aware, soft-coded fields
# ---------------------------------------------------------------------------
def _resolve_entity(user, entry_id: str):
    """Return (entity, kind, can_write) for the given id, or (None, None, False)."""
    from ..models import NonTeffExtractionJob, NonTeffBatch
    role = resolve_user_role(user)
    cross_ok = role in HISTORY_CONFIG['cross_user_access_roles']

    for kind, model in (('job', NonTeffExtractionJob), ('batch', NonTeffBatch)):
        try:
            ent = model.objects.get(pk=entry_id)
        except (model.DoesNotExist, ValueError, Exception):
            continue
        owner_id = getattr(ent, 'created_by_id', None)
        is_owner = owner_id == getattr(user, 'id', None) and owner_id is not None
        can_write = bool(cross_ok or is_owner)
        return ent, kind, can_write
    return None, None, False


def delete_history(user, entry_id: str):
    """Delete a history entry. Returns ``(ok, error)``."""
    ent, kind, can_write = _resolve_entity(user, entry_id)
    if ent is None:
        return False, 'not_found'
    if not can_write:
        return False, 'forbidden'
    try:
        ent.delete()
    except Exception as exc:
        logger.warning('History delete failed for %s: %s', entry_id, exc)
        return False, 'delete_failed'
    return True, None


def update_history(user, entry_id: str, payload: dict):
    """
    Modify a history entry. Only fields listed in
    ``HISTORY_CONFIG['mutable_fields'][kind]`` are honoured; everything
    else is silently ignored. Returns ``(ok, error, data)``.
    """
    ent, kind, can_write = _resolve_entity(user, entry_id)
    if ent is None:
        return False, 'not_found', None
    if not can_write:
        return False, 'forbidden', None

    allowed = set(HISTORY_CONFIG['mutable_fields'].get(kind, []))
    aliases = HISTORY_CONFIG['field_aliases'].get(kind, {})

    changed = []
    for raw_key, value in (payload or {}).items():
        target = aliases.get(raw_key, raw_key)
        if target not in allowed:
            continue
        if value is None:
            continue
        # Cap string lengths to a sane bound to avoid abuse.
        if isinstance(value, str):
            value = value.strip()[:500]
        setattr(ent, target, value)
        changed.append(target)

    if not changed:
        return False, 'no_valid_fields', None

    try:
        ent.save(update_fields=changed + ['updated_at']) if hasattr(ent, 'updated_at') else ent.save()
    except Exception as exc:
        logger.warning('History update failed for %s: %s', entry_id, exc)
        return False, 'update_failed', None

    data = {
        'kind':        kind,
        'entry_id':    str(entry_id),
        'file_name':   getattr(ent, 'file_name', None) or getattr(ent, 'name', None),
        'plant':       getattr(ent, 'plant', None),
        'updated':     changed,
    }
    return True, None, data
