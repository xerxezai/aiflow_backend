"""
Finance URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    InvoiceViewSet, 
    ApprovalRouteViewSet, 
    approval_action, 
    dashboard_stats,
    get_approval_details,
    submit_approval_decision
)

# Import salary slip views
from .salary_views import (
    EmployeeSalaryInfoViewSet,
    SalaryComponentViewSet,
    EmployeeSalaryComponentViewSet,
    PayrollRunViewSet,
    SalarySlipViewSet,
    SalarySlipApprovalViewSet,
    SalarySlipEmailViewSet,
    SalarySlipAuditLogViewSet,
    PayrollScheduleViewSet,
    slip_download_pdf,
)

# Import workflow views
from .workflow_views import PayrollWorkflowViewSet

app_name = 'finance'

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'approval-routes', ApprovalRouteViewSet, basename='approval-route')

# Salary slip routes
router.register(r'employee-salary-info', EmployeeSalaryInfoViewSet, basename='employee-salary-info')
router.register(r'salary-components', SalaryComponentViewSet, basename='salary-component')
router.register(r'employee-salary-components', EmployeeSalaryComponentViewSet, basename='employee-salary-component')
router.register(r'payroll-runs', PayrollRunViewSet, basename='payroll-run')
router.register(r'salary-slips', SalarySlipViewSet, basename='salary-slip')
router.register(r'salary-approvals', SalarySlipApprovalViewSet, basename='salary-approval')
router.register(r'salary-emails', SalarySlipEmailViewSet, basename='salary-email')
router.register(r'salary-audit-logs', SalarySlipAuditLogViewSet, basename='salary-audit-log')
router.register(r'payroll-schedule', PayrollScheduleViewSet, basename='payroll-schedule')

# Payroll workflow routes
router.register(r'payroll-workflows', PayrollWorkflowViewSet, basename='payroll-workflow')

urlpatterns = [
    # Router URLs
    path('', include(router.urls)),
    
    # Approval details and decision endpoints (no auth required - token-based)
    path('approval/<uuid:token>/details/', get_approval_details, name='approval-details'),
    path('approval/<uuid:token>/submit/', submit_approval_decision, name='approval-submit'),
    
    # Legacy approval action (email links)
    path('approve/<uuid:token>/', approval_action, name='approval-action'),
    
    # Dashboard
    path('dashboard/stats/', dashboard_stats, name='dashboard-stats'),

    # Salary slip PDF download (presigned S3 URL or local stream)
    path('salary-slips/<uuid:slip_id>/download-pdf/', slip_download_pdf, name='slip-download-pdf'),
]
