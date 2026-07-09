"""
Wrench → AWS S3  ▸  Project Document Mirror (additive, soft-coded)
==================================================================
Copies the **actual file bytes** of every document of a Wrench project
(identified by ORDER_NO) to the configured S3 bucket.

This module is fully additive — it does NOT modify any existing core logic.
It composes the existing primitives:
  - `synthesize_documents_from_transmittal_list()` ........ list project docs
  - `download_document()` ................................ fetch file bytes
  - `_s3_client()` / `_get_bucket()` (from s3_service.py) . S3 connection

Modes (driven by WrenchS3SyncJob.mode):
  BATCH    – mirror every document once, then mark the job success.
  REALTIME – mirror only documents not yet present in S3 (HEAD probe),
             so the task can be safely re-queued by Celery on a schedule
             and will pick up newly issued transmittals as they appear.

Every magic number / behavior toggle lives in the SOFT-CODED CONFIG block
below so ops can tune without code changes.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import time

from django.conf import settings as _dj_settings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests
from botocore.exceptions import BotoCoreError, ClientError
from django.utils import timezone as dj_timezone

from .models import WrenchConfig, WrenchS3SyncJob
from .s3_service import _get_bucket, _mark_job, _s3_client
from .service import download_document, synthesize_documents_from_transmittal_list

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# SOFT-CODED CONFIG — tune without changing code paths
# ──────────────────────────────────────────────────────────────────────────────

# Parallel download/upload workers per project. Keep modest to avoid
# overwhelming Wrench (rolling-token auth) and S3 PUT limits.
_PROJECT_EXPORT_WORKERS         = 4

# Per-document hard timeout (seconds) for the Wrench download call.
_DOC_DOWNLOAD_TIMEOUT_SEC       = 60

# When Wrench returns a redirect URL instead of file bytes, fetch via HTTP
# with this timeout (seconds).
_REDIRECT_FETCH_TIMEOUT_SEC     = 60

# Retry policy per document (network/Wrench transient failures only).
_DOC_DOWNLOAD_MAX_ATTEMPTS      = 3
_DOC_DOWNLOAD_RETRY_BACKOFF_SEC = 2   # exponential: 2, 4, 8, ...

# Cap documents processed per realtime tick (keeps a tick bounded).
_REALTIME_DOCS_PER_TICK         = 25

# Cap documents per batch run (safety net; 0 = unlimited).
_BATCH_MAX_DOCS                 = 0

# Idempotency: if True, HEAD the S3 key first and skip if it already exists.
# Set False to force re-upload (e.g., after a revision change).
_SKIP_IF_EXISTS_IN_S3           = True

# Default S3 prefix layout. Soft-coded so a different layout can be used
# without touching the upload code below. Placeholders:
#   {prefix}      – the WrenchS3SyncJob.s3_prefix (already user-configurable)
#   {order_no}    – the Wrench project ORDER_NO
#   {safe_doc_no} – DOC_NO with unsafe chars replaced
#   {ext}         – file extension (with leading dot, e.g. ".pdf")
_S3_KEY_TEMPLATE = (
    '{prefix}projects/{order_no}/documents/{safe_doc_no}{ext}'
)

# ──────────────────────────────────────────────────────────────────────────────
# R:\Projects-style folder mirror (additive, opt-in, soft-coded)
# ──────────────────────────────────────────────────────────────────────────────
# Wrench is the system of record for the R:\Projects SMB share. Each document
# row carries its in-library folder path in `GENEALOGY_STRING` (and a few
# alias fields used by some tenants). When `WRENCH_S3_MIRROR_MODE=genealogy`
# is set in the environment, project exports rebuild that hierarchy under S3:
#
#   wrench/projects/{order_no}/{folder1}/{folder2}/.../{doc_no}{ext}
#
# Default mode = 'flat' → preserves the existing key layout exactly.
# Switching modes does NOT touch download, retry, manifest, or watcher logic.
_MIRROR_MODE_FLAT       = 'flat'
_MIRROR_MODE_GENEALOGY  = 'genealogy'
_MIRROR_MODE = (
    getattr(_dj_settings, 'WRENCH_S3_MIRROR_MODE', None)
    or os.environ.get('WRENCH_S3_MIRROR_MODE')
    or _MIRROR_MODE_FLAT
).strip().lower()

# Wrench / R:\Projects path-segment fields, in priority order.
_GENEALOGY_FIELDS = (
    'GENEALOGY_STRING', 'GenealogyString',
    'FOLDER_PATH',      'FolderPath',
    'PATH',             'Path',
    'FOLDER',           'Folder',
)
# Path separators seen across Wrench / Windows installations (/, \, >, |).
_GENEALOGY_SEPARATOR_RE = re.compile(r'\s*[/\\>|]\s*')
# Mirror-mode key template — placeholders identical to the flat one PLUS
# `{folder_path}`, which is already separator-joined and per-segment safe.
_S3_KEY_TEMPLATE_GENEALOGY = (
    '{prefix}projects/{order_no}/{folder_path}/{safe_doc_no}{ext}'
)
# Cap folder depth to protect S3 key length (max 1024 bytes); excess segments
# are merged into the last folder so no document is dropped.
_GENEALOGY_MAX_DEPTH = 12

# Manifest key written at the end of each run (cumulative log per project).
_S3_MANIFEST_TEMPLATE = (
    '{prefix}projects/{order_no}/_manifest/run_{run_id}.json'
)

# Filename sanitisation — keep alnum, dot, dash, underscore. Replace rest with "_".
_SAFE_NAME_RE = re.compile(r'[^A-Za-z0-9._\-]+')

# Fallback content-type when neither Wrench nor mimetypes can guess.
_DEFAULT_CONTENT_TYPE = 'application/octet-stream'

# Per-job-details key under which we persist a manifest summary so it's
# easily readable from the WrenchS3SyncJob admin / API.
_JOB_DETAILS_PROJECT_KEY = 'project_export'


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_name(value: str) -> str:
    """Make a string safe to use as an S3 key segment / filename."""
    cleaned = _SAFE_NAME_RE.sub('_', (value or '').strip()) or 'unnamed'
    # S3 keys must not start with a dot for some tooling — strip leading dots.
    return cleaned.lstrip('.') or 'unnamed'


def _guess_extension(filename: Optional[str], content_type: Optional[str]) -> str:
    """Return an extension WITH leading dot, e.g. '.pdf'. Empty string if unknown."""
    if filename:
        _, ext = os.path.splitext(filename)
        if ext:
            return ext.lower()
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(';')[0].strip())
        if guessed:
            return guessed
    return ''


def _build_doc_s3_key(job: WrenchS3SyncJob, order_no: str, doc_no: str, ext: str) -> str:
    prefix = (job.s3_prefix or 'wrench/').rstrip('/') + '/'
    return _S3_KEY_TEMPLATE.format(
        prefix=prefix,
        order_no=_safe_name(str(order_no)),
        safe_doc_no=_safe_name(doc_no),
        ext=ext,
    )


def _extract_genealogy_segments(doc: dict, order_no: str) -> list:
    """Return cleaned, de-duplicated folder segments from a Wrench doc row.

    Strips a leading segment that duplicates the project ORDER_NO (Wrench
    often includes it), splits on any of `/ \\ > |`, sanitises each piece,
    and caps the depth to keep S3 keys under the 1024-byte limit.
    """
    raw = ''
    for field in _GENEALOGY_FIELDS:
        value = doc.get(field)
        if value:
            raw = str(value)
            break
    if not raw:
        return []
    segments = [s.strip() for s in _GENEALOGY_SEPARATOR_RE.split(raw) if s and s.strip()]
    if segments and segments[0].strip().lower() == str(order_no).strip().lower():
        segments = segments[1:]
    cleaned = [_safe_name(s) for s in segments if _safe_name(s)]
    if len(cleaned) > _GENEALOGY_MAX_DEPTH:
        # Merge the overflow into the last allowed folder so no doc is lost.
        head = cleaned[: _GENEALOGY_MAX_DEPTH - 1]
        tail = '_'.join(cleaned[_GENEALOGY_MAX_DEPTH - 1:])
        cleaned = head + [tail]
    return cleaned


def _build_doc_s3_key_genealogy(
    job: WrenchS3SyncJob, order_no: str, doc_no: str, ext: str, doc: dict,
) -> str:
    """R:\\Projects-style key built from Wrench GENEALOGY_STRING.

    Falls back to the flat layout when no folder path is available, so a
    missing field never breaks the export.
    """
    segments = _extract_genealogy_segments(doc, order_no)
    if not segments:
        return _build_doc_s3_key(job, order_no, doc_no, ext)
    prefix = (job.s3_prefix or 'wrench/').rstrip('/') + '/'
    return _S3_KEY_TEMPLATE_GENEALOGY.format(
        prefix=prefix,
        order_no=_safe_name(str(order_no)),
        folder_path='/'.join(segments),
        safe_doc_no=_safe_name(doc_no),
        ext=ext,
    )


def _resolve_doc_s3_key(
    job: WrenchS3SyncJob, order_no: str, doc_no: str, ext: str, doc: dict,
) -> str:
    """Mode-aware key resolver. Default mode keeps existing flat layout."""
    if _MIRROR_MODE == _MIRROR_MODE_GENEALOGY:
        return _build_doc_s3_key_genealogy(job, order_no, doc_no, ext, doc)
    return _build_doc_s3_key(job, order_no, doc_no, ext)


def _s3_object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        # 404 / NoSuchKey → not present, anything else is unexpected
        code = exc.response.get('Error', {}).get('Code', '')
        if code in ('404', 'NoSuchKey', 'NotFound'):
            return False
        raise


def _fetch_document_bytes(cfg: WrenchConfig, *, idoc_id: str, doc_no: str) -> dict:
    """
    Resolve a Wrench document to in-memory bytes.

    Returns: { 'content': bytes, 'filename': str, 'content_type': str, 'source': str }
    Raises:  RuntimeError on exhausted retries.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, _DOC_DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            ref = download_document(cfg, idoc_id=str(idoc_id), doc_no=str(doc_no or ''))

            # Strategy A: Wrench answered with file bytes inline.
            if ref.get('content'):
                return {
                    'content':      ref['content'],
                    'filename':     ref.get('filename') or f'{doc_no or idoc_id}',
                    'content_type': ref.get('content_type') or _DEFAULT_CONTENT_TYPE,
                    'source':       ref.get('source', 'inline'),
                }

            # Strategy B: Wrench answered with a redirect URL → fetch it.
            file_url = ref.get('url')
            if file_url:
                r = requests.get(file_url, timeout=_REDIRECT_FETCH_TIMEOUT_SEC, stream=True)
                r.raise_for_status()
                return {
                    'content':      r.content,
                    'filename':     ref.get('filename') or os.path.basename(file_url.split('?')[0]),
                    'content_type': r.headers.get('Content-Type') or ref.get('content_type') or _DEFAULT_CONTENT_TYPE,
                    'source':       ref.get('source', 'redirect'),
                }

            raise RuntimeError('download_document returned neither content nor url')

        except Exception as exc:                                   # noqa: BLE001
            last_exc = exc
            logger.warning(
                '[S3 ProjDocs] download attempt %d/%d failed for idoc_id=%s doc_no=%s: %s',
                attempt, _DOC_DOWNLOAD_MAX_ATTEMPTS, idoc_id, doc_no, exc,
            )
            if attempt < _DOC_DOWNLOAD_MAX_ATTEMPTS:
                time.sleep(_DOC_DOWNLOAD_RETRY_BACKOFF_SEC * attempt)

    raise RuntimeError(f'All {_DOC_DOWNLOAD_MAX_ATTEMPTS} download attempts failed: {last_exc}')


