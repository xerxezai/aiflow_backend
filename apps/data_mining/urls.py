"""
Data Mining URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    DataMiningProjectViewSet,
    TransformationPipelineViewSet,
    WrenchDocumentSearchViewSet,
)

router = DefaultRouter()
router.register(r'projects', DataMiningProjectViewSet, basename='data-mining-project')
router.register(r'pipelines', TransformationPipelineViewSet, basename='transformation-pipeline')
router.register(r'wrench', WrenchDocumentSearchViewSet, basename='wrench-search')

urlpatterns = [
    path('', include(router.urls)),
]
