from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.apps import apps
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.core.cors_test_views import CorsTestView, cors_health_check, railway_health_check
from apps.core.health_check import comprehensive_health_check, database_connectivity_check
from django.http import HttpResponse, JsonResponse
from django.db import connection
from django.core.exceptions import ImproperlyConfigured

# Import PID analysis models and services for export functionality
from apps.pid_analysis.models import PIDDrawing
from apps.pid_analysis.export_service import PIDReportExportService

# Import feature registry views
from apps.api.feature_views import list_features, get_feature, get_categories, get_navigation


def is_app_installed(app_label):
    """Check if a Django app is installed - for safe URL inclusion"""
    return apps.is_installed(app_label)


def railway_diagnostic_health_check(request):
    """Comprehensive health check for Railway deployment debugging"""
    status = {
        'status': 'healthy',
        'checks': {}
    }
    
    # Check Django settings
    try:
        from django.conf import settings
        status['checks']['django_settings'] = 'OK'
        status['checks']['debug_mode'] = settings.DEBUG
        status['checks']['allowed_hosts'] = settings.ALLOWED_HOSTS
    except Exception as e:
        status['checks']['django_settings'] = f'ERROR: {str(e)}'
        status['status'] = 'unhealthy'
    
    # Check database connection
    try:
        connection.ensure_connection()
        status['checks']['database'] = 'OK'
    except Exception as e:
        status['checks']['database'] = f'ERROR: {str(e)}'
        status['status'] = 'unhealthy'
    
    # Check environment variables
    import os
    critical_vars = ['DATABASE_URL', 'SECRET_KEY', 'PORT']
    missing_vars = [var for var in critical_vars if not os.environ.get(var)]
    if missing_vars:
        status['checks']['env_vars'] = f'MISSING: {", ".join(missing_vars)}'
        # In local dev, DATABASE_URL/PORT come from docker-compose, not env vars
        # Only mark as degraded, not unhealthy
        if status['status'] != 'unhealthy':
            status['status'] = 'degraded'
    else:
        status['checks']['env_vars'] = 'OK'
    
    # Check static files
    try:
        from django.contrib.staticfiles.storage import staticfiles_storage
        staticfiles_storage.exists('admin/css/base.css')
        status['checks']['static_files'] = 'OK'
    except Exception as e:
        status['checks']['static_files'] = f'WARNING: {str(e)}'
    
    # Return 200 for degraded (local dev) or healthy, only 503 for unhealthy
    response_status = 503 if status['status'] == 'unhealthy' else 200
    return JsonResponse(status, status=response_status)


def pid_export_view(request, pk):
    """Plain Django view for export - no DRF decorators"""
    
    print(f"\n{'='*60}")
    print(f"[PID EXPORT] Request received!")
    print(f"[PID EXPORT] PK: {pk}")
    print(f"[PID EXPORT] Method: {request.method}")
    print(f"[PID EXPORT] Path: {request.path}")
    print(f"{'='*60}\n")
    
    try:
        drawing = PIDDrawing.objects.get(id=pk)
        print(f"[PID EXPORT] Drawing found: {drawing.drawing_number}")
    except PIDDrawing.DoesNotExist:
        return HttpResponse('{"error": "Drawing not found"}', status=404, content_type='application/json')
    
    if not hasattr(drawing, 'analysis_report'):
        return HttpResponse('{"error": "No analysis report"}', status=404, content_type='application/json')
    
    export_format = request.GET.get('format', 'pdf')
    print(f"[PID EXPORT] Format: {export_format}")
    
    export_service = PIDReportExportService()
    
    try:
        if export_format == 'pdf':
            return export_service.export_pdf(drawing)
        elif export_format == 'excel':
            return export_service.export_excel(drawing)
        elif export_format == 'csv':
            return export_service.export_csv(drawing)
        else:
            return HttpResponse('{"error": "Invalid format"}', status=400, content_type='application/json')
    except Exception as e:
        print(f"[PID EXPORT ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return HttpResponse(f'{{"error": "{str(e)}"}}', status=500, content_type='application/json')

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Railway Health Checks
    path('api/v1/health/', railway_health_check, name='railway-health'),
    path('api/v1/health/diagnostic/', railway_diagnostic_health_check, name='railway-diagnostic'),
    
    # Comprehensive System Health Checks
    path('api/v1/system-health/', comprehensive_health_check, name='system-health'),
    path('api/v1/database-check/', database_connectivity_check, name='database-check'),
    path('health/', railway_diagnostic_health_check, name='health-check'),  # Alternative endpoint
    
    # CORS diagnostic endpoints (no auth required)
    path('api/v1/cors-test/', CorsTestView.as_view(), name='cors-test'),
    path('api/v1/cors/health/', cors_health_check, name='cors-health'),
    
    # Feature Registry API (Dynamic Feature Discovery)
    path('api/v1/features/', list_features, name='list-features'),
    path('api/v1/features/<str:feature_id>/', get_feature, name='get-feature'),
    path('api/v1/features/meta/categories/', get_categories, name='feature-categories'),
    path('api/v1/features/meta/navigation/', get_navigation, name='feature-navigation'),
    
    # API endpoints - Core
    path('api/v1/', include('apps.api.urls')),
    # path('api/v1/core/', include('apps.core.urls')),  # REMOVED: Duplicate - already included via apps.api.urls
    path('api/v1/rbac/', include('apps.rbac.urls')),
    path('api/v1/users/', include('apps.users.urls')),  # User management endpoints
    path('api/v1/timesheet/', include('apps.timesheet.urls')),  # Time Sheet Analytics (SQL Server)
    path('api/v1/enquiry/', include('apps.core.urls_enquiry')),  # Public enquiry endpoint
    
    # API endpoints - Features (Plugin Architecture)
    path('api/v1/pid/', include('apps.pid_analysis.urls')),
    path('api/v1/pfd/', include('apps.pfd_converter.urls')),
    path('api/v1/crs/', include('apps.crs.urls')),
    path('api/v1/finance/', include('apps.finance.urls')),  # Finance Invoice Automation
    path('api/v1/payroll/', include('apps.payroll.urls')),  # Payroll Intelligence Platform
    path('api/v1/payroll-engine/', include('apps.payroll_engine.urls')),  # Payroll Engine — monthly automation
    path('api/v1/onboarding/', include('apps.onboarding.urls')),  # Onboarding & Offboarding — employee lifecycle management
    path('api/v1/site-visits/', include('apps.site_visits.urls')),  # Site Visit Tracking — GPS attendance for off-site engineers
    path('api/v1/invoice-tracker/', include('apps.invoice_tracker.urls')),  # Invoice Tracker (A/R) — Excel-driven + S3 attachments
    path('api/v1/designiq/', include('apps.designiq.urls')),  # DesignIQ - AI Design Intelligence
    path('api/v1/process-datasheet/', include('apps.process_datasheet.urls')),  # Process Datasheet
    path('api/v1/electrical-datasheet/', include('apps.electrical_datasheet.urls')),  # Electrical Datasheet with Transformer & Switchgear
    path('api/v1/usage/', include('apps.usage_tracking.urls')),  # Usage Tracking & Internal Analytics
    path('api/v1/instrument-tools/', include('apps.instrument_tools.urls')),  # Instrument Tools — IO List / Cable Block / Cable Schedule
    path('api/v1/instrument-io-workflow/', include('apps.instrument_io_workflow.urls')),  # CRS-style multi-revision IO List documents
    path('api/v1/marketing-analytics/', include('apps.marketing_analytics.urls')),  # GA4 Real-time
    path('api/v1/projects/', include('apps.core.project_urls')),
    path('api/v1/project-control/', include('apps.project_control.urls')),  # Project Management — cost dashboards, estimates, documents
    path('api/v1/dashboard/', include('apps.dashboard.urls')),  # Personal Dashboard — role-scoped bundles + AI insights
]