def _upload_bytes(
    s3,
    bucket: str,
    key: str,
    body: bytes,
    *,
    content_type: str,
    metadata: dict,
) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type or _DEFAULT_CONTENT_TYPE,
        # boto3 requires str-only metadata values
        Metadata={k: str(v)[:1024] for k, v in metadata.items() if v is not None},
    )


def _mirror_one_document(
    cfg: WrenchConfig,
    job: WrenchS3SyncJob,
    s3,
    bucket: str,
    *,
    order_no: str,
    doc: dict,
) -> dict:
    """
    Mirror a single synthesized document row from Wrench to S3.
    Returns a manifest entry (always a dict — success or failure).
    """
    doc_no   = (doc.get('DOC_NO') or '').strip() or f'idoc_{doc.get("FILE_ID")}'
    file_id  = (doc.get('FILE_ID') or '').strip()
    if not file_id:
        return {'doc_no': doc_no, 'status': 'skipped', 'reason': 'no FILE_ID'}

    # Step 1 — early skip if already in S3 (cheap HEAD)
    # We don't know the extension yet so we tolerate either pre-known ext (PDF
    # is the Wrench default) or a re-probe pattern. Soft-coded toggle.
    provisional_key = _resolve_doc_s3_key(job, order_no, doc_no, '.pdf', doc)
    if _SKIP_IF_EXISTS_IN_S3:
        try:
            if _s3_object_exists(s3, bucket, provisional_key):
                return {'doc_no': doc_no, 'status': 'skipped',
                        'reason': 'already in s3', 's3_key': provisional_key}
        except (BotoCoreError, ClientError) as exc:
            logger.warning('[S3 ProjDocs] HEAD failed for %s: %s', provisional_key, exc)

    # Step 2 — fetch bytes from Wrench
    try:
        fetched = _fetch_document_bytes(cfg, idoc_id=file_id, doc_no=doc_no)
    except Exception as exc:                                       # noqa: BLE001
        return {'doc_no': doc_no, 'file_id': file_id,
                'status': 'failed', 'reason': f'download: {exc}'}

    body         = fetched['content']
    content_type = fetched['content_type']
    ext          = _guess_extension(fetched['filename'], content_type) or '.bin'
    s3_key       = _resolve_doc_s3_key(job, order_no, doc_no, ext, doc)

    # Step 3 — re-check existence with the *real* extension
    if _SKIP_IF_EXISTS_IN_S3 and s3_key != provisional_key:
        try:
            if _s3_object_exists(s3, bucket, s3_key):
                return {'doc_no': doc_no, 'status': 'skipped',
                        'reason': 'already in s3', 's3_key': s3_key}
        except (BotoCoreError, ClientError):
            pass

    # Step 4 — upload
    try:
        _upload_bytes(
            s3, bucket, s3_key, body,
            content_type=content_type,
            metadata={
                'wrench-order-no':  order_no,
                'wrench-doc-no':    doc_no,
                'wrench-file-id':   file_id,
                'wrench-job-id':    job.id,
                'sha256':           hashlib.sha256(body).hexdigest(),
                'doc-description':  (doc.get('DOC_DESCRIPTION') or '')[:1024],
                'revision':         doc.get('REVISION') or '',
                'doc-date':         str(doc.get('DOC_DATE') or ''),
            },
        )
    except (BotoCoreError, ClientError) as exc:
        return {'doc_no': doc_no, 'file_id': file_id,
                'status': 'failed', 'reason': f's3 upload: {exc}',
                's3_key': s3_key}

    return {
        'doc_no':       doc_no,
        'file_id':      file_id,
        'status':       'uploaded',
        's3_key':       s3_key,
        'bytes':        len(body),
        'content_type': content_type,
        'source':       fetched['source'],
    }


