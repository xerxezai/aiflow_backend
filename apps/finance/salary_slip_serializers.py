"""
Salary Slip Serializers - Smart Data Transformation
Handles validation, auto-calculation, and nested relationships
SOFT-CODED for flexibility
"""
from rest_framework import serializers
from decimal import Decimal
from apps.finance.salary_models import SalarySlip, EmployeeSalaryInfo, PayrollRun


# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED VALIDATION RULES
# ═══════════════════════════════════════════════════════════════════════════

MIN_BASIC_SALARY = Decimal('0.00')
MAX_BASIC_SALARY = Decimal('999999.99')
MIN_WORKING_DAYS = 1
MAX_WORKING_DAYS = 31
MIN_PRESENT_DAYS = 0
MIN_AMOUNT = Decimal('0.00')
MAX_AMOUNT = Decimal('999999.99')


class EmployeeSalaryInfoNestedSerializer(serializers.ModelSerializer):
    """Nested employee info for salary slip display"""
    full_name = serializers.CharField(source='user.get_full_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    
    class Meta:
        model = EmployeeSalaryInfo
        fields = ['id', 'employee_id', 'full_name', 'email', 'designation', 
                  'department', 'join_date', 'basic_salary']
        read_only_fields = fields


class PayrollRunNestedSerializer(serializers.ModelSerializer):
    """Nested payroll run info"""
    class Meta:
        model = PayrollRun
        fields = ['id', 'run_code', 'month', 'year', 'status', 'period_start', 'period_end']
        read_only_fields = fields


class SalarySlipSerializer(serializers.ModelSerializer):
    """
    Basic salary slip serializer for list view
    Read-only with computed employee info
    """
    employee = EmployeeSalaryInfoNestedSerializer(source='employee_salary_info', read_only=True)
    payroll = PayrollRunNestedSerializer(source='payroll_run', read_only=True)
    
    class Meta:
        model = SalarySlip
        fields = [
            'id', 'slip_number', 'employee', 'payroll',
            'month', 'year', 'basic_salary', 'total_allowances',
            'gross_salary', 'total_deductions', 'net_salary',
            'currency', 'status', 'working_days', 'present_days',
            'absent_days', 'created_at', 'updated_at'
        ]
        read_only_fields = fields


class SalarySlipDetailSerializer(serializers.ModelSerializer):
    """
    Detailed salary slip serializer with full breakdown
    Includes all allowances, deductions, and audit info
    """
    employee = EmployeeSalaryInfoNestedSerializer(source='employee_salary_info', read_only=True)
    payroll = PayrollRunNestedSerializer(source='payroll_run', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True)
    generated_by_name = serializers.CharField(source='generated_by.get_full_name', read_only=True)
    
    class Meta:
        model = SalarySlip
        fields = [
            'id', 'slip_number', 'employee', 'payroll',
            'month', 'year',
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
            'id', 'slip_number', 'employee', 'payroll', 'month', 'year',
            'gross_salary', 'total_allowances', 'total_deductions', 'net_salary',
            'approved_by_name', 'generated_by_name', 'created_at', 'updated_at'
        ]


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
        if value < MIN_PRESENT_DAYS:
            raise serializers.ValidationError(
                f"Present days cannot be negative"
            )
        return value
    
    def validate_allowances_breakdown(self, value):
        """
        Validate allowances breakdown structure
        Ensures all values are non-negative decimals
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("Allowances breakdown must be a dictionary")
        
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
        """
        Validate deductions breakdown structure
        Ensures all values are non-negative decimals
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("Deductions breakdown must be a dictionary")
        
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
        """
        Cross-field validation
        Ensures present_days <= working_days
        """
        working_days = data.get('working_days', getattr(self.instance, 'working_days', 30))
        present_days = data.get('present_days', getattr(self.instance, 'present_days', 30))
        
        if present_days > working_days:
            raise serializers.ValidationError({
                'present_days': f"Present days ({present_days}) cannot exceed working days ({working_days})"
            })
        
        return data
