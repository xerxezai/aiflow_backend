"""
Salary Slip Automation System - Serializers
REST API serializers for payroll management
SOFT-CODED for easy customization
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
from .salary_models import (
    EmployeeSalaryInfo,
    SalaryComponent,
    EmployeeSalaryComponent,
    PayrollRun,
    SalarySlip,
    SalarySlipApproval,
    SalarySlipEmail,
    SalarySlipAuditLog,
)

User = get_user_model()


# ===========================
# USER & EMPLOYEE SERIALIZERS
# ===========================

class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user information for nested serialization"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'email', 'username', 'first_name', 'last_name', 'full_name']
    
    def get_full_name(self, obj):
        return obj.get_full_name() or obj.email


class EmployeeSalaryInfoSerializer(serializers.ModelSerializer):
    """Employee salary information serializer"""
    user = UserBasicSerializer(read_only=True)
    user_id = serializers.UUIDField(write_only=True, required=True)
    
    class Meta:
        model = EmployeeSalaryInfo
        fields = [
            'id', 'user', 'user_id', 'employee_id', 'department', 'designation',
            'join_date', 'bank_name', 'account_number', 'iban', 'swift_code',
            'tax_id', 'tax_exemption', 'basic_salary', 'currency', 'is_active',
            'termination_date', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_employee_id(self, value):
        """Ensure employee ID is unique"""
        if self.instance:
            # Update scenario
            if EmployeeSalaryInfo.objects.filter(employee_id=value).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError("Employee ID already exists.")
        else:
            # Create scenario
            if EmployeeSalaryInfo.objects.filter(employee_id=value).exists():
                raise serializers.ValidationError("Employee ID already exists.")
        return value


class EmployeeSalaryInfoListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing employees"""
    user_name = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = EmployeeSalaryInfo
        fields = [
            'id', 'employee_id', 'user_name', 'user_email', 'department',
            'designation', 'basic_salary', 'currency', 'is_active'
        ]
    
    def get_user_name(self, obj):
        return obj.user.get_full_name() or obj.user.email


# ===========================
# SALARY COMPONENT SERIALIZERS
# ===========================