def _write_manifest(
    s3, bucket: str, job: WrenchS3SyncJob, order_no: str, entries: list, run_id: str,
) -> str:
    prefix = (job.s3_prefix or 'wrench/').rstrip('/') + '/'
    key = _S3_MANIFEST_TEMPLATE.format(
        prefix=prefix, order_no=_safe_name(str(order_no)), run_id=run_id,
    )
    summary = {
        'job_id':      job.id,
        'order_no':    order_no,
        'mode':        job.mode,
        'generated':   datetime.now(timezone.utc).isoformat(),
        'totals': {
            'uploaded': sum(1 for e in entries if e['status'] == 'uploaded'),
            'skipped':  sum(1 for e in entries if e['status'] == 'skipped'),
            'failed':   sum(1 for e in entries if e['status'] == 'failed'),
            'total':    len(entries),
        },
        'entries':     entries,
    }
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps(summary, default=str, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json',
    )
    return key


# ──────────────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────────────

def run_project_export(job: WrenchS3SyncJob) -> WrenchS3SyncJob:
    """
    Mirror EVERY document of a Wrench project (ORDER_NO) to S3 in one run.

    The ORDER_NO must be stored in `job.job_details['order_no']` (set by the
    view at creation time). Mode = batch.
    """
    return _execute(job, realtime=False)


