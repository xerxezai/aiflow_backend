"""URL routing for the Payroll Engine.

Mounted at /api/v1/payroll-engine/ from config/urls.py.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    PayrollAdjustmentViewSet, PayrollEmployeeViewSet, PayrollRunViewSet,
    PayslipLineItemViewSet, PayslipViewSet, PayrollWorkflowLogViewSet,
    PayrollComparisonViewSet,
    catalog_view, engine_dashboard_summary,
)

router = DefaultRouter()
router.register(r'employees',    PayrollEmployeeViewSet,    basename='payroll-employee')
router.register(r'runs',         PayrollRunViewSet,         basename='payroll-run')
router.register(r'payslips',     PayslipViewSet,            basename='payroll-payslip')
router.register(r'line-items',   PayslipLineItemViewSet,    basename='payroll-line-item')
router.register(r'adjustments',  PayrollAdjustmentViewSet,  basename='payroll-adjustment')
router.register(r'comparisons',  PayrollComparisonViewSet,  basename='payroll-comparison')
router.register(r'workflow-log', PayrollWorkflowLogViewSet, basename='payroll-workflow-log')

urlpatterns = [
    path('catalog/',           catalog_view,             name='payroll-engine-catalog'),
    path('dashboard-summary/', engine_dashboard_summary, name='payroll-engine-dashboard'),
    path('', include(router.urls)),
]
