"""
Onboarding & Offboarding URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'onboarding', views.OnboardingRecordViewSet, basename='onboarding')
router.register(r'offboarding', views.OffboardingRecordViewSet, basename='offboarding')
router.register(r'equipment', views.EquipmentViewSet, basename='equipment')
router.register(r'documents', views.DocumentViewSet, basename='documents')
router.register(r'access', views.AccessProvisioningViewSet, basename='access')
router.register(r'checklist', views.ChecklistViewSet, basename='checklist')

urlpatterns = [
    path('', include(router.urls)),
]
