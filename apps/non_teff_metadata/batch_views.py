"""
Non-TEFF Bulk Master Index - DRF views.

Additive endpoints (do not touch the existing single-file views):

    POST   /api/v1/non-teff/batch/create/
    POST   /api/v1/non-teff/batch/<batch_id>/upload/
    POST   /api/v1/non-teff/batch/<batch_id>/start/
    GET    /api/v1/non-teff/batch/<batch_id>/status/
    GET    /api/v1/non-teff/batch/<batch_id>/items/
    PATCH  /api/v1/non-teff/batch/<batch_id>/items/<item_id>/
    POST   /api/v1/non-teff/batch/<batch_id>/bulk-update/
    GET    /api/v1/non-teff/batch/<batch_id>/export/
    GET    /api/v1/non-teff/batch/template/      (returns template + taxonomy)

All heavy work runs in a daemon thread (mirroring the existing
``run_extraction_async`` pattern in ``extractor.py`` — no Celery required for
Phase 1).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from typing import Dict, List

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import get_valid_filename
from rest_framework import status as http_status
from rest_framework.decorators import (
    api_view,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.http import FileResponse

from .models import NonTeffBatch, NonTeffBatchItem
from .services import document_search, master_index_export, master_index_service
from .services import smartplant_connector
from .services import history_archive

# ------------------------------------------------------------------
# Soft-coded toggle: archive every uploaded source file + the final
# master-index JSON to S3 (best-effort, never blocks the request).
# Flip to False to disable without touching the call sites.
# ------------------------------------------------------------------
BATCH_S3_ARCHIVAL_ENABLED = True

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SOFT-CODED constants
# ---------------------------------------------------------------------------

# Sub-directory under MEDIA_ROOT where batch files are written.
MEDIA_SUBDIR = 'non_teff_batches'

EXPORT_FILENAME_TEMPLATE = 'Master_Index_{plant}.xlsx'
DEFAULT_EXPORT_PLANT = 'BATCH'

# Item page size for the grid view.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _batch_dir(batch_id) -> str:
    path = os.path.join(settings.MEDIA_ROOT, MEDIA_SUBDIR, str(batch_id))
    os.makedirs(path, exist_ok=True)
    return path


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _extract_batch_payload(body: dict) -> Dict:
    """Validate the batch-create payload and fold in template hints."""
    name = (body.get('name') or '').strip() or f"Batch {uuid.uuid4().hex[:8]}"
    plant = (body.get('plant') or '').strip()
    # Soft-coded list of accepted aliases for project reference.
    project_id = ''
    for key in ('project_id', 'project', 'projectId'):
        raw = body.get(key)
        if raw:
            project_id = str(raw).strip()
            break
    defaults = body.get('batch_defaults') or {}
    if not isinstance(defaults, dict):
        defaults = {}
    # Merge template hints for any batch_default column not provided
    hints = master_index_service.get_batch_default_hints()
    for key, value in hints.items():
        defaults.setdefault(key, value)
    if plant:
        defaults.setdefault('plant', plant)
    return {'name': name, 'plant': plant, 'project_id': project_id,
            'batch_defaults': defaults}


def _run_extraction_thread(batch_id: str) -> None:
    """Background worker: populate every pending/uploaded item."""
    try:
        batch = NonTeffBatch.objects.get(batch_id=batch_id)
    except NonTeffBatch.DoesNotExist:
        logger.error('Batch %s vanished before extraction', batch_id)
        return

    batch.status = NonTeffBatch.BATCH_STATUS_PROCESSING
    batch.save(update_fields=['status', 'updated_at'])

    items = list(NonTeffBatchItem.objects.filter(batch=batch).order_by('file_name'))
    ready = failed = 0
    for index, item in enumerate(items, start=1):
        item.status = NonTeffBatchItem.ITEM_STATUS_EXTRACTING
        item.save(update_fields=['status', 'updated_at'])
        try:
            row = master_index_service.build_row(
                row_index=index,
                file_name=item.file_name,
                relative_path=item.relative_path or item.file_name,
                file_path=item.storage_key,
                batch_defaults=batch.batch_defaults or {},
            )
            item.fields = row
            item.status = NonTeffBatchItem.ITEM_STATUS_READY
            item.error = ''
            ready += 1
        except Exception as exc:
            logger.exception('Extraction failed for %s', item.file_name)
            item.status = NonTeffBatchItem.ITEM_STATUS_FAILED
            item.error = str(exc)
            failed += 1
        item.save(update_fields=['fields', 'status', 'error', 'updated_at'])

    batch.ready_files = ready
    batch.failed_files = failed
    batch.status = (
        NonTeffBatch.BATCH_STATUS_READY if ready else NonTeffBatch.BATCH_STATUS_FAILED
    )
    batch.save(update_fields=['ready_files', 'failed_files', 'status', 'updated_at'])

    # Best-effort: archive the aggregated master-index payload to S3 once
    # the batch finishes. Idempotent (overwrite OK) — failures never raise.
    if BATCH_S3_ARCHIVAL_ENABLED:
        try:
            history_archive.archive_batch_result(batch)
        except Exception:
            logger.warning('Batch S3 result archive failed for %s',
                           batch.batch_id, exc_info=True)


def _serialize_batch(batch: NonTeffBatch) -> dict:
    return {
        'batch_id': str(batch.batch_id),
        'name': batch.name,
        'plant': batch.plant,
        'status': batch.status,
        'total_files': batch.total_files,
        'ready_files': batch.ready_files,
        'failed_files': batch.failed_files,
        'batch_defaults': batch.batch_defaults or {},
        'created_at': batch.created_at.isoformat(),
        'updated_at': batch.updated_at.isoformat(),
    }


def _serialize_item(item: NonTeffBatchItem) -> dict:
    return {
        'item_id': str(item.item_id),
        'batch_id': str(item.batch_id),
        'file_name': item.file_name,
        'relative_path': item.relative_path,
        'size_bytes': item.size_bytes,
        'status': item.status,
        'reviewed': item.reviewed,
        'error': item.error,
        'fields': item.fields or {},
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_batch_template(_request):
    """Return the template + taxonomy so the frontend grid can auto-configure."""
    return Response({
        'template': master_index_service.load_template(),
        'taxonomy': master_index_service.load_taxonomy(),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_batch(request):
    payload = _extract_batch_payload(request.data or {})
    # Resolve optional project assignment (silently ignore bad references).
    project_obj = None
    if payload.get('project_id'):
        from .models import NonTeffProject
        try:
            project_obj = NonTeffProject.objects.get(project_id=payload['project_id'])
        except (NonTeffProject.DoesNotExist, ValueError, Exception):
            project_obj = None
    batch = NonTeffBatch.objects.create(
        name=payload['name'],
        plant=payload['plant'],
        batch_defaults=payload['batch_defaults'],
        status=NonTeffBatch.BATCH_STATUS_DRAFT,
        created_by=request.user if request.user.is_authenticated else None,
        project=project_obj,
    )
    return Response(_serialize_batch(batch), status=http_status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_batch_files(request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    uploaded = request.FILES.getlist('files') or request.FILES.getlist('file')
    if not uploaded:
        return Response({'error': 'no files provided'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    limits = master_index_service.get_limits()
    max_bytes = int(limits.get('max_file_size_mb', 500)) * 1024 * 1024
    max_files = int(limits.get('max_files_per_batch', 2000))
    allowed_ext = {e.lower() for e in limits.get('allowed_extensions', [])}

    existing = batch.items.count()
    if existing + len(uploaded) > max_files:
        return Response({'error': f'exceeds max_files_per_batch={max_files}'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    batch.status = NonTeffBatch.BATCH_STATUS_UPLOADING
    batch.save(update_fields=['status', 'updated_at'])

    base_dir = _batch_dir(batch.batch_id)
    created: List[dict] = []
    skipped: List[dict] = []

    # Relative paths (from the directory picker) arrive in a parallel list so
    # we can preserve folder hierarchy inside batch_defaults['source_folder'].
    rel_paths = request.POST.getlist('relative_paths')

    for idx, up in enumerate(uploaded):
        original = up.name
        ext = os.path.splitext(original)[1].lower()
        if allowed_ext and ext not in allowed_ext:
            skipped.append({'file_name': original, 'reason': 'extension not allowed'})
            continue
        if up.size > max_bytes:
            skipped.append({'file_name': original, 'reason': 'exceeds max size'})
            continue

        safe_name = get_valid_filename(original)
        # Namespace with a short uuid to avoid collisions inside the batch dir.
        stored = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        abs_path = os.path.join(base_dir, stored)
        with open(abs_path, 'wb') as dst:
            for chunk in up.chunks():
                dst.write(chunk)

        rel = rel_paths[idx] if idx < len(rel_paths) else original

        with transaction.atomic():
            item = NonTeffBatchItem.objects.create(
                batch=batch,
                file_name=original,
                relative_path=rel,
                storage_key=abs_path,
                size_bytes=up.size,
                sha256=_sha256(abs_path),
                status=NonTeffBatchItem.ITEM_STATUS_UPLOADED,
            )
        created.append(_serialize_item(item))

        # Best-effort S3 archival of the raw source file. Failures here
        # MUST NOT impact the upload response — archival is additive.
        if BATCH_S3_ARCHIVAL_ENABLED:
            try:
                history_archive.archive_batch_source(batch, item, abs_path)
            except Exception:
                logger.warning('Batch S3 source archive failed for %s',
                               original, exc_info=True)

    batch.total_files = batch.items.count()
    batch.storage_prefix = base_dir
    batch.save(update_fields=['total_files', 'storage_prefix', 'updated_at'])

    return Response({
        'batch': _serialize_batch(batch),
        'created': created,
        'skipped': skipped,
    })


# ---------------------------------------------------------------------------
# Direct-to-S3 presigned upload (large-file / 2 GB path).
#
# Mirrors the proven `spec_customization` flow so very large files bypass
# Railway's edge proxy entirely (~100-500 MB body cap, ~5-10 min request
# timeout). Only the tiny `presign` and `complete` RPCs touch Django; the
# bytes go browser → S3 directly. The legacy multipart `upload_batch_files`
# view above is unchanged and remains the path for small files.
#
# Soft-coding: all behaviour lives in `services.presigned_upload`. The
# client receives `{enabled: false, reason}` if S3 isn't configured and
# silently falls back to the multipart path.
# ---------------------------------------------------------------------------
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def presign_batch_upload(request, batch_id):
    """
    Returns a presigned PUT URL for ONE file. The frontend issues one
    presign call per large file in a chunk, then PUTs each blob directly
    to S3 in parallel.

    Body: { filename, content_type?, size?, relative_path? }
    """
    from .services.presigned_upload import (
        PRESIGNED_UPLOAD_CONFIG,
        generate_presigned_put,
    )

    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    filename     = request.data.get('filename') or ''
    content_type = request.data.get('content_type') or 'application/octet-stream'
    try:
        size_bytes = int(request.data.get('size') or 0)
    except (TypeError, ValueError):
        size_bytes = 0

    if not filename:
        return Response({'error': 'filename is required'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    result = generate_presigned_put(
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        batch_id=str(batch.batch_id),
    )

    if not result.enabled:
        # 200 OK with enabled=False — expected fallback signal, not an error.
        return Response({
            'enabled': False,
            'reason':  result.reason,
            'min_mb':  PRESIGNED_UPLOAD_CONFIG['min_mb_advisory'],
        })

    return Response({
        'enabled':     True,
        'method':      result.method,
        'upload_url':  result.upload_url,
        's3_key':      result.s3_key,
        'headers':     result.headers,
        'expires_in':  result.expires_in,
        'bucket':      result.bucket,
        'max_bytes':   PRESIGNED_UPLOAD_CONFIG['max_bytes'],
        'min_mb':      PRESIGNED_UPLOAD_CONFIG['min_mb_advisory'],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_batch_upload(request, batch_id):
    """
    Ingest one or more files that have already been PUT to S3 via
    presigned URLs. Mirrors `upload_batch_files` registration logic so
    downstream extraction sees identical NonTeffBatchItem rows.

    Body: { items: [{ s3_key, file_name, relative_path?, size?, content_type? }, ...] }
    """
    from .services.presigned_upload import (
        best_effort_delete,
        fetch_uploaded_to_path,
        is_presigned_upload_available,
    )

    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)

    if not is_presigned_upload_available():
        return Response(
            {'error': 'presigned uploads are not available in this environment'},
            status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    items_in = request.data.get('items') or []
    if not isinstance(items_in, list) or not items_in:
        return Response({'error': 'items array is required'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    limits = master_index_service.get_limits()
    max_bytes = int(limits.get('max_file_size_mb', 500)) * 1024 * 1024
    max_files = int(limits.get('max_files_per_batch', 2000))
    allowed_ext = {e.lower() for e in limits.get('allowed_extensions', [])}

    existing = batch.items.count()
    if existing + len(items_in) > max_files:
        return Response({'error': f'exceeds max_files_per_batch={max_files}'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    # Move batch into uploading state — same as legacy view.
    batch.status = NonTeffBatch.BATCH_STATUS_UPLOADING
    batch.save(update_fields=['status', 'updated_at'])

    base_dir = _batch_dir(batch.batch_id)
    created: List[dict] = []
    skipped: List[dict] = []

    for entry in items_in:
        s3_key   = (entry or {}).get('s3_key') or ''
        original = (entry or {}).get('file_name') or ''
        rel      = (entry or {}).get('relative_path') or original

        if not s3_key or not original:
            skipped.append({'file_name': original or '(missing)',
                            'reason': 'missing s3_key or file_name'})
            continue

        ext = os.path.splitext(original)[1].lower()
        if allowed_ext and ext not in allowed_ext:
            skipped.append({'file_name': original, 'reason': 'extension not allowed'})
            best_effort_delete(s3_key)
            continue

        safe_name = get_valid_filename(original)
        stored = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        abs_path = os.path.join(base_dir, stored)

        try:
            written = fetch_uploaded_to_path(s3_key=s3_key, dest_path=abs_path)
        except RuntimeError as exc:
            logger.warning('S3 fetch failed for %s: %s', s3_key, exc)
            skipped.append({'file_name': original, 'reason': f'S3 fetch failed: {exc}'})
            continue

        if written > max_bytes:
            skipped.append({'file_name': original, 'reason': 'exceeds max size'})
            try:
                os.remove(abs_path)
            except Exception:
                pass
            best_effort_delete(s3_key)
            continue

        with transaction.atomic():
            item = NonTeffBatchItem.objects.create(
                batch=batch,
                file_name=original,
                relative_path=rel,
                storage_key=abs_path,
                size_bytes=written,
                sha256=_sha256(abs_path),
                status=NonTeffBatchItem.ITEM_STATUS_UPLOADED,
            )
        created.append(_serialize_item(item))

        # Best-effort S3 archival mirrors the legacy view.
        if BATCH_S3_ARCHIVAL_ENABLED:
            try:
                history_archive.archive_batch_source(batch, item, abs_path)
            except Exception:
                logger.warning('Batch S3 source archive failed for %s',
                               original, exc_info=True)

        # Staged object has been ingested — drop it. Failure is non-fatal.
        best_effort_delete(s3_key)

    batch.total_files = batch.items.count()
    batch.storage_prefix = base_dir
    batch.save(update_fields=['total_files', 'storage_prefix', 'updated_at'])

    return Response({
        'batch': _serialize_batch(batch),
        'created': created,
        'skipped': skipped,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_batch(request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    if batch.status == NonTeffBatch.BATCH_STATUS_PROCESSING:
        return Response({'error': 'already processing'},
                        status=http_status.HTTP_409_CONFLICT)
    if not batch.items.exists():
        return Response({'error': 'no files uploaded'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    thread = threading.Thread(
        target=_run_extraction_thread,
        args=(str(batch.batch_id),),
        daemon=True,
        name=f"non_teff_batch_{batch.batch_id}",
    )
    thread.start()
    return Response({'started': True, 'batch_id': str(batch.batch_id)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def batch_status(_request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    counts = {
        'pending':    batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_PENDING).count(),
        'uploaded':   batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_UPLOADED).count(),
        'extracting': batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_EXTRACTING).count(),
        'ready':      batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_READY).count(),
        'failed':     batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_FAILED).count(),
    }
    return Response({'batch': _serialize_batch(batch), 'counts': counts})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_batch_items(request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    try:
        page = max(1, int(request.GET.get('page', '1')))
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))))
    except ValueError:
        page, page_size = 1, DEFAULT_PAGE_SIZE

    qs = batch.items.all().order_by('file_name')
    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    total = qs.count()
    start = (page - 1) * page_size
    items = qs[start:start + page_size]
    return Response({
        'batch_id': str(batch.batch_id),
        'page': page,
        'page_size': page_size,
        'total': total,
        'items': [_serialize_item(i) for i in items],
    })


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_batch_item(request, batch_id, item_id):
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    payload = request.data or {}
    if 'fields' in payload and isinstance(payload['fields'], dict):
        merged = dict(item.fields or {})
        merged.update({k: ('' if v is None else str(v)) for k, v in payload['fields'].items()})
        item.fields = merged
    if 'reviewed' in payload:
        item.reviewed = bool(payload['reviewed'])
    item.save()
    return Response(_serialize_item(item))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_update_items(request, batch_id):
    """Apply one or more column values to many items at once."""
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    payload = request.data or {}
    item_ids = payload.get('item_ids') or []
    updates = payload.get('fields') or {}
    if not item_ids or not isinstance(updates, dict):
        return Response({'error': 'item_ids and fields required'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    items = batch.items.filter(item_id__in=item_ids)
    updated = 0
    for item in items:
        merged = dict(item.fields or {})
        merged.update({k: ('' if v is None else str(v)) for k, v in updates.items()})
        item.fields = merged
        item.save(update_fields=['fields', 'updated_at'])
        updated += 1
    return Response({'updated': updated})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_batch(_request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    items = [
        (i.fields or {})
        for i in batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_READY).order_by('file_name')
    ]
    data = master_index_export.build_workbook(
        batch_name=batch.name,
        plant=batch.plant or DEFAULT_EXPORT_PLANT,
        items=items,
    )
    filename = EXPORT_FILENAME_TEMPLATE.format(
        plant=(batch.plant or DEFAULT_EXPORT_PLANT).replace(' ', '_')
    )
    response = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    batch.status = NonTeffBatch.BATCH_STATUS_EXPORTED
    batch.save(update_fields=['status', 'updated_at'])
    return response


# ---------------------------------------------------------------------------
# DOCUMENT SEARCH CANVAS — additive, read-only endpoints
# ---------------------------------------------------------------------------
# /search/                                          → cross-batch + cross-job text search
# /batch/<batch_id>/items/<item_id>/locate/?q=…     → bbox(es) per page for query
# /batch/<batch_id>/items/<item_id>/page/<n>/image/ → cached PNG of page n
# ---------------------------------------------------------------------------

# Soft-coded mime + cache-control for rendered PDF page images.
_PAGE_IMAGE_MIME = 'image/png'
_PAGE_IMAGE_CACHE_HEADER = 'private, max-age=3600'


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_documents(request):
    """Free-text search across batch items (and optionally legacy jobs)."""
    query = (request.GET.get('q') or '').strip()
    batch_id = request.GET.get('batch_id') or None
    kind = request.GET.get('kind') or None  # 'batch' | 'job' | None
    if not query:
        return Response({'query': '', 'total': 0, 'results': []})
    payload = document_search.search_records(query=query, batch_id=batch_id, kind=kind)
    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def locate_in_item(request, batch_id, item_id):
    """Return per-page bounding boxes (page-size %) for *q* inside an item PDF."""
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    query = (request.GET.get('q') or '').strip()
    pdf_path, err = document_search.resolve_item_pdf(item)
    if err:
        return Response({'matches': [], 'page_count': 0, 'error': err})
    payload = document_search.locate_in_pdf(pdf_path, query)
    payload.update({
        'item_id': str(item.item_id),
        'batch_id': str(item.batch_id),
        'file_name': item.file_name,
        'query': query,
    })
    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_item_page_image(request, batch_id, item_id, page_no):
    """Stream a rendered PNG of the requested 1-based page of the item's PDF."""
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    pdf_path, err = document_search.resolve_item_pdf(item)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)
    try:
        page_int = int(page_no)
    except (TypeError, ValueError):
        return Response({'error': 'invalid page number'}, status=http_status.HTTP_400_BAD_REQUEST)
    img_path = document_search.render_pdf_page_png(pdf_path, page_int, str(item.item_id))
    if not img_path or not os.path.exists(img_path):
        return Response({'error': 'page render failed'}, status=http_status.HTTP_404_NOT_FOUND)
    fh = open(img_path, 'rb')
    response = FileResponse(fh, content_type=_PAGE_IMAGE_MIME)
    response['Cache-Control'] = _PAGE_IMAGE_CACHE_HEADER
    return response


