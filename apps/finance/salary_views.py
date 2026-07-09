"""
Salary Slip Automation System - Views
REST API endpoints for payroll management
SOFT-CODED for easy customization and extensibility
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Sum, Count
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from .salary_models import (
    EmployeeSalaryInfo,
    SalaryComponent,
    EmployeeSalaryComponent,
    PayrollRun,
    SalarySlip,
    SalarySlipApproval,
    SalarySlipEmail,
    SalarySlipAuditLog,
    SalaryStatus,
    ApprovalStatus,
    EmailStatus,
)
from .salary_serializers import (
    EmployeeSalaryInfoSerializer,
    EmployeeSalaryInfoListSerializer,
    SalaryComponentSerializer,
    EmployeeSalaryComponentSerializer,
    PayrollRunSerializer,
    PayrollRunListSerializer,
    SalarySlipSerializer,
    SalarySlipListSerializer,
    SalarySlipApprovalSerializer,
    ApprovalDecisionSerializer,
    SalarySlipEmailSerializer,
    SendSalarySlipEmailSerializer,
    SalarySlipAuditLogSerializer,
    SalarySlipStatsSerializer,
    PayrollSummarySerializer,
)
# Import enhanced serializers for individual salary editing
from .salary_slip_serializers import (
    SalarySlipDetailSerializer,
    SalarySlipUpdateSerializer,
)

logger = logging.getLogger(__name__)


# ===========================
# EMPLOYEE SALARY INFO VIEWSET
# ===========================

class EmployeeSalaryInfoViewSet(viewsets.ModelViewSet):
    """
    API endpoint for employee salary information management
    CRUD operations for employee payroll data
    """
    queryset = EmployeeSalaryInfo.objects.select_related('user').all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return EmployeeSalaryInfoListSerializer
        return EmployeeSalaryInfoSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Filter by user UUID ' used by the ESS portal (?employee=<User UUID>)
        employee_uid = self.request.query_params.get('employee')
        if employee_uid:
            queryset = queryset.filter(user_id=employee_uid)

        # Search by employee ID or name
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_id__icontains=search) |
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search) |
                Q(user__email__icontains=search)
            )
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['get'])
    def salary_history(self, request, pk=None):
        """Get salary history for an employee"""
        employee = self.get_object()
        slips = SalarySlip.objects.filter(
            employee_salary_info=employee
        ).order_by('-year', '-month')
        
        serializer = SalarySlipListSerializer(slips, many=True)
        return Response(serializer.data)


# ===========================
# SALARY COMPONENT VIEWSET
# ===========================

class SalaryComponentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for salary components (allowances/deductions)
    Manage component master data
    """
    queryset = SalaryComponent.objects.all()
    serializer_class = SalaryComponentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by component type
        component_type = self.request.query_params.get('component_type')
        if component_type:
            queryset = queryset.filter(component_type=component_type)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset


# ===========================
# EMPLOYEE SALARY COMPONENT VIEWSET
# ===========================

class EmployeeSalaryComponentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for employee-specific salary components
    Manage individual employee allowances/deductions
    """
    queryset = EmployeeSalaryComponent.objects.select_related(
        'employee_salary_info', 'component'
    ).all()
    serializer_class = EmployeeSalaryComponentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by employee
        employee_id = self.request.query_params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(employee_salary_info__id=employee_id)
        
        return queryset


# ===========================
# PAYROLL RUN VIEWSET
# ===========================

class PayrollRunViewSet(viewsets.ModelViewSet):
    """
    API endpoint for payroll run management
    Create and manage monthly payroll cycles
    """
    queryset = PayrollRun.objects.all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return PayrollRunListSerializer
        return PayrollRunSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        # Filter by year
        year = self.request.query_params.get('year')
        if year:
            queryset = queryset.filter(year=year)
        
        return queryset
    
    def perform_create(self, serializer):
        # Generate unique run code
        instance = serializer.save(created_by=self.request.user)
        if not instance.run_code:
            run_code = f"PAY-{instance.year}-{instance.month:02d}"
            instance.run_code = run_code
            instance.save()
    
    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """Process payroll run - generate salary slips for all employees"""
        payroll_run = self.get_object()
        
        if payroll_run.status != 'draft':
            return Response(
                {'error': 'Only draft payroll runs can be processed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Mark as processing
        payroll_run.status = 'processing'
        payroll_run.processing_started_at = timezone.now()
        payroll_run.save()
        
        try:
            # Get all active employees
            employees = EmployeeSalaryInfo.objects.filter(is_active=True)
            payroll_run.total_employees = employees.count()
            payroll_run.save()
            
            # Generate salary slips using service
            from .salary_service import SalaryCalculationService
            service = SalaryCalculationService()
            
            for employee in employees:
                try:
                    service.generate_salary_slip(
                        employee=employee,
                        payroll_run=payroll_run,
                        month=payroll_run.month,
                        year=payroll_run.year,
                        generated_by=request.user
                    )
                    payroll_run.processed_employees += 1
                    payroll_run.save()
                except Exception as e:
                    logger.error(f"Error generating slip for {employee.employee_id}: {str(e)}")
                    payroll_run.error_log += f"\nEmployee {employee.employee_id}: {str(e)}"
                    payroll_run.save()
            
            # Calculate totals
            slips = SalarySlip.objects.filter(payroll_run=payroll_run)
            totals = slips.aggregate(
                total_gross=Sum('gross_salary'),
                total_deductions=Sum('total_deductions'),
                total_net=Sum('net_salary')
            )
            
            payroll_run.total_gross_salary = totals['total_gross'] or Decimal('0.00')
            payroll_run.total_deductions = totals['total_deductions'] or Decimal('0.00')
            payroll_run.total_net_salary = totals['total_net'] or Decimal('0.00')
            payroll_run.status = 'completed'
            payroll_run.processing_completed_at = timezone.now()
            payroll_run.save()
            
            return Response({
                'message': 'Payroll processed successfully',
                'total_employees': payroll_run.total_employees,
                'processed_employees': payroll_run.processed_employees,
                'total_net_salary': str(payroll_run.total_net_salary)
            })
            
        except Exception as e:
            payroll_run.status = 'failed'
            payroll_run.error_log += f"\nProcessing error: {str(e)}"
            payroll_run.save()
            logger.error(f"Payroll run processing failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='bulk-approve')
    def bulk_approve(self, request, pk=None):
        """Approve all pending-approval slips in this payroll run in a single DB update."""
        run = self.get_object()
        pending_qs = SalarySlip.objects.filter(
            payroll_run=run,
            status=SalaryStatus.PENDING_APPROVAL,
        )
        count = pending_qs.count()
        pending_qs.update(
            status=SalaryStatus.APPROVED,
            approved_by=request.user,
            approved_at=timezone.now(),
        )
        return Response({'approved': count, 'run_code': run.run_code})

    @action(detail=True, methods=['post'], url_path='bulk-send-approved')
    def bulk_send_approved(self, request, pk=None):
        """Queue email delivery for all approved slips in this run."""
        run = self.get_object()
        approved_slips = SalarySlip.objects.filter(
            payroll_run=run,
            status=SalaryStatus.APPROVED,
        ).values_list('id', flat=True)
        slip_ids = list(approved_slips)
        if not slip_ids:
            return Response({'sent': 0, 'message': 'No approved slips to send.'})
        from .salary_email_service import SalarySlipEmailService
        email_service = SalarySlipEmailService()
        success = 0
        failed  = 0
        for slip_id in slip_ids:
            try:
                slip = SalarySlip.objects.get(id=slip_id)
                email_service.send_salary_slip_email(slip, request.user)
                slip.status = SalaryStatus.SENT
                slip.save(update_fields=['status'])
                success += 1
            except Exception as e:
                logger.error(f"bulk_send_approved: slip {slip_id} failed: {e}")
                failed += 1
        return Response({'sent': success, 'failed': failed, 'run_code': run.run_code})


# ===========================
# SALARY SLIP VIEWSET
# ===========================

class SalarySlipViewSet(viewsets.ModelViewSet):
    """
    API endpoint for salary slip management
    Core functionality for salary slip CRUD and workflows
    """
    queryset = SalarySlip.objects.select_related(
        'employee_salary_info__user', 'payroll_run'
    ).all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return SalarySlipListSerializer
        elif self.action == 'retrieve':
            return SalarySlipDetailSerializer
        elif self.action in ['update', 'partial_update']:
            return SalarySlipUpdateSerializer
        return SalarySlipSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        # Filter by month/year
        month = self.request.query_params.get('month')
        year = self.request.query_params.get('year')
        if month:
            queryset = queryset.filter(month=month)
        if year:
            queryset = queryset.filter(year=year)
        
        # Filter by employee (User UUID) ' used by the ESS self-service portal
        employee_uid = self.request.query_params.get('employee')
        if employee_uid:
            queryset = queryset.filter(employee_salary_info__user_id=employee_uid)

        # Filter by employee (biometric code)
        employee_id = self.request.query_params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(employee_salary_info__employee_id=employee_id)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(slip_number__icontains=search) |
                Q(employee_salary_info__employee_id__icontains=search) |
                Q(employee_salary_info__user__first_name__icontains=search) |
                Q(employee_salary_info__user__last_name__icontains=search)
            )
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get salary slip statistics for dashboard"""
        # Overall stats
        total_slips = SalarySlip.objects.count()
        by_status = SalarySlip.objects.values('status').annotate(count=Count('id'))
        
        status_counts = {item['status']: item['count'] for item in by_status}
        
        # Current month stats
        now = timezone.now()
        current_month_slips = SalarySlip.objects.filter(
            month=now.month,
            year=now.year
        ).count()
        
        # Total employees
        total_employees = EmployeeSalaryInfo.objects.filter(is_active=True).count()
        
        # Total payroll (current year)
        total_payroll = SalarySlip.objects.filter(
            year=now.year,
            status__in=[SalaryStatus.APPROVED, SalaryStatus.SENT]
        ).aggregate(total=Sum('net_salary'))['total'] or Decimal('0.00')
        
        data = {
            'total_slips': total_slips,
            'generated': status_counts.get(SalaryStatus.GENERATED, 0),
            'pending_approval': status_counts.get(SalaryStatus.PENDING_APPROVAL, 0),
            'approved': status_counts.get(SalaryStatus.APPROVED, 0),
            'sent': status_counts.get(SalaryStatus.SENT, 0),
            'total_employees': total_employees,
            'total_payroll': str(total_payroll),
            'current_month_slips': current_month_slips,
        }
        
        serializer = SalarySlipStatsSerializer(data)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def generate_pdf(self, request, pk=None):
        """Generate PDF for a salary slip"""
        salary_slip = self.get_object()
        
        try:
            from .salary_pdf_service import SalarySlipPDFService
            pdf_service = SalarySlipPDFService()
            pdf_path = pdf_service.generate_pdf(salary_slip)
            
            salary_slip.pdf_file_path = pdf_path
            salary_slip.pdf_generated_at = timezone.now()
            salary_slip.save()
            
            # Log action
            SalarySlipAuditLog.objects.create(
                salary_slip=salary_slip,
                action='generated',
                performed_by=request.user,
                description='PDF generated'
            )
            
            return Response({
                'message': 'PDF generated successfully',
                'pdf_path': pdf_path
            })
        except Exception as e:
            logger.error(f"PDF generation failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def send_email(self, request, pk=None):
        """Send salary slip via email to employee"""
        salary_slip = self.get_object()
        
        if salary_slip.status not in [SalaryStatus.APPROVED, SalaryStatus.SENT]:
            return Response(
                {'error': 'Only approved slips can be sent'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from .salary_email_service import SalarySlipEmailService
            email_service = SalarySlipEmailService()
            email_service.send_salary_slip_email(salary_slip, request.user)
            
            salary_slip.status = SalaryStatus.SENT
            salary_slip.save()
            
            return Response({'message': 'Email sent successfully'})
        except Exception as e:
            logger.error(f"Email sending failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def bulk_send_email(self, request):
        """Send salary slips to multiple employees"""
        serializer = SendSalarySlipEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        slip_ids = serializer.validated_data['salary_slip_ids']
        custom_message = serializer.validated_data.get('custom_message', '')
        
        success_count = 0
        failed_count = 0
        
        from .salary_email_service import SalarySlipEmailService
        email_service = SalarySlipEmailService()
        
        for slip_id in slip_ids:
            try:
                salary_slip = SalarySlip.objects.get(id=slip_id)
                email_service.send_salary_slip_email(
                    salary_slip, 
                    request.user,
                    custom_message=custom_message
                )
                salary_slip.status = SalaryStatus.SENT
                salary_slip.save()
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send email for slip {slip_id}: {str(e)}")
                failed_count += 1
        
        return Response({
            'message': f'Emails processed: {success_count} sent, {failed_count} failed',
            'success_count': success_count,
            'failed_count': failed_count
        })
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a salary slip"""
        salary_slip = self.get_object()
        
        if salary_slip.status != SalaryStatus.PENDING_APPROVAL:
            return Response(
                {'error': 'Only pending slips can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        salary_slip.status = SalaryStatus.APPROVED
        salary_slip.approved_by = request.user
        salary_slip.approved_at = timezone.now()
        salary_slip.save()
        
        # Log action
        SalarySlipAuditLog.objects.create(
            salary_slip=salary_slip,
            action='approved',
            performed_by=request.user,
            description='Salary slip approved'
        )
        
        return Response({'message': 'Salary slip approved successfully'})
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a salary slip"""
        salary_slip = self.get_object()
        reason = request.data.get('reason', '')
        
        if salary_slip.status != SalaryStatus.PENDING_APPROVAL:
            return Response(
                {'error': 'Only pending slips can be rejected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        salary_slip.status = SalaryStatus.REJECTED
        salary_slip.rejection_reason = reason
        salary_slip.save()
        
        # Log action
        SalarySlipAuditLog.objects.create(
            salary_slip=salary_slip,
            action='rejected',
            performed_by=request.user,
            description=f'Salary slip rejected: {reason}'
        )
        
        return Response({'message': 'Salary slip rejected'})
    
    @action(detail=True, methods=['post'], url_path='apply-deduction')
    @transaction.atomic
    def apply_deduction(self, request, pk=None):
        """
        Apply percentage-based deduction to allowances
        INTELLIGENT: AI-driven recommendations based on salary range
        SOFT-CODED: Uses DEDUCTIBLE_ALLOWANCE_COMPONENTS configuration
        
        Request body:
        {
            "percentage": 15.5,  # 0-100
            "preview": false,     # if true, returns preview without saving
            "reason": "Salary adjustment for Q2"
        }
        """
        from .deduction_config import (
            validate_deduction_request,
            calculate_deduction_breakdown,
            get_ai_recommendation,
            DEDUCTION_ALLOWED_STATUSES,
            DEDUCTION_AMOUNT_KEY,
            DEDUCTION_METADATA_KEY,
        )
        
        salary_slip = self.get_object()
        percentage = request.data.get('percentage')
        preview_only = request.data.get('preview', False)
        reason = request.data.get('reason', 'Percentage-based allowance deduction')
        
        # Validate percentage
        if percentage is None:
            return Response(
                {'error': 'Percentage is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            percentage = Decimal(str(percentage))
        except (ValueError, TypeError):
            return Response(
                {'error': 'Invalid percentage value'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate deduction request
        validation = validate_deduction_request(salary_slip, percentage)
        if not validation['valid']:
            return Response(
                {'error': validation['error']},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get AI recommendation for this salary
        ai_recommendation = get_ai_recommendation(salary_slip.net_salary)
        
        # Calculate deduction breakdown
        deduction_calc = calculate_deduction_breakdown(
            salary_slip.allowances_breakdown or {},
            percentage
        )
        
        # Preview mode - return calculation without saving
        if preview_only:
            new_net_salary = salary_slip.net_salary - Decimal(str(deduction_calc['total_deduction']))
            
            return Response({
                'preview': True,
                'current_net_salary': str(salary_slip.net_salary),
                'deduction_percentage': str(percentage),
                'component_deductions': deduction_calc['component_deductions'],
                'total_deduction_amount': deduction_calc['total_deduction'],
                'new_net_salary': str(new_net_salary),
                'affected_components': deduction_calc['affected_components'],
                'ai_recommendation': ai_recommendation,
            })
        
        # Capture old values for audit
        old_values = self._capture_old_values(salary_slip)
        
        # Apply deduction to deductions_breakdown
        if not salary_slip.deductions_breakdown:
            salary_slip.deductions_breakdown = {}
        
        # Add/update percentage deduction
        salary_slip.deductions_breakdown[DEDUCTION_AMOUNT_KEY] = deduction_calc['total_deduction']
        
        # Store metadata for transparency
        salary_slip.deductions_breakdown[DEDUCTION_METADATA_KEY] = {
            'percentage': str(percentage),
            'applied_at': timezone.now().isoformat(),
            'applied_by': request.user.email,
            'reason': reason,
            'component_breakdown': deduction_calc['component_deductions'],
            'affected_components': deduction_calc['affected_components'],
        }
        
        # Recalculate slip totals
        salary_slip = self._recalculate_slip(salary_slip)
        salary_slip.save()
        
        # Update payroll run totals
        self._update_payroll_run_totals(salary_slip.payroll_run)
        
        # Create audit log
        audit_description = (
            f"Applied {percentage}% deduction from allowances. "
            f"Total deduction: AED {deduction_calc['total_deduction']}. "
            f"Reason: {reason}"
        )
        SalarySlipAuditLog.objects.create(
            salary_slip=salary_slip,
            action='deduction_applied',
            performed_by=request.user,
            description=audit_description,
            old_values=old_values,
            new_values=self._capture_old_values(salary_slip),
        )
        
        # Return success with details
        serializer = self.get_serializer(salary_slip)
        return Response({
            'message': 'Deduction applied successfully',
            'slip': serializer.data,
            'deduction_details': {
                'percentage': str(percentage),
                'total_amount': deduction_calc['total_deduction'],
                'component_breakdown': deduction_calc['component_deductions'],
                'affected_components': deduction_calc['affected_components'],
                'new_net_salary': str(salary_slip.net_salary),
            },
            'ai_recommendation': ai_recommendation,
        })
    
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Update salary slip with intelligent recalculation
        Automatically updates payroll run totals
        SOFT-CODED: Uses breakdown dictionaries for flexibility
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
        response_serializer = self.get_serializer(updated_slip)
        return Response(response_serializer.data)
    
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """Partial update (PATCH) with auto-calculation"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)
    
    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):
        """
        Force recalculation of salary slip totals
        Useful after manual component updates
        """
        slip = self.get_object()
        
        # Recalculate from scratch
        recalculated_slip = self._recalculate_slip(slip)
        recalculated_slip.save()
        
        # Update payroll run
        self._update_payroll_run_totals(slip.payroll_run)
        
        # Log action
        SalarySlipAuditLog.objects.create(
            salary_slip=slip,
            action='recalculated',
            performed_by=request.user,
            description='Salary slip recalculated'
        )
        
        serializer = self.get_serializer(recalculated_slip)
        return Response({
            'message': 'Salary slip recalculated successfully',
            'slip': serializer.data
        })
    
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
            'allowances_breakdown': slip.allowances_breakdown.copy() if slip.allowances_breakdown else {},
            'deductions_breakdown': slip.deductions_breakdown.copy() if slip.deductions_breakdown else {},
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
                      'allowances_breakdown', 'deductions_breakdown', 'tax_deduction',
                      'status', 'remarks', 'internal_notes']:
            if field in validated_data:
                setattr(slip, field, validated_data[field])
        
        # Recalculate derived fields
        slip = self._recalculate_slip(slip)
        
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
        
        # Add tax deduction
        total_deductions += slip.tax_deduction
        slip.total_deductions = total_deductions
        
        # Calculate net salary
        slip.net_salary = slip.gross_salary - slip.total_deductions
        
        # Calculate absent days if not explicitly set
        if slip.working_days and slip.present_days is not None:
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
                      'total_deductions', 'net_salary', 'working_days', 
                      'present_days', 'absent_days']:
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


# ===========================
# APPROVAL WORKFLOW VIEWSET
# ===========================

class SalarySlipApprovalViewSet(viewsets.ModelViewSet):
    """
    API endpoint for salary slip approval workflow
    """
    queryset = SalarySlipApproval.objects.select_related(
        'salary_slip', 'approver'
    ).all()
    serializer_class = SalarySlipApprovalSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=True, methods=['post'])
    def decide(self, request, pk=None):
        """Make approval decision"""
        approval = self.get_object()
        serializer = ApprovalDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        decision = serializer.validated_data['decision']
        comments = serializer.validated_data.get('comments', '')
        
        if decision == 'approve':
            approval.status = ApprovalStatus.APPROVED
        else:
            approval.status = ApprovalStatus.REJECTED
        
        approval.decision_date = timezone.now()
        approval.comments = comments
        approval.save()
        
        return Response({'message': f'Approval {decision}d successfully'})


# ===========================
# EMAIL TRACKING VIEWSET
# ===========================

class SalarySlipEmailViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for salary slip email tracking
    Read-only access to email delivery logs
    """
    queryset = SalarySlipEmail.objects.select_related('salary_slip').all()
    serializer_class = SalarySlipEmailSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        return queryset


# ===========================
# AUDIT LOG VIEWSET
# ===========================

class SalarySlipAuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for salary slip audit logs
    Read-only access to audit trail
    """
    queryset = SalarySlipAuditLog.objects.select_related(
        'salary_slip', 'performed_by'
    ).all()
    serializer_class = SalarySlipAuditLogSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Filter by slip
        slip_id = self.request.query_params.get('slip_id')
        if slip_id:
            queryset = queryset.filter(salary_slip__id=slip_id)
        
        # Filter by action
        action = self.request.query_params.get('action')
        if action:
            queryset = queryset.filter(action=action)
        
        return queryset


# ===========================
# PAYROLL SCHEDULE VIEWSET
# ===========================

class PayrollScheduleViewSet(viewsets.ViewSet):
    """
    Singleton endpoint ' GET / PATCH the auto-generate schedule config.
    Only super-admins can modify; all HR managers can read.
    """
    permission_classes = [IsAuthenticated]

    def _get_or_create(self):
        from .salary_models import PayrollSchedule
        obj = PayrollSchedule.objects.order_by('created_at').first()
        if obj is None:
            obj = PayrollSchedule.objects.create()
        return obj

    def list(self, request):
        obj = self._get_or_create()
        return Response({
            'id':                  str(obj.id),
            'enabled':             obj.enabled,
            'day_of_month':        obj.day_of_month,
            'days_after_month_end':obj.days_after_month_end,
            'auto_send_emails':    obj.auto_send_emails,
            'notify_emails':       obj.notify_emails,
            'last_run_at':         obj.last_run_at,
            'last_run_status':     obj.last_run_status,
            'last_run_details':    obj.last_run_details,
            'updated_at':          obj.updated_at,
        })

    def partial_update(self, request, pk=None):
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({'error': 'Admin only.'}, status=403)
        obj = self._get_or_create()
        fields = ['enabled', 'day_of_month', 'days_after_month_end', 'auto_send_emails', 'notify_emails']
        changed = []
        for f in fields:
            if f in request.data:
                setattr(obj, f, request.data[f])
                changed.append(f)
        if changed:
            obj.updated_by = request.user
            changed += ['updated_by', 'updated_at']
            obj.save(update_fields=changed)
        return Response({'ok': True, 'updated': changed})

    @action(detail=False, methods=['post'], url_path='trigger-now')
    def trigger_now(self, request):
        """Manually fire the monthly payroll generation immediately (super-admin)."""
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({'error': 'Admin only.'}, status=403)
        from .tasks import auto_generate_monthly_payroll
        task = auto_generate_monthly_payroll.delay()
        return Response({'ok': True, 'task_id': str(task.id)})


# ===========================
# SALARY SLIP ' PDF DOWNLOAD
# ===========================

def slip_download_pdf(request, slip_id):
    """
    GET /api/v1/finance/salary-slips/<slip_id>/download-pdf/
    Returns a presigned S3 URL (or streams the local PDF) for download.
    Accessible to: the owning employee (self-service) + HR staff.
    """
    from rest_framework.decorators import api_view, permission_classes as pc
    from rest_framework.permissions import IsAuthenticated as IA

    if not request.user.is_authenticated:
        return Response({'error': 'Authentication required.'}, status=401)

    try:
        slip = SalarySlip.objects.select_related('employee_salary_info__user').get(id=slip_id)
    except SalarySlip.DoesNotExist:
        return Response({'error': 'Slip not found.'}, status=404)

    # Permission: owner or staff/superuser
    is_owner = (
        hasattr(slip.employee_salary_info, 'user') and
        slip.employee_salary_info.user_id == request.user.id
    )
    if not (is_owner or request.user.is_staff or request.user.is_superuser):
        return Response({'error': 'Access denied.'}, status=403)

    # If we have an S3 key, generate a presigned URL
    from apps.payroll.storage import PayrollSlipStorage, S3_AVAILABLE
    if slip.pdf_s3_key and S3_AVAILABLE and PayrollSlipStorage:
        try:
            storage = PayrollSlipStorage()
            url = storage.url(slip.pdf_s3_key)
            return Response({
                'url':        url,
                'filename':   f'{slip.slip_number}.pdf',
                'source':     's3',
                'expires_in': 3600,
            })
        except Exception as exc:
            logger.warning('slip_download_pdf: S3 URL generation failed for %s: %s', slip.slip_number, exc)

    # Fallback: trigger on-demand PDF generation + return local file stream
    if not slip.pdf_file_path:
        from .salary_pdf_service import SalarySlipPDFService
        try:
            svc  = SalarySlipPDFService()
            path = svc.generate_pdf(slip)
            slip.pdf_file_path  = path
            slip.pdf_generated_at = timezone.now()
            slip.save(update_fields=['pdf_file_path', 'pdf_generated_at'])
        except Exception as exc:
            logger.error('slip_download_pdf: on-demand generation failed: %s', exc)
            return Response({'error': 'PDF not available. Please try again later.'}, status=503)

    # Stream the local PDF file
    import os
    from django.conf import settings
    from django.http import FileResponse
    full_path = os.path.join(settings.MEDIA_ROOT, slip.pdf_file_path.lstrip('/'))
    if not os.path.exists(full_path):
        return Response({'error': 'PDF file missing. Regenerating - please retry.'}, status=404)

    return FileResponse(
        open(full_path, 'rb'),
        content_type='application/pdf',
        as_attachment=True,
        filename=f'{slip.slip_number}.pdf',
    )


# -- Helper used by the auto-generate task ------------------------------------
def _queue_bulk_emails_for_run(run, triggered_by):
    """Queue email for all approved slips in 
un."""
    from .salary_email_service import SalarySlipEmailService
    approved = SalarySlip.objects.filter(payroll_run=run, status=SalaryStatus.APPROVED)
    svc = SalarySlipEmailService()
    for slip in approved:
        try:
            svc.send_salary_slip_email(slip, triggered_by)
            slip.status = SalaryStatus.SENT
            slip.save(update_fields=['status'])
        except Exception as exc:
            logger.warning('_queue_bulk_emails_for_run: slip %s -- %s', slip.slip_number, exc)

