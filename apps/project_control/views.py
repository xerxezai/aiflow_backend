"""Project Management — DRF ViewSets.

URL roots (wired in apps.project_control.urls):
    /api/v1/project-control/estimates/
    /api/v1/project-control/estimate-line-items/
    /api/v1/project-control/wbs-nodes/
    /api/v1/project-control/documents/
    /api/v1/project-control/cost-snapshots/
    /api/v1/project-control/change-events/
    /api/v1/project-control/analytics/...
    /api/v1/project-control/phase-flags/
"""
from __future__ import annotations

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.project_models import Project

from .config import (
    DOCUMENT_KINDS, MAX_DOCUMENT_BYTES, PHASE_FLAGS, VARIANCE_THRESHOLDS,
    is_phase_enabled,
)
from .models import (
    ChangeEvent, CostSnapshot, Estimate, EstimateLineItem,
    ProjectDocument, WBSNode,
)
from .serializers import (
    ChangeEventSerializer, CostSnapshotSerializer,
    EstimateLineItemSerializer, EstimateListSerializer, EstimateSerializer,
    ProjectDocumentSerializer, WBSNodeSerializer,
)
from .services.excel_import import import_boq_excel
from .services.finance_sync import sync_project_spend
from .services.kpis import compute_project_kpis
from .services.s3 import presign_document_download
from .services.variance import compute_variance
from .tasks import parse_uploaded_document

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Read-only phase flags endpoint — keeps frontend in sync without a rebuild.
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def phase_flags_view(request):
    return Response({
        'phase_flags': PHASE_FLAGS,
        'variance_thresholds': VARIANCE_THRESHOLDS,
        'document_kinds': DOCUMENT_KINDS,
        'max_document_bytes': MAX_DOCUMENT_BYTES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Filter mixin — every viewset accepts ?project=<id>
# ─────────────────────────────────────────────────────────────────────────────
class _ProjectFilteredMixin:
    def get_queryset(self):
        qs = super().get_queryset().filter(is_deleted=False)
        pid = self.request.query_params.get('project')
        if pid:
            qs = qs.filter(project_id=pid)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# Estimate viewset
# ─────────────────────────────────────────────────────────────────────────────
class EstimateViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = Estimate.objects.all().select_related('project').prefetch_related('line_items')

    def get_serializer_class(self):
        if self.action == 'list':
            return EstimateListSerializer
        return EstimateSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user if self.request.user.is_authenticated else None)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        est = self.get_object()
        est.status = 'approved'
        est.save(update_fields=['status', 'updated_at'])
        return Response(EstimateSerializer(est).data)

    @action(detail=True, methods=['post'])
    def supersede(self, request, pk=None):
        est = self.get_object()
        est.status = 'superseded'
        est.save(update_fields=['status', 'updated_at'])
        return Response(EstimateSerializer(est).data)


class EstimateLineItemViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EstimateLineItemSerializer
    queryset = EstimateLineItem.objects.all().filter(is_deleted=False)

    def get_queryset(self):
        qs = super().get_queryset()
        est = self.request.query_params.get('estimate')
        if est:
            qs = qs.filter(estimate_id=est)
        return qs


# ─────────────────────────────────────────────────────────────────────────────
# WBS viewset
# ─────────────────────────────────────────────────────────────────────────────
class WBSNodeViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = WBSNodeSerializer
    queryset = WBSNode.objects.all()


# ─────────────────────────────────────────────────────────────────────────────
# Document viewset — file uploads + presign + Excel-import shortcut
# ─────────────────────────────────────────────────────────────────────────────
class ProjectDocumentViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectDocumentSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = ProjectDocument.objects.all().select_related('project', 'uploaded_by')

    def perform_create(self, serializer):
        file = serializer.validated_data.get('file')
        if file and file.size and file.size > MAX_DOCUMENT_BYTES:
            raise ValueError(f'File exceeds maximum allowed size ({MAX_DOCUMENT_BYTES} bytes).')

        doc = serializer.save(
            uploaded_by=self.request.user if self.request.user.is_authenticated else None,
            original_filename=getattr(file, 'name', ''),
            content_type=getattr(file, 'content_type', ''),
            size_bytes=getattr(file, 'size', 0) or 0,
            parse_status='queued',
        )
        # Best-effort async; fall back to inline on broker failure.
        try:
            parse_uploaded_document.delay(doc.id)
        except Exception as exc:  # noqa: BLE001
            logger.info('parse_uploaded_document.delay failed (%s); running inline', exc)
            try:
                parse_uploaded_document(doc.id)
            except Exception as inner:  # noqa: BLE001
                logger.warning('inline parse_uploaded_document failed: %s', inner)

    @action(detail=True, methods=['get'], url_path='presign-download')
    def presign_download(self, request, pk=None):
        doc = self.get_object()
        url = presign_document_download(doc)
        return Response({'document_id': doc.id, 'download_url': url})

    @action(detail=False, methods=['post'], url_path='import-boq')
    def import_boq(self, request):
        """Upload an Excel BOQ + create an Estimate in one shot."""
        project_id = request.data.get('project')
        if not project_id:
            return Response({'error': 'project is required'}, status=status.HTTP_400_BAD_REQUEST)
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'file is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            project = Project.objects.get(pk=project_id, is_deleted=False)
        except Project.DoesNotExist:
            return Response({'error': 'project not found'}, status=status.HTTP_404_NOT_FOUND)

        kind = request.data.get('kind') or 'estimate'
        title = request.data.get('title', '')
        notes = request.data.get('notes', '')

        # Save as document first so the estimate retains an audit link.
        doc = ProjectDocument.objects.create(
            project=project,
            kind='boq',
            title=title or file.name,
            file=file,
            original_filename=file.name,
            content_type=getattr(file, 'content_type', ''),
            size_bytes=getattr(file, 'size', 0) or 0,
            uploaded_by=request.user if request.user.is_authenticated else None,
            parse_status='done',
        )
        try:
            doc.file.open('rb')
            summary = import_boq_excel(
                project=project, file_obj=doc.file, kind=kind,
                title=title, notes=notes, user=request.user, source_document=doc,
            )
        except Exception as exc:  # noqa: BLE001
            doc.parse_status = 'failed'
            doc.parse_error = str(exc)
            doc.save(update_fields=['parse_status', 'parse_error', 'updated_at'])
            return Response({'error': str(exc), 'document_id': doc.id},
                            status=status.HTTP_400_BAD_REQUEST)
        finally:
            try:
                doc.file.close()
            except Exception:
                pass

        return Response({'document': ProjectDocumentSerializer(doc).data, 'summary': summary},
                        status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot + Change viewsets (Phase 3/4 read mostly)
# ─────────────────────────────────────────────────────────────────────────────
class CostSnapshotViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CostSnapshotSerializer
    queryset = CostSnapshot.objects.all()


class ChangeEventViewSet(_ProjectFilteredMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangeEventSerializer
    queryset = ChangeEvent.objects.all()


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — Phase 1 endpoints + Phase 2-4 stubs (501)
# ─────────────────────────────────────────────────────────────────────────────
class ProjectAnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    basename = 'project-control-analytics'

    def _get_project(self, request):
        pid = request.query_params.get('project') or request.data.get('project')
        if not pid:
            return None, Response({'error': 'project is required'},
                                  status=status.HTTP_400_BAD_REQUEST)
        try:
            return Project.objects.get(pk=pid, is_deleted=False), None
        except Project.DoesNotExist:
            return None, Response({'error': 'project not found'},
                                  status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'], url_path='cost-kpis')
    def cost_kpis(self, request):
        if not is_phase_enabled('phase_1_cost_dashboard'):
            return Response({'error': 'phase_1_cost_dashboard disabled'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        project, err = self._get_project(request)
        if err:
            return err
        return Response(compute_project_kpis(project))

    @action(detail=False, methods=['get'], url_path='estimate-variance')
    def estimate_variance(self, request):
        if not is_phase_enabled('phase_1_estimate_variance'):
            return Response({'error': 'phase_1_estimate_variance disabled'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        project, err = self._get_project(request)
        if err:
            return err
        base_id = request.query_params.get('base')
        cmp_id  = request.query_params.get('compare')
        group_by = request.query_params.get('group_by', 'wbs')
        base = Estimate.objects.filter(pk=base_id, is_deleted=False).first() if base_id else None
        cmp_ = Estimate.objects.filter(pk=cmp_id,  is_deleted=False).first() if cmp_id  else None
        return Response(compute_variance(
            project=project, base_estimate=base, compare_estimate=cmp_, group_by=group_by,
        ))

    @action(detail=False, methods=['post'], url_path='finance-sync')
    def finance_sync(self, request):
        if not is_phase_enabled('phase_1_finance_sync'):
            return Response({'error': 'phase_1_finance_sync disabled'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        project, err = self._get_project(request)
        if err:
            return err
        return Response(sync_project_spend(project))

    # Phase 2/3/4 stubs — all return 501 with a uniform shape so the frontend
    # can render a "Coming in Phase X" card without bespoke handling.
    def _phase_stub(self, request, flag, label):
        return Response({
            'error': 'not_implemented',
            'phase_flag': flag,
            'message': f'{label} ships when {flag.upper()} flag is enabled.',
        }, status=status.HTTP_501_NOT_IMPLEMENTED)

    @action(detail=False, methods=['post'], url_path='ai-takeoff')
    def ai_takeoff(self, request):
        if is_phase_enabled('phase_2_ai_takeoff'):
            return Response({'message': 'AI Take-Off endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_2_ai_takeoff', 'AI Take-Off')

    @action(detail=False, methods=['get'], url_path='evm')
    def evm(self, request):
        if is_phase_enabled('phase_3_evm_forecast'):
            return Response({'message': 'EVM endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_3_evm_forecast', 'EVM Forecasting')

    @action(detail=False, methods=['get'], url_path='cashflow')
    def cashflow(self, request):
        if is_phase_enabled('phase_3_cashflow_curve'):
            return Response({'message': 'Cashflow endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_3_cashflow_curve', 'Cashflow Curve')

    @action(detail=False, methods=['get'], url_path='risk')
    def risk(self, request):
        if is_phase_enabled('phase_4_risk_analytics'):
            return Response({'message': 'Risk endpoint placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_4_risk_analytics', 'Risk Analytics')

    @action(detail=False, methods=['post'], url_path='change-detection')
    def change_detection(self, request):
        if is_phase_enabled('phase_4_change_detection'):
            return Response({'message': 'Change Detection placeholder — implementation pending.'})
        return self._phase_stub(request, 'phase_4_change_detection', 'Change Detection')
