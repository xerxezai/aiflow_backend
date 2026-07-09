from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import IOListDocumentViewSet, config_view, diff_view

app_name = 'instrument_io_workflow'

router = DefaultRouter()
router.register(r'documents', IOListDocumentViewSet, basename='io-document')

urlpatterns = [
    path('config/', config_view, name='config'),
    path('diff/',   diff_view,   name='diff'),
    path('',        include(router.urls)),
]
