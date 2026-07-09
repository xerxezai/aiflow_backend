"""
Payroll Intelligence — Serializers
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import (
    PayrollValidationLog,
    PayrollAuditAlert,
    ProjectCostAllocation,
    AIInsightSnapshot,
    ChatbotMessage,
    PublicHoliday,
    AttendanceOverride,
    SalaryComponent,
    EmployeeSalaryStructure,
    SalaryHistory,
)

User = get_user_model()


class PayrollValidationLogSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PayrollValidationLog
        fields = [
            'id', 'payroll_run', 'employee_salary_info', 'employee_name',
            'rule_id', 'rule_label', 'severity', 'description',
            'suggested_action', 'is_resolved', 'resolved_by',
            'resolved_by_name', 'resolved_at', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_employee_name(self, obj):
        if obj.employee_salary_info:
            u = obj.employee_salary_info.user
            return f'{u.first_name} {u.last_name}'.strip() or u.email
        return None

    def get_resolved_by_name(self, obj):
        if obj.resolved_by:
            return f'{obj.resolved_by.first_name} {obj.resolved_by.last_name}'.strip() or obj.resolved_by.email
        return None


class PayrollAuditAlertSerializer(serializers.ModelSerializer):
    employee_name       = serializers.SerializerMethodField()
    alert_type_display  = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display    = serializers.CharField(source='get_severity_display', read_only=True)
    status_display      = serializers.CharField(source='get_status_display', read_only=True)
    acknowledged_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PayrollAuditAlert
        fields = [
            'id', 'payroll_run', 'compared_to_run', 'employee_salary_info',
            'employee_name', 'alert_type', 'alert_type_display',
            'severity', 'severity_display', 'change_percent',
            'previous_value', 'current_value', 'root_cause',
            'suggested_action', 'status', 'status_display',
            'acknowledged_by', 'acknowledged_by_name', 'acknowledged_at',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_employee_name(self, obj):
        if obj.employee_salary_info:
            u = obj.employee_salary_info.user
            return f'{u.first_name} {u.last_name}'.strip() or u.email
        return None

    def get_acknowledged_by_name(self, obj):
        if obj.acknowledged_by:
            return f'{obj.acknowledged_by.first_name} {obj.acknowledged_by.last_name}'.strip()
        return None


class ProjectCostAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ProjectCostAllocation
        fields = [
            'id', 'salary_slip', 'project_code', 'project_name',
            'cost_center', 'allocated_hours', 'allocation_percent',
            'allocated_cost', 'currency', 'month', 'year',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AIInsightSnapshotSerializer(serializers.ModelSerializer):
    insight_type_display = serializers.CharField(source='get_insight_type_display', read_only=True)
    severity_display     = serializers.CharField(source='get_severity_display', read_only=True)
    employee_name        = serializers.SerializerMethodField()

    class Meta:
        model  = AIInsightSnapshot
        fields = [
            'id', 'employee_salary_info', 'employee_name',
            'insight_type', 'insight_type_display',
            'severity', 'severity_display',
            'title', 'description', 'value', 'metadata',
            'month', 'year', 'computed_at', 'expires_at',
        ]
        read_only_fields = ['id', 'computed_at']

    def get_employee_name(self, obj):
        u = obj.employee_salary_info.user
        return f'{u.first_name} {u.last_name}'.strip() or u.email


class ChatbotMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ChatbotMessage
        fields = [
            'id', 'user', 'session_id', 'role', 'content',
            'intent', 'data_payload', 'persona', 'created_at',
        ]
        read_only_fields = ['id', 'user', 'created_at']


# ──────────────────────────────────────────────────────────────────────────────
# Leave Record serializers
# ──────────────────────────────────────────────────────────────────────────────
from .models import EmployeeLeaveRecord, EmployeeLeaveMonthly  # noqa: E402


class EmployeeLeaveMonthlySerializer(serializers.ModelSerializer):
    month_label = serializers.CharField(source='get_month_display', read_only=True)

    class Meta:
        model  = EmployeeLeaveMonthly
        fields = ['id', 'month', 'month_label', 'earned', 'taken', 'encashed', 'balance']
        read_only_fields = ['id']


class EmployeeLeaveRecordSerializer(serializers.ModelSerializer):
    monthly_breakdown = EmployeeLeaveMonthlySerializer(many=True, read_only=True)
    branch_display    = serializers.CharField(source='get_branch_display', read_only=True)

    class Meta:
        model  = EmployeeLeaveRecord
        fields = [
            'id', 'employee_code', 'employee_name', 'department',
            'job_title', 'joining_date', 'annual_entitlement', 'year',
            'branch', 'branch_display',
            'total_earned', 'total_taken', 'total_encashed', 'leave_balance',
            'carryforward', 'source_file', 'imported_at', 'monthly_breakdown',
        ]
        read_only_fields = ['id', 'imported_at']


class EmployeeLeaveRecordListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list view (no monthly breakdown)."""
    branch_display = serializers.CharField(source='get_branch_display', read_only=True)

    class Meta:
        model  = EmployeeLeaveRecord
        fields = [
            'id', 'employee_code', 'employee_name', 'department',
            'job_title', 'joining_date', 'annual_entitlement', 'year',
            'branch', 'branch_display',
            'total_earned', 'total_taken', 'total_encashed', 'leave_balance',
            'carryforward', 'imported_at',
        ]
        read_only_fields = ['id', 'imported_at']