# ---------------------------------------------------------------------------
# Smart Recommendations (additive, hover-driven)
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def recommend_for_item(request, batch_id, item_id):
    """
    Return a small AI-powered recommendation card for an item.

    Cost-first: provider chain is Gemini-flash → OpenAI-mini, with a pure
    heuristic fallback so the UI is never empty. Result is cached server-side
    keyed by the item context, so repeated hovers cost nothing.
    """
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    try:
        from .services import ai_recommendations, document_search, master_index_service

        # Pull a unique-per-document text excerpt so the AI has something
        # concrete to reason about even when the metadata row is sparse.
        # Falls back gracefully if the file cannot be resolved.
        text_excerpt = ''
        try:
            pdf_path, _err = document_search.resolve_item_pdf(item)
            if pdf_path:
                text_excerpt = master_index_service.read_file_text(pdf_path) or ''
        except Exception:
            logger.debug('text_excerpt build failed for %s', item_id, exc_info=True)

        payload = ai_recommendations.recommend_for_item(
            item_id=str(item.item_id),
            file_name=item.file_name,
            fields=item.fields or {},
            text_excerpt=text_excerpt,
            sha256=item.sha256 or '',
        )
    except Exception:
        logger.exception('recommend_for_item failed for item %s', item_id)
        payload = {}
    return Response({
        'item_id':  str(item.item_id),
        'batch_id': str(item.batch_id),
        'recommendations': payload,
    })


