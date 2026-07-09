from django.contrib import admin
from .models import (
    PayrollValidationLog,
    PayrollAuditAlert,
    ProjectCostAllocation,
    AIInsightSnapshot,
    ChatbotMessage,
    LeaveType,
    LeaveRequest,
    MonthlyLeaveAccrualLog,
)


@admin.register(PayrollValidationLog)
class PayrollValidationLogAdmin(admin.ModelAdmin):
    list_display  = ('rule_label', 'severity', 'payroll_run', 'is_resolved', 'created_at')
    list_filter   = ('severity', 'is_resolved')
    search_fields = ('rule_id', 'rule_label', 'description')
    raw_id_fields = ('payroll_run', 'employee_salary_info', 'resolved_by')


@admin.register(PayrollAuditAlert)
class PayrollAuditAlertAdmin(admin.ModelAdmin):
    list_display  = ('alert_type', 'severity', 'status', 'payroll_run', 'change_percent', 'created_at')
    list_filter   = ('alert_type', 'severity', 'status')
    raw_id_fields = ('payroll_run', 'compared_to_run', 'employee_salary_info', 'acknowledged_by')


@admin.register(ProjectCostAllocation)
class ProjectCostAllocationAdmin(admin.ModelAdmin):
    list_display  = ('project_code', 'project_name', 'cost_center', 'allocation_percent', 'allocated_cost', 'month', 'year')
    list_filter   = ('year', 'month')
    search_fields = ('project_code', 'project_name', 'cost_center')
    raw_id_fields = ('salary_slip',)


@admin.register(AIInsightSnapshot)
class AIInsightSnapshotAdmin(admin.ModelAdmin):
    list_display  = ('insight_type', 'severity', 'title', 'employee_salary_info', 'month', 'year', 'computed_at')
    list_filter   = ('insight_type', 'severity', 'year', 'month')
    raw_id_fields = ('employee_salary_info',)


@admin.register(ChatbotMessage)
class ChatbotMessageAdmin(admin.ModelAdmin):
    list_display  = ('role', 'persona', 'user', 'intent', 'created_at')
    list_filter   = ('role', 'persona')
    search_fields = ('content', 'intent')
    raw_id_fields = ('user',)


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'color_hex', 'is_paid', 'requires_approval', 'is_active', 'display_order')
    list_editable = ('display_order', 'is_active')
    search_fields = ('code', 'name')
    ordering      = ('display_order', 'code')


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display   = ('employee_name', 'employee_code', 'leave_type', 'start_date', 'end_date', 'days_requested', 'status', 'created_at')
    list_filter    = ('status', 'leave_type', 'start_date')
    search_fields  = ('employee_name', 'employee_code', 'department')
    raw_id_fields  = ('employee', 'reviewed_by')
    date_hierarchy = 'start_date'
    readonly_fields = ('days_requested', 'created_at', 'updated_at')


@admin.register(MonthlyLeaveAccrualLog)
class MonthlyLeaveAccrualLogAdmin(admin.ModelAdmin):
    """Admin interface for monthly leave accrual execution logs"""
    list_display = (
        'year',
        'month',
        'executed_at',
        'triggered_by',
        'records_processed',
        'records_created',
        'records_updated',
        'monthly_accrual_used',
        'status',
    )
    list_filter = ('status', 'triggered_by', 'year', 'month')
    search_fields = ('year', 'month')
    readonly_fields = (
        'id',
        'year',
        'month',
        'executed_at',
        'triggered_by',
        'records_processed',
        'records_created',
        'records_updated',
        'monthly_accrual_used',
        'branch_filter',
        'status',
        'error_message',
    )
    ordering = ('-year', '-month', '-executed_at')
    date_hierarchy = 'executed_at'
    
    def has_add_permission(self, request):
        """Prevent manual creation - logs created automatically by task"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deletion - audit trail must be preserved"""
        return False
