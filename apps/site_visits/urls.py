"""
Site Visit Tracking — URL Configuration
========================================
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'sites', views.ClientSiteViewSet, basename='site')
router.register(r'requests', views.SiteVisitRequestViewSet, basename='site-visit-request')
router.register(r'check-ins', views.SiteVisitCheckInViewSet, basename='site-visit-checkin')

urlpatterns = [
    path('', include(router.urls)),
]
