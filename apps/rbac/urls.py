"""
RBAC URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrganizationViewSet, ModuleViewSet, PermissionViewSet,
    RoleViewSet, UserProfileViewSet, AuditLogViewSet, StorageViewSet,
    AccessRequestViewSet,
    # Analytics ViewSets
    AnalyticsDashboardViewSet, SystemMetricsViewSet, UserActivityAnalyticsViewSet,
    SecurityAlertViewSet, PredictiveInsightViewSet, FeatureUsageAnalyticsViewSet,
    ErrorLogAnalyticsViewSet, SystemHealthCheckViewSet,
    UserExportView,
)
from .dashboard_views import (
    user_dashboard_stats, user_files_list, user_activity_timeline
)
from .ai_champion_views import AIChampionViewSet
from .activity_report_views import ActivityReportViewSet
from apps.users.views_password import change_password

router = DefaultRouter()
# RBAC Core
router.register(r'organizations', OrganizationViewSet, basename='organization')
router.register(r'modules', ModuleViewSet, basename='module')
router.register(r'permissions', PermissionViewSet, basename='permission')
router.register(r'roles', RoleViewSet, basename='role')
router.register(r'users', UserProfileViewSet, basename='user')
router.register(r'audit-logs', AuditLogViewSet, basename='audit-log')
router.register(r'storage', StorageViewSet, basename='storage')
router.register(r'access-requests', AccessRequestViewSet, basename='access-request')

# AI-Powered Analytics
router.register(r'analytics/dashboard', AnalyticsDashboardViewSet, basename='analytics-dashboard')
router.register(r'analytics/system-metrics', SystemMetricsViewSet, basename='system-metrics')
router.register(r'analytics/user-activity', UserActivityAnalyticsViewSet, basename='user-activity')
router.register(r'analytics/security-alerts', SecurityAlertViewSet, basename='security-alerts')
router.register(r'analytics/predictions', PredictiveInsightViewSet, basename='predictions')
router.register(r'analytics/feature-usage', FeatureUsageAnalyticsViewSet, basename='feature-usage')
router.register(r'analytics/error-logs', ErrorLogAnalyticsViewSet, basename='error-logs')
router.register(r'analytics/health-checks', SystemHealthCheckViewSet, basename='health-checks')

# AI Champion of the Month — gamification, tracking, cost analytics
router.register(r'ai-champion', AIChampionViewSet, basename='ai-champion')

# Activity Reports — admin-only engagement analytics (weekly, monthly, by-user, by-feature)
router.register(r'activity-reports', ActivityReportViewSet, basename='activity-reports')

urlpatterns = [
    # User Export — must come BEFORE router.urls to prevent users/{pk}/ swallowing 'export' as a pk
    path('users/export/', UserExportView.as_view(), name='user-export-users'),
    path('', include(router.urls)),
    # User Dashboard endpoints
    path('dashboard/stats/', user_dashboard_stats, name='user-dashboard-stats'),
    path('dashboard/files/', user_files_list, name='user-files-list'),
    path('dashboard/activity/', user_activity_timeline, name='user-activity-timeline'),
    # Password management
    path('users/change-password/', change_password, name='rbac-change-password'),
    # Subscription Management (7.3)
    path('subscriptions/', include('apps.rbac.subscription_urls')),
]