# ────────────────────────────────────────────────────────────────────────────────
# Leave Type + Leave Request serializers
# ────────────────────────────────────────────────────────────────────────────────
from .models import LeaveType, LeaveRequest  # noqa: E402


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model  = LeaveType
        fields = [
            'id', 'code', 'name', 'color_hex',
            'badge_bg', 'badge_text', 'badge_border',
            'category',
            'is_paid', 'requires_approval', 'requires_document',
            'is_active', 'display_order',
        ]
        read_only_fields = ['id']


class LeaveRequestSerializer(serializers.ModelSerializer):
    leave_type_detail    = LeaveTypeSerializer(source='leave_type', read_only=True)
    status_display       = serializers.CharField(source='get_status_display', read_only=True)
    reviewed_by_name     = serializers.SerializerMethodField()
    rm_reviewed_by_name  = serializers.SerializerMethodField()

    class Meta:
        model  = LeaveRequest
        fields = [
            'id', 'employee', 'employee_code', 'employee_name', 'department',
            'leave_type', 'leave_type_detail',
            'start_date', 'end_date', 'days_requested', 'reason',
            'status', 'status_display',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at', 'reviewer_note',
            # Stage-1 Reporting Manager fields
            'rm_reviewed_by', 'rm_reviewed_by_name', 'rm_reviewed_at', 'rm_note',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'days_requested', 'status', 'status_display',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at',
            'rm_reviewed_by', 'rm_reviewed_by_name', 'rm_reviewed_at',
            'created_at', 'updated_at', 'leave_type_detail',
        ]

    def get_reviewed_by_name(self, obj):
        if obj.reviewed_by:
            return (
                f'{obj.reviewed_by.first_name} {obj.reviewed_by.last_name}'.strip()
                or obj.reviewed_by.email
            )
        return None

    def get_rm_reviewed_by_name(self, obj):
        if obj.rm_reviewed_by:
            return (
                f'{obj.rm_reviewed_by.first_name} {obj.rm_reviewed_by.last_name}'.strip()
                or obj.rm_reviewed_by.email
            )
        return None


# ---------------------------------------------------------------------------
# PublicHoliday
# ---------------------------------------------------------------------------