# ✨ SMART URL LOADING - Conditionally include optional app URLs
if is_app_installed('apps.qhse'):
    urlpatterns.append(path('api/v1/qhse/', include('apps.qhse.urls')))
    print("[URL] ✅ QHSE URLs registered")

if is_app_installed('apps.procurement'):
    urlpatterns.append(path('api/v1/procurement/', include('apps.procurement.urls')))
    print("[URL] ✅ Procurement URLs registered")

if is_app_installed('apps.notifications'):
    urlpatterns.append(path('api/v1/notifications/', include('apps.notifications.urls')))
    print("[URL] ✅ Notifications URLs registered")

if is_app_installed('apps.activity'):
    urlpatterns.append(path('api/v1/activity/', include('apps.activity.urls')))
    print("[URL] ✅ Activity URLs registered")

if is_app_installed('apps.sales'):
    urlpatterns.append(path('api/v1/sales/', include('apps.sales.urls')))
    print("[URL] ✅ Sales URLs registered")

if is_app_installed('apps.wrench_integration'):
    urlpatterns.append(path('api/v1/wrench/', include('apps.wrench_integration.urls')))
    print("[URL] ✅ Wrench Integration URLs registered")

# Data Mining Platform — AI-powered data integration with Wrench
if is_app_installed('apps.data_mining'):
    urlpatterns.append(path('api/v1/data-mining/', include('apps.data_mining.urls')))
    print("[URL] ✅ Data Mining Platform URLs registered")

# P&ID Verification — deterministic quality checker
if is_app_installed('apps.pid_verification'):
    urlpatterns.append(path('api/v1/pid-verification/', include('apps.pid_verification.urls')))
    print("[URL] ✅ P&ID Verification URLs registered")

# SLD Verification — electrical single line diagram quality checker
if is_app_installed('apps.sld_verification'):
    urlpatterns.append(path('api/v1/sld-verification/', include('apps.sld_verification.urls')))
    print("[URL] ✅ SLD Verification URLs registered")

# PFD Quality Checker — deterministic rule engine
if is_app_installed('apps.pfd_quality'):
    urlpatterns.append(path('api/v1/pfd-quality/', include('apps.pfd_quality.urls')))
    print("[URL] ✅ PFD Quality URLs registered")

# Cross Recommendation Bridge — PID ↔ PFD smart suggestions
if is_app_installed('apps.cross_recommendation'):
    urlpatterns.append(path('api/v1/cross-recommendation/', include('apps.cross_recommendation.urls')))

# Non-TEFF Metadata Extractor — multi-format document metadata extraction
if is_app_installed('apps.non_teff_metadata'):
    urlpatterns.append(path('api/v1/non-teff/', include('apps.non_teff_metadata.urls')))
    print("[URL] ✅ Cross Recommendation URLs registered")

# Spec Customization — Paper Spec PDF extraction (Piping Classes)
if is_app_installed('apps.spec_customization'):
    urlpatterns.append(path('api/v1/spec-customization/', include('apps.spec_customization.urls')))
    print("[URL] ✅ Spec Customization URLs registered")

# MLflow Model Orchestration API (DISABLED - not in use)
# urlpatterns.extend([
#     path('api/v1/mlflow/', include('apps.mlflow_integration.urls')),
# ])

urlpatterns.extend([
    # Add new feature URLs here - no routing changes needed!
    
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='swagger-ui'),  name='swagger-ui'),
])

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
