"""
DRF views for the Instrument IO List Workflow.

Endpoints
─────────
GET    /api/v1/instrument-io-workflow/config/
POST   /api/v1/instrument-io-workflow/documents/                  (multipart upload)
GET    /api/v1/instrument-io-workflow/documents/
GET    /api/v1/instrument-io-workflow/documents/{id}/
DELETE /api/v1/instrument-io-workflow/documents/{id}/
POST   /api/v1/instrument-io-workflow/documents/{id}/re-extract/
GET    /api/v1/instrument-io-workflow/documents/{id}/export-xlsx/
POST   /api/v1/instrument-io-workflow/diff/                       ({old_id, new_id})
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.http import HttpResponse, Http404

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    IOListDocument, IOListExtractedComment, IOListExtractedRow,
)
from .serializers import (
    IOListDocumentListSerializer, IOListDocumentDetailSerializer,
)
from .services.config import (
    ENABLE_VISION_FALLBACK, ENABLE_HASH_CACHE,
    IO_LIST_CANONICAL_COLUMNS, COMMENT_SHEET_COLUMNS, STATUS_CODE_MEANING,
    CHAIN_DEFAULT_MAX_REVISIONS, CHAIN_RISK_THRESHOLDS,
)
from .services.orchestrator import extract_document, sha256_of
from .services.revision_diff import diff_revisions
from .excel_export import export_document_to_xlsx

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Config endpoint — what the frontend needs (soft-coded surface)
# ──────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def config_view(request):
    return Response({
        'columns': {
            'comments': COMMENT_SHEET_COLUMNS + ['status_meaning',
                                                  'page_number', 'linked_tags'],
            'io_rows':  IO_LIST_CANONICAL_COLUMNS,
        },
        'status_codes': STATUS_CODE_MEANING,
        'features': {
            'vision_fallback': ENABLE_VISION_FALLBACK,
            'hash_cache':      ENABLE_HASH_CACHE,
            'chain_defaults':  {
                'max_revisions':    CHAIN_DEFAULT_MAX_REVISIONS,
                'risk_thresholds':  CHAIN_RISK_THRESHOLDS,
            },
        },
    })


# ──────────────────────────────────────────────────────────────────────
# Helper — persist extraction result onto a document row
# ──────────────────────────────────────────────────────────────────────
def _persist_extraction(document: IOListDocument, result: dict) -> None:
    with transaction.atomic():
        # Wipe previous extraction (re-extract path)
        document.extracted_comments.all().delete()
        document.extracted_rows.all().delete()

        # Bulk-create comments
        IOListExtractedComment.objects.bulk_create([
            IOListExtractedComment(
                document=document,
                s_no=c.get('s_no', ''),
                company_comment=c.get('company_comment', ''),
                contractor_reply=c.get('contractor_reply', ''),
                company_decision=c.get('company_decision', ''),
                status_code=c.get('status_code', ''),
                status_meaning=c.get('status_meaning', ''),
                page_number=c.get('page_number'),
                linked_tags=c.get('linked_tags', []),
            )
            for c in result.get('comments', [])
        ])

        # Bulk-create IO rows
        IOListExtractedRow.objects.bulk_create([
            IOListExtractedRow(
                document=document,
                tag_number=r.get('tag_number', ''),
                page_number=r.get('page_number'),
                data={k: v for k, v in r.items()
                      if k not in ('tag_number', 'page_number')},
            )
            for r in result.get('io_rows', [])
        ])

        document.extraction_stats = {
            **result.get('stats', {}),
            'cost_profile': result.get('cost_profile', {}),
        }
        document.status = 'completed'
        document.extraction_error = ''
        document.pdf_sha256 = result.get('sha256', document.pdf_sha256)
        document.save(update_fields=[
            'extraction_stats', 'status', 'extraction_error',
            'pdf_sha256', 'updated_at',
        ])


# ──────────────────────────────────────────────────────────────────────
# Main viewset — documents (one PDF per row)
# ──────────────────────────────────────────────────────────────────────
class IOListDocumentViewSet(viewsets.ModelViewSet):
    queryset = IOListDocument.objects.all()
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action in ('list',):
            return IOListDocumentListSerializer
        return IOListDocumentDetailSerializer

    # ---- list filters ------------------------------------------------
    def get_queryset(self):
        qs = super().get_queryset()
        chain_id = self.request.query_params.get('crs_chain_id')
        doc_no   = self.request.query_params.get('document_number')
        if chain_id:
            qs = qs.filter(crs_chain_id=chain_id)
        if doc_no:
            qs = qs.filter(document_number=doc_no)
        return qs

    # ---- create + extract synchronously (free path is fast) ----------
    def create(self, request, *args, **kwargs):
        pdf = request.FILES.get('pdf_file') or request.FILES.get('file')
        if not pdf:
            return Response(
                {'error': 'pdf_file is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pdf_bytes = pdf.read()
        pdf.seek(0)
        digest = sha256_of(pdf_bytes)

        # Hash-cache hit: re-use prior extraction if user already uploaded this PDF
        if ENABLE_HASH_CACHE:
            existing = IOListDocument.objects.filter(
                pdf_sha256=digest, status='completed',
            ).first()
            if existing:
                logger.info("[IOWF] Hash cache hit on %s", digest[:12])
                ser = self.get_serializer(existing)
                return Response(
                    {'cached': True, 'document': ser.data},
                    status=status.HTTP_200_OK,
                )

        document = IOListDocument.objects.create(
            project_name=request.data.get('project_name', '') or '',
            document_number=request.data.get('document_number', '') or '',
            revision_label=request.data.get('revision_label', '') or '',
            plant=request.data.get('plant', '') or '',
            unit=request.data.get('unit', '') or '',
            crs_chain_id=request.data.get('crs_chain_id', '') or '',
            pdf_file=pdf,
            pdf_sha256=digest,
            status='extracting',
            uploaded_by=request.user if request.user.is_authenticated else None,
        )

        try:
            result = extract_document(pdf_bytes)
            _persist_extraction(document, result)
        except Exception as exc:
            logger.exception("[IOWF] Extraction failed for doc %s", document.id)
            document.status = 'failed'
            document.extraction_error = str(exc)
            document.save(update_fields=['status', 'extraction_error',
                                          'updated_at'])
            return Response(
                {'error': 'Extraction failed', 'detail': str(exc),
                 'document_id': document.id},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        ser = self.get_serializer(document)
        return Response({'cached': False, 'document': ser.data},
                        status=status.HTTP_201_CREATED)

    # ---- re-run extraction on an existing PDF ------------------------
    @action(detail=True, methods=['post'], url_path='re-extract')
    def re_extract(self, request, pk=None):
        document = self.get_object()
        try:
            with document.pdf_file.open('rb') as f:
                pdf_bytes = f.read()
        except Exception as exc:
            return Response(
                {'error': 'Could not read PDF', 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        document.status = 'extracting'
        document.save(update_fields=['status', 'updated_at'])
        try:
            result = extract_document(pdf_bytes)
            _persist_extraction(document, result)
        except Exception as exc:
            document.status = 'failed'
            document.extraction_error = str(exc)
            document.save(update_fields=['status', 'extraction_error',
                                          'updated_at'])
            return Response(
                {'error': 'Re-extract failed', 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(self.get_serializer(document).data)

    # ---- patch a single extracted row --------------------------------
    @action(
        detail=True,
        methods=['patch'],
        url_path=r'rows/(?P<row_pk>[^/.]+)',
        url_name='patch-row',
    )
    def patch_row(self, request, pk=None, row_pk=None):
        """
        PATCH /documents/{doc_id}/rows/{row_id}/

        Body: flat dict — any subset of column keys.
        Special top-level model fields: 'tag_number', 'page_number'.
        All other keys are merged (not replaced) into the row.data JSONField.
        """
        document = self.get_object()
        try:
            row = document.extracted_rows.get(pk=row_pk)
        except IOListExtractedRow.DoesNotExist:
            raise Http404('Row not found')

        payload = request.data
        update_fields = []

        if 'tag_number' in payload:
            row.tag_number = str(payload['tag_number'])
            update_fields.append('tag_number')

        if 'page_number' in payload:
            try:
                row.page_number = int(payload['page_number'])
            except (ValueError, TypeError):
                row.page_number = None
            update_fields.append('page_number')

        # All remaining keys are merged into the data JSONField
        data_updates = {
            k: v for k, v in payload.items()
            if k not in ('tag_number', 'page_number')
        }
        if data_updates:
            row.data = {**(row.data or {}), **data_updates}
            update_fields.append('data')

        if update_fields:
            row.save(update_fields=update_fields)

        return Response(IOListExtractedRowSerializer(row).data)

    # ---- xlsx export -------------------------------------------------
    @action(detail=True, methods=['get'], url_path='export-xlsx')
    def export_xlsx(self, request, pk=None):
        document = self.get_object()
        if document.status != 'completed':
            return Response(
                {'error': 'Document extraction is not complete'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = export_document_to_xlsx(document)
        filename = (
            f'IOList_{document.document_number or document.id}_'
            f'{document.revision_label or "rev"}.xlsx'
        )
        resp = HttpResponse(
            data,
            content_type=('application/vnd.openxmlformats-'
                          'officedocument.spreadsheetml.sheet'),
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp


# ──────────────────────────────────────────────────────────────────────
# Diff endpoint — compare any two documents (typically two revisions)
# ──────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def diff_view(request):
    old_id = request.data.get('old_id')
    new_id = request.data.get('new_id')
    if not old_id or not new_id:
        return Response(
            {'error': 'old_id and new_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        old_doc = IOListDocument.objects.get(pk=old_id)
        new_doc = IOListDocument.objects.get(pk=new_id)
    except IOListDocument.DoesNotExist:
        raise Http404('One of the documents does not exist')

    def _flatten(doc):
        return [
            {'tag_number': r.tag_number, **(r.data or {})}
            for r in doc.extracted_rows.all()
        ]

    result = diff_revisions(_flatten(old_doc), _flatten(new_doc))
    return Response({
        'old_document_id': old_doc.id,
        'new_document_id': new_doc.id,
        **result,
    })
