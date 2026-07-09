from django.urls import path
from .views import (
    PersonalDashboardView,
    PersonalInsightsView,
    ProjectControlBundleView,
)

urlpatterns = [
    path('personal/', PersonalDashboardView.as_view(), name='personal-dashboard'),
    path('personal/insights/', PersonalInsightsView.as_view(), name='personal-insights'),
    path('personal/project-control/', ProjectControlBundleView.as_view(), name='personal-project-control'),
]