class PublicHolidaySerializer(serializers.ModelSerializer):
    """Full read/write serializer for PublicHoliday.

    HR Managers can create, update, and deactivate holidays.
    The created_by and updated_by fields are set automatically in the
    ViewSet perform_create / perform_update hooks -- never accepted from the
    request body (prevents spoofing).
    """
    created_by_name = serializers.SerializerMethodField(read_only=True)
    updated_by_name = serializers.SerializerMethodField(read_only=True)
    region_display  = serializers.CharField(source='get_region_display', read_only=True)
    source_display  = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model  = PublicHoliday
        fields = [
            'id', 'date', 'name', 'name_ar', 'region', 'region_display',
            'source', 'source_display', 'note', 'is_active',
            'created_by', 'created_by_name', 'updated_by', 'updated_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'updated_by']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f'{obj.created_by.first_name} {obj.created_by.last_name}'.strip() or obj.created_by.email
        return None

    def get_updated_by_name(self, obj):
        if obj.updated_by:
            return f'{obj.updated_by.first_name} {obj.updated_by.last_name}'.strip() or obj.updated_by.email
        return None


# ---------------------------------------------------------------------------
# AttendanceOverride
# ---------------------------------------------------------------------------

class AttendanceOverrideSerializer(serializers.ModelSerializer):
    """Full read/write serializer for AttendanceOverride.

    created_by is stamped automatically in the ViewSet -- never accepted
    from the client.  is_active defaults to True and can only be set to
    False (deactivation); deletion is intentionally not supported.
    """
    created_by_name = serializers.SerializerMethodField(read_only=True)
    reason_display  = serializers.CharField(source='get_reason_display', read_only=True)

    class Meta:
        model  = AttendanceOverride
        fields = [
            'id', 'employee_code', 'employee_name', 'date',
            'original_hours', 'override_hours',
            'reason', 'reason_display', 'note', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f'{obj.created_by.first_name} {obj.created_by.last_name}'.strip() or obj.created_by.email
        return None

# -----------------------------------------------------------------------------
# PublicHoliday
# -----------------------------------------------------------------------------

class PublicHolidaySerializer(serializers.ModelSerializer):
    """Full read/write serializer for PublicHoliday.

    HR Managers can create, update, and deactivate holidays.
    The `created_by` and `updated_by` fields are set automatically in the
    ViewSet's perform_create / perform_update hooks � never accepted from the
    request body (prevents spoofing).
    """
    created_by_name = serializers.SerializerMethodField(read_only=True)
    updated_by_name = serializers.SerializerMethodField(read_only=True)
    region_display  = serializers.CharField(source='get_region_display', read_only=True)
    source_display  = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model  = PublicHoliday
        fields = [
            'id', 'date', 'name', 'name_ar', 'region', 'region_display',
            'source', 'source_display', 'note', 'is_active',
            'created_by', 'created_by_name', 'updated_by', 'updated_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'updated_by']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f'{obj.created_by.first_name} {obj.created_by.last_name}'.strip() or obj.created_by.email
        return None

    def get_updated_by_name(self, obj):
        if obj.updated_by:
            return f'{obj.updated_by.first_name} {obj.updated_by.last_name}'.strip() or obj.updated_by.email
        return None


# -----------------------------------------------------------------------------
# SalaryComponent
# -----------------------------------------------------------------------------

class SalaryComponentSerializer(serializers.ModelSerializer):
    created_by_name  = serializers.SerializerMethodField(read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model  = SalaryComponent
        fields = [
            'id', 'code', 'name', 'category', 'category_display',
            'is_taxable', 'description', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f'{obj.created_by.first_name} {obj.created_by.last_name}'.strip() or obj.created_by.email
        return None


# -----------------------------------------------------------------------------
# EmployeeSalaryStructure
# -----------------------------------------------------------------------------

class EmployeeSalaryStructureSerializer(serializers.ModelSerializer):
    status_display    = serializers.CharField(source='get_status_display', read_only=True)
    currency_display  = serializers.CharField(source='get_currency_display', read_only=True)
    created_by_name   = serializers.SerializerMethodField(read_only=True)
    submitted_by_name = serializers.SerializerMethodField(read_only=True)
    reviewed_by_name  = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = EmployeeSalaryStructure
        fields = [
            'id', 'employee_code', 'employee_name', 'department',
            'effective_date', 'currency', 'currency_display',
            'basic_salary', 'components',
            'total_gross', 'total_deductions', 'net_salary',
            'status', 'status_display',
            'submitted_by', 'submitted_by_name', 'submitted_at',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at', 'reviewer_note',
            'is_active', 'superseded_by',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'total_gross', 'total_deductions', 'net_salary',
            'submitted_by', 'submitted_at',
            'reviewed_by', 'reviewed_at',
            'created_by', 'created_at', 'updated_at',
        ]

    def _user_name(self, user):
        if not user:
            return None
        return f'{user.first_name} {user.last_name}'.strip() or user.email

    def get_created_by_name(self, obj):
        return self._user_name(obj.created_by)

    def get_submitted_by_name(self, obj):
        return self._user_name(obj.submitted_by)

    def get_reviewed_by_name(self, obj):
        return self._user_name(obj.reviewed_by)


# -----------------------------------------------------------------------------
# SalaryHistory
# -----------------------------------------------------------------------------

class SalaryHistorySerializer(serializers.ModelSerializer):
    approved_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = SalaryHistory
        fields = [
            'id', 'employee_code', 'employee_name', 'change_date',
            'previous_basic', 'new_basic',
            'previous_net', 'new_net', 'change_percent',
            'change_reason',
            'structure', 'approved_by', 'approved_by_name',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return f'{obj.approved_by.first_name} {obj.approved_by.last_name}'.strip() or obj.approved_by.email
        return None

class AttendanceOverrideSerializer(serializers.ModelSerializer):
    """Full read/write serializer for AttendanceOverride.

    `created_by` is stamped automatically in the ViewSet � never accepted
    from the client.  `is_active` defaults to True and can only be set to
    False (deactivation); deletion is intentionally not supported.
    """
    created_by_name = serializers.SerializerMethodField(read_only=True)
    reason_display  = serializers.CharField(source='get_reason_display', read_only=True)

    class Meta:
        model  = AttendanceOverride
        fields = [
            'id', 'employee_code', 'employee_name', 'date',
            'original_hours', 'override_hours',
            'reason', 'reason_display', 'note', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return f'{obj.created_by.first_name} {obj.created_by.last_name}'.strip() or obj.created_by.email
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DailyWorkLog
# ─────────────────────────────────────────────────────────────────────────────
from .models import DailyWorkLog  # noqa: E402


class DailyWorkLogSerializer(serializers.ModelSerializer):
    user_full_name       = serializers.SerializerMethodField(read_only=True)
    priority_display     = serializers.CharField(source='get_priority_display',         read_only=True)
    status_display       = serializers.CharField(source='get_status_display',           read_only=True)
    approval_status_display = serializers.CharField(source='get_approval_status_display', read_only=True)
    approved_by_name     = serializers.SerializerMethodField(read_only=True)
    submitted_to_role_display = serializers.CharField(source='get_submitted_to_role_display', read_only=True)

    class Meta:
        model  = DailyWorkLog
        fields = [
            'id', 'user', 'user_full_name',
            'log_date', 'task_title', 'project_category',
            'hours_spent', 'priority', 'priority_display',
            'status', 'status_display',
            'notes', 's3_export_key',
            # approval
            'approval_status', 'approval_status_display',
            'approved_by', 'approved_by_name', 'approved_at', 'approval_note',
            # routing
            'submitted_to_role', 'submitted_to_role_display',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'user', 'user_full_name',
            's3_export_key', 'created_at', 'updated_at',
            'priority_display', 'status_display',
            'approval_status', 'approval_status_display',
            'approved_by', 'approved_by_name', 'approved_at', 'approval_note',
            'submitted_to_role_display',
        ]

    def get_user_full_name(self, obj):
        u = obj.user
        return f'{u.first_name} {u.last_name}'.strip() or u.email

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return f'{obj.approved_by.first_name} {obj.approved_by.last_name}'.strip() or obj.approved_by.email
        return None
