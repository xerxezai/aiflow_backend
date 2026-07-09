"""
Salary Slip Management API - Individual Employee Payroll Updates
Smart CRUD operations with auto-calculation and validation
SOFT-CODED for maximum flexibility
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from apps.finance.salary_models import SalarySlip, PayrollRun, EmployeeSalaryInfo, SalarySlipAuditLog
from apps.finance.salary_slip_serializers import (
    SalarySlipSerializer, SalarySlipUpdateSerializer, SalarySlipDetailSerializer
)


# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Salary component keys (frontend uses these exact keys)
ALLOWANCE_KEYS = [
    'Housing Allowance',
    'Transport Allowance',
    'Home Leave Allowance',
    'Other Allowance',
    'Others',
]

DEDUCTION_KEYS = [
    'Absent Deduction',
    'Housing Allowance Advance',
    'Salary Advance',
    'Sick Leave Deduction',
    'Telephone',
    'Other Deductions',
]

# Validation rules
MIN_BASIC_SALARY = Decimal('0.00')
MAX_BASIC_SALARY = Decimal('999999.99')
MIN_WORKING_DAYS = 1
MAX_WORKING_DAYS = 31
MIN_PRESENT_DAYS = 0


class SalarySlipViewSet(viewsets.ModelViewSet):
    """
    API endpoints for salary slip management
    Supports full CRUD with intelligent auto-calculation
    """
    queryset = SalarySlip.objects.all()
    serializer_class = SalarySlipSerializer
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        """Use different serializers for different actions"""
        if self.action == 'retrieve':
            return SalarySlipDetailSerializer
        elif self.action in ['update', 'partial_update']:
            return SalarySlipUpdateSerializer
        return SalarySlipSerializer
    
    def get_queryset(self):
        """Filter salary slips with query parameters"""
        queryset = SalarySlip.objects.select_related(
            'employee_salary_info__user',
            'payroll_run',
            'approved_by',
            'generated_by'
        ).prefetch_related(
            'approvals',
            'audit_logs'
        )
        
        # Filter by payroll run
        run_id = self.request.query_params.get('payroll_run')
        if run_id:
            queryset = queryset.filter(payroll_run_id=run_id)
        
        # Filter by month/year
        month = self.request.query_params.get('month')
        year = self.request.query_params.get('year')
        if month and year:
            queryset = queryset.filter(month=month, year=year)
        
        # Filter by status
        slip_status = self.request.query_params.get('status')
        if slip_status:
            queryset = queryset.filter(status=slip_status)
        
        # Filter by employee
        employee_id = self.request.query_params.get('employee')
        if employee_id:
            queryset = queryset.filter(employee_salary_info__employee_id=employee_id)
        
        return queryset.order_by('-year', '-month', 'employee_salary_info__employee_id')
    
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Update salary slip with intelligent recalculation
        Automatically updates payroll run totals
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        old_values = self._capture_old_values(instance)
        
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        
        # Perform update with auto-calculation
        updated_slip = self._perform_update_with_calculation(
            instance, 
            serializer.validated_data,
            request.user
        )
        
        # Update payroll run totals
        self._update_payroll_run_totals(updated_slip.payroll_run)
        
        # Create audit log
        self._create_audit_log(updated_slip, old_values, request.user)
        
        # Return updated data
        response_serializer = SalarySlipDetailSerializer(updated_slip)
        return Response(response_serializer.data)
    
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """Partial update (PATCH)"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)
    
    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):
        """
        Force recalculation of salary slip
        Useful after bulk component changes
        """
        slip = self.get_object()
        
        # Recalculate from scratch
        recalculated_slip = self._recalculate_slip(slip)
        
        # Update payroll run
        self._update_payroll_run_totals(slip.payroll_run)
        
        serializer = SalarySlipDetailSerializer(recalculated_slip)
        return Response({
            'message': 'Salary slip recalculated successfully',
            'slip': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Get summary statistics for filtered salary slips
        """
        queryset = self.filter_queryset(self.get_queryset())
        
        summary = {
            'total_slips': queryset.count(),
            'total_gross_salary': sum(slip.gross_salary for slip in queryset),
            'total_deductions': sum(slip.total_deductions for slip in queryset),
            'total_net_salary': sum(slip.net_salary for slip in queryset),
            'by_status': {},
        }
        
        # Count by status
        for slip_status in ['draft', 'generated', 'pending_approval', 'approved', 'sent']:
            count = queryset.filter(status=slip_status).count()
            if count > 0:
                summary['by_status'][slip_status] = count
        
        return Response(summary)
    
    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE HELPER METHODS - Intelligent Calculation Engine
    # ═══════════════════════════════════════════════════════════════════════
    
    def _capture_old_values(self, slip):
        """Capture current values for audit trail"""
        return {
            'basic_salary': slip.basic_salary,
            'total_allowances': slip.total_allowances,
            'gross_salary': slip.gross_salary,
            'total_deductions': slip.total_deductions,
            'net_salary': slip.net_salary,
            'allowances_breakdown': slip.allowances_breakdown.copy(),
            'deductions_breakdown': slip.deductions_breakdown.copy(),
            'working_days': slip.working_days,
            'present_days': slip.present_days,
            'absent_days': slip.absent_days,
            'status': slip.status,
        }
    
    def _perform_update_with_calculation(self, slip, validated_data, user):
        """
        Update slip with intelligent auto-calculation
        Recalculates all derived fields
        """
        # Update direct fields
        for field in ['basic_salary', 'working_days', 'present_days', 'absent_days', 
                      'allowances_breakdown', 'deductions_breakdown', 'status', 'remarks']:
            if field in validated_data:
                setattr(slip, field, validated_data[field])
        
        # Recalculate derived fields
        slip = self._recalculate_slip(slip)
        
        # Save
        slip.save()
        
        return slip
    
    def _recalculate_slip(self, slip):
        """
        Intelligent recalculation of all salary components
        SOFT-CODED: Uses breakdown dictionaries for flexibility
        """
        # Calculate total allowances from breakdown
        total_allowances = Decimal('0.00')
        if slip.allowances_breakdown:
            for key, value in slip.allowances_breakdown.items():
                total_allowances += Decimal(str(value))
        slip.total_allowances = total_allowances
        
        # Calculate gross salary
        slip.gross_salary = slip.basic_salary + slip.total_allowances
        
        # Calculate total deductions from breakdown
        total_deductions = Decimal('0.00')
        if slip.deductions_breakdown:
            for key, value in slip.deductions_breakdown.items():
                total_deductions += Decimal(str(value))
        slip.total_deductions = total_deductions
        
        # Add tax deduction
        slip.total_deductions += slip.tax_deduction
        
        # Calculate net salary
        slip.net_salary = slip.gross_salary - slip.total_deductions
        
        # Calculate absent days if not set
        if slip.working_days and slip.present_days:
            slip.absent_days = max(0, slip.working_days - slip.present_days)
        
        return slip
    
    def _update_payroll_run_totals(self, payroll_run):
        """
        Update payroll run aggregate totals
        Recalculates from all slips in the run
        """
        slips = SalarySlip.objects.filter(payroll_run=payroll_run)
        
        payroll_run.total_employees = slips.count()
        payroll_run.processed_employees = slips.count()
        payroll_run.total_gross_salary = sum(slip.gross_salary for slip in slips)
        payroll_run.total_deductions = sum(slip.total_deductions for slip in slips)
        payroll_run.total_net_salary = sum(slip.net_salary for slip in slips)
        
        payroll_run.save()
    
    def _create_audit_log(self, slip, old_values, user):
        """
        Create audit trail entry
        Tracks what changed and who changed it
        """
        changes = []
        
        # Track numeric field changes
        for field in ['basic_salary', 'total_allowances', 'gross_salary', 
                      'total_deductions', 'net_salary']:
            old_val = old_values.get(field)
            new_val = getattr(slip, field)
            if old_val != new_val:
                changes.append(f"{field}: {old_val} → {new_val}")
        
        # Track status changes
        if old_values.get('status') != slip.status:
            changes.append(f"status: {old_values.get('status')} → {slip.status}")
        
        if changes:
            SalarySlipAuditLog.objects.create(
                salary_slip=slip,
                action='updated',
                performed_by=user,
                changes_made='; '.join(changes),
                timestamp=timezone.now()
            )