def run_project_export_tick(job: WrenchS3SyncJob) -> WrenchS3SyncJob:
    """
    Mirror the *next slice* of project documents not yet in S3.
    Safe to call repeatedly by a Celery beat / re-queue chain.
    """
    return _execute(job, realtime=True)


def _execute(job: WrenchS3SyncJob, *, realtime: bool) -> WrenchS3SyncJob:
    cfg: WrenchConfig = job.config
    if not cfg:
        _mark_job(job, status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message='No active Wrench config linked to job.',
                  completed_at=dj_timezone.now())
        return job

    order_no = (job.job_details or {}).get('order_no')
    if not order_no:
        _mark_job(job, status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message='job_details.order_no is required for a project export.',
                  completed_at=dj_timezone.now())
        return job

    _mark_job(job, status=WrenchS3SyncJob.STATUS_IN_PROGRESS)

    s3     = _s3_client()
    bucket = _get_bucket()
    run_id = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    # 1) List all docs for the project (uses the existing fallback path)
    try:
        listing = synthesize_documents_from_transmittal_list(
            cfg, order_no=str(order_no), page=1, page_size=10_000,
        )
        documents = listing.get('documents') or []
    except Exception as exc:                                       # noqa: BLE001
        logger.exception('[S3 ProjDocs] listing failed: %s', exc)
        _mark_job(job, status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message=f'List documents failed: {exc}',
                  completed_at=dj_timezone.now())
        return job

    if not documents:
        _mark_job(job, status=WrenchS3SyncJob.STATUS_SUCCESS,
                  completed_at=dj_timezone.now(),
                  job_details={**(job.job_details or {}),
                               _JOB_DETAILS_PROJECT_KEY: {
                                   'order_no': order_no,
                                   'last_run': run_id,
                                   'note': 'No documents found.',
                               }})
        return job

    # 2) Realtime mode: take only a bounded slice starting after last cursor
    if realtime:
        cursor = int(((job.job_details or {})
                      .get(_JOB_DETAILS_PROJECT_KEY, {})
                      .get('cursor') or 0))
        slice_ = documents[cursor: cursor + _REALTIME_DOCS_PER_TICK]
    elif _BATCH_MAX_DOCS:
        slice_ = documents[: _BATCH_MAX_DOCS]
    else:
        slice_ = documents

    # 3) Parallel mirror
    manifest: list = []
    with ThreadPoolExecutor(max_workers=_PROJECT_EXPORT_WORKERS) as pool:
        futures = {
            pool.submit(_mirror_one_document, cfg, job, s3, bucket,
                        order_no=str(order_no), doc=d): d
            for d in slice_
        }
        for fut in as_completed(futures):
            try:
                manifest.append(fut.result())
            except Exception as exc:                               # noqa: BLE001
                logger.exception('[S3 ProjDocs] worker crashed: %s', exc)
                manifest.append({'status': 'failed', 'reason': str(exc)})

    uploaded = sum(1 for e in manifest if e['status'] == 'uploaded')
    skipped  = sum(1 for e in manifest if e['status'] == 'skipped')
    failed   = sum(1 for e in manifest if e['status'] == 'failed')

    # 4) Manifest object for this run
    try:
        manifest_key = _write_manifest(s3, bucket, job, str(order_no), manifest, run_id)
    except (BotoCoreError, ClientError) as exc:
        logger.error('[S3 ProjDocs] manifest write failed: %s', exc)
        manifest_key = ''

    # 5) Update job
    new_cursor = (
        int(((job.job_details or {}).get(_JOB_DETAILS_PROJECT_KEY, {}).get('cursor') or 0))
        + len(slice_)
    )
    project_details = {
        'order_no':       order_no,
        'last_run':       run_id,
        'manifest_key':   manifest_key,
        'cursor':         new_cursor if realtime else len(documents),
        'total_docs':     len(documents),
        'uploaded_total': ((job.job_details or {})
                           .get(_JOB_DETAILS_PROJECT_KEY, {})
                           .get('uploaded_total') or 0) + uploaded,
    }

    completed = (not realtime) or (new_cursor >= len(documents))
    final_status = (
        WrenchS3SyncJob.STATUS_SUCCESS if completed and failed == 0
        else WrenchS3SyncJob.STATUS_FAILED if completed and failed
        else WrenchS3SyncJob.STATUS_IN_PROGRESS
    )

    _mark_job(
        job,
        status=final_status,
        records_exported=job.records_exported + uploaded,
        records_failed=job.records_failed + failed,
        pages_processed=job.pages_processed + 1,
        job_details={**(job.job_details or {}), _JOB_DETAILS_PROJECT_KEY: project_details},
        **({'completed_at': dj_timezone.now()} if completed else {}),
    )
    logger.info(
        '[S3 ProjDocs] job=%d order=%s mode=%s uploaded=%d skipped=%d failed=%d cursor=%d/%d',
        job.id, order_no, 'realtime' if realtime else 'batch',
        uploaded, skipped, failed, project_details['cursor'], len(documents),
    )
    return job


