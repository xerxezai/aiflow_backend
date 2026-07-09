"""DRF serializers for the Payroll Engine."""
from __future__ import annotations
from rest_framework import serializers

from . import catalog
from .models import (
    PayrollAdjustment, PayrollEmployee, PayrollRun, Payslip, PayslipLineItem,
    PayrollWorkflowLog, PayrollComparison, PayrollComparisonRow, PayrollRunUpload,
)


class PayrollEmployeeSerializer(serializers.ModelSerializer):
    default_gross = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = PayrollEmployee
        fields = [
            'id', 'employee_no', 'user', 'full_name', 'emirates_id', 'mol_no',
            'iban', 'bank_name', 'routing_code',
            'department', 'discipline', 'designation', 'grade', 'nationality_group',
            'joining_date', 'leaving_date',
            'hours',
            'basic', 'housing', 'transport', 'home_leave', 'default_gross',
            'default_payment_mode',
            'is_active', 'effective_from', 'effective_to',
            'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'default_gross', 'created_at', 'updated_at']


class PayslipLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLineItem
        fields = [
            'id', 'payslip', 'kind', 'component_code', 'label', 'description',
            'amount', 'source', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PayslipSerializer(serializers.ModelSerializer):
    line_items = PayslipLineItemSerializer(many=True, read_only=True)
    employee_no = serializers.CharField(source='employee.employee_no', read_only=True)
    run_cycle = serializers.CharField(source='run.cycle_code', read_only=True)
    run_status = serializers.CharField(source='run.status', read_only=True)
    
    # Computed field: Employee category (Emirates vs Expatriate)
    employee_category = serializers.SerializerMethodField()
    hours_per_day = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = [
            'id', 'run', 'run_cycle', 'run_status', 'employee', 'employee_no',
            'hours', 'days', 'total_worked_days',
            'employee_category', 'hours_per_day',
            'public_holiday_days', 'annual_leave_days', 'unpaid_leave_days',
            'basic', 'housing', 'transport', 'home_leave',
            'other_earnings', 'gross_earnings', 'total_deductions', 'net_payable',
            'payment_mode',
            'snapshot_full_name', 'snapshot_department', 'snapshot_designation',
            'snapshot_iban', 'snapshot_joining_date',
            'status', 'notes', 'line_items',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'run_cycle', 'run_status', 'employee_no',
            'employee_category', 'hours_per_day',
            'other_earnings', 'gross_earnings', 'total_deductions', 'net_payable',
            'days', 'total_worked_days',
            'snapshot_full_name', 'snapshot_iban',
            'created_at', 'updated_at',
        ]
    
    def get_employee_category(self, obj) -> str:
        """Return 'Emirates' if employee has only basic salary, else 'Expatriate'."""
        from .calculation_config import EMIRATES_DETECTION_THRESHOLD
        from decimal import Decimal
        
        total_allowances = (
            Decimal(str(obj.housing or 0)) + 
            Decimal(str(obj.transport or 0)) + 
            Decimal(str(obj.home_leave or 0))
        )
        
        return 'Emirates' if total_allowances < EMIRATES_DETECTION_THRESHOLD else 'Expatriate'
    
    def get_hours_per_day(self, obj) -> float:
        """Return working hours per day (8 for Emirates, 9 for Expatriates)."""
        from .calculation_config import get_employee_hours_per_day
        from decimal import Decimal
        
        hours = get_employee_hours_per_day(
            Decimal(str(obj.basic or 0)),
            Decimal(str(obj.housing or 0)),
            Decimal(str(obj.transport or 0)),
            Decimal(str(obj.home_leave or 0))
        )
        return float(hours)


class PayrollRunSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = PayrollRun
        fields = [
            'id', 'year', 'month', 'cycle_code',
            'status', 'status_label',
            'source_type',
            'employee_count', 'total_gross', 'total_deductions', 'total_net',
            'total_hours', 'total_days',
            'working_days_in_month', 'public_holidays_in_month',
            'generated_at', 'hr_approved_at', 'finance_approved_at', 'released_at',
            'hr_approved_by', 'finance_approved_by', 'released_by',
            'notes', 'created_at', 'updated_at', 'created_by',
        ]
        read_only_fields = [
            'id', 'cycle_code', 'status', 'status_label',
            'employee_count', 'total_gross', 'total_deductions', 'total_net',
            'total_hours', 'total_days',
            'public_holidays_in_month',
            'generated_at', 'hr_approved_at', 'finance_approved_at', 'released_at',
            'hr_approved_by', 'finance_approved_by', 'released_by',
            'created_at', 'updated_at', 'created_by',
        ]

    def get_status_label(self, obj):
        return catalog.status_meta(obj.status).get('label', obj.status)


class PayrollAdjustmentSerializer(serializers.ModelSerializer):
    employee_no = serializers.CharField(source='employee.employee_no', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)

    class Meta:
        model = PayrollAdjustment
        fields = [
            'id', 'employee', 'employee_no', 'employee_name',
            'target_year', 'target_month',
            'kind', 'component_code', 'label', 'description', 'amount',
            'status', 'applied_to', 'applied_at',
            'created_at', 'updated_at', 'created_by',
        ]
        read_only_fields = [
            'id', 'employee_no', 'employee_name', 'applied_to', 'applied_at',
            'created_at', 'updated_at', 'created_by',
        ]


class PayrollWorkflowLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = PayrollWorkflowLog
        fields = [
            'id', 'run', 'from_status', 'to_status', 'actor', 'actor_name', 'note', 'at',
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        if not obj.actor:
            return ''
        return getattr(obj.actor, 'get_full_name', lambda: '')() or getattr(obj.actor, 'username', '')


# ── Comparison ───────────────────────────────────────────────────────────────────
class PayrollComparisonRowSerializer(serializers.ModelSerializer):
    payroll_employee_no = serializers.SerializerMethodField()
    payroll_employee_name = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    variance_count = serializers.SerializerMethodField()
    max_severity = serializers.SerializerMethodField()

    class Meta:
        model = PayrollComparisonRow
        fields = [
            'id', 'comparison', 'payroll_employee', 'payroll_employee_no',
            'payroll_employee_name', 'external_employee_no', 'external_name',
            'matched_by', 'our_values', 'external_values', 'variances',
            'status', 'status_label', 'variance_count', 'max_severity',
        ]
        read_only_fields = fields

    def get_payroll_employee_no(self, obj):
        return obj.payroll_employee.employee_no if obj.payroll_employee else ''

    def get_payroll_employee_name(self, obj):
        return obj.payroll_employee.full_name if obj.payroll_employee else ''

    def get_status_label(self, obj):
        return catalog.COMPARISON_STATUS_LABELS.get(obj.status, {}).get('label', obj.status)

    def get_variance_count(self, obj):
        return sum(1 for v in (obj.variances or []) if v.get('field') != '__match__')

    def get_max_severity(self, obj):
        order = {'critical': 3, 'warning': 2, 'info': 1}
        severities = [v.get('severity') for v in (obj.variances or [])]
        if not severities:
            return ''
        return max(severities, key=lambda s: order.get(s, 0))


class PayrollComparisonSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    run_cycle_code = serializers.CharField(source='run.cycle_code', read_only=True)
    run_status = serializers.CharField(source='run.status', read_only=True)

    class Meta:
        model = PayrollComparison
        fields = [
            'id', 'run', 'run_cycle_code', 'run_status', 'source_label',
            'source_profile', 'source_filename', 'column_mapping', 'summary',
            'uploaded_by', 'uploaded_by_name', 'created_at',
        ]
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by:
            return ''
        return getattr(obj.uploaded_by, 'get_full_name', lambda: '')() \
            or getattr(obj.uploaded_by, 'username', '')


class PayrollComparisonDetailSerializer(PayrollComparisonSerializer):
    """Detail view embeds rows inline (capped — use the rows endpoint for paginated access)."""
    rows = PayrollComparisonRowSerializer(many=True, read_only=True)

    class Meta(PayrollComparisonSerializer.Meta):
        fields = PayrollComparisonSerializer.Meta.fields + ['rows']


# ── Catalog serializer (one big read-only blob for the frontend) ────
class CatalogSerializer(serializers.Serializer):
    currency = serializers.CharField()
    workflow_statuses = serializers.ListField()
    workflow_transitions = serializers.DictField()
    payment_modes = serializers.ListField()
    fixed_earnings = serializers.ListField()
    earning_components = serializers.ListField()
    deduction_components = serializers.ListField()
    line_item_kinds = serializers.ListField()
    line_item_sources = serializers.ListField()
    adjustment_statuses = serializers.ListField()
    grade_options = serializers.ListField()
    nationality_groups = serializers.ListField()
    # Dynamic options (departments/designations derived from live employee data)
    departments  = serializers.ListField(child=serializers.CharField(), default=list)
    designations = serializers.ListField(child=serializers.CharField(), default=list)


class PayrollRunUploadSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    file_type_label  = serializers.CharField(source='get_file_type_display', read_only=True)

    class Meta:
        model  = PayrollRunUpload
        fields = [
            'id', 'run', 'file_type', 'file_type_label', 'original_filename',
            's3_key', 'uploaded_by', 'uploaded_by_name', 'uploaded_at',
            'rows_matched', 'rows_updated', 'unmatched', 'updated_fields',
            'status', 'error_message',
        ]
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by:
            return 'System'
        return obj.uploaded_by.get_full_name() or obj.uploaded_by.username