# ---------------------------------------------------------------------------
# Yellow-region detector (additive — overlays valuable highlight stamps)
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def yellow_regions_for_item(request, batch_id, item_id):
    """
    Detect yellow-highlighted rectangles in the item's PDF and OCR them.
    Returns rect_pct coordinates (0..1) so the canvas can overlay them on
    the rendered page image without needing a re-render.
    """
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    try:
        from .services import document_search, yellow_region_extractor
        pdf_path, err = document_search.resolve_item_pdf(item)
        if err:
            return Response({'regions': [], 'error': err})
        regions = yellow_region_extractor.extract_yellow_regions(pdf_path)
    except Exception:
        logger.exception('yellow_regions_for_item failed for item %s', item_id)
        regions = []
    # Slim down the payload — only return the fields the canvas needs.
    payload = [
        {
            'page':       r.get('page'),
            'rect_pct':   r.get('rect_pct'),
            'text':       r.get('text'),
            'label':      r.get('label', ''),
            'confidence': r.get('confidence', 0.0),
        }
        for r in regions
    ]
    return Response({
        'item_id':  str(item.item_id),
        'batch_id': str(item.batch_id),
        'regions':  payload,
        'count':    len(payload),
    })


# ---------------------------------------------------------------------------
# Completeness / Coverage  (additive — no core logic touched)
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def batch_coverage(request, batch_id):
    """
    Return a soft-coded completeness report for the batch:
    overall %, per-column fill rates, weakest items, suggestions, and a
    preview of which fields the reconcile pass would back-fill.
    """
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    try:
        from .services import completeness_analyzer
        items = list(batch.items.all())
        report = completeness_analyzer.coverage_report(items)
    except Exception:
        logger.exception('batch_coverage failed for %s', batch_id)
        report = {}
    return Response({
        'batch_id': str(batch.batch_id),
        'report':   report,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def batch_reconcile(request, batch_id):
    """
    Apply the cross-row reconciliation pass: back-fills NA cells of
    "constant-across-batch" columns from the modal value when at least
    `min_modal_share` of populated rows agree. Per-document unique columns
    (document_number, tag, …) are never touched.

    Idempotent — calling twice does nothing extra.
    """
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    try:
        from .services import completeness_analyzer
        items = list(batch.items.all())
        result = completeness_analyzer.apply_reconciliation(items)
        # Persist any items that were touched.
        if result.get('applied_cells', 0) > 0:
            touched = result.pop('touched_item_ids', set())
            with transaction.atomic():
                for it in items:
                    if getattr(it, 'item_id', None) in touched:
                        it.save(update_fields=['fields', 'updated_at'])
        else:
            result.pop('touched_item_ids', None)
    except Exception:
        logger.exception('batch_reconcile failed for %s', batch_id)
        return Response({'detail': 'Reconciliation failed'},
                        status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({
        'batch_id': str(batch.batch_id),
        **result,
    })


# ---------------------------------------------------------------------------
# Direct-link to original drawing/record (additive — saves user search time)
# ---------------------------------------------------------------------------
# Soft-coded MIME map for inline browser preview. Anything not listed falls
# back to application/octet-stream and triggers a download.
_INLINE_MIME_MAP = {
    '.pdf':  'application/pdf',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.txt':  'text/plain',
    '.csv':  'text/csv',
}
# Extensions that should always be served inline (rendered in browser tab).
# Everything else streams as attachment so the user gets a download.
_INLINE_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.txt'}


def _guess_inline_mime(file_name: str) -> tuple[str, bool]:
    """Return (mime, inline?) for a stored file name."""
    ext = os.path.splitext(file_name or '')[1].lower()
    mime = _INLINE_MIME_MAP.get(ext, 'application/octet-stream')
    return mime, ext in _INLINE_EXTENSIONS


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def item_location(request, batch_id, item_id):
    """
    JSON metadata about where an item's source file lives. Used by the
    frontend to display a tooltip/breadcrumb without having to download the
    blob. Never returns the absolute server path (security).
    """
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    storage_path = item.storage_key or ''
    exists = bool(storage_path) and os.path.exists(storage_path)
    size = 0
    if exists:
        try:
            size = os.path.getsize(storage_path)
        except OSError:
            size = 0
    mime, inline = _guess_inline_mime(item.file_name)
    return Response({
        'batch_id':      str(item.batch_id),
        'item_id':       str(item.item_id),
        'file_name':     item.file_name,
        'relative_path': item.relative_path or item.file_name,
        'size_bytes':    size,
        'mime':          mime,
        'inline':        inline,
        'available':     exists,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_item_file(request, batch_id, item_id):
    """
    Stream the original uploaded file for an item.

    - PDFs / images / text → ``Content-Disposition: inline`` so the browser
      opens them in a new tab.
    - Everything else → ``attachment`` so the user gets a download.

    The file is read from ``item.storage_key`` directly; no path traversal
    risk because the value is set server-side at upload time and never
    accepted from the client.
    """
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    storage_path = item.storage_key or ''
    if not storage_path or not os.path.exists(storage_path):
        return Response({'detail': 'file missing on disk'},
                        status=http_status.HTTP_404_NOT_FOUND)
    mime, inline = _guess_inline_mime(item.file_name)
    safe_name = get_valid_filename(item.file_name or 'document')
    fh = open(storage_path, 'rb')
    response = FileResponse(fh, content_type=mime)
    disposition = 'inline' if inline else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{safe_name}"'
    response['Cache-Control'] = 'private, max-age=300'
    return response


# ---------------------------------------------------------------------------
# SmartPlant Foundation integration (additive — uses smartplant_connector)
# ---------------------------------------------------------------------------
# All transport / mapping / endpoint config lives in
# `config/smartplant_config.json`.  Endpoints below are thin wrappers.
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def smartplant_status(request, batch_id=None):
    """Return connector readiness + last-push history for the UI button."""
    payload = smartplant_connector.get_status()
    if batch_id:
        try:
            batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
            payload['history'] = smartplant_connector.get_audit_history(batch)
        except Exception:
            logger.exception('smartplant_status history lookup failed for %s', batch_id)
            payload['history'] = []
    return Response(payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smartplant_push(request, batch_id):
    """
    Push the finished Master Index for `batch_id` to SmartPlant Foundation.

    Body (all optional):
        {"mode": "rest_api" | "webhook" | "s3_dropzone"
                  | "local_dropzone" | "dry_run"}

    The grid rows used are the same ones that go into the Excel export
    (status == ready), so what SPF receives matches what the user sees.
    """
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    requested_mode = (request.data.get('mode') if hasattr(request, 'data') else None) or None

    items = [
        (i.fields or {})
        for i in batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_READY).order_by('file_name')
    ]
    if not items:
        return Response(
            {'status': 'error', 'mode': requested_mode or 'unknown',
             'message': 'No ready rows in this batch — nothing to push.'},
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    try:
        workbook_bytes = master_index_export.build_workbook(
            batch_name=batch.name,
            plant=batch.plant or DEFAULT_EXPORT_PLANT,
            items=items,
        )
    except Exception:
        logger.exception('smartplant_push: workbook build failed for %s', batch_id)
        return Response(
            {'status': 'error', 'mode': requested_mode or 'unknown',
             'message': 'Failed to build the Master Index workbook for SPF.'},
            status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    result = smartplant_connector.push_batch(
        batch=batch, items=items,
        workbook_bytes=workbook_bytes,
        mode=requested_mode,
        user=getattr(request, 'user', None),
    )
    http_code = http_status.HTTP_200_OK if result.get('status') == 'ok' else http_status.HTTP_502_BAD_GATEWAY
    return Response(result, status=http_code)