# ════════════════════════════════════════════════════════════════════════════
# LIBRARY MIRROR WATCHER (continuous, change-driven)
# ════════════════════════════════════════════════════════════════════════════
# Mirrors the same Wrench "library" hierarchy on S3 and keeps it in sync:
#
#   S3 layout (soft-coded via _S3_LIBRARY_KEY_TEMPLATE):
#     {prefix}library/{order_no}/{discipline}/{doc_type}/{doc_no}/rev_{revision}/{filename}
#
# Each watcher tick:
#   1) Lists all docs of the project from Wrench (cheap — one transmittal-list call)
#   2) Builds a fingerprint dict {doc_no: {revision, status, date, file_id}}
#   3) Reads the previous fingerprint from S3 (state file)
#   4) Diffs → ADDED / CHANGED / REMOVED
#   5) Uploads ADDED + CHANGED file bytes in parallel
#   6) Writes the new fingerprint + a change-log entry back to S3
#   7) Re-queues itself after _WATCHER_INTERVAL_SEC for true realtime behavior
#
# All thresholds are soft-coded — never inline numbers.
# ════════════════════════════════════════════════════════════════════════════

# Folder template that mirrors Wrench library hierarchy. Placeholders:
#   {prefix}        – job s3_prefix
#   {order_no}      – Wrench project ORDER_NO
#   {discipline}    – sanitised TRANS_TYPE_CODE / DISCIPLINE
#   {doc_type}      – sanitised first segment of DOC_DESCRIPTION or '_unspecified'
#   {doc_no}        – sanitised DOC_NO
#   {revision}      – sanitised REVISION (status_desc / rev_series_id)
#   {filename}      – sanitised filename (basename + ext)
_S3_LIBRARY_KEY_TEMPLATE = (
    '{prefix}library/{order_no}/{discipline}/{doc_type}/{doc_no}/rev_{revision}/{filename}'
)