class SalaryComponentSerializer(serializers.ModelSerializer):
    """Salary component (allowances/deductions) serializer"""
    
    class Meta:
        model = SalaryComponent
        fields = [
            'id', 'code', 'name', 'description', 'component_type',
            'calculation_type', 'default_value', 'is_taxable', 'is_active',
            'display_order', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class EmployeeSalaryComponentSerializer(serializers.ModelSerializer):
    """Employee-specific salary component serializer"""
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_type = serializers.CharField(source='component.component_type', read_only=True)
    
    class Meta:
        model = EmployeeSalaryComponent
        fields = [
            'id', 'employee_salary_info', 'component', 'component_name',
            'component_type', 'value', 'effective_from', 'effective_to',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


# ===========================
# PAYROLL RUN SERIALIZERS
# ===========================

class PayrollRunSerializer(serializers.ModelSerializer):
    """Payroll run serializer"""
    created_by_name = serializers.SerializerMethodField()
    duration_minutes = serializers.SerializerMethodField()
    
    class Meta:
        model = PayrollRun
        fields = [
            'id', 'run_code', 'month', 'year', 'period_start', 'period_end',
            'total_employees', 'processed_employees', 'total_gross_salary',
            'total_deductions', 'total_net_salary', 'status',
            'processing_started_at', 'processing_completed_at', 'duration_minutes',
            'error_log', 'created_at', 'updated_at', 'created_by', 'created_by_name'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() if obj.created_by else None
    
    def get_duration_minutes(self, obj):
        if obj.processing_started_at and obj.processing_completed_at:
            delta = obj.processing_completed_at - obj.processing_started_at
            return round(delta.total_seconds() / 60, 2)
        return None


class PayrollRunListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing payroll runs"""
    
    class Meta:
        model = PayrollRun
        fields = [
            'id', 'run_code', 'month', 'year', 'total_employees',
            'processed_employees', 'total_net_salary', 'status', 'created_at'
        ]


# ===========================
# SALARY SLIP SERIALIZERS
# ===========================

class SalarySlipSerializer(serializers.ModelSerializer):
    """Comprehensive salary slip serializer"""
    employee_name = serializers.SerializerMethodField()
    employee_email = serializers.EmailField(source='employee_salary_info.user.email', read_only=True)
    employee_id = serializers.CharField(source='employee_salary_info.employee_id', read_only=True)
    # employee_code is a canonical alias for employee_id so frontend can join
    # biometric timesheet rows (keyed on employee_code) without namespace confusion
    employee_code = serializers.CharField(source='employee_salary_info.employee_id', read_only=True)
    department = serializers.CharField(source='employee_salary_info.department', read_only=True)
    designation = serializers.CharField(source='employee_salary_info.designation', read_only=True)
    payroll_run_code = serializers.CharField(source='payroll_run.run_code', read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    generated_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = SalarySlip
        fields = [
            'id', 'slip_number', 'payroll_run', 'payroll_run_code',
            'employee_salary_info', 'employee_name', 'employee_email',
            'employee_id', 'employee_code', 'department', 'designation', 'month', 'year',
            'basic_salary', 'total_allowances', 'gross_salary',
            'total_deductions', 'tax_deduction', 'net_salary', 'currency',
            'allowances_breakdown', 'deductions_breakdown',
            'working_days', 'present_days', 'absent_days', 'status',
            'pdf_file_path', 'pdf_generated_at', 'approved_by',
            'approved_by_name', 'approved_at', 'rejection_reason',
            'remarks', 'internal_notes', 'created_at', 'updated_at',
            'generated_by', 'generated_by_name'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'slip_number']
    
    def get_employee_name(self, obj):
        return obj.employee_salary_info.user.get_full_name() or obj.employee_salary_info.user.email
    
    def get_approved_by_name(self, obj):
        return obj.approved_by.get_full_name() if obj.approved_by else None
    
    def get_generated_by_name(self, obj):
        return obj.generated_by.get_full_name() if obj.generated_by else None


class SalarySlipListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing salary slips"""
    employee_name = serializers.SerializerMethodField()
    employee_id = serializers.CharField(source='employee_salary_info.employee_id', read_only=True)
    # employee_code aliases employee_id so list responses support biometric joins
    employee_code = serializers.CharField(source='employee_salary_info.employee_id', read_only=True)
    employee_email = serializers.EmailField(source='employee_salary_info.user.email', read_only=True)
    employee_join_date = serializers.DateField(source='employee_salary_info.join_date', read_only=True)
    department = serializers.CharField(source='employee_salary_info.department', read_only=True)
    designation = serializers.CharField(source='employee_salary_info.designation', read_only=True)
    employee_designation = serializers.CharField(source='employee_salary_info.designation', read_only=True)
    month_year = serializers.SerializerMethodField()
    
    class Meta:
        model = SalarySlip
        fields = [
            'id', 'slip_number', 'employee_name', 'employee_id', 'employee_code', 'employee_email',
            'employee_join_date', 'department', 'designation', 'employee_designation',
            'month', 'year', 'month_year', 'basic_salary', 'total_allowances', 'gross_salary', 'total_deductions',
            'tax_deduction', 'net_salary', 'currency', 'allowances_breakdown', 'deductions_breakdown',
            'working_days', 'present_days', 'absent_days', 'status', 'created_at', 'updated_at'
        ]
    
    def get_employee_name(self, obj):
        return obj.employee_salary_info.user.get_full_name() or obj.employee_salary_info.user.email
    
    def get_month_year(self, obj):
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        return f"{months[obj.month-1]} {obj.year}"


class SalarySlipCreateSerializer(serializers.Serializer):
    """Serializer for bulk salary slip generation"""
    payroll_run_id = serializers.UUIDField(required=True)
    employee_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of employee salary info IDs. If empty, generates for all active employees."
    )
    auto_approve = serializers.BooleanField(
        default=False,
        help_text="Automatically approve slips without requiring workflow."
    )


class SalarySlipDetailSerializer(serializers.ModelSerializer):
    """
    Detailed salary slip serializer with full breakdown
    Includes all allowances, deductions, and audit info
    """
    employee_name = serializers.SerializerMethodField()
    employee_id = serializers.CharField(source='employee_salary_info.employee_id', read_only=True)
    employee_email = serializers.EmailField(source='employee_salary_info.user.email', read_only=True)
    employee_designation = serializers.CharField(source='employee_salary_info.designation', read_only=True)
    payroll_run_code = serializers.CharField(source='payroll_run.run_code', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True)
    generated_by_name = serializers.CharField(source='generated_by.get_full_name', read_only=True)
    
    class Meta:
        model = SalarySlip
        fields = [
            'id', 'slip_number', 
            'employee_name', 'employee_id', 'employee_email', 'employee_designation',
            'payroll_run_code', 'month', 'year',
            'basic_salary', 'total_allowances', 'gross_salary',
            'total_deductions', 'tax_deduction', 'net_salary',
            'currency',
            'allowances_breakdown', 'deductions_breakdown',
            'working_days', 'present_days', 'absent_days',
            'status', 'approved_by', 'approved_by_name', 'approved_at',
            'rejection_reason', 'remarks', 'internal_notes',
            'pdf_file_path', 'pdf_generated_at', 'pdf_s3_key',
            'generated_by', 'generated_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'slip_number', 'employee_name', 'employee_id', 'employee_email',
            'employee_designation', 'payroll_run_code', 'month', 'year',
            'gross_salary', 'total_allowances', 'total_deductions', 'net_salary',
            'approved_by_name', 'generated_by_name', 'created_at', 'updated_at'
        ]
    
    def get_employee_name(self, obj):
        return obj.employee_salary_info.user.get_full_name() or obj.employee_salary_info.user.email


class SalarySlipUpdateSerializer(serializers.ModelSerializer):
    """
    Update serializer with validation and smart auto-calculation
    Allows editing: basic_salary, allowances, deductions, working days
    Auto-calculates: gross, net, totals
    """
    
    class Meta:
        model = SalarySlip
        fields = [
            'basic_salary',
            'allowances_breakdown',
            'deductions_breakdown',
            'tax_deduction',
            'working_days',
            'present_days',
            'absent_days',
            'status',
            'remarks',
            'internal_notes'
        ]
    
    def validate_basic_salary(self, value):
        """Validate basic salary is within acceptable range"""
        MIN_BASIC_SALARY = Decimal('0.00')
        MAX_BASIC_SALARY = Decimal('999999.99')
        
        if value < MIN_BASIC_SALARY:
            raise serializers.ValidationError(
                f"Basic salary cannot be less than {MIN_BASIC_SALARY}"
            )
        if value > MAX_BASIC_SALARY:
            raise serializers.ValidationError(
                f"Basic salary cannot exceed {MAX_BASIC_SALARY}"
            )
        return value
    
    def validate_working_days(self, value):
        """Validate working days"""
        MIN_WORKING_DAYS = 1
        MAX_WORKING_DAYS = 31
        
        if value < MIN_WORKING_DAYS:
            raise serializers.ValidationError(
                f"Working days must be at least {MIN_WORKING_DAYS}"
            )
        if value > MAX_WORKING_DAYS:
            raise serializers.ValidationError(
                f"Working days cannot exceed {MAX_WORKING_DAYS}"
            )
        return value
    
    def validate_present_days(self, value):
        """Validate present days"""
        if value < 0:
            raise serializers.ValidationError("Present days cannot be negative")
        return value
    
    def validate_allowances_breakdown(self, value):
        """Validate allowances breakdown structure"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Allowances breakdown must be a dictionary")
        
        MIN_AMOUNT = Decimal('0.00')
        MAX_AMOUNT = Decimal('999999.99')
        
        for key, amount in value.items():
            try:
                amount_decimal = Decimal(str(amount))
                if amount_decimal < MIN_AMOUNT:
                    raise serializers.ValidationError(
                        f"Allowance '{key}' cannot be negative"
                    )
                if amount_decimal > MAX_AMOUNT:
                    raise serializers.ValidationError(
                        f"Allowance '{key}' exceeds maximum allowed amount"
                    )
            except (ValueError, TypeError):
                raise serializers.ValidationError(
                    f"Allowance '{key}' must be a valid number"
                )
        
        return value
    
    def validate_deductions_breakdown(self, value):
        """Validate deductions breakdown structure"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Deductions breakdown must be a dictionary")
        
        MIN_AMOUNT = Decimal('0.00')
        MAX_AMOUNT = Decimal('999999.99')
        
        for key, amount in value.items():
            try:
                amount_decimal = Decimal(str(amount))
                if amount_decimal < MIN_AMOUNT:
                    raise serializers.ValidationError(
                        f"Deduction '{key}' cannot be negative"
                    )
                if amount_decimal > MAX_AMOUNT:
                    raise serializers.ValidationError(
                        f"Deduction '{key}' exceeds maximum allowed amount"
                    )
            except (ValueError, TypeError):
                raise serializers.ValidationError(
                    f"Deduction '{key}' must be a valid number"
                )
        
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        working_days = data.get('working_days', getattr(self.instance, 'working_days', 30))
        present_days = data.get('present_days', getattr(self.instance, 'present_days', 30))
        
        if present_days > working_days:
            raise serializers.ValidationError({
                'present_days': f"Present days ({present_days}) cannot exceed working days ({working_days})"
            })
        
        return data


# ===========================
# APPROVAL WORKFLOW SERIALIZERS
# ===========================

class SalarySlipApprovalSerializer(serializers.ModelSerializer):
    """Salary slip approval serializer"""
    approver_name = serializers.SerializerMethodField()
    salary_slip_number = serializers.CharField(source='salary_slip.slip_number', read_only=True)
    
    class Meta:
        model = SalarySlipApproval
        fields = [
            'id', 'salary_slip', 'salary_slip_number', 'approval_level',
            'approval_role', 'approver', 'approver_name', 'status',
            'decision_date', 'comments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_approver_name(self, obj):
        return obj.approver.get_full_name() if obj.approver else None


class ApprovalDecisionSerializer(serializers.Serializer):
    """Serializer for approval/rejection decisions"""
    decision = serializers.ChoiceField(
        choices=['approve', 'reject'],
        required=True
    )
    comments = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1000
    )


# ===========================
# EMAIL TRACKING SERIALIZERS
# ===========================

class SalarySlipEmailSerializer(serializers.ModelSerializer):
    """Salary slip email delivery serializer"""
    salary_slip_number = serializers.CharField(source='salary_slip.slip_number', read_only=True)
    employee_name = serializers.SerializerMethodField()
    
    class Meta:
        model = SalarySlipEmail
        fields = [
            'id', 'salary_slip', 'salary_slip_number', 'employee_name',
            'recipient_email', 'subject', 'sent_at', 'status',
            'email_provider_id', 'opened_at', 'downloaded_at',
            'retry_count', 'last_error', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_employee_name(self, obj):
        return obj.salary_slip.employee_salary_info.user.get_full_name()


class SendSalarySlipEmailSerializer(serializers.Serializer):
    """Serializer for sending salary slip emails"""
    salary_slip_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        help_text="List of salary slip IDs to send emails for"
    )
    custom_message = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        help_text="Optional custom message to include in email"
    )


