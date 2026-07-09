"""
Payroll Intelligence — URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    PayrollDashboardSummaryView,
    PayrollValidationLogViewSet,
    PayrollAuditAlertViewSet,
    ProjectCostAllocationViewSet,
    AIInsightSnapshotViewSet,
    EmployeeLeaveRecordViewSet,
    LeaveTypeViewSet,
    LeaveRequestViewSet,
    leave_calendar,
    branch_employee_codes,
    PublicHolidayViewSet,
    AttendanceOverrideViewSet,
    SalaryComponentViewSet,
    EmployeeSalaryStructureViewSet,
    SalaryHistoryViewSet,
    annual_leave_balance,
    sync_leave_data,
    initialize_current_month_leave,
    DailyWorkLogViewSet,
    generate_master_payroll,
    master_payroll_history,
    master_payroll_download,
    master_payroll_rows,
    master_payroll_delete,
    export_rows_to_excel,
    ai_analytics_generate,
    master_payroll_freeze,
    master_payroll_unfreeze,
    master_payroll_hr_approve,
    master_payroll_finance_review,
    master_payroll_finance_approve,
    master_payroll_release,
    master_payroll_workflow_status,
    master_payroll_row_update,
    master_payroll_approval_tracker,
    leave_encashment_status,
    leave_encashment_preview,
    leave_encashment_run,
    leave_encashment_list,
)

app_name = 'payroll'

router = DefaultRouter()
router.register(r'validation-logs',       PayrollValidationLogViewSet,    basename='validation-log')
router.register(r'audit-alerts',          PayrollAuditAlertViewSet,       basename='audit-alert')
router.register(r'project-costs',         ProjectCostAllocationViewSet,   basename='project-cost')
router.register(r'ai-insights',           AIInsightSnapshotViewSet,       basename='ai-insight')
router.register(r'leave-records',         EmployeeLeaveRecordViewSet,     basename='leave-record')
router.register(r'leave-types',           LeaveTypeViewSet,               basename='leave-type')
router.register(r'leave-requests',        LeaveRequestViewSet,            basename='leave-request')
router.register(r'public-holidays',       PublicHolidayViewSet,              basename='public-holiday')
router.register(r'attendance-overrides',  AttendanceOverrideViewSet,         basename='attendance-override')
router.register(r'salary-components',     SalaryComponentViewSet,            basename='salary-component')
router.register(r'salary-structures',     EmployeeSalaryStructureViewSet,    basename='salary-structure')
router.register(r'salary-history',        SalaryHistoryViewSet,              basename='salary-history')
router.register(r'daily-logs',            DailyWorkLogViewSet,               basename='daily-log')

urlpatterns = [
    path('dashboard-summary/',       PayrollDashboardSummaryView.as_view(), name='dashboard-summary'),
    path('leave-calendar/',          leave_calendar,                        name='leave-calendar'),
    path('branch-employee-codes/',   branch_employee_codes,                 name='branch-employee-codes'),
    path('annual-leave-balance/',    annual_leave_balance,                  name='annual-leave-balance'),
    path('sync-leave-data/',         sync_leave_data,                       name='sync-leave-data'),
    path('initialize-current-month-leave/', initialize_current_month_leave, name='initialize-current-month-leave'),
    path('generate-master-payroll/',                                   generate_master_payroll,  name='generate-master-payroll'),
    path('master-payroll-history/',                                    master_payroll_history,   name='master-payroll-history'),
    path('master-payroll-history/<uuid:import_id>/download/',          master_payroll_download,  name='master-payroll-download'),
    path('master-payroll-history/<uuid:import_id>/rows/',              master_payroll_rows,      name='master-payroll-rows'),
    path('master-payroll-history/<uuid:import_id>/rows/<uuid:row_id>/', master_payroll_row_update, name='master-payroll-row-update'),
    path('master-payroll-history/<uuid:import_id>/delete/',            master_payroll_delete,    name='master-payroll-delete'),
    path('export-rows-to-excel/',                                      export_rows_to_excel,          name='export-rows-to-excel'),
    path('ai-analytics/generate/',                                     ai_analytics_generate,         name='ai-analytics-generate'),
    # Super-admin approval tracker — overview of all payroll files + SLA status
    path('approval-tracker/',                                              master_payroll_approval_tracker,        name='approval-tracker'),
    # Leave Encashment
    path('leave-encashment/',                 leave_encashment_list,    name='leave-encashment-list'),
    path('leave-encashment/status/',          leave_encashment_status,  name='leave-encashment-status'),
    path('leave-encashment/preview/',         leave_encashment_preview, name='leave-encashment-preview'),
    path('leave-encashment/run/',             leave_encashment_run,     name='leave-encashment-run'),
    # Workflow action endpoints
    path('master-payroll-history/<uuid:import_id>/workflow/',          master_payroll_workflow_status, name='master-payroll-workflow'),
    path('master-payroll-history/<uuid:import_id>/freeze/',            master_payroll_freeze,          name='master-payroll-freeze'),
    path('master-payroll-history/<uuid:import_id>/unfreeze/',          master_payroll_unfreeze,        name='master-payroll-unfreeze'),
    path('master-payroll-history/<uuid:import_id>/hr-approve/',        master_payroll_hr_approve,      name='master-payroll-hr-approve'),
    path('master-payroll-history/<uuid:import_id>/finance-review/',    master_payroll_finance_review,  name='master-payroll-finance-review'),
    path('master-payroll-history/<uuid:import_id>/finance-approve/',   master_payroll_finance_approve, name='master-payroll-finance-approve'),
    path('master-payroll-history/<uuid:import_id>/release/',           master_payroll_release,         name='master-payroll-release'),

    path('', include(router.urls)),
]