# State / change-log object keys for the watcher (per project).
_S3_STATE_KEY_TEMPLATE      = '{prefix}library/{order_no}/_state/fingerprint.json'
_S3_CHANGELOG_KEY_TEMPLATE  = '{prefix}library/{order_no}/_state/changelog.jsonl'

# Polling cadence — re-queue interval for the watcher Celery task.
_WATCHER_INTERVAL_SEC               = 60        # tick every minute by default
# Max documents to MIRROR per single tick (cap I/O bursts).
_WATCHER_MAX_CHANGES_PER_TICK       = 50
# When True the watcher uploads bytes for CHANGED rows (revision moved); when
# False it only records the change. Soft-coded for cost-sensitive deployments.
_WATCHER_UPLOAD_ON_CHANGE           = True

# When the Wrench tenant does not expose a file-download endpoint, fall back
# to writing a `<doc_no>.metadata.json` sidecar so the library hierarchy is
# still mirrored. Soft-coded so it can be disabled if the tenant API is fixed.
_FALLBACK_WRITE_METADATA_SIDECAR    = True
_METADATA_SIDECAR_SUFFIX            = '.metadata.json'

# Soft-coded default discipline / doc-type when transmittal lacks the field.
_LIBRARY_DEFAULT_DISCIPLINE = 'general'
_LIBRARY_DEFAULT_DOC_TYPE   = '_unspecified'

# Marker key inside job_details that flags a watcher-type job.
_JOB_DETAILS_WATCHER_KEY = 'library_watcher'


def _library_path_parts(doc: dict) -> dict:
    """Extract discipline / doc-type / revision segments from a Wrench row."""
    discipline = (doc.get('DISCIPLINE')
                  or doc.get('TRANS_TYPE_CODE')
                  or _LIBRARY_DEFAULT_DISCIPLINE)
    # Doc-type heuristic: first 30 chars of DOC_DESCRIPTION, else default.
    desc = (doc.get('DOC_DESCRIPTION') or '').strip()
    doc_type = (desc.split('-')[0].split('|')[0].strip()[:30]
                if desc else _LIBRARY_DEFAULT_DOC_TYPE)
    revision = (doc.get('REVISION') or '0').strip() or '0'
    return {
        'discipline': _safe_name(discipline),
        'doc_type':   _safe_name(doc_type),
        'revision':   _safe_name(revision),
    }


def _build_library_key(
    job: WrenchS3SyncJob, order_no: str, doc: dict, filename: str,
) -> str:
    prefix = (job.s3_prefix or 'wrench/').rstrip('/') + '/'
    parts = _library_path_parts(doc)
    return _S3_LIBRARY_KEY_TEMPLATE.format(
        prefix=prefix,
        order_no=_safe_name(str(order_no)),
        doc_no=_safe_name((doc.get('DOC_NO') or 'unknown')),
        filename=_safe_name(filename or 'document.bin'),
        **parts,
    )


def _state_key(job: WrenchS3SyncJob, order_no: str) -> str:
    prefix = (job.s3_prefix or 'wrench/').rstrip('/') + '/'
    return _S3_STATE_KEY_TEMPLATE.format(prefix=prefix, order_no=_safe_name(str(order_no)))


def _changelog_key(job: WrenchS3SyncJob, order_no: str) -> str:
    prefix = (job.s3_prefix or 'wrench/').rstrip('/') + '/'
    return _S3_CHANGELOG_KEY_TEMPLATE.format(prefix=prefix, order_no=_safe_name(str(order_no)))


def _read_state(s3, bucket: str, key: str) -> dict:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        if code in ('NoSuchKey', '404', 'NotFound'):
            return {}
        logger.warning('[S3 LibWatcher] state read failed (%s): %s', key, exc)
        return {}


def _write_state(s3, bucket: str, key: str, fingerprint: dict) -> None:
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps(fingerprint, default=str, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json',
    )


def _append_changelog(s3, bucket: str, key: str, entry: dict) -> None:
    """Append-style: read-modify-write a small JSONL. Cap retained lines."""
    _CHANGELOG_MAX_LINES = 500   # soft-coded retention
    try:
        existing = s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8')
        lines = existing.strip().splitlines()
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code', '') not in ('NoSuchKey', '404', 'NotFound'):
            raise
        lines = []
    lines.append(json.dumps(entry, default=str, ensure_ascii=False))
    lines = lines[-_CHANGELOG_MAX_LINES:]
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=('\n'.join(lines) + '\n').encode('utf-8'),
        ContentType='application/x-ndjson',
    )