# ===========================
# AUDIT LOG SERIALIZERS
# ===========================

class SalarySlipAuditLogSerializer(serializers.ModelSerializer):
    """Salary slip audit log serializer"""
    performed_by_name = serializers.SerializerMethodField()
    salary_slip_number = serializers.CharField(source='salary_slip.slip_number', read_only=True)
    
    class Meta:
        model = SalarySlipAuditLog
        fields = [
            'id', 'salary_slip', 'salary_slip_number', 'action',
            'performed_by', 'performed_by_name', 'performed_at',
            'old_values', 'new_values', 'description', 'ip_address', 'user_agent'
        ]
        read_only_fields = ['id', 'performed_at']
    
    def get_performed_by_name(self, obj):
        return obj.performed_by.get_full_name() if obj.performed_by else 'System'


# ===========================
# DASHBOARD & STATS SERIALIZERS
# ===========================

class SalarySlipStatsSerializer(serializers.Serializer):
    """Statistics serializer for dashboard"""
    total_slips = serializers.IntegerField()
    generated = serializers.IntegerField()
    pending_approval = serializers.IntegerField()
    approved = serializers.IntegerField()
    sent = serializers.IntegerField()
    total_employees = serializers.IntegerField()
    total_payroll = serializers.DecimalField(max_digits=15, decimal_places=2)
    current_month_slips = serializers.IntegerField()


class PayrollSummarySerializer(serializers.Serializer):
    """Payroll summary for specific month/year"""
    month = serializers.IntegerField()
    year = serializers.IntegerField()
    total_employees = serializers.IntegerField()
    total_gross = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_deductions = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_net = serializers.DecimalField(max_digits=15, decimal_places=2)
    status = serializers.CharField()
