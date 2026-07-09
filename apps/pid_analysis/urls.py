from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PIDDrawingViewSet, 
    PIDAnalysisReportViewSet, 
    PIDIssueViewSet,
    ReferenceDocumentViewSet,
    import_linelist_from_designiq,
)
from .project_views import PIDProjectViewSet
from .history_views import (
    pid_history_overview,
    pid_all_uploads,
    pid_all_analyses,
    download_pid_drawing,
    download_pid_report,
    delete_pid_drawing
)
from .pressure_instrument_views import (
    analyze_pid_for_pressure_instruments,
    download_pressure_instrument_excel,
    get_instrument_types
)
from .instrument_index_views import (
    extract_instrument_index,
    download_instrument_index_excel,
    get_instrument_categories,
)
from .cable_block_views import (
    extract_cable_block_diagram,
    download_cable_block_excel,
)
from .equipment_analysis_views import (
    analyze_pid_equipment,
    analyze_pid_equipment_batch,
    download_equipment_excel,
    get_equipment_analysis_results,
    get_equipment_analysis_status
)
from rest_framework.decorators import api_view, permission_classes
from rest_framework import permissions
from rest_framework.response import Response
from django.http import HttpResponse

# Export endpoint - using test-export pattern because drawings/pk/export doesn't work
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def test_export(request, pk):
    """Export drawing report in PDF/Excel/CSV format - accessible at /test-export/{pk}/"""
    from .models import PIDDrawing
    from .export_service import PIDReportExportService
    
    print(f"[EXPORT via TEST] Reached export endpoint! PK={pk}")
    
    try:
        drawing = PIDDrawing.objects.get(id=pk)
        print(f"[EXPORT via TEST] Drawing found: {drawing.drawing_number}")
    except PIDDrawing.DoesNotExist:
        return Response({'error': 'Drawing not found'}, status=404)
    
    if not hasattr(drawing, 'analysis_report'):
        return Response({'error': 'No analysis report available'}, status=404)
    
    export_format = request.query_params.get('format', 'pdf')
    print(f"[EXPORT via TEST] Format: {export_format}")
    
    export_service = PIDReportExportService()
    
    try:
        if export_format == 'pdf':
            return export_service.export_pdf(drawing)
        elif export_format == 'excel':
            return export_service.export_excel(drawing)
        elif export_format == 'csv':
            return export_service.export_csv(drawing)
        else:
            return Response({'error': 'Invalid format'}, status=400)
    except Exception as e:
        print(f"[EXPORT via TEST ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)

# Export endpoint - EXACT COPY of test_export but with export logic
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def export_drawing(request, pk):
    """Export drawing report in PDF/Excel/CSV format"""
    from .models import PIDDrawing
    from .export_service import PIDReportExportService
    
    print(f"[EXPORT DRAWING] Reached export endpoint! PK={pk}")
    
    try:
        drawing = PIDDrawing.objects.get(id=pk)
        print(f"[EXPORT DRAWING] Drawing found: {drawing.drawing_number}")
    except PIDDrawing.DoesNotExist:
        return Response({'error': 'Drawing not found'}, status=404)
    
    if not hasattr(drawing, 'analysis_report'):
        return Response({'error': 'No analysis report available'}, status=404)
    
    export_format = request.query_params.get('format', 'pdf')
    print(f"[EXPORT DRAWING] Format: {export_format}")
    
    export_service = PIDReportExportService()
    
    try:
        if export_format == 'pdf':
            return export_service.export_pdf(drawing)
        elif export_format == 'excel':
            return export_service.export_excel(drawing)
        elif export_format == 'csv':
            return export_service.export_csv(drawing)
        else:
            return Response({'error': 'Invalid format'}, status=400)
    except Exception as e:
        print(f"[EXPORT DRAWING ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)

# Direct export handler that bypasses ViewSet
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def direct_export(request, pk):
    """Direct export endpoint that doesn't rely on ViewSet"""
    from .models import PIDDrawing
    from .export_service import PIDReportExportService
    
    print(f"\n[DIRECT EXPORT] ===== DIRECT EXPORT CALLED =====")
    print(f"[DIRECT EXPORT] PK: {pk}, Format: {request.query_params.get('format', 'pdf')}")
    
    try:
        drawing = PIDDrawing.objects.get(id=pk)
    except PIDDrawing.DoesNotExist:
        return Response({'error': 'Drawing not found'}, status=404)
    
    if not hasattr(drawing, 'analysis_report'):
        return Response({'error': 'No analysis report available'}, status=404)
    
    export_format = request.query_params.get('format', 'pdf')
    export_service = PIDReportExportService()
    
    try:
        if export_format == 'pdf':
            return export_service.export_pdf(drawing)
        elif export_format == 'excel':
            return export_service.export_excel(drawing)
        elif export_format == 'csv':
            return export_service.export_csv(drawing)
        else:
            return Response({'error': 'Invalid format'}, status=400)
    except Exception as e:
        print(f"[DIRECT EXPORT ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)

router = DefaultRouter()
router.register(r'projects', PIDProjectViewSet, basename='pid-project')
router.register(r'drawings', PIDDrawingViewSet, basename='pid-drawing')
router.register(r'reports', PIDAnalysisReportViewSet, basename='pid-report')
router.register(r'issues', PIDIssueViewSet, basename='pid-issue')
router.register(r'reference-documents', ReferenceDocumentViewSet, basename='reference-document')

urlpatterns = [
    # Pressure Instrument Analysis endpoints
    path('pressure-instruments/analyze/', analyze_pid_for_pressure_instruments, name='pressure-instrument-analyze'),
    path('pressure-instruments/download-excel/', download_pressure_instrument_excel, name='pressure-instrument-download'),
    path('pressure-instruments/types/', get_instrument_types, name='pressure-instrument-types'),

    # Instrument Index — ALL instruments extraction
    path('instrument-index/analyze/', extract_instrument_index, name='instrument-index-analyze'),
    path('instrument-index/download-excel/<str:upload_id>/', download_instrument_index_excel, name='instrument-index-download'),
    path('instrument-index/categories/', get_instrument_categories, name='instrument-index-categories'),

    # Cable Block Diagram — ADNOC-aligned JB / multicore / cabinet layout
    path('cable-block-diagram/analyze/', extract_cable_block_diagram, name='cable-block-diagram-analyze'),
    path('cable-block-diagram/download-excel/<str:upload_id>/', download_cable_block_excel, name='cable-block-diagram-download'),
    
    # Equipment Analysis endpoints
    path('equipment/analyze/', analyze_pid_equipment, name='equipment-analyze'),
    path('equipment/analyze-batch/', analyze_pid_equipment_batch, name='equipment-analyze-batch'),
    path('equipment/download-excel/<str:upload_id>/', download_equipment_excel, name='equipment-download-excel'),
    path('equipment/results/<str:upload_id>/', get_equipment_analysis_results, name='equipment-results'),
    path('equipment/status/<str:upload_id>/', get_equipment_analysis_status, name='equipment-status'),
    
    # History endpoints
    path('history/', pid_history_overview, name='pid-history-overview'),
    path('history/uploads/', pid_all_uploads, name='pid-history-uploads'),
    path('history/analyses/', pid_all_analyses, name='pid-history-analyses'),
    path('history/download/drawing/<int:drawing_id>/', download_pid_drawing, name='pid-history-download-drawing'),
    path('history/download/report/<int:report_id>/', download_pid_report, name='pid-history-download-report'),
    path('history/delete/drawing/<int:drawing_id>/', delete_pid_drawing, name='pid-history-delete-drawing'),
    
    # Export endpoint - using different pattern to avoid router conflict
    path('export/<int:pk>/', export_drawing, name='pid-drawing-export'),
    # Test URL
    path('test-export/<int:pk>/', test_export, name='test-export'),
    # DesignIQ line list import (SOFT-CODED: fetch structured line list for P&ID cross-checking)
    path('import-linelist/', import_linelist_from_designiq, name='pid-import-linelist'),
    # Router URLs LAST
    path('', include(router.urls)),
]