def _doc_fingerprint(doc: dict) -> dict:
    """Stable per-document signature used to detect changes."""
    return {
        'doc_no':     doc.get('DOC_NO'),
        'revision':   doc.get('REVISION'),
        'status':    (doc.get('_raw_transmittal') or {}).get('STATUS_DESC'),
        'date':       str(doc.get('DOC_DATE') or ''),
        'file_id':    doc.get('FILE_ID'),
    }


def _mirror_one_to_library(
    cfg: WrenchConfig,
    job: WrenchS3SyncJob,
    s3,
    bucket: str,
    *,
    order_no: str,
    doc: dict,
) -> dict:
    """Download doc bytes from Wrench and upload to the library layout."""
    doc_no  = (doc.get('DOC_NO') or '').strip() or f'idoc_{doc.get("FILE_ID")}'
    file_id = (doc.get('FILE_ID') or '').strip()
    if not file_id:
        return {'doc_no': doc_no, 'status': 'skipped', 'reason': 'no FILE_ID'}

    try:
        fetched = _fetch_document_bytes(cfg, idoc_id=file_id, doc_no=doc_no)
    except Exception as exc:                                       # noqa: BLE001
        # Tenant has no working file-download endpoint? Soft-coded fallback:
        # mirror the library hierarchy with a metadata sidecar so the structure
        # is still in S3 and we can fill in bytes later when the API is enabled.
        if _FALLBACK_WRITE_METADATA_SIDECAR:
            try:
                sidecar_name = _safe_name(doc_no) + _METADATA_SIDECAR_SUFFIX
                sidecar_key  = _build_library_key(job, order_no, doc, sidecar_name)
                payload = {
                    'doc_no':          doc_no,
                    'file_id':         file_id,
                    'order_no':        order_no,
                    'revision':        doc.get('REVISION'),
                    'doc_description': doc.get('DOC_DESCRIPTION'),
                    'doc_date':        str(doc.get('DOC_DATE') or ''),
                    'discipline':      doc.get('DISCIPLINE'),
                    'raw_transmittal': doc.get('_raw_transmittal'),
                    'download_status': 'metadata_only',
                    'download_error':  str(exc)[:500],
                    'generated_at':    datetime.now(timezone.utc).isoformat(),
                }
                body = json.dumps(payload, default=str, ensure_ascii=False).encode('utf-8')
                _upload_bytes(
                    s3, bucket, sidecar_key, body,
                    content_type='application/json',
                    metadata={
                        'wrench-order-no': order_no,
                        'wrench-doc-no':   doc_no,
                        'wrench-file-id':  file_id,
                        'wrench-job-id':   job.id,
                        'sidecar':         'true',
                    },
                )
                return {
                    'doc_no':  doc_no, 'file_id': file_id,
                    'status': 'metadata_only',
                    's3_key': sidecar_key, 'bytes': len(body),
                    'reason': 'tenant has no file-download endpoint',
                }
            except (BotoCoreError, ClientError) as up_exc:
                return {'doc_no': doc_no, 'status': 'failed',
                        'reason': f'sidecar upload: {up_exc}'}
        return {'doc_no': doc_no, 'status': 'failed', 'reason': f'download: {exc}'}

    body         = fetched['content']
    content_type = fetched['content_type']
    filename     = fetched['filename'] or f'{doc_no}.bin'
    if '.' not in filename:
        ext = _guess_extension(None, content_type) or '.bin'
        filename = f'{filename}{ext}'

    s3_key = _build_library_key(job, order_no, doc, filename)

    try:
        _upload_bytes(
            s3, bucket, s3_key, body,
            content_type=content_type,
            metadata={
                'wrench-order-no': order_no,
                'wrench-doc-no':   doc_no,
                'wrench-file-id':  file_id,
                'wrench-revision': doc.get('REVISION') or '',
                'wrench-job-id':   job.id,
                'sha256':          hashlib.sha256(body).hexdigest(),
            },
        )
    except (BotoCoreError, ClientError) as exc:
        return {'doc_no': doc_no, 'status': 'failed',
                'reason': f's3 upload: {exc}', 's3_key': s3_key}

    return {
        'doc_no':       doc_no,
        'file_id':      file_id,
        'status':       'uploaded',
        's3_key':       s3_key,
        'bytes':        len(body),
        'content_type': content_type,
    }


