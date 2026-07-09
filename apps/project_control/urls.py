"""URL routes for Project Management — mounted at /api/v1/project-control/."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ChangeEventViewSet,
    CostSnapshotViewSet,
    EstimateLineItemViewSet,
    EstimateViewSet,
    ProjectAnalyticsViewSet,
    ProjectDocumentViewSet,
    WBSNodeViewSet,
    phase_flags_view,
)

router = DefaultRouter()
router.register(r'estimates', EstimateViewSet, basename='project-control-estimate')
router.register(r'estimate-line-items', EstimateLineItemViewSet, basename='project-control-line-item')
router.register(r'wbs-nodes', WBSNodeViewSet, basename='project-control-wbs')
router.register(r'documents', ProjectDocumentViewSet, basename='project-control-document')
router.register(r'cost-snapshots', CostSnapshotViewSet, basename='project-control-snapshot')
router.register(r'change-events', ChangeEventViewSet, basename='project-control-change')
router.register(r'analytics', ProjectAnalyticsViewSet, basename='project-control-analytics')

urlpatterns = [
    path('phase-flags/', phase_flags_view, name='project-control-phase-flags'),
    path('', include(router.urls)),
]