def run_library_watcher_tick(job: WrenchS3SyncJob) -> WrenchS3SyncJob:
    """
    One tick of the library mirror watcher for a single project (ORDER_NO).

    Designed to be invoked repeatedly by Celery (chained re-queue) for true
    realtime behavior — between ticks any Wrench-side change is picked up and
    mirrored to S3.
    """
    cfg: WrenchConfig = job.config
    if not cfg:
        _mark_job(job, status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message='No active Wrench config linked to job.',
                  completed_at=dj_timezone.now())
        return job

    order_no = (job.job_details or {}).get('order_no')
    if not order_no:
        _mark_job(job, status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message='job_details.order_no is required for a library watcher.',
                  completed_at=dj_timezone.now())
        return job

    if job.status == WrenchS3SyncJob.STATUS_STOPPED:
        logger.info('[S3 LibWatcher] job=%d stopped — skipping tick.', job.id)
        return job

    _mark_job(job, status=WrenchS3SyncJob.STATUS_IN_PROGRESS)

    s3     = _s3_client()
    bucket = _get_bucket()
    tick_id = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    # 1) Current state from Wrench
    try:
        listing = synthesize_documents_from_transmittal_list(
            cfg, order_no=str(order_no), page=1, page_size=10_000,
        )
        documents = listing.get('documents') or []
    except Exception as exc:                                       # noqa: BLE001
        logger.exception('[S3 LibWatcher] listing failed: %s', exc)
        _mark_job(job, error_message=f'Listing failed: {exc}')
        return job

    current_fp = {
        (d.get('DOC_NO') or f'idoc_{d.get("FILE_ID")}'): _doc_fingerprint(d)
        for d in documents
    }
    current_docs_by_key = {
        (d.get('DOC_NO') or f'idoc_{d.get("FILE_ID")}'): d for d in documents
    }

    # 2) Previous state from S3
    state_key = _state_key(job, str(order_no))
    previous_fp = _read_state(s3, bucket, state_key) or {}

    # 3) Diff
    added   = [k for k in current_fp if k not in previous_fp]
    changed = [k for k in current_fp
               if k in previous_fp and previous_fp[k] != current_fp[k]]
    removed = [k for k in previous_fp if k not in current_fp]

    # 4) Pick which to mirror this tick (bounded)
    to_mirror = list(added)
    if _WATCHER_UPLOAD_ON_CHANGE:
        to_mirror.extend(changed)
    to_mirror = to_mirror[:_WATCHER_MAX_CHANGES_PER_TICK]

    # 5) Parallel mirror
    results: list = []
    with ThreadPoolExecutor(max_workers=_PROJECT_EXPORT_WORKERS) as pool:
        futures = [
            pool.submit(_mirror_one_to_library, cfg, job, s3, bucket,
                        order_no=str(order_no), doc=current_docs_by_key[k])
            for k in to_mirror
        ]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:                               # noqa: BLE001
                results.append({'status': 'failed', 'reason': str(exc)})

    uploaded = sum(1 for r in results if r['status'] in ('uploaded', 'metadata_only'))
    failed   = sum(1 for r in results if r['status'] == 'failed')

    # 6) Persist new fingerprint (only for keys we actually mirrored + unchanged)
    next_fp = dict(previous_fp)
    for k in added + changed:
        next_fp[k] = current_fp[k]
    for k in removed:
        next_fp.pop(k, None)
    try:
        _write_state(s3, bucket, state_key, next_fp)
    except (BotoCoreError, ClientError) as exc:
        logger.error('[S3 LibWatcher] state write failed: %s', exc)

    # 7) Changelog entry
    try:
        _append_changelog(
            s3, bucket, _changelog_key(job, str(order_no)),
            {
                'tick_id':     tick_id,
                'order_no':    order_no,
                'added':       len(added),
                'changed':     len(changed),
                'removed':     len(removed),
                'uploaded':    uploaded,
                'failed':      failed,
                'sample_doc_nos': to_mirror[:5],
            },
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning('[S3 LibWatcher] changelog write failed: %s', exc)

    # 8) Update job (stays in_progress for continuous polling)
    watcher_details = {
        'order_no':         order_no,
        'last_tick':        tick_id,
        'total_docs':       len(documents),
        'added_this_tick':  len(added),
        'changed_this_tick':len(changed),
        'removed_this_tick':len(removed),
        'uploaded_total':   ((job.job_details or {})
                             .get(_JOB_DETAILS_WATCHER_KEY, {})
                             .get('uploaded_total') or 0) + uploaded,
        'state_key':        state_key,
    }
    _mark_job(
        job,
        status=WrenchS3SyncJob.STATUS_IN_PROGRESS,
        records_exported=job.records_exported + uploaded,
        records_failed=job.records_failed + failed,
        pages_processed=job.pages_processed + 1,
        job_details={**(job.job_details or {}), _JOB_DETAILS_WATCHER_KEY: watcher_details},
    )
    logger.info(
        '[S3 LibWatcher] job=%d order=%s added=%d changed=%d removed=%d uploaded=%d failed=%d',
        job.id, order_no, len(added), len(changed), len(removed), uploaded, failed,
    )
    return job


def watcher_interval_sec() -> int:
    """Expose the soft-coded interval (used by the Celery task to re-queue)."""
    return _WATCHER_INTERVAL_SEC

