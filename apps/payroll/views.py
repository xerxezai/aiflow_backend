"""
Payroll Intelligence — Views
==============================
7 viewsets + 1 dashboard summary view.
All endpoints require authentication.
"""
from __future__ import annotations

import datetime
import os
import uuid
import logging

from decimal import Decimal

from django.db.models import Sum, Count, Q, Avg
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.finance.salary_models import (
    PayrollRun, SalarySlip, EmployeeSalaryInfo, SalaryStatus,
)

from .models import (
    PayrollValidationLog,
    PayrollAuditAlert,
    ProjectCostAllocation,
    AIInsightSnapshot,
    AlertStatus,
    EmployeeLeaveRecord,
    EmployeeLeaveMonthly,
    LeaveType,
    LeaveRequest,
    LeaveRequestStatus,
    PublicHoliday,
    AttendanceOverride,
    SalaryComponent,
    EmployeeSalaryStructure,
    SalaryStructureStatus,
    SalaryHistory,
    DailyWorkLog,
    DailyWorkLogApprovalStatus,
    MasterPayrollWorkflowLog,
    MasterPayrollWorkflowStage,
)
from .serializers import (
    PayrollValidationLogSerializer,
    PayrollAuditAlertSerializer,
    ProjectCostAllocationSerializer,
    AIInsightSnapshotSerializer,
    EmployeeLeaveRecordSerializer,
    EmployeeLeaveRecordListSerializer,
    LeaveTypeSerializer,
    LeaveRequestSerializer,
    PublicHolidaySerializer,
    AttendanceOverrideSerializer,
    SalaryComponentSerializer,
    EmployeeSalaryStructureSerializer,
    SalaryHistorySerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dashboard Summary View
# ─────────────────────────────────────────────────────────────────────────────

class PayrollDashboardSummaryView(APIView):
    """
    Single endpoint that aggregates all KPI data for the Payroll Dashboard.
    Returns a flat dict consumed directly by the frontend KPI tiles.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        current_month = now.month
        current_year  = now.year

        # ── Salary-slip KPIs (current month) ────────────────────────────────
        slip_agg = SalarySlip.objects.filter(
            month=current_month,
            year=current_year,
        ).aggregate(
            total_gross=Sum('gross_salary'),
            total_net=Sum('net_salary'),
            total_deductions=Sum('total_deductions'),
            slip_count=Count('id'),
        )

        # YTD totals
        ytd_agg = SalarySlip.objects.filter(
            year=current_year,
        ).aggregate(ytd_net=Sum('net_salary'))

        pending_approvals = SalarySlip.objects.filter(
            status=SalaryStatus.PENDING_APPROVAL
        ).count()

        # ── Active employee count ────────────────────────────────────────────
        # Primary: EmployeeSalaryInfo (set when salary structures are loaded).
        # Fallback: distinct employee codes from annual leave records.
        salary_employee_count = EmployeeSalaryInfo.objects.filter(is_active=True).count()
        leave_employee_count = (
            EmployeeLeaveRecord.objects
            .filter(year=current_year)
            .exclude(employee_code__isnull=True)
            .values('employee_code')
            .distinct()
            .count()
        )
        total_employees = salary_employee_count or leave_employee_count

        avg_salary = EmployeeSalaryInfo.objects.filter(
            is_active=True
        ).aggregate(avg=Avg('basic_salary'))['avg'] or Decimal('0')

        # ── Open issues ──────────────────────────────────────────────────────
        open_validations = PayrollValidationLog.objects.filter(is_resolved=False).count()
        open_alerts      = PayrollAuditAlert.objects.filter(status=AlertStatus.OPEN).count()

        # Employees with a negative or zero leave balance are an alert
        leave_critical = (
            EmployeeLeaveRecord.objects
            .filter(year=current_year, leave_balance__lte=0)
            .count()
        )

        # ── Leave summary (annual aggregates) ───────────────────────────────
        leave_agg = EmployeeLeaveRecord.objects.filter(year=current_year).aggregate(
            total_taken=Sum('total_taken'),
            total_earned=Sum('total_earned'),
            avg_balance=Avg('leave_balance'),
        )
        leave_employees_taken = (
            EmployeeLeaveRecord.objects.filter(year=current_year, total_taken__gt=0).count()
        )

        # Current-month leave taken (from monthly breakdown table)
        current_month_leave_taken = (
            EmployeeLeaveMonthly.objects
            .filter(record__year=current_year, month=current_month)
            .aggregate(taken=Sum('taken'))['taken'] or Decimal('0')
        )

        # ── Latest payroll run ───────────────────────────────────────────────
        latest_run = PayrollRun.objects.order_by('-year', '-month').first()

        return Response({
            'current_month':       current_month,
            'current_year':        current_year,
            # Employee count — accurate regardless of whether salary runs exist
            'total_employees':         total_employees,
            'salary_employees':        salary_employee_count,
            'leave_employees':         leave_employee_count,
            # Salary-slip KPIs (zero until payroll runs are processed)
            'current_month_gross': str(slip_agg['total_gross'] or 0),
            'current_month_net':   str(slip_agg['total_net'] or 0),
            'total_deductions':    str(slip_agg['total_deductions'] or 0),
            'slip_count':          slip_agg['slip_count'] or 0,
            'pending_approvals':   pending_approvals,
            'ytd_payroll':         str(ytd_agg['ytd_net'] or 0),
            'avg_basic_salary':    str(avg_salary),
            'open_validations':    open_validations,
            'open_alerts':         open_alerts + leave_critical,
            # Leave intelligence — always populated from imported leave data
            'leave_data_available':        leave_employee_count > 0,
            'leave_total_taken_ytd':       str(leave_agg['total_taken'] or 0),
            'leave_total_earned_ytd':      str(leave_agg['total_earned'] or 0),
            'leave_avg_balance':           str(round(leave_agg['avg_balance'] or 0, 2)),
            'leave_employees_taken':       leave_employees_taken,
            'leave_current_month_taken':   str(current_month_leave_taken),
            'leave_critical_alerts':       leave_critical,
            # Activity intelligence — approved daily work logs (month-to-date)
            'approved_activity_hours_mtd':  float(
                DailyWorkLog.objects
                .filter(
                    approval_status=DailyWorkLogApprovalStatus.APPROVED,
                    log_date__year=current_year,
                    log_date__month=current_month,
                )
                .aggregate(h=Sum('hours_spent'))['h'] or 0
            ),
            'approved_activity_count_mtd': DailyWorkLog.objects.filter(
                approval_status=DailyWorkLogApprovalStatus.APPROVED,
                log_date__year=current_year,
                log_date__month=current_month,
            ).count(),
            'latest_run': {
                'id':     str(latest_run.id)     if latest_run else None,
                'code':   latest_run.run_code    if latest_run else None,
                'month':  latest_run.month       if latest_run else None,
                'year':   latest_run.year        if latest_run else None,
                'status': latest_run.status      if latest_run else None,
            },
        })


# ─────────────────────────────────────────────────────────────────────────────
# 2. PayrollValidationLog
# ─────────────────────────────────────────────────────────────────────────────

class PayrollValidationLogViewSet(viewsets.ModelViewSet):
    queryset           = PayrollValidationLog.objects.select_related(
        'payroll_run', 'employee_salary_info__user', 'resolved_by'
    ).all()
    serializer_class   = PayrollValidationLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        run_id = self.request.query_params.get('payroll_run')
        if run_id:
            qs = qs.filter(payroll_run_id=run_id)
        severity = self.request.query_params.get('severity')
        if severity:
            qs = qs.filter(severity=severity)
        is_resolved = self.request.query_params.get('is_resolved')
        if is_resolved is not None:
            qs = qs.filter(is_resolved=is_resolved.lower() == 'true')
        return qs

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        log = self.get_object()
        log.is_resolved = True
        log.resolved_by = request.user
        log.resolved_at = timezone.now()
        log.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
        return Response(self.get_serializer(log).data)

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        """Receive a list of validation findings from the frontend rule engine."""
        items = request.data if isinstance(request.data, list) else request.data.get('items', [])
        created = []
        for item in items:
            ser = self.get_serializer(data=item)
            if ser.is_valid():
                ser.save()
                created.append(ser.data)
        return Response({'created': len(created), 'items': created}, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# 3. PayrollAuditAlert
# ─────────────────────────────────────────────────────────────────────────────

class PayrollAuditAlertViewSet(viewsets.ModelViewSet):
    queryset           = PayrollAuditAlert.objects.select_related(
        'payroll_run', 'compared_to_run', 'employee_salary_info__user', 'acknowledged_by'
    ).all()
    serializer_class   = PayrollAuditAlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        run_id = self.request.query_params.get('payroll_run')
        if run_id:
            qs = qs.filter(payroll_run_id=run_id)
        alert_status = self.request.query_params.get('status')
        if alert_status:
            qs = qs.filter(status=alert_status)
        severity = self.request.query_params.get('severity')
        if severity:
            qs = qs.filter(severity=severity)
        return qs

    @action(detail=True, methods=['post'], url_path='acknowledge')
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.status         = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['status', 'acknowledged_by', 'acknowledged_at'])
        return Response(self.get_serializer(alert).data)

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        alert = self.get_object()
        alert.status = AlertStatus.RESOLVED
        alert.save(update_fields=['status'])
        return Response(self.get_serializer(alert).data)


# ─────────────────────────────────────────────────────────────────────────────
# 4. ProjectCostAllocation
# ─────────────────────────────────────────────────────────────────────────────

class ProjectCostAllocationViewSet(viewsets.ModelViewSet):
    queryset           = ProjectCostAllocation.objects.select_related('salary_slip').all()
    serializer_class   = ProjectCostAllocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        month = self.request.query_params.get('month')
        year  = self.request.query_params.get('year')
        project = self.request.query_params.get('project_code')
        if month:
            qs = qs.filter(month=month)
        if year:
            qs = qs.filter(year=year)
        if project:
            qs = qs.filter(project_code__icontains=project)
        return qs

    @action(detail=False, methods=['get'], url_path='department-summary')
    def department_summary(self, request):
        """Aggregate cost by cost_center for charts."""
        month = request.query_params.get('month')
        year  = request.query_params.get('year')
        qs = self.get_queryset()
        if month:
            qs = qs.filter(month=month)
        if year:
            qs = qs.filter(year=year)
        summary = (
            qs.values('cost_center')
              .annotate(total_cost=Sum('allocated_cost'), total_hours=Sum('allocated_hours'))
              .order_by('-total_cost')
        )
        return Response(list(summary))


# ─────────────────────────────────────────────────────────────────────────────
# 5. AIInsightSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class AIInsightSnapshotViewSet(viewsets.ModelViewSet):
    queryset           = AIInsightSnapshot.objects.select_related('employee_salary_info__user').all()
    serializer_class   = AIInsightSnapshotSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        emp_id = self.request.query_params.get('employee_salary_info')
        month  = self.request.query_params.get('month')
        year   = self.request.query_params.get('year')
        if emp_id:
            qs = qs.filter(employee_salary_info_id=emp_id)
        if month:
            qs = qs.filter(month=month)
        if year:
            qs = qs.filter(year=year)
        return qs

    @action(detail=False, methods=['delete'], url_path='clear-expired')
    def clear_expired(self, request):
        deleted, _ = AIInsightSnapshot.objects.filter(expires_at__lt=timezone.now()).delete()
        return Response({'deleted': deleted})


# ─────────────────────────────────────────────────────────────────────────────
# 7. Employee Leave Record ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeLeaveRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API for employee leave records imported from the HR Excel.
    Supports filtering by year, department, employee_code, and name search.
    Detail view includes the full monthly breakdown.
    
    SOFT-CODED AUTO-SCOPING (2026-06-26):
    - If no explicit employee_code/search filter → auto-scope to current user's employee_id
    - Fallback to name search if user has no employee_id configured
    - HR/Admin can still query all records by providing explicit filters
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from apps.rbac.models import UserProfile
        
        qs = EmployeeLeaveRecord.objects.prefetch_related('monthly_breakdown').all()
        year   = self.request.query_params.get('year')
        dept   = self.request.query_params.get('department')
        code   = self.request.query_params.get('employee_code')
        search = self.request.query_params.get('search')
        branch = self.request.query_params.get('branch')
        
        # Soft-coded auto-scoping: if no explicit employee_code/search filter provided,
        # automatically scope to current user's employee_id from RBAC profile
        if not code and not search:
            try:
                profile = UserProfile.objects.select_related('user').get(user=self.request.user)
                employee_id = profile.employee_id
                
                # Define invalid/marker employee_id values (soft-coded list)
                invalid_markers = ['DELETED', 'TEST_ACCOUNT', 'EXTERNAL']
                
                # Validate employee_id (not empty, not email format, not placeholder, not marker)
                if (employee_id and employee_id.strip() and 
                    '@' not in employee_id and 
                    not employee_id.startswith('EMP') and 
                    employee_id not in invalid_markers):
                    # Valid employee_id → filter by it
                    code = employee_id
                else:
                    # Invalid/missing employee_id → fallback to name search
                    user_first = profile.user.first_name or ''
                    user_last = profile.user.last_name or ''
                    if user_first or user_last:
                        search = f"{user_first} {user_last}".strip()
                    elif profile.user.username:
                        search = profile.user.username
            except UserProfile.DoesNotExist:
                # User has no RBAC profile → try username fallback
                if self.request.user.username:
                    search = self.request.user.username
        
        # Apply filters using soft-coded values or explicit query params
        if year:
            qs = qs.filter(year=year)
        if dept:
            qs = qs.filter(department__iexact=dept)
        if code:
            qs = qs.filter(employee_code=code)
        if search:
            qs = qs.filter(employee_name__icontains=search)
        if branch:
            qs = qs.filter(branch__iexact=branch)
        
        return qs.order_by('employee_name')

    def get_serializer_class(self):
        # Detail view returns monthly breakdown; list view is lightweight
        if self.action == 'retrieve':
            return EmployeeLeaveRecordSerializer
        return EmployeeLeaveRecordListSerializer


# ─────────────────────────────────────────────────────────────────────────────
# 8. LeaveType — read-only list of leave type codes
# ─────────────────────────────────────────────────────────────────────────────

class LeaveTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only list of active leave types.
    Admins can create/edit types in the Django admin; they appear here immediately.
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = LeaveTypeSerializer
    queryset           = LeaveType.objects.filter(is_active=True)


# ─────────────────────────────────────────────────────────────────────────────
# 9. LeaveRequest — full CRUD with approve / reject / cancel actions
# ─────────────────────────────────────────────────────────────────────────────

class LeaveRequestViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for leave requests.
    POST to create (any auth user / HR on behalf of employee).
    Custom actions: /approve/, /reject/, /cancel/
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = LeaveRequestSerializer

    @staticmethod
    def _user_employee_code(user):
        """Safely read the biometric employee_id from the user's RBAC profile."""
        try:
            return user.rbac_profile.employee_id or None
        except Exception:
            return None

    def get_queryset(self):
        qs = (
            LeaveRequest.objects
            .select_related('leave_type', 'employee', 'reviewed_by', 'rm_reviewed_by')
            .all()
        )
        user = self.request.user

        if not user.is_staff:
            if _is_hr_manager(user):
                # HR Managers see all requests (no filter needed)
                pass
            else:
                # Regular employees: own requests only.
                # Also include requests where this user is the Reporting Manager
                # (so they can action Stage-1 approvals for their team).
                emp_code  = self._user_employee_code(user)
                own_q     = Q(employee=user)
                if emp_code:
                    own_q |= Q(employee_code=emp_code)
                # Include requests where the employee's RBAC profile manager = this user
                managed_q = Q(employee__rbac_profile__manager__user=user)
                qs = qs.filter(own_q | managed_q)

        params = self.request.query_params
        st      = params.get('status')
        code    = params.get('employee_code')
        year    = params.get('year')
        month   = params.get('month')
        search  = params.get('search')
        if st:
            qs = qs.filter(status__iexact=st)
        if code:
            qs = qs.filter(employee_code=code)
        if year and month:
            import datetime as _dt, calendar as _cal
            y, m = int(year), int(month)
            first = _dt.date(y, m, 1)
            last  = _dt.date(y, m, _cal.monthrange(y, m)[1])
            qs = qs.filter(start_date__lte=last, end_date__gte=first)
        elif year:
            qs = qs.filter(start_date__year=int(year))
        if search:
            qs = qs.filter(employee_name__icontains=search)
        return qs.order_by('-created_at')

    def perform_create(self, serializer):
        """Auto-link the leave request to the authenticated user."""
        user     = self.request.user
        emp_code = self._user_employee_code(user)
        emp_name = f'{user.first_name} {user.last_name}'.strip() or user.username
        extra    = {'employee': user}
        # Only fill denormalised fields if the caller didn’t supply them
        if emp_code and not serializer.validated_data.get('employee_code'):
            extra['employee_code'] = emp_code
        if not serializer.validated_data.get('employee_name'):
            extra['employee_name'] = emp_name
        serializer.save(**extra)

    @action(detail=True, methods=['post'], url_path='rm-approve')
    def rm_approve(self, request, pk=None):
        """Stage-1: Reporting Manager approves a PENDING request → RM_APPROVED."""
        req = self.get_object()
        if req.status != LeaveRequestStatus.PENDING:
            return Response(
                {'error': f'Cannot RM-approve a {req.get_status_display()} request. '
                          f'Only PENDING requests can be approved at this stage.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req.status          = LeaveRequestStatus.RM_APPROVED
        req.rm_reviewed_by  = request.user
        req.rm_reviewed_at  = timezone.now()
        req.rm_note         = request.data.get('note', '')
        req.save()
        return Response(LeaveRequestSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='rm-reject')
    def rm_reject(self, request, pk=None):
        """Stage-1: Reporting Manager rejects a PENDING request → RM_REJECTED."""
        req = self.get_object()
        if req.status != LeaveRequestStatus.PENDING:
            return Response(
                {'error': f'Cannot RM-reject a {req.get_status_display()} request. '
                          f'Only PENDING requests can be rejected at this stage.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req.status          = LeaveRequestStatus.RM_REJECTED
        req.rm_reviewed_by  = request.user
        req.rm_reviewed_at  = timezone.now()
        req.rm_note         = request.data.get('note', '')
        req.save()
        return Response(LeaveRequestSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Stage-2: HR Manager gives final approval — only after Reporting Manager approved."""
        if not _is_hr_manager(request.user):
            return Response(
                {'error': 'Only HR Managers can give final approval.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        req = self.get_object()
        if req.status != LeaveRequestStatus.RM_APPROVED:
            return Response(
                {'error': 'Reporting Manager approval is required before HR final approval. '
                          f'Current status: {req.get_status_display()}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req.status        = LeaveRequestStatus.APPROVED
        req.reviewed_by   = request.user
        req.reviewed_at   = timezone.now()
        req.reviewer_note = request.data.get('note', '')
        req.save()
        return Response(LeaveRequestSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Stage-2: HR Manager rejects — accepts RM_APPROVED or PENDING (HR can override)."""
        if not _is_hr_manager(request.user):
            return Response(
                {'error': 'Only HR Managers can reject leave requests at this stage.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        req = self.get_object()
        if req.status not in (LeaveRequestStatus.RM_APPROVED, LeaveRequestStatus.PENDING):
            return Response(
                {'error': f'Cannot reject a {req.get_status_display()} request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        req.status        = LeaveRequestStatus.REJECTED
        req.reviewed_by   = request.user
        req.reviewed_at   = timezone.now()
        req.reviewer_note = request.data.get('note', '')
        req.save()
        return Response(LeaveRequestSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        req = self.get_object()
        req.status = LeaveRequestStatus.CANCELLED
        req.save()
        return Response(LeaveRequestSerializer(req).data)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Leave Calendar  — per-employee, per-day approved leave map
#     Consumed by the Summary attendance pivot table to overlay leave codes.
# ─────────────────────────────────────────────────────────────────────────────
# 10. branch_employee_codes — lightweight codes-only endpoint for branch filter
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def branch_employee_codes(request):
    """
    GET /api/v1/payroll/branch-employee-codes/?branch=RIN&year=2026

    Returns a flat list of employee_code values for a given branch (and
    optionally a year).  Used by the frontend Attendance Summary tab to
    filter biometric rows by legal entity without fetching full leave records.

    Response: { "branch": "RIN", "year": 2026, "codes": ["E001", "E002", …] }
    """
    branch = request.GET.get('branch', '').upper().strip()
    year   = request.GET.get('year')

    if not branch:
        return Response({'error': 'branch parameter is required.'}, status=400)

    qs = EmployeeLeaveRecord.objects.filter(branch__iexact=branch)
    if year:
        qs = qs.filter(year=int(year))

    codes = list(qs.values_list('employee_code', flat=True).order_by('employee_code'))
    return Response({'branch': branch, 'year': int(year) if year else None, 'codes': codes})


# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_calendar(request):
    """
    GET /api/v1/payroll/leave-calendar/?year=2026&month=6

    Returns approved leave for all employees in a given month, keyed by
    employee_code → { YYYY-MM-DD: {code, name, color, badge_bg, badge_text, request_id} }.
    Only working days (Mon–Fri) are included.  Used by the Summary attendance
    view to overlay leave codes on the biometric attendance pivot table.
    """
    import calendar as _cal
    import datetime as _dt

    year  = int(request.GET.get('year',  timezone.now().year))
    month = int(request.GET.get('month', timezone.now().month))

    first_day = _dt.date(year, month, 1)
    last_day  = _dt.date(year, month, _cal.monthrange(year, month)[1])

    requests = (
        LeaveRequest.objects
        .select_related('leave_type')
        .filter(
            status=LeaveRequestStatus.APPROVED,
            start_date__lte=last_day,
            end_date__gte=first_day,
        )
    )

    calendar_data: dict = {}
    for req in requests:
        if not req.employee_code:
            continue
        # Safety: skip requests with missing leave_type (data integrity issue)
        if not req.leave_type:
            continue
        emp = req.employee_code
        if emp not in calendar_data:
            calendar_data[emp] = {}
        # Expand date range, clamped to the requested month
        cur = max(req.start_date, first_day)
        end = min(req.end_date, last_day)
        while cur <= end:
            if cur.weekday() < 5:   # weekdays only (0=Mon…4=Fri)
                calendar_data[emp][cur.isoformat()] = {
                    'code':       req.leave_type.code,
                    'name':       req.leave_type.name,
                    'color':      req.leave_type.color_hex,
                    'badge_bg':   req.leave_type.badge_bg,
                    'badge_text': req.leave_type.badge_text,
                    'request_id': str(req.id),
                }
            cur += _dt.timedelta(days=1)

    return Response({'year': year, 'month': month, 'calendar': calendar_data})


# =============================================================================
# Helper: is_hr_manager
# =============================================================================
def _is_hr_manager(user) -> bool:
    """Return True if the user holds an HR Manager (or admin) role.

    Soft-coded role codes live in apps.rbac -- we look for any role whose code
    starts with 'hr' or equals 'admin'/'superadmin'.  This keeps the check
    forward-compatible: adding a new HR sub-role in the RBAC admin will
    automatically grant access here without code changes.

    Falls back to user.is_staff / user.is_superuser so Django admin accounts
    always retain access even if RBAC is not fully configured.
    """
    if user.is_superuser or user.is_staff:
        return True
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if profile and profile.role:
            code = (profile.role.code or '').lower()
            return code.startswith('hr') or code in ('admin', 'superadmin', 'manager')
    except Exception:
        pass
    return False


# =============================================================================
# 10. PublicHoliday ViewSet
# =============================================================================

class PublicHolidayViewSet(viewsets.ModelViewSet):
    """
    CRUD for public holidays.

    List/Retrieve: any authenticated user (read-only employees can see the calendar).
    Create/Update/Deactivate: HR Manager or admin only.

    Soft-coded filter params:
      ?year=2026          filter by year (default: current year)
      ?region=AE-AZ       filter by region (default: no filter ? return all)
      ?active_only=true   return only is_active=True entries (default: true)
    """
    serializer_class   = PublicHolidaySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        params   = self.request.query_params
        year     = int(params.get('year', datetime.date.today().year))
        region   = params.get('region', None)
        active   = params.get('active_only', 'true').lower() not in ('0', 'false', 'no')

        qs = PublicHoliday.objects.filter(date__year=year)
        if region:
            qs = qs.filter(region=region)
        if active:
            qs = qs.filter(is_active=True)
        return qs.order_by('date')

    def perform_create(self, serializer):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can create public holidays.')
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            source='hr_added',
        )

    def perform_update(self, serializer):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can update public holidays.')
        serializer.save(updated_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """Override destroy to deactivate instead of hard-delete."""
        if not _is_hr_manager(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can deactivate public holidays.')
        obj = self.get_object()
        obj.is_active = False
        obj.updated_by = request.user
        obj.save(update_fields=['is_active', 'updated_by', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# 11. AttendanceOverride ViewSet
# =============================================================================

class AttendanceOverrideViewSet(viewsets.ModelViewSet):
    """
    HR Manager manual corrections for Summary pivot cells.

    List: returns all active overrides for a given month.
        ?year=2026&month=6     filter by year+month (required for Summary tab)
        ?employee_code=E001    filter by specific employee
    Retrieve/Create/Update/Deactivate: HR Manager or admin only.
    Delete is disabled -- overrides are deactivated (is_active=False) instead.

    The frontend applies overrides by building a lookup dict:
        { 'E001': { '2026-06-15': { override_hours: 8.0, reason: '...', ... } } }
    so only ONE override per (employee_code, date) is returned -- the most recent
    active one.  The endpoint returns flat rows; deduplication is in the frontend.
    """
    serializer_class   = AttendanceOverrideSerializer
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']  # no DELETE

    def get_queryset(self):
        params        = self.request.query_params
        year          = params.get('year')
        month         = params.get('month')
        employee_code = params.get('employee_code')

        qs = AttendanceOverride.objects.filter(is_active=True)
        if year and month:
            qs = qs.filter(date__year=int(year), date__month=int(month))
        if employee_code:
            qs = qs.filter(employee_code=employee_code)
        return qs.order_by('employee_code', 'date')

    def _require_hr(self):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only HR Managers can edit attendance overrides.')

    def perform_create(self, serializer):
        self._require_hr()
        # Deactivate any previous override for same (employee_code, date) to
        # ensure only one active record per cell.
        employee_code = serializer.validated_data.get('employee_code')
        date          = serializer.validated_data.get('date')
        if employee_code and date:
            AttendanceOverride.objects.filter(
                employee_code=employee_code,
                date=date,
                is_active=True,
            ).update(is_active=False)
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._require_hr()
        serializer.save()


# =============================================================================
# Salary Management helpers
# =============================================================================

def _is_senior_hr(user) -> bool:
    """True for superuser, staff, or roles with senior-level HR access."""
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    try:
        roles = user.userprofile.roles.all()
        for role in roles:
            code = (role.code or '').lower()
            if code.startswith('senior_hr') or code in ('admin', 'superadmin', 'manager'):
                return True
    except Exception:
        pass
    return False


# =============================================================================
# 10. SalaryComponent
# =============================================================================

class SalaryComponentViewSet(viewsets.ModelViewSet):
    """
    HR Managers can create/update component types.
    Senior HR / Admin can deactivate.
    Read access for all authenticated users.
    """
    serializer_class   = SalaryComponentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = SalaryComponent.objects.all()
        active_only = self.request.query_params.get('active')
        if active_only and active_only.lower() == 'true':
            qs = qs.filter(is_active=True)
        cat = self.request.query_params.get('category')
        if cat:
            qs = qs.filter(category=cat)
        return qs

    def _require_hr(self):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('HR Manager role required.')

    def perform_create(self, serializer):
        self._require_hr()
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._require_hr()
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Soft-delete (deactivate) instead of hard delete."""
        if not _is_senior_hr(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Senior HR role required to deactivate components.')
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# 11. EmployeeSalaryStructure
# =============================================================================

class EmployeeSalaryStructureViewSet(viewsets.ModelViewSet):
    """
    Workflow: DRAFT -> PENDING_APPROVAL -> APPROVED | REJECTED

    Endpoints:
      POST   salary-structures/                   create (HR Manager)
      PATCH  salary-structures/{id}/              update draft
      POST   salary-structures/{id}/submit/       submit for approval
      POST   salary-structures/{id}/approve/      approve (Senior HR)
      POST   salary-structures/{id}/reject/       reject (Senior HR)
      GET    salary-structures/pending/           list pending (Senior HR)
      GET    salary-structures/summary/           one active row per employee
    """
    serializer_class   = EmployeeSalaryStructureSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = EmployeeSalaryStructure.objects.all()
        emp = self.request.query_params.get('employee_code')
        if emp:
            qs = qs.filter(employee_code=emp)
        stat = self.request.query_params.get('status')
        if stat:
            qs = qs.filter(status=stat)
        active = self.request.query_params.get('active')
        if active and active.lower() == 'true':
            qs = qs.filter(is_active=True)
        return qs

    def _require_hr(self):
        if not _is_hr_manager(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('HR Manager role required.')

    def _require_senior_hr(self):
        if not _is_senior_hr(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Senior HR role required.')

    def perform_create(self, serializer):
        self._require_hr()
        serializer.save(
            created_by=self.request.user,
            status=SalaryStructureStatus.DRAFT,
        )

    def perform_update(self, serializer):
        self._require_hr()
        obj = self.get_object()
        if obj.status not in (SalaryStructureStatus.DRAFT, SalaryStructureStatus.REJECTED):
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Only DRAFT or REJECTED structures can be edited.')
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Soft-delete (deactivate) — no hard deletes."""
        self._require_hr()
        obj = self.get_object()
        if obj.status == SalaryStructureStatus.APPROVED:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Cannot delete an APPROVED salary structure.')
        obj.is_active = False
        obj.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        """HR Manager submits draft for approval."""
        self._require_hr()
        obj = self.get_object()
        if obj.status != SalaryStructureStatus.DRAFT:
            return Response(
                {'detail': 'Only DRAFT structures can be submitted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj.status       = SalaryStructureStatus.PENDING_APPROVAL
        obj.submitted_by = request.user
        obj.submitted_at = timezone.now()
        obj.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        return Response(EmployeeSalaryStructureSerializer(obj).data)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Senior HR approves a pending structure and writes SalaryHistory."""
        self._require_senior_hr()
        obj = self.get_object()
        if obj.status != SalaryStructureStatus.PENDING_APPROVAL:
            return Response(
                {'detail': 'Only PENDING_APPROVAL structures can be approved.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Deactivate prior active structure
        prev_qs = EmployeeSalaryStructure.objects.filter(
            employee_code=obj.employee_code,
            is_active=True,
            status=SalaryStructureStatus.APPROVED,
        ).exclude(pk=obj.pk)
        prev = prev_qs.first()
        previous_basic = prev.basic_salary if prev else None
        previous_net   = prev.net_salary   if prev else None
        if prev:
            prev.is_active     = False
            prev.superseded_by = obj
            prev.save(update_fields=['is_active', 'superseded_by', 'updated_at'])

        # Approve current
        obj.status      = SalaryStructureStatus.APPROVED
        obj.reviewed_by = request.user
        obj.reviewed_at = timezone.now()
        obj.reviewer_note = request.data.get('reviewer_note', '')
        obj.is_active   = True
        obj.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'reviewer_note',
            'is_active', 'updated_at',
        ])

        # Compute change_percent
        change_pct = None
        if previous_net and previous_net != 0:
            change_pct = round(
                ((obj.net_salary - previous_net) / previous_net) * 100,
                2,
            )

        # Write audit history
        SalaryHistory.objects.create(
            employee_code  = obj.employee_code,
            employee_name  = obj.employee_name,
            change_date    = obj.effective_date,
            previous_basic = previous_basic,
            new_basic      = obj.basic_salary,
            previous_net   = previous_net,
            new_net        = obj.net_salary,
            change_percent = change_pct,
            change_reason  = obj.reviewer_note or 'Salary structure approved',
            structure      = obj,
            approved_by    = request.user,
        )

        return Response(EmployeeSalaryStructureSerializer(obj).data)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Senior HR rejects a pending structure."""
        self._require_senior_hr()
        obj = self.get_object()
        if obj.status != SalaryStructureStatus.PENDING_APPROVAL:
            return Response(
                {'detail': 'Only PENDING_APPROVAL structures can be rejected.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj.status        = SalaryStructureStatus.REJECTED
        obj.reviewed_by   = request.user
        obj.reviewed_at   = timezone.now()
        obj.reviewer_note = request.data.get('reviewer_note', '')
        obj.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'reviewer_note', 'updated_at',
        ])
        return Response(EmployeeSalaryStructureSerializer(obj).data)

    @action(detail=False, methods=['get'], url_path='pending')
    def pending(self, request):
        """Return all structures awaiting approval (Senior HR only)."""
        self._require_senior_hr()
        qs = EmployeeSalaryStructure.objects.filter(
            status=SalaryStructureStatus.PENDING_APPROVAL,
        ).order_by('submitted_at')
        return Response(EmployeeSalaryStructureSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """
        Return one active APPROVED structure per employee (current salary).
        Optionally filter by department.
        """
        qs = EmployeeSalaryStructure.objects.filter(
            is_active=True,
            status=SalaryStructureStatus.APPROVED,
        ).order_by('employee_name')
        dept = request.query_params.get('department')
        if dept:
            qs = qs.filter(department__icontains=dept)
        return Response(EmployeeSalaryStructureSerializer(qs, many=True).data)


# =============================================================================
# 12. SalaryHistory
# =============================================================================

class SalaryHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only. Returns the salary change audit trail.
    Filter by employee_code or date range.
    """
    serializer_class   = SalaryHistorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs  = SalaryHistory.objects.all()
        emp = self.request.query_params.get('employee_code')
        if emp:
            qs = qs.filter(employee_code=emp)
        date_from = self.request.query_params.get('date_from')
        date_to   = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(change_date__gte=date_from)
        if date_to:
            qs = qs.filter(change_date__lte=date_to)
        return qs


# =============================================================================
# Annual Leave Balance Summary
# =============================================================================

def _norm_name(name: str) -> str:
    """Normalise an employee name to a stable lowercase-stripped key for fuzzy match."""
    return ' '.join((name or '').lower().split())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def annual_leave_balance(request):
    """
    GET /api/v1/payroll/annual-leave-balance/?year=2026&month=6

    Returns the YTD leave balance for every employee as of end of *month*.
    The response carries TWO lookup indices so the frontend can match even
    when biometric employee_code format differs from HR Excel codes:

      balances          → keyed by employee_code  (primary lookup)
      balances_by_name  → keyed by normalised lowercase name (fallback)

    Each value:
      { employee_name, joining_date, carryforward,
        earned_ytd, taken_ytd, encashed_ytd, balance,
        annual_entitlement }

    Computation: 22 days/year accrual service formula (pro-rated joining month).
    taken/encashed come from the stored HR-Excel import rows; earned is always
    recomputed from the formula so it stays accurate even if monthly rows are stale.
    """
    from apps.payroll.models import EmployeeLeaveRecord
    from apps.payroll.services.leave_accrual import (
        compute_monthly_earned, compute_running_balance, _dec, ANNUAL_LEAVE_DAYS,
    )
    from datetime import date as _date

    year  = int(request.GET.get('year',  timezone.now().year))
    month = int(request.GET.get('month', timezone.now().month))

    qs = (
        EmployeeLeaveRecord.objects
        .prefetch_related('monthly_breakdown')
        .filter(year=year)
    )
    branch = request.GET.get('branch')
    if branch:
        qs = qs.filter(branch__iexact=branch)

    today  = _date.today()
    result_by_code = {}
    result_by_name = {}

    for rec in qs:
        # Build monthly taken/encashed from stored import rows (HR source of truth)
        stored = {
            r.month: r
            for r in rec.monthly_breakdown.all()
        }

        # Always recompute earned from formula; use stored taken/encashed
        monthly_rows = []
        for m in range(1, month + 1):
            earned   = compute_monthly_earned(
                rec.joining_date, year, m,
                rec.annual_entitlement or ANNUAL_LEAVE_DAYS, today,
            )
            row = stored.get(m)
            taken    = _dec(row.taken    if row else 0)
            encashed = _dec(row.encashed if row else 0)
            monthly_rows.append({'month': m, 'earned': earned, 'taken': taken, 'encashed': encashed})

        balances   = compute_running_balance(_dec(rec.carryforward), monthly_rows, up_to_month=month)
        balance_at = float(balances.get(month, 0))
        earned_ytd   = round(sum(float(r['earned'])   for r in monthly_rows), 4)
        taken_ytd    = round(sum(float(r['taken'])    for r in monthly_rows), 4)
        encashed_ytd = round(sum(float(r['encashed']) for r in monthly_rows), 4)

        payload = {
            'employee_name':      rec.employee_name,
            'joining_date':       rec.joining_date.isoformat() if rec.joining_date else None,
            'carryforward':       float(rec.carryforward),
            'earned_ytd':         earned_ytd,
            'taken_ytd':          taken_ytd,
            'encashed_ytd':       encashed_ytd,
            'balance':            round(balance_at, 4),
            'annual_entitlement': int(rec.annual_entitlement or ANNUAL_LEAVE_DAYS),
        }

        # Primary key: employee_code (string)
        if rec.employee_code:
            result_by_code[str(rec.employee_code).strip()] = payload

        # Fallback key: normalised name (handles code-format mismatches)
        norm = _norm_name(rec.employee_name)
        if norm:
            result_by_name[norm] = payload

    return Response({
        'year':             year,
        'month':            month,
        'balances':         result_by_code,    # keyed by employee_code
        'balances_by_name': result_by_name,    # keyed by normalised name
    })


# =============================================================================
# HR Admin: Upload leave Excel + trigger import+compute (one-shot seed)
# =============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_leave_data(request):
    """
    POST /api/v1/payroll/sync-leave-data/

    HR Manager only.  Accepts a multipart Excel upload (field: file) and:
      1. Saves it to a temp path
      2. Runs import_leave_excel  (upsert all EmployeeLeaveRecord rows)
      3. Runs compute_leave_accrual  (recompute earned + balance)

    Returns a summary dict:  { imported, updated, errors, computed, year }

    Allows seeding / refreshing production from the HR Manager's browser
    without needing Railway CLI access.
    """
    import os, tempfile, warnings
    from decimal import Decimal, InvalidOperation
    from apps.payroll.models import EmployeeLeaveRecord, EmployeeLeaveMonthly
    from apps.payroll.management.commands.import_leave_excel import (
        LEAVE_EXCEL_MAP, BRANCH_OVERRIDES, _to_dec, _to_str,
        _clean_emp_code, _clean_title,
    )
    from apps.payroll.services.leave_accrual import compute_accrual_for_record
    from django.db import transaction

    if not _is_hr_manager(request.user):
        raise PermissionDenied('HR Manager role required to sync leave data.')

    upload = request.FILES.get('file')
    if not upload:
        return Response({'error': 'No file uploaded. POST with field name "file".'}, status=400)

    year   = int(request.data.get('year',   timezone.now().year))
    branch = (request.data.get('branch') or 'RAD').upper().strip()

    # Soft-coded column map for RAD branch
    override = BRANCH_OVERRIDES.get(branch, {})
    em   = {**LEAVE_EXCEL_MAP, **{k: v for k, v in override.items() if k not in ('default_path',)}}
    src  = override.get('source_tag', LEAVE_EXCEL_MAP['source_tag'])
    mmap = em['month_map']

    try:
        import openpyxl
    except ImportError:
        return Response({'error': 'openpyxl not installed on this server.'}, status=500)

    # Save upload to a temp file
    suffix = os.path.splitext(upload.name)[1] or '.xlsx'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.close()

        warnings.filterwarnings('ignore')
        wb = openpyxl.load_workbook(tmp.name, data_only=True)
        sheets = wb.sheetnames

        created = updated = skipped = import_errors = 0

        for sheet_name in sheets:
            ws = wb[sheet_name]
            raw_name    = ws.cell(*em['name_cell']).value
            raw_emp_no  = ws.cell(*em['emp_no_cell']).value
            raw_dept    = ws.cell(*em['dept_cell']).value
            raw_title   = ws.cell(*em['title_cell']).value
            raw_joining = ws.cell(*em['joining_cell']).value

            name = _to_str(raw_name)
            if not name or name.lower() in ('name', 'employee', ''):
                skipped += 1
                continue

            emp_code    = _clean_emp_code(raw_emp_no)
            job_title   = _clean_title(raw_emp_no, raw_title)
            department  = _to_str(raw_dept) or None
            joining_date = raw_joining.date() if hasattr(raw_joining, 'date') else None

            tr  = em['total_row']
            cfr = em['carryforward_row']
            total_earned   = _to_dec(ws.cell(tr, em['col_earned']).value)
            total_taken    = _to_dec(ws.cell(tr, em['col_taken']).value)
            total_encashed = _to_dec(ws.cell(tr, em['col_encashed']).value)
            leave_balance  = _to_dec(ws.cell(tr, em['col_balance']).value)
            carryforward   = _to_dec(ws.cell(cfr, em['col_balance']).value)

            try:
                with transaction.atomic():
                    defaults = dict(
                        employee_name=name, department=department,
                        job_title=job_title or None, joining_date=joining_date,
                        year=year, branch=branch,
                        total_earned=total_earned, total_taken=total_taken,
                        total_encashed=total_encashed, leave_balance=leave_balance,
                        carryforward=carryforward, source_file=src,
                    )
                    if emp_code:
                        rec, was_created = EmployeeLeaveRecord.objects.update_or_create(
                            employee_code=emp_code, year=year, defaults=defaults,
                        )
                    else:
                        rec, was_created = EmployeeLeaveRecord.objects.update_or_create(
                            employee_name=name, year=year,
                            defaults={**defaults, 'employee_code': None},
                        )
                    if was_created:
                        created += 1
                    else:
                        updated += 1

                    for row_idx in range(em['month_start_row'], em['month_end_row'] + 1):
                        month_label = _to_str(ws.cell(row_idx, em['col_month']).value).lower()
                        month_num   = mmap.get(month_label)
                        if month_num is None:
                            continue
                        EmployeeLeaveMonthly.objects.update_or_create(
                            record=rec, month=month_num,
                            defaults=dict(
                                earned   = _to_dec(ws.cell(row_idx, em['col_earned']).value),
                                taken    = _to_dec(ws.cell(row_idx, em['col_taken']).value),
                                encashed = _to_dec(ws.cell(row_idx, em['col_encashed']).value),
                                balance  = _to_dec(ws.cell(row_idx, em['col_balance']).value),
                            ),
                        )
            except Exception as exc:
                import_errors += 1

        # Now recompute accruals for all imported records
        recs = (
            EmployeeLeaveRecord.objects
            .prefetch_related('monthly_breakdown')
            .filter(year=year, branch__iexact=branch)
        )
        computed = computed_errors = 0
        for rec in recs:
            try:
                compute_accrual_for_record(rec, target_year=year, dry_run=False)
                computed += 1
            except Exception:
                computed_errors += 1

    finally:
        os.unlink(tmp.name)

    return Response({
        'year':            year,
        'branch':          branch,
        'sheets_found':    len(sheets),
        'created':         created,
        'updated':         updated,
        'skipped':         skipped,
        'import_errors':   import_errors,
        'computed':        computed,
        'compute_errors':  computed_errors,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def initialize_current_month_leave(request):
    """
    POST /api/v1/payroll/initialize-current-month-leave/
    
    Initialize current month leave balance for all employees.
    Sets the earned value for the current month to the standard monthly accrual (1.83 days)
    for all active leave records in the current year.
    
    Soft-coded values:
      - Monthly accrual = ANNUAL_LEAVE_DAYS / 12 (from leave_accrual.py)
      - Year and month default to current date, or can be overridden via query params
    
    Query Parameters:
      - year (optional): Target year (default: current year)
      - month (optional): Target month 1-12 (default: current month)
      - branch (optional): Filter by branch code (e.g., 'RAD')
      - dry_run (optional): If 'true', preview changes without saving
    
    Returns:
      {
        'year': int,
        'month': int,
        'monthly_accrual': float,  // Standard monthly accrual amount
        'records_processed': int,
        'records_updated': int,
        'records_created': int,
        'dry_run': bool,
        'preview': [...] (if dry_run=true)
      }
    
    Requires: HR Manager role
    """
    from apps.payroll.models import EmployeeLeaveRecord, EmployeeLeaveMonthly
    from apps.payroll.services.leave_accrual import (
        MONTHLY_LEAVE_ACCRUAL, ANNUAL_LEAVE_DAYS, compute_monthly_earned, _dec
    )
    from django.db import transaction
    from datetime import date
    
    if not _is_hr_manager(request.user):
        raise PermissionDenied('HR Manager role required to initialize leave balances.')
    
    # Parse parameters (soft-coded defaults)
    today = date.today()
    year = int(request.data.get('year') or request.GET.get('year') or today.year)
    month = int(request.data.get('month') or request.GET.get('month') or today.month)
    branch = (request.data.get('branch') or request.GET.get('branch') or '').strip().upper()
    dry_run = str(request.data.get('dry_run') or request.GET.get('dry_run') or 'false').lower() == 'true'
    
    # Validate month
    if not (1 <= month <= 12):
        return Response({'error': 'month must be between 1 and 12.'}, status=400)
    
    # Get all leave records for the target year
    qs = EmployeeLeaveRecord.objects.filter(year=year)
    if branch:
        qs = qs.filter(branch__iexact=branch)
    
    records_processed = 0
    records_updated = 0
    records_created = 0
    preview_data = []
    
    for record in qs:
        records_processed += 1
        
        # Compute earned leave for this month using soft-coded formula
        earned = compute_monthly_earned(
            record.joining_date,
            year,
            month,
            record.annual_entitlement or ANNUAL_LEAVE_DAYS,
            reference_date=today
        )
        
        # Get or create monthly record
        monthly, was_created = EmployeeLeaveMonthly.objects.get_or_create(
            record=record,
            month=month,
            defaults={
                'earned': earned,
                'taken': _dec(0),
                'encashed': _dec(0),
                'balance': _dec(0),
            }
        )
        
        if dry_run:
            preview_data.append({
                'employee_code': record.employee_code,
                'employee_name': record.employee_name,
                'earned': float(earned),
                'action': 'create' if was_created else 'update',
                'previous_earned': float(monthly.earned) if not was_created else 0,
            })
        else:
            if not was_created and monthly.earned != earned:
                monthly.earned = earned
                monthly.save(update_fields=['earned'])
                records_updated += 1
            elif was_created:
                records_created += 1

            # Sync EmployeeLeaveRecord.leave_balance from the sum of all monthly
            # earned − taken − encashed for this year (soft-coded: recomputed on every run).
            from django.db.models import Sum as _Sum
            totals = EmployeeLeaveMonthly.objects.filter(record=record).aggregate(
                e=_Sum('earned'), t=_Sum('taken'), enc=_Sum('encashed')
            )
            new_balance = max(
                _dec(0),
                _dec(totals['e'] or 0) - _dec(totals['t'] or 0) - _dec(totals['enc'] or 0),
            )
            if record.leave_balance != new_balance:
                record.leave_balance = new_balance
                record.save(update_fields=['leave_balance'])
    
    response_data = {
        'year': year,
        'month': month,
        'monthly_accrual': float(MONTHLY_LEAVE_ACCRUAL),
        'annual_entitlement': ANNUAL_LEAVE_DAYS,
        'records_processed': records_processed,
        'dry_run': dry_run,
    }
    
    if dry_run:
        response_data['preview'] = preview_data
        response_data['records_would_create'] = sum(1 for p in preview_data if p['action'] == 'create')
        response_data['records_would_update'] = sum(1 for p in preview_data if p['action'] == 'update')
    else:
        response_data['records_updated'] = records_updated
        response_data['records_created'] = records_created
    
    return Response(response_data)


# =============================================================================
# DailyWorkLog ViewSet
# =============================================================================
from .models import DailyWorkLog, DailyWorkLogStatus, DailyWorkLogApprovalStatus  # noqa: E402
from .serializers import DailyWorkLogSerializer  # noqa: E402


class DailyWorkLogViewSet(viewsets.ModelViewSet):
    """
    CRUD for personal daily work logs.

    Default scope: the requesting user''s own logs.
    Staff overrides:
      * ?all=true          -> all users'' logs (staff only)
      * ?user_id=<uuid>    -> specific user''s logs (staff only)

    Date filters (compatible with above scopes):
      * ?date=YYYY-MM-DD
      * ?from=YYYY-MM-DD&to=YYYY-MM-DD
      * ?status=in_progress|done|blocked|deferred

    Custom actions:
      GET  /daily-logs/summary/        -> daily totals (hours + task count) per date
      GET  /daily-logs/export-to-s3/   -> export filtered logs as JSON to S3
      GET  /daily-logs/team/           -> latest log per user for a date (staff only)
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = DailyWorkLogSerializer
    http_method_names  = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user   = self.request.user
        params = self.request.query_params
        qs     = DailyWorkLog.objects.select_related('user')

        # Scope
        if user.is_staff and params.get('all') == 'true':
            pass  # no user filter — all logs
        elif user.is_staff and params.get('user_id'):
            qs = qs.filter(user_id=params.get('user_id'))
        else:
            qs = qs.filter(user=user)

        # Date filters
        exact_date = params.get('date')
        from_date  = params.get('from')
        to_date    = params.get('to')
        if exact_date:
            qs = qs.filter(log_date=exact_date)
        else:
            if from_date:
                qs = qs.filter(log_date__gte=from_date)
            if to_date:
                qs = qs.filter(log_date__lte=to_date)

        # Status filter
        status_filter = params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        # Approval status filter
        approval_filter = params.get('approval_status')
        if approval_filter:
            qs = qs.filter(approval_status=approval_filter)

        return qs.order_by('-log_date', '-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    # ── Permission helper ──────────────────────────────────────────────────
    @staticmethod
    def _can_approve(request_user, log_obj):
        """
        Returns True if request_user may approve/reject log_obj.
        Rules (OR):
          1. request_user.is_staff or is_superuser
          2. request_user is the direct manager of log_obj.user
             (UserProfile.manager FK points to a UserProfile; check if that
              UserProfile.user == request_user)
        """
        if request_user.is_staff or request_user.is_superuser:
            return True
        try:
            from apps.rbac.models import UserProfile  # noqa: F811
            return UserProfile.objects.filter(
                user=log_obj.user,
                manager__user=request_user,
                is_deleted=False,
            ).exists()
        except Exception:
            return False


# =============================================================================
# Master Payroll Generator — Sympa + ValueFrame + RADAI attendance merge
# =============================================================================
#
# Soft-coded field alias maps: add new column synonyms here without changing
# any view logic.  All comparisons are case-insensitive and whitespace-trimmed.
#
# POST /api/v1/payroll/generate-master-payroll/
#   multipart fields:  sympa_file (opt), valueframe_file (opt), year, month
#   query param:       ?format=xlsx  →  returns binary Excel instead of JSON
#
# =============================================================================

# ── Soft-coded parser constants ───────────────────────────────────────────────
_HEADER_SCAN_ROWS = 25   # max rows to probe when auto-detecting the real header row
_HEADER_MIN_COLS  = 3    # minimum non-empty cells to qualify a row as the header
# Soft-coded payroll constants — override via env vars without touching code
_PAYROLL_WORKING_DAYS           = int(os.environ.get('PAYROLL_WORKING_DAYS', 22))
# Flag a cross-source hours discrepancy when VF vs biometric diverge by more
# than this percentage of the larger value.
_BIOMETRIC_HOURS_DISCREPANCY_PCT = float(os.environ.get('BIOMETRIC_HOURS_DISCREPANCY_PCT', 20))

# ── Alias tables (first match wins) ──────────────────────────────────────────
_SYMPA_ALIASES = {
    # NOTE: standard Sympa exports have NO employee-code column.
    # The view falls back to name-based keying when this field is absent.
    'employee_code':       ['employee no', 'employee id', 'emp no', 'emp id',
                            'personnel no', 'staff id', 'empno', 'id no',
                            'employee number', 'employee_number'],
    # Sympa "Preferred given name" contains the full display name
    'employee_name':       ['preferred given name', 'preferred name', 'display name',
                            'full name', 'employee name', 'emp name', 'name',
                            'staff name', 'employee full name'],
    # Sympa "Business Area" is the department
    'department':          ['business area', 'business unit', 'department', 'dept',
                            'division', 'cost centre', 'cost center', 'team'],
    'job_title':           ['job title uae', 'job title', 'position', 'title',
                            'designation', 'role'],
    'joining_date':        ['joining date', 'join date', 'hire date', 'date of joining',
                            'doj', 'start date', 'employment date', 'commencement date',
                            'employment start date'],
    # Sympa uses "Currently valid …" salary column names
    'basic_salary':        ['currently valid monthly base salary', 'monthly base salary',
                            'basic salary', 'basic', 'base salary', 'monthly salary',
                            'salary', 'basic pay'],
    'housing_allowance':   ['currently valid housing allowance', 'housing allowance',
                            'house allowance', 'hra', 'housing', 'home allowance',
                            'accommodation allowance'],
    'transport_allowance': ['currently valid transportation allowance',
                            'currently valid transport allowance',
                            'transport allowance', 'transport', 'ta',
                            'travel allowance', 'commute allowance', 'transportation'],
    # "Home leave allowance" in Sympa maps to other allowances
    'other_allowances':    ['currently valid home leave allowance', 'home leave allowance',
                            'other allowances', 'misc allowances', 'miscellaneous',
                            'additional allowances'],
    'other_pay':           ['other pay', 'other payment', 'extra pay', 'additional pay',
                            'other compensation', 'additional compensation', 'bonus pay',
                            'other emoluments'],
    'deductions':          ['deductions', 'total deductions', 'deduction', 'monthly deduction',
                            'salary deduction'],
    'deduction_details':   ['deduction details', 'deduction remarks', 'deduction notes',
                            'deduction breakdown', 'salary deduction details',
                            'deduction description'],
    'details':             ['details', 'notes', 'remarks', 'additional details',
                            'employee notes', 'comments', 'employee remarks'],
    'leave_balance':       ['annual leave balance', 'leave balance', 'remaining leave',
                            'al balance', 'leave days remaining'],
    # Combined-allowance fallback: some HR exports provide a single total instead of
    # individual breakdown columns.  When detected, the value is placed into
    # other_allowances so the cascade formula runs correctly end-to-end.
    'total_allowances':    ['total allowances', 'total allowance', 'allowance total',
                            'gross allowances', 'total benefits', 'allowances',
                            'total monthly allowances', 'monthly allowances'],
    # Sympa "Surname" — used to build a full name when preferred name is first-name-only
    '_surname':            ['surname', 'last name', 'family name'],
}

_VF_ALIASES = {
    # ValueFrame "Employee Number" is the canonical ID (integer in the report)
    'employee_code':  ['employee number', 'employee no', 'employee id', 'emp no',
                       'resource id', 'staff id', 'personnel no', 'resource code'],
    'employee_name':  ['employee name', 'name', 'full name', 'resource', 'resource name'],
    # VF wage-type report: "Total Hours" is the sum row; "Normal" is regular hours
    'total_hours':    ['total hours', 'hours', 'normal', 'billed hours', 'worked hours',
                       'billable hours', 'actual hours', 'logged hours'],
    'overtime_hours': ['overtime hours', 'ot hours', 'extra hours', 'overtime', 'ot',
                       'working time flexibility free'],
    'project_code':   ['project code', 'project no', 'project', 'project id',
                       'proj code', 'project number'],
    'project_name':   ['project name', 'proj name', 'project description', 'project title'],
    'month':          ['month', 'period month', 'billing month', 'period'],
    'year':           ['year', 'period year', 'billing year'],
}

# Alias table for the supplementary "other" file.
# Also handles Sympa annual-leave exports (Employee number + leave days per request).
_OTHER_ALIASES = {
    # Annual-leave export: "Employee number" (lowercase n)
    'employee_code':     ['employee number', 'employee no', 'employee id', 'emp no',
                          'emp id', 'personnel no', 'staff id', 'empno', 'id no'],
    # Annual-leave export shares "Preferred given name" with Sympa — used as bridge
    'employee_name':     ['preferred given name', 'preferred name', 'full name',
                          'employee name', 'emp name', 'name', 'staff name'],
    # Annual-leave exports: "Annual leave:\nDuration in days"
    'leave_days_used':   ['annual leave:\nduration in days', 'duration in days',
                          'leave duration', 'annual leave duration', 'leave days',
                          'days taken', 'annual leave days'],
    'leave_type':        ['annual leave:\ntype of leave', 'type of leave', 'leave type'],
    'leave_status':      ['annual leave:\napproval', 'approval', 'leave approval',
                          'annual leave:\nstatus', 'leave status', 'status'],
    'leave_start':       ['annual leave:\nstart date', 'start date', 'leave start'],
    'leave_end':         ['annual leave:\nend date', 'end date', 'leave end'],
    # Generic financial supplementary fields
    'bonus':             ['bonus', 'performance bonus', 'annual bonus', 'variable pay',
                          'bonus amount', 'variable bonus'],
    'gratuity':          ['gratuity', 'end of service', 'eos', 'eos benefit',
                          'gratuity pay', 'end of service gratuity', 'gratuity amount'],
    'insurance':         ['insurance', 'health insurance', 'medical insurance',
                          'insurance deduction', 'medical deduction', 'health deduction'],
    'commission':        ['commission', 'sales commission', 'incentive commission',
                          'commission amount'],
    'incentive':         ['incentive', 'performance incentive', 'kpi incentive',
                          'target incentive', 'monthly incentive', 'incentive amount'],
    'special_deduction': ['special deduction', 'loan deduction', 'advance deduction',
                          'advance recovery', 'salary advance', 'loan recovery',
                          'other deduction'],
    'adjustment':        ['adjustment', 'salary adjustment', 'pay adjustment',
                          'correction', 'retroactive', 'arrears', 'net adjustment'],
    'notes':             ['notes', 'remarks', 'details', 'comments', 'description',
                          'employee remarks'],
}


def _detect_columns(df_columns, alias_map):
    """Return {canonical_field: actual_df_column} by matching headers to aliases."""
    cols_lower = {c.strip().lower(): c for c in df_columns}
    mapping = {}
    for field, aliases in alias_map.items():
        for alias in aliases:
            if alias in cols_lower:
                mapping[field] = cols_lower[alias]
                break
    return mapping


def _parse_file(upload):
    """
    Parse an uploaded file (XLSX, XLS, or CSV) into a pandas DataFrame.

    Smart header detection: scans up to _HEADER_SCAN_ROWS rows to find the
    actual column-header row.  Handles ValueFrame-style reports where metadata
    text fills rows 1-11 before the real table starts at row 12.
    """
    import pandas as pd
    import tempfile, os

    suffix = (os.path.splitext(upload.name)[1] or '').lower() or '.xlsx'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.close()
        if suffix == '.csv':
            df = pd.read_csv(tmp.name, dtype=str).fillna('')
        else:
            # ── Smart header-row detection ────────────────────────────────────
            # Read a probe slice (no header) and find the first row that has
            # at least _HEADER_MIN_COLS non-empty / non-nan cells — that row
            # is the real column header.
            probe = pd.read_excel(
                tmp.name, header=None, dtype=str, nrows=_HEADER_SCAN_ROWS
            ).fillna('')
            header_row = 0
            for idx, row_s in probe.iterrows():
                populated = sum(
                    1 for v in row_s
                    if str(v).strip() and str(v).strip().lower() != 'nan'
                )
                if populated >= _HEADER_MIN_COLS:
                    header_row = int(idx)
                    break
            df = pd.read_excel(tmp.name, header=header_row, dtype=str).fillna('')
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    # Normalise column names: strip leading/trailing whitespace
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _safe_dec(v):
    """Convert a string value to Decimal, returning 0 on failure."""
    try:
        return Decimal(str(v).replace(',', '').strip())
    except Exception:
        return Decimal('0')


def _safe_float(v):
    """Convert a string value to float, returning 0 on failure."""
    try:
        return float(str(v).replace(',', '').strip())
    except Exception:
        return 0.0


def _norm_name_master(v):
    """
    Normalise a full name for cross-source matching in master-payroll generation.

    Steps:
      1. Lowercase + collapse whitespace
      2. Deduplicate tokens (removes repeated surname in VF format
         "Achbani Zakrya Achbani" → ["achbani", "zakrya"])
      3. Sort tokens alphabetically so "Abbas Anam" and "Anam Abbas"
         both produce the same canonical key "anam abbas"
    """
    import re
    parts = re.sub(r'\s+', ' ', str(v or '').strip().lower()).split()
    seen = set()
    unique = [p for p in parts if not (p in seen or seen.add(p))]
    return ' '.join(sorted(unique)) if unique else ''


def _norm_code(v):
    """Normalise an employee code for matching: lowercase, stripped."""
    return str(v or '').strip().lower()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_master_payroll(request):
    """
    POST /api/v1/payroll/generate-master-payroll/

    Accepts optional Sympa and ValueFrame file uploads.
    Merges with RADAI attendance (EmployeeLeaveMonthly + DailyWorkLog) for
    the requested period and RADAI salary structures.

    Returns JSON list of master payroll rows or, when ?format=xlsx, an
    Excel binary response.

    Request multipart fields:
      sympa_file       — Sympa HR export  (XLSX / XLS / CSV)  — optional
      valueframe_file  — ValueFrame hours (XLSX / XLS / CSV)  — optional
      other_file       — Supplementary data: bonuses, gratuity, insurance,
                         special deductions, adjustments (XLSX / XLS / CSV) — optional
      year             — int  (defaults to current year)
      month            — int  (defaults to current month)
    """
    import pandas as pd

    if not _is_hr_manager(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('HR Manager role required.')

    now   = timezone.now()
    year  = int(request.data.get('year',  now.year))
    month = int(request.data.get('month', now.month))
    fmt   = request.query_params.get('format', 'json').lower()

    master  = {}   # employee_code → master row dict
    stats   = {'sympa_rows': 0, 'vf_employees': 0, 'radai_rows': 0, 'matched': 0}
    warnings_list = []

    # Bridge lookups built during VF / other-file steps so SYMPA name-keys can
    # be resolved to real numeric employee codes at the end.
    # norm_name(full_name) → employee_code
    _vf_name_to_code    = {}
    _other_name_to_code = {}
    # Track which master keys were created with a temporary 'name:…' prefix
    # (i.e. when SYMPA has no employee-code column).
    _sympa_name_keys    = {}   # norm_name → name:… key in master

    def _make_skeleton(code, name=''):
        return {
            'employee_code':       code,
            'employee_name':       name or code,
            'department': '', 'job_title': '', 'joining_date': '',
            'basic_salary': '0', 'housing_allowance': '0',
            'transport_allowance': '0', 'other_allowances': '0',
            'other_pay': '0', 'total_allowances': '0',
            'total_deductions': '0', 'deduction_details': '',
            'details': '', 'leave_balance': '0',
            'total_hours': '0', 'overtime_hours': '0',
            'project_breakdown': [], 'days_present': None,
            'days_absent': None, 'sources': [], 'warnings': [],
        }

    # ── 1. Parse Sympa file ───────────────────────────────────────────────────
    sympa_file = request.FILES.get('sympa_file')
    if sympa_file:
        try:
            df = _parse_file(sympa_file)
            col_map    = _detect_columns(df.columns, _SYMPA_ALIASES)
            has_code   = 'employee_code' in col_map
            has_name   = 'employee_name' in col_map
            surname_col = col_map.get('_surname')   # internal alias; not a real field

            if not has_code and not has_name:
                warnings_list.append(
                    'Sympa: no employee-code or name column detected. '
                    'Rows cannot be keyed — check the file format.'
                )
            else:
                for _, row in df.iterrows():
                    # ── Determine the master key ──────────────────────────────
                    if has_code:
                        code = _norm_code(row.get(col_map['employee_code'], ''))
                        if not code:
                            continue
                    else:
                        # No employee-code column (standard Sympa export).
                        # Use "Preferred given name" as the display name; combine
                        # with Surname to build a complete name for bridge lookups.
                        preferred = str(row.get(col_map['employee_name'], '')).strip()
                        surname   = str(row.get(surname_col, '')).strip() if surname_col else ''
                        # If surname is not already contained in preferred name, append it
                        if surname and surname.lower() not in preferred.lower():
                            full_name = f'{preferred} {surname}'.strip()
                        else:
                            full_name = preferred
                        if not full_name:
                            continue
                        norm = _norm_name_master(full_name)
                        code = f'name:{norm}'
                        _sympa_name_keys[norm] = code

                    # ── Parse salary / HR fields ──────────────────────────────
                    basic     = _safe_dec(row.get(col_map.get('basic_salary', ''), 0))
                    housing   = _safe_dec(row.get(col_map.get('housing_allowance', ''), 0))
                    transport = _safe_dec(row.get(col_map.get('transport_allowance', ''), 0))
                    other     = _safe_dec(row.get(col_map.get('other_allowances', ''), 0))
                    other_pay = _safe_dec(row.get(col_map.get('other_pay', ''), 0))
                    deduct    = _safe_dec(row.get(col_map.get('deductions', ''), 0))
                    total_allow = housing + transport + other

                    # Resolve display name
                    if has_name:
                        disp_name = str(row.get(col_map['employee_name'], '')).strip()
                        if surname_col:
                            sn = str(row.get(surname_col, '')).strip()
                            if sn and sn.lower() not in disp_name.lower():
                                disp_name = f'{disp_name} {sn}'.strip()
                    else:
                        disp_name = code

                    master[code] = {
                        'employee_code':       code,
                        'employee_name':       disp_name or code,
                        'department':          str(row.get(col_map.get('department', ''), '')).strip(),
                        'job_title':           str(row.get(col_map.get('job_title', ''), '')).strip(),
                        'joining_date':        str(row.get(col_map.get('joining_date', ''), '')).strip(),
                        'basic_salary':        str(basic),
                        'housing_allowance':   str(housing),
                        'transport_allowance': str(transport),
                        'other_allowances':    str(other),
                        'other_pay':           str(other_pay),
                        'total_allowances':    str(total_allow),
                        'total_deductions':    str(deduct),
                        'deduction_details':   str(row.get(col_map.get('deduction_details', ''), '')).strip(),
                        'details':             str(row.get(col_map.get('details', ''), '')).strip(),
                        'leave_balance':       str(_safe_dec(row.get(col_map.get('leave_balance', ''), 0))),
                        'total_hours':         '0',
                        'overtime_hours':      '0',
                        'project_breakdown':   [],
                        'days_present':        None,
                        'days_absent':         None,
                        'sources':             ['sympa'],
                        'warnings':            [],
                    }

                stats['sympa_rows'] = sum(1 for k in master if not str(k).startswith('name:')) + \
                                      len(_sympa_name_keys)
        except Exception as e:
            logger.warning(f'generate_master_payroll: Sympa parse error: {e}')
            warnings_list.append(f'Sympa file error: {e}')

    # ── 2. Parse ValueFrame file ──────────────────────────────────────────────
    vf_file = request.FILES.get('valueframe_file')
    if vf_file:
        try:
            df = _parse_file(vf_file)
            col_map = _detect_columns(df.columns, _VF_ALIASES)
            if 'employee_code' not in col_map:
                warnings_list.append('ValueFrame: could not detect employee code column.')
            else:
                vf_emp_hours = {}
                vf_emp_ot    = {}
                vf_emp_proj  = {}
                vf_emp_names = {}
                for _, row in df.iterrows():
                    raw_code = row.get(col_map['employee_code'], '')
                    # Skip non-numeric summary rows (e.g. a "Total" footer row)
                    if not str(raw_code).strip().replace('.', '').isdigit():
                        continue
                    code = _norm_code(raw_code)
                    if not code:
                        continue
                    # Month/year filter when those columns are present
                    if 'month' in col_map and 'year' in col_map:
                        row_month = _safe_float(row.get(col_map['month'], 0))
                        row_year  = _safe_float(row.get(col_map['year'], 0))
                        if row_month and row_year:
                            if int(row_month) != month or int(row_year) != year:
                                continue
                    hours = _safe_float(row.get(col_map.get('total_hours', ''), 0))
                    ot    = _safe_float(row.get(col_map.get('overtime_hours', ''), 0))
                    proj_code = str(row.get(col_map.get('project_code', ''), '')).strip()
                    proj_name = str(row.get(col_map.get('project_name', ''), '')).strip()
                    vf_emp_hours[code] = vf_emp_hours.get(code, 0) + hours
                    vf_emp_ot[code]    = vf_emp_ot.get(code, 0) + ot
                    if proj_code:
                        vf_emp_proj.setdefault(code, []).append({
                            'project_code': proj_code,
                            'project_name': proj_name,
                            'hours': round(hours, 2),
                        })
                    if 'employee_name' in col_map:
                        raw_name = str(row.get(col_map['employee_name'], '')).strip()
                        vf_emp_names[code] = raw_name
                        # Build name→code bridge for SYMPA resolution
                        if raw_name:
                            _vf_name_to_code[_norm_name_master(raw_name)] = code

                # Merge VF hours into master
                for code in vf_emp_hours:
                    if code not in master:
                        master[code] = _make_skeleton(code, vf_emp_names.get(code, code))
                        master[code]['warnings'].append('No Sympa record found for this employee.')
                    master[code]['total_hours']       = str(round(vf_emp_hours[code], 2))
                    master[code]['overtime_hours']    = str(round(vf_emp_ot.get(code, 0), 2))
                    master[code]['project_breakdown'] = vf_emp_proj.get(code, [])
                    if 'valueframe' not in master[code]['sources']:
                        master[code]['sources'].append('valueframe')
                stats['vf_employees'] = len(vf_emp_hours)
        except Exception as e:
            logger.warning(f'generate_master_payroll: ValueFrame parse error: {e}')
            warnings_list.append(f'ValueFrame file error: {e}')

    # ── 2.5. Parse supplementary / other file ────────────────────────────────
    # When the file is a Sympa annual-leave export it also serves as a
    # name → employee-code bridge so SYMPA HR rows (which have no code) can
    # be matched to their VF counterparts.
    other_file = request.FILES.get('other_file')
    if other_file:
        try:
            df = _parse_file(other_file)
            col_map = _detect_columns(df.columns, _OTHER_ALIASES)

            has_other_code = 'employee_code' in col_map
            has_other_name = 'employee_name' in col_map

            # ── Detect annual-leave export ────────────────────────────────────
            is_leave_export = 'leave_days_used' in col_map
            # Per-employee aggregated leave days (approved requests only)
            leave_agg = {}   # employee_code → total leave days

            if not has_other_code and not has_other_name:
                warnings_list.append(
                    'Other file: could not detect employee code or name column. '
                    'Ensure the file has a column like "Employee number" or "Preferred given name".'
                )
            else:
                for _, row in df.iterrows():
                    # Determine employee code
                    if has_other_code:
                        raw_code = row.get(col_map['employee_code'], '')
                        code = _norm_code(raw_code)
                    else:
                        code = ''

                    # Determine display name & build name→code bridge
                    if has_other_name:
                        raw_name = str(row.get(col_map['employee_name'], '')).strip()
                    else:
                        raw_name = ''

                    # If we have both code and name, register bridge
                    if code and raw_name:
                        _other_name_to_code[_norm_name_master(raw_name)] = code

                    # ── Annual-leave export handling ──────────────────────────
                    if is_leave_export:
                        # Only count approved leave
                        status_val = str(row.get(col_map.get('leave_status', ''), '')).strip().lower()
                        if status_val and status_val not in ('approved', 'new', ''):
                            continue  # skip rejected / cancelled entries
                        days = _safe_float(row.get(col_map.get('leave_days_used', ''), 0))
                        if days and code:
                            leave_agg[code] = leave_agg.get(code, 0) + days
                        continue   # leave export rows carry no financial payload

                    if not code:
                        continue

                    # ── Generic supplementary financial data ──────────────────
                    if code not in master:
                        master[code] = _make_skeleton(code, raw_name or code)
                        master[code]['warnings'].append(
                            'No Sympa/ValueFrame record — sourced from Other file.'
                        )

                    mr = master[code]
                    extra_pay = (
                        _safe_dec(row.get(col_map.get('bonus', ''), 0)) +
                        _safe_dec(row.get(col_map.get('commission', ''), 0)) +
                        _safe_dec(row.get(col_map.get('incentive', ''), 0)) +
                        _safe_dec(row.get(col_map.get('adjustment', ''), 0))
                    )
                    if extra_pay:
                        mr['other_pay'] = str(_safe_dec(mr.get('other_pay', 0)) + extra_pay)
                    extra_deduct = (
                        _safe_dec(row.get(col_map.get('insurance', ''), 0)) +
                        _safe_dec(row.get(col_map.get('special_deduction', ''), 0))
                    )
                    if extra_deduct:
                        mr['total_deductions'] = str(
                            _safe_dec(mr.get('total_deductions', 0)) + extra_deduct
                        )
                    gratuity = _safe_dec(row.get(col_map.get('gratuity', ''), 0))
                    if gratuity:
                        mr.setdefault('raw_data', {})['gratuity'] = str(gratuity)
                    notes = str(row.get(col_map.get('notes', ''), '')).strip()
                    if notes:
                        existing = mr.get('details', '')
                        mr['details'] = f'{existing}; {notes}'.lstrip('; ') if existing else notes
                    if 'other' not in mr['sources']:
                        mr['sources'].append('other')

                # Apply aggregated leave days to master rows
                for code, days in leave_agg.items():
                    if code in master:
                        master[code].setdefault('raw_data', {})['leave_days_taken'] = round(days, 1)
                        if 'other' not in master[code]['sources']:
                            master[code]['sources'].append('other')

                stats['other_rows'] = sum(1 for r in master.values() if 'other' in r.get('sources', []))
        except Exception as e:
            logger.warning(f'generate_master_payroll: Other file parse error: {e}')
            warnings_list.append(f'Other file error: {e}')

    # ── 2.6. Bridge resolution: map SYMPA name-keys → real employee codes ─────
    # When SYMPA has no employee-code column, rows were keyed as 'name:<norm>'.
    # We now resolve those keys using the name→code maps built from:
    #   1. VF             (employee_code + Employee Name)
    #   2. BiometricUserMaster (office access-control, name normalised by RADAI)
    #   3. annual-leave / other file (Preferred given name + Employee number)
    if _sympa_name_keys:
        # ── Third bridge: Biometric User Master ──────────────────────────────
        _biometric_name_to_code = {}
        try:
            from apps.timesheet.models import BiometricUserMaster as _BUM
            for _bum in _BUM.objects.values('employee_code', 'full_name').iterator():
                _bcode = _norm_code(_bum['employee_code'])
                _bname = (_bum['full_name'] or '').strip()
                if _bcode and _bname:
                    _biometric_name_to_code[_norm_name_master(_bname)] = _bcode
        except Exception as _e:
            logger.info(
                'generate_master_payroll: BiometricUserMaster bridge skipped (not configured): %s', _e
            )
        # LEAVES/other overrides VF overrides biometric (most-specific wins)
        combined_bridge = {
            **_biometric_name_to_code,
            **_vf_name_to_code,
            **_other_name_to_code,
        }
        resolved = 0
        for norm_name, name_key in list(_sympa_name_keys.items()):
            if name_key not in master:
                continue
            real_code = combined_bridge.get(norm_name)
            if not real_code:
                continue  # leave unresolved — will appear in the output with name key
            sympa_row = master.pop(name_key)
            sympa_row['employee_code'] = real_code
            if real_code in master:
                # Employee already exists from VF — merge Sympa fields in
                existing = master[real_code]
                for field in ('basic_salary', 'housing_allowance', 'transport_allowance',
                              'other_allowances', 'other_pay', 'total_allowances',
                              'total_deductions', 'department', 'job_title',
                              'employee_name', 'joining_date', 'leave_balance',
                              'deduction_details', 'details'):
                    val = sympa_row.get(field, '')
                    if (not existing.get(field) or existing[field] in ('', '0')) and val:
                        existing[field] = val
                if 'sympa' not in existing['sources']:
                    existing['sources'].insert(0, 'sympa')
            else:
                master[real_code] = sympa_row
            resolved += 1

        if resolved:
            logger.info(
                'generate_master_payroll: resolved %d/%d SYMPA name-keys to employee codes '
                '(VF bridge: %d, biometric bridge: %d, other bridge: %d)',
                resolved, len(_sympa_name_keys),
                len(_vf_name_to_code), len(_biometric_name_to_code), len(_other_name_to_code),
            )
        unresolved = len(_sympa_name_keys) - resolved
        if unresolved:
            warnings_list.append(
                f'{unresolved} Sympa employee(s) could not be matched to an employee code. '
                f'They will appear in the output with a name-based key. '
                f'Upload the annual-leave file as "Supplementary Data" to enable automatic matching.'
            )

    # ── 3. Merge RADAI attendance (EmployeeLeaveMonthly + DailyWorkLog) ───────
    try:
        # DailyWorkLog: aggregate approved days/hours per employee for the period
        from django.db.models import Count as DjCount
        log_agg = (
            DailyWorkLog.objects
            .filter(log_date__year=year, log_date__month=month, status='approved')
            .values('user__rbac_profile__employee_id')
            .annotate(days=DjCount('id'), hours=Sum('hours'))
        )
        for row in log_agg:
            raw_code = row.get('user__rbac_profile__employee_id') or ''
            code = _norm_code(raw_code)
            if not code:
                continue
            days  = row.get('days', 0) or 0
            if code in master:
                master[code]['days_present'] = days
                master[code]['days_absent']  = max(0, 22 - days)   # soft-coded working days default
                if 'radai' not in master[code]['sources']:
                    master[code]['sources'].append('radai')
            stats['radai_rows'] = stats.get('radai_rows', 0) + 1
    except Exception as e:
        logger.warning(f'generate_master_payroll: RADAI attendance query error: {e}')
        warnings_list.append(f'RADAI attendance partial: {e}')

    # ── 3b. Biometric attendance cross-verification (DailyAttendanceSummary) ──
    # Query the materialised biometric attendance rows for the target month and
    # enrich each master employee with real access-control presence data.
    # Also cross-verifies VF hours vs. biometric hours and warns on large gaps.
    try:
        from apps.timesheet.models import DailyAttendanceSummary as _DAS
        from django.db.models import Sum as _DjSum, Q as _DjQ, Count as _DjCount
        _bio_qs = (
            _DAS.objects
            .filter(date__year=year, date__month=month)
            .values('employee_code')
            .annotate(
                bio_days=_DjCount('id'),
                bio_hours=_DjSum('effective_hours'),
                bio_days_late=_DjCount('id', filter=_DjQ(is_late=True)),
                bio_days_full=_DjCount('id', filter=_DjQ(is_full_day=True)),
            )
        )
        _biometric_count = 0
        for _bio_row in _bio_qs:
            _code = _norm_code(_bio_row['employee_code'])
            if not _code or _code not in master:
                continue
            _mr = master[_code]
            _bio_days  = int(_bio_row['bio_days'] or 0)
            _bio_hours = round(float(_bio_row['bio_hours'] or 0), 2)
            _bio_late  = int(_bio_row['bio_days_late'] or 0)
            _bio_full  = int(_bio_row['bio_days_full'] or 0)

            # Biometric attendance is the authoritative presence source — always
            # overrides the DailyWorkLog approximation set in step 3.
            _mr['days_present'] = _bio_days
            _mr['days_absent']  = max(0, _PAYROLL_WORKING_DAYS - _bio_days)

            # Cross-verify VF hours vs. biometric effective hours
            _vf_h = _safe_float(_mr.get('total_hours', 0))
            if _vf_h and _bio_hours:
                _diff_pct = abs(_vf_h - _bio_hours) / max(_vf_h, _bio_hours) * 100
                if _diff_pct > _BIOMETRIC_HOURS_DISCREPANCY_PCT:
                    _mr.setdefault('warnings', []).append(
                        f'Hours discrepancy: VF={_vf_h:.1f}h vs biometric={_bio_hours:.1f}h '
                        f'({_diff_pct:.0f}% gap)'
                    )

            # Persist biometric breakdown for downstream use (detailed row view)
            _mr.setdefault('raw_data', {}).update({
                'bio_days_present': _bio_days,
                'bio_hours':        _bio_hours,
                'bio_days_late':    _bio_late,
                'bio_days_full':    _bio_full,
            })
            if 'biometric' not in _mr.get('sources', []):
                _mr.setdefault('sources', []).append('biometric')
            _biometric_count += 1

        stats['biometric_rows'] = _biometric_count
        logger.info(
            'generate_master_payroll: biometric attendance enriched %d employees for %04d-%02d',
            _biometric_count, year, month,
        )
    except Exception as _e:
        logger.warning('generate_master_payroll: DailyAttendanceSummary query error: %s', _e)
        warnings_list.append(f'Biometric attendance partial: {_e}')

    # ── 4. Overlay with existing RADAI salary structures ─────────────────────
    try:
        from .models import EmployeeSalaryStructure, SalaryStructureStatus
        active_structs = EmployeeSalaryStructure.objects.filter(
            is_active=True, status=SalaryStructureStatus.APPROVED,
        ).values('employee_code', 'employee_name', 'department', 'basic_salary',
                 'net_salary', 'total_gross', 'total_deductions')
        for s in active_structs:
            code = _norm_code(s['employee_code'])
            if code in master:
                # Prefer Sympa values if already set; use RADAI as fallback
                if master[code]['basic_salary'] == '0':
                    master[code]['basic_salary']   = str(s['basic_salary'] or 0)
                    master[code]['total_deductions']= str(s['total_deductions'] or 0)
                if not master[code]['department'] and s['department']:
                    master[code]['department'] = s['department']
                if not master[code]['employee_name'] or master[code]['employee_name'] == code:
                    master[code]['employee_name'] = s['employee_name'] or code
                if 'radai' not in master[code]['sources']:
                    master[code]['sources'].append('radai')
    except Exception as e:
        logger.warning(f'generate_master_payroll: salary structure overlay error: {e}')
    
    # ── 4b. Overlay with EmployeeSalaryInfo (join_date, designation) ─────────
    try:
        from .models import EmployeeSalaryInfo
        # Fetch active employees with their join_date and other missing fields
        salary_infos = EmployeeSalaryInfo.objects.filter(
            is_active=True
        ).values('employee_id', 'user__first_name', 'user__last_name', 
                 'join_date', 'designation', 'department')
        
        for info in salary_infos:
            code = _norm_code(info['employee_id'])
            if not code:
                continue
                
            # Create entry if doesn't exist (employee in RADAI but not in uploaded files)
            if code not in master:
                full_name = f"{info.get('user__first_name', '')} {info.get('user__last_name', '')}".strip()
                master[code] = _make_skeleton(code, full_name or code)
                master[code]['sources'] = ['radai']
            
            # Overlay joining_date if not already set from Sympa
            if not master[code].get('joining_date') and info.get('join_date'):
                master[code]['joining_date'] = str(info['join_date'])
            
            # Overlay department if missing
            if not master[code].get('department') and info.get('department'):
                master[code]['department'] = info['department']
            
            # Overlay designation/job_title if missing
            if not master[code].get('job_title') and info.get('designation'):
                master[code]['job_title'] = info['designation']
            
            # Ensure employee name is set
            if not master[code].get('employee_name') or master[code]['employee_name'] == code:
                full_name = f"{info.get('user__first_name', '')} {info.get('user__last_name', '')}".strip()
                if full_name:
                    master[code]['employee_name'] = full_name
            
            if 'radai' not in master[code]['sources']:
                master[code]['sources'].append('radai')
                
        logger.info('generate_master_payroll: enriched %d employees from EmployeeSalaryInfo', len(salary_infos))
    except Exception as e:
        logger.warning(f'generate_master_payroll: EmployeeSalaryInfo overlay error: {e}')
        warnings_list.append(f'RADAI employee info partial: {e}')

    # ── 5. Build final rows ───────────────────────────────────────────────────
    rows = []
    for code, r in master.items():
        # Always recompute total_allowances from the individual breakdown fields
        # (transport + housing + other).  This guarantees the cascade is
        # correct even when a source file had only a combined allowance column.
        transport_dec = _safe_dec(r.get('transport_allowance', 0))
        housing_dec   = _safe_dec(r.get('housing_allowance',   0))
        other_alw_dec = _safe_dec(r.get('other_allowances',    0))
        total_alw     = transport_dec + housing_dec + other_alw_dec

        # Fallback: source file provided a combined total but no breakdown
        # columns.  Move the total into other_allowances so it appears in the
        # UI and is correctly reflected in employee_salary / final_salary.
        if total_alw == Decimal('0'):
            direct_total = _safe_dec(r.get('total_allowances', 0))
            if direct_total:
                other_alw_dec = direct_total
                total_alw     = direct_total
                r['other_allowances'] = str(other_alw_dec)

        r['total_allowances'] = str(total_alw)

        basic      = _safe_dec(r.get('basic_salary', 0))
        other_pay  = _safe_dec(r.get('other_pay', 0))
        deduct     = _safe_dec(r.get('total_deductions', 0))
        gross      = basic + total_alw + other_pay      # employee_salary
        final_sal  = max(Decimal('0'), gross - deduct)
        r['employee_salary'] = str(gross)
        r['final_salary']    = str(final_sal)
        # Legacy aliases kept for backward-compat JSON consumers
        r['gross_salary']    = str(gross)
        r['net_salary_est']  = str(final_sal)
        rows.append(r)
        if r['sources']:
            stats['matched'] += 1

    rows.sort(key=lambda x: (x.get('department') or '', x.get('employee_name') or ''))

    # ── 6. Persist to DB + trigger async S3 upload ────────────────────────────
    import_session = None
    try:
        from .models import MasterPayrollImport, MasterPayrollRow, MasterPayrollImportStatus
        from .tasks import upload_master_payroll_to_s3

        sympa_fn = request.FILES['sympa_file'].name      if 'sympa_file'      in request.FILES else ''
        vf_fn    = request.FILES['valueframe_file'].name if 'valueframe_file' in request.FILES else ''
        other_fn = request.FILES['other_file'].name      if 'other_file'      in request.FILES else ''

        import_session = MasterPayrollImport.objects.create(
            year=year,
            month=month,
            generated_by=request.user if request.user.is_authenticated else None,
            sympa_filename=sympa_fn,
            valueframe_filename=vf_fn,
            other_filename=other_fn,
            stats=stats,
            warnings=warnings_list,
            total_rows=len(rows),
            status=MasterPayrollImportStatus.PROCESSING,
        )

        # Build encashment lookup: employee_code → (days, pay) for this period
        # Populated only if HR has run the monthly encashment before generating payroll
        from .models import EmployeeLeaveMonthly
        _enc_qs = (EmployeeLeaveMonthly.objects
                   .filter(record__year=year, month=month)
                   .select_related('record')
                   .values('record__employee_code', 'encashed', 'encashment_pay'))
        _enc_map = {
            row['record__employee_code']: (row['encashed'], row['encashment_pay'])
            for row in _enc_qs
        }

        # Bulk-create rows (ignore conflicts — idempotent on re-generation)
        row_objs = [
            MasterPayrollRow(
                import_session   = import_session,
                employee_code    = r.get('employee_code', ''),
                employee_name    = r.get('employee_name', ''),
                joining_date     = r.get('joining_date', '') or '',
                total_hours      = Decimal(str(r.get('total_hours', 0) or 0)),
                employee_salary  = Decimal(str(r.get('employee_salary', 0) or 0)),
                basic_salary     = Decimal(str(r.get('basic_salary', 0) or 0)),
                total_allowances     = Decimal(str(r.get('total_allowances', 0) or 0)),
                transport_allowance  = Decimal(str(r.get('transport_allowance', 0) or 0)),
                housing_allowance    = Decimal(str(r.get('housing_allowance', 0) or 0)),
                other_allowances     = Decimal(str(r.get('other_allowances', 0) or 0)),
                other_pay            = Decimal(str(r.get('other_pay', 0) or 0)),
                details              = r.get('details', '') or '',
                total_deductions     = Decimal(str(r.get('total_deductions', 0) or 0)),
                deduction_details    = r.get('deduction_details', '') or '',
                final_salary         = Decimal(str(r.get('final_salary', 0) or 0)),
                leave_encashment_days = Decimal(str(_enc_map.get(r.get('employee_code', ''), (0, 0))[0] or 0)),
                leave_encashment_pay  = Decimal(str(_enc_map.get(r.get('employee_code', ''), (0, 0))[1] or 0)),
                sources              = r.get('sources', []),
                row_warnings         = r.get('warnings', []),
                raw_data             = {k: v for k, v in r.items()
                                        if k not in ('sources', 'warnings', 'project_breakdown')},
            )
            for r in rows
        ]
        MasterPayrollRow.objects.bulk_create(row_objs, ignore_conflicts=True)

        # Mark ready now; Celery will flip to 'uploaded' once S3 upload completes
        import_session.status = MasterPayrollImportStatus.READY
        import_session.save(update_fields=['status'])

        # Fire async S3 upload — non-blocking
        upload_master_payroll_to_s3.delay(str(import_session.id))

        logger.info(
            'generate_master_payroll: saved import %s (%d rows), S3 upload queued',
            import_session.id, len(row_objs),
        )
    except Exception as persist_err:
        logger.warning('generate_master_payroll: DB/S3 persist failed: %s', persist_err)
        # Non-fatal — still return the data to the user even if persistence fails

    # ── 7. Return response ────────────────────────────────────────────────────
    if fmt == 'xlsx':
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f'Payroll Master {year}-{month:02d}'
            hdr_font = Font(bold=True, color='FFFFFF')
            hdr_fill = PatternFill('solid', fgColor='2563EB')
            thin = Side(style='thin', color='CCCCCC')
            border = Border(left=thin, right=thin, top=thin, bottom=thin)
            headers = [
                'Employee Code',        # 1
                'Employee Name',        # 2
                'Joining Date',         # 3
                'No. of Working Hours', # 4
                'Employee Salary',      # 5  (gross = basic + allow + other pay)
                'Basic',                # 6
                'Allowance',            # 7  (total allow)
                'Transportation',       # 8
                'Home Allowance',       # 9
                'Other Allowance',      # 10
                'Other Pay',            # 11
                'Details',              # 12
                'Salary Deduction',     # 13
                'Deduction Details',    # 14
                'Final Salary',         # 15  (gross – deductions)
            ]
            for col_idx, hdr in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=hdr)
                cell.font = hdr_font
                cell.fill = hdr_fill
                cell.alignment = Alignment(horizontal='center')
                cell.border = border
                ws.column_dimensions[cell.column_letter].width = max(14, len(hdr) + 4)
            for r_idx, r in enumerate(rows, 2):
                vals = [
                    r.get('employee_code', ''),                           # 1
                    r.get('employee_name', ''),                           # 2
                    r.get('joining_date', ''),                            # 3
                    float(r.get('total_hours') or 0),                    # 4
                    float(r.get('employee_salary') or 0),                # 5
                    float(r.get('basic_salary') or 0),                   # 6
                    float(r.get('total_allowances') or 0),               # 7
                    float(r.get('transport_allowance') or 0),            # 8
                    float(r.get('housing_allowance') or 0),              # 9
                    float(r.get('other_allowances') or 0),               # 10
                    float(r.get('other_pay') or 0),                      # 11
                    r.get('details') or r.get('job_title', ''),          # 12
                    float(r.get('total_deductions') or 0),               # 13
                    r.get('deduction_details', ''),                       # 14
                    float(r.get('final_salary') or 0),                   # 15
                ]
                for col_idx, val in enumerate(vals, 1):
                    cell = ws.cell(row=r_idx, column=col_idx, value=val)
                    cell.border = border
                    if isinstance(val, float):
                        cell.number_format = '#,##0.00'
            # Freeze header row
            ws.freeze_panes = 'A2'
            import io
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            filename = f'master_payroll_{year}_{month:02d}.xlsx'
            response = HttpResponse(
                buf.read(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            logger.error(f'generate_master_payroll: Excel generation error: {e}')
            return Response({'error': f'Excel generation failed: {e}'}, status=500)

    return Response({
        'year':         year,
        'month':        month,
        'generated_at': timezone.now().isoformat(),
        'import_id':    str(import_session.id) if import_session else None,
        'rows':         rows,
        'stats':        stats,
        'warnings':     warnings_list,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Export Edited Rows to Excel
# POST /api/v1/payroll/export-rows-to-excel/
# Body: { year, month, rows: [...] }
# Returns Excel binary — used when the user edits the master payroll preview
# and wants to download the modified data without re-uploading source files.
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def export_rows_to_excel(request):
    """
    Accepts a JSON body with 'rows', 'year', 'month'.
    Generates and returns an Excel binary in the same format as
    generate_master_payroll, but using the caller-supplied rows directly.
    Any authenticated user may export data they are already viewing.
    """
    try:
        year  = int(request.data.get('year',  timezone.now().year))
        month = int(request.data.get('month', timezone.now().month))
    except (TypeError, ValueError):
        return Response({'error': 'year and month must be integers.'}, status=400)

    rows = request.data.get('rows', [])
    if not isinstance(rows, list):
        return Response({'error': 'rows must be a list.'}, status=400)

    def _flt(v):
        try:
            return float(str(v).replace(',', '').strip())
        except Exception:
            return 0.0

    try:
        import io
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb       = openpyxl.Workbook()
        ws       = wb.active
        ws.title = f'Payroll Master {year}-{month:02d}'

        hdr_font = Font(bold=True, color='FFFFFF')
        hdr_fill = PatternFill('solid', fgColor='2563EB')
        thin     = Side(style='thin', color='CCCCCC')
        border   = Border(left=thin, right=thin, top=thin, bottom=thin)

        headers = [
            'Employee Code',        # 1
            'Employee Name',        # 2
            'Joining Date',         # 3
            'No. of Working Hours', # 4
            'Employee Salary',      # 5
            'Basic',                # 6
            'Allowance',            # 7
            'Transportation',       # 8
            'Home Allowance',       # 9
            'Other Allowance',      # 10
            'Other Pay',            # 11
            'Details',              # 12
            'Salary Deduction',     # 13
            'Deduction Details',    # 14
            'Final Salary',         # 15
        ]
        for col_idx, hdr in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=hdr)
            cell.font      = hdr_font
            cell.fill      = hdr_fill
            cell.alignment = Alignment(horizontal='center')
            cell.border    = border
            ws.column_dimensions[cell.column_letter].width = max(14, len(hdr) + 4)

        for r_idx, r in enumerate(rows, 2):
            vals = [
                r.get('employee_code', ''),
                r.get('employee_name', ''),
                r.get('joining_date', ''),
                _flt(r.get('total_hours')),
                _flt(r.get('employee_salary')),
                _flt(r.get('basic_salary')),
                _flt(r.get('total_allowances')),
                _flt(r.get('transport_allowance')),
                _flt(r.get('housing_allowance')),
                _flt(r.get('other_allowances')),
                _flt(r.get('other_pay')),
                r.get('details') or r.get('job_title', ''),
                _flt(r.get('total_deductions')),
                r.get('deduction_details', ''),
                _flt(r.get('final_salary')),
            ]
            for col_idx, val in enumerate(vals, 1):
                cell = ws.cell(row=r_idx, column=col_idx, value=val)
                cell.border = border
                if isinstance(val, float):
                    cell.number_format = '#,##0.00'

        ws.freeze_panes = 'A2'

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        filename = f'master_payroll_{year}_{month:02d}.xlsx'
        response = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error('export_rows_to_excel: %s', e)
        return Response({'error': f'Excel generation failed: {e}'}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Master Payroll History — list past import sessions
# GET /api/v1/payroll/master-payroll-history/
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def master_payroll_history(request):
    """
    Returns paginated list of past MasterPayrollImport sessions.
    HR managers see all; regular users only see their own.

    Query params:
      year, month  — filter by period
      page         — 1-based page number (default 1)
      page_size    — results per page (default 20, max 100)
    """
    from .models import MasterPayrollImport

    PAGE_SIZE_DEFAULT = 20
    PAGE_SIZE_MAX     = 100

    qs = MasterPayrollImport.objects.select_related('generated_by').order_by('-year', '-month', '-generated_at')

    if not _is_hr_manager(request.user):
        qs = qs.filter(generated_by=request.user)

    year  = request.query_params.get('year')
    month = request.query_params.get('month')
    if year:
        qs = qs.filter(year=int(year))
    if month:
        qs = qs.filter(month=int(month))

    try:
        page_size = min(int(request.query_params.get('page_size', PAGE_SIZE_DEFAULT)), PAGE_SIZE_MAX)
        page      = max(int(request.query_params.get('page', 1)), 1)
    except (ValueError, TypeError):
        page_size, page = PAGE_SIZE_DEFAULT, 1

    total  = qs.count()
    offset = (page - 1) * page_size
    items  = qs[offset: offset + page_size]

    def _serialize(imp):
        return {
            'id':                   str(imp.id),
            'year':                 imp.year,
            'month':                imp.month,
            'generated_at':         imp.generated_at.isoformat(),
            'generated_by':         imp.generated_by.get_full_name() if imp.generated_by else None,
            'sympa_filename':       imp.sympa_filename,
            'valueframe_filename':  imp.valueframe_filename,
            'status':               imp.status,
            'total_rows':           imp.total_rows,
            'stats':                imp.stats,
            'has_s3':               bool(imp.s3_key),
            # Workflow fields
            'workflow_stage':       imp.workflow_stage,
            'is_editable_by_hr':    imp.workflow_stage == MasterPayrollWorkflowStage.DRAFT,
            'frozen_at':            imp.frozen_at.isoformat() if imp.frozen_at else None,
            'hr_approved_at':       imp.hr_approved_at.isoformat() if imp.hr_approved_at else None,
            'finance_approved_at':  imp.finance_approved_at.isoformat() if imp.finance_approved_at else None,
            'released_at':          imp.released_at.isoformat() if imp.released_at else None,
        }

    return Response({
        'count':     total,
        'page':      page,
        'page_size': page_size,
        'results':   [_serialize(i) for i in items],
    })


# ─────────────────────────────────────────────────────────────────────────────
# Master Payroll Rows — restore preview from DB without re-uploading files
# GET /api/v1/payroll/master-payroll-history/<import_id>/rows/
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def master_payroll_rows(request, import_id):
    """
    Returns all employee rows for a stored master payroll import session as JSON.
    Used by the frontend "Restore Preview" button so the user can reload the
    15-column table without re-uploading the original source files.
    HR managers only.
    """
    from .models import MasterPayrollImport

    if not _is_hr_manager(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('HR Manager role required.')

    try:
        session = MasterPayrollImport.objects.prefetch_related('rows').get(id=import_id)
    except MasterPayrollImport.DoesNotExist:
        return Response({'error': 'Import session not found.'}, status=404)

    rows = [
        {
            'id':                  str(row.id),     # expose UUID so frontend can PATCH individual rows
            'employee_code':       row.employee_code,
            'employee_name':       row.employee_name,
            'joining_date':        row.joining_date or '',
            'total_hours':         float(row.total_hours),
            'employee_salary':     float(row.employee_salary),
            'basic_salary':        float(row.basic_salary),
            'total_allowances':    float(row.total_allowances),
            'transport_allowance': float(row.transport_allowance),
            'housing_allowance':   float(row.housing_allowance),
            'other_allowances':    float(row.other_allowances),
            'other_pay':           float(row.other_pay),
            'details':             row.details or '',
            'total_deductions':    float(row.total_deductions),
            'deduction_details':   row.deduction_details or '',
            'final_salary':        float(row.final_salary),
            'sources':             row.sources or [],
        }
        for row in session.rows.order_by('employee_name')
    ]

    return Response({
        'id':         str(session.id),
        'year':       session.year,
        'month':      session.month,
        'stats':      session.stats    or {},
        'warnings':   session.warnings or [],
        'total_rows': session.total_rows,
        'rows':       rows,
        # Include workflow info so the frontend can lock/unlock the edit UI
        'workflow_stage':      session.workflow_stage,
        'is_editable_by_hr':   session.workflow_stage == MasterPayrollWorkflowStage.DRAFT,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Master Payroll Delete — remove an import session + all its rows
# DELETE /api/v1/payroll/master-payroll-history/<import_id>/delete/
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Master Payroll Row Update — edit a single employee row (draft only)
# PATCH /api/v1/payroll/master-payroll-history/<import_id>/rows/<row_id>/
# ─────────────────────────────────────────────────────────────────────────────

# Editable fields — computed fields (total_allowances, employee_salary,
# final_salary) are intentionally excluded; model.save() cascades them.
_ROW_EDITABLE_FIELDS = [
    'employee_name', 'joining_date', 'total_hours',
    'basic_salary', 'transport_allowance', 'housing_allowance',
    'other_allowances', 'other_pay', 'details',
    'total_deductions', 'deduction_details',
]
_ROW_NUMERIC_FIELDS = {
    'total_hours', 'basic_salary', 'transport_allowance',
    'housing_allowance', 'other_allowances', 'other_pay', 'total_deductions',
}

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def master_payroll_row_update(request, import_id, row_id):
    """
    PATCH one employee row in a draft master payroll import.
    Only HR managers may edit; the import must still be in draft stage.
    Computed fields (total_allowances, employee_salary, final_salary) are
    recalculated automatically by MasterPayrollRow.save().
    """
    from .models import MasterPayrollImport, MasterPayrollRow, MasterPayrollWorkflowStage
    from rest_framework.exceptions import PermissionDenied

    if not _is_hr_manager(request.user):
        raise PermissionDenied('HR Manager role required.')

    try:
        session = MasterPayrollImport.objects.get(id=import_id)
    except MasterPayrollImport.DoesNotExist:
        return Response({'error': 'Import session not found.'}, status=404)

    if session.workflow_stage != MasterPayrollWorkflowStage.DRAFT:
        return Response(
            {'error': 'Only payroll files in Draft stage can be edited.'},
            status=403,
        )

    try:
        row = MasterPayrollRow.objects.get(id=row_id, import_session=session)
    except MasterPayrollRow.DoesNotExist:
        return Response({'error': 'Employee row not found.'}, status=404)

    for field in _ROW_EDITABLE_FIELDS:
        if field not in request.data:
            continue
        val = request.data[field]
        if field in _ROW_NUMERIC_FIELDS:
            setattr(row, field, _safe_dec(val))
        else:
            setattr(row, field, str(val).strip())

    # model.save() cascades: total_allowances → employee_salary → final_salary
    row.save()

    return Response({
        'id':                  str(row.id),
        'employee_code':       row.employee_code,
        'employee_name':       row.employee_name,
        'joining_date':        row.joining_date or '',
        'total_hours':         float(row.total_hours),
        'employee_salary':     float(row.employee_salary),
        'basic_salary':        float(row.basic_salary),
        'total_allowances':    float(row.total_allowances),
        'transport_allowance': float(row.transport_allowance),
        'housing_allowance':   float(row.housing_allowance),
        'other_allowances':    float(row.other_allowances),
        'other_pay':           float(row.other_pay),
        'details':             row.details or '',
        'total_deductions':    float(row.total_deductions),
        'deduction_details':   row.deduction_details or '',
        'final_salary':        float(row.final_salary),
        'sources':             row.sources or [],
    })

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def master_payroll_delete(request, import_id):
    """
    Permanently deletes a master payroll import session and all its employee rows.
    Also attempts to remove the Excel file from S3 if one was uploaded.
    HR managers only.
    """
    from .models import MasterPayrollImport

    if not _is_hr_manager(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('HR Manager role required.')

    try:
        session = MasterPayrollImport.objects.get(id=import_id)
    except MasterPayrollImport.DoesNotExist:
        return Response({'error': 'Import session not found.'}, status=404)

    year  = session.year
    month = session.month

    # Attempt S3 cleanup — non-fatal if it fails
    if session.s3_key:
        try:
            from apps.payroll.storage import PayrollExportStorage, S3_AVAILABLE
            if S3_AVAILABLE:
                storage = PayrollExportStorage()
                relative = session.s3_key.split(f'{storage.location}/', 1)[-1]
                storage.delete(relative)
                logger.info('master_payroll_delete: S3 file removed: %s', session.s3_key)
        except Exception as e:
            logger.warning('master_payroll_delete: S3 delete failed (non-fatal): %s', e)

    session.delete()  # CASCADE removes all MasterPayrollRow records
    logger.info('master_payroll_delete: deleted import %s (%d-%02d) by %s', import_id, year, month, request.user)

    return Response({'detail': f'Master payroll for {year}/{month:02d} deleted successfully.'}, status=200)


# ─────────────────────────────────────────────────────────────────────────────
# Master Payroll Download — presigned S3 URL or on-the-fly Excel
# GET /api/v1/payroll/master-payroll-history/<import_id>/download/
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def master_payroll_download(request, import_id):
    """
    Returns a presigned S3 URL for the stored Excel, or regenerates the
    Excel on-the-fly from DB rows if the S3 upload is still pending.
    HR managers only.
    """
    from .models import MasterPayrollImport
    from apps.payroll.storage import PayrollExportStorage, S3_AVAILABLE

    if not _is_hr_manager(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('HR Manager role required.')

    try:
        session = MasterPayrollImport.objects.get(id=import_id)
    except MasterPayrollImport.DoesNotExist:
        return Response({'error': 'Import session not found.'}, status=404)

    # ── Case 1: S3 key exists → return presigned URL ──────────────────────────
    if session.s3_key and S3_AVAILABLE:
        try:
            storage = PayrollExportStorage()
            relative_key = session.s3_key.split(f'{storage.location}/', 1)[-1]
            url = storage.url(relative_key)
            return Response({
                'download_url': url,
                'source':       's3',
                'filename':     f'master_payroll_{session.year}_{session.month:02d}.xlsx',
            })
        except Exception as e:
            logger.warning('master_payroll_download: presigned URL failed: %s', e)
            # Fall through to on-the-fly generation

    # ── Case 2: Generate Excel on-the-fly from DB rows ─────────────────────────
    try:
        import io as _io
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f'Payroll Master {session.year}-{session.month:02d}'

        hdr_font = Font(bold=True, color='FFFFFF')
        hdr_fill = PatternFill('solid', fgColor='2563EB')
        thin     = Side(style='thin', color='CCCCCC')
        border   = Border(left=thin, right=thin, top=thin, bottom=thin)

        headers = [
            'Employee Code', 'Employee Name', 'Joining Date', 'No. of Working Hours',
            'Employee Salary', 'Basic', 'Allowance', 'Transportation', 'Home Allowance',
            'Other Allowance', 'Other Pay', 'Details', 'Salary Deduction',
            'Deduction Details', 'Final Salary',
        ]
        for col_idx, hdr in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=hdr)
            cell.font = hdr_font; cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal='center'); cell.border = border
            ws.column_dimensions[cell.column_letter].width = max(14, len(hdr) + 4)

        for r_idx, row in enumerate(session.rows.all().order_by('employee_name'), 2):
            vals = [
                row.employee_code,   row.employee_name,   row.joining_date or '',
                float(row.total_hours),       float(row.employee_salary),
                float(row.basic_salary),      float(row.total_allowances),
                float(row.transport_allowance), float(row.housing_allowance),
                float(row.other_allowances),  float(row.other_pay),
                row.details or '',
                float(row.total_deductions),  row.deduction_details or '',
                float(row.final_salary),
            ]
            for col_idx, val in enumerate(vals, 1):
                cell = ws.cell(row=r_idx, column=col_idx, value=val)
                cell.border = border
                if isinstance(val, float):
                    cell.number_format = '#,##0.00'

        ws.freeze_panes = 'A2'
        buf = _io.BytesIO()
        wb.save(buf); buf.seek(0)
        filename = f'master_payroll_{session.year}_{session.month:02d}.xlsx'
        response = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    except Exception as e:
        logger.error('master_payroll_download: on-the-fly generation failed: %s', e)
        return Response({'error': f'Download failed: {e}'}, status=500)


    # ── Approve ───────────────────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        log = self.get_object()
        if not self._can_approve(request.user, log):
            return Response(
                {'error': 'You do not have permission to approve this activity.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if log.approval_status == DailyWorkLogApprovalStatus.APPROVED:
            return Response(
                {'error': 'Activity is already approved.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        note = request.data.get('note', '')
        log.approval_status = DailyWorkLogApprovalStatus.APPROVED
        log.approved_by     = request.user
        log.approved_at     = timezone.now()
        log.approval_note   = note
        log.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note'])

        # ── Auto-create ProjectCostAllocation if a SalarySlip exists ───
        try:
            from apps.finance.salary_models import SalarySlip as _Slip  # noqa
            slip = _Slip.objects.filter(
                month=log.log_date.month,
                year=log.log_date.year,
            ).filter(
                # Match by user → EmployeeSalaryInfo → SalarySlip
                employee_salary_info__user=log.user,
            ).first()
            if slip:
                ProjectCostAllocation.objects.create(
                    salary_slip=slip,
                    project_code=log.project_category or 'DAILY-ACTIVITY',
                    project_name=log.project_category or 'Daily Activity',
                    allocated_hours=log.hours_spent,
                    allocation_percent=Decimal('0'),
                    allocated_cost=Decimal('0'),
                    month=log.log_date.month,
                    year=log.log_date.year,
                )
        except Exception:
            pass  # Never block the approval if cost allocation fails

        # ── Notify employee by email ───────────────────────────────────
        try:
            from django.core.mail import send_mail
            from django.conf import settings as _s
            role_label = (
                'Project Manager' if log.submitted_to_role == 'project_manager'
                else 'Reporting Manager' if log.submitted_to_role == 'reporting_manager'
                else 'Manager'
            )
            send_mail(
                subject=f'[RAD AI] Your activity log has been approved',
                message=(
                    f'Hi {log.user.first_name or log.user.email},\n\n'
                    f'Your activity "{log.task_title}" on {log.log_date} '
                    f'({log.hours_spent} hrs) has been approved by your {role_label}'
                    f'{" with note: " + note if note else "."}'
                    f'\n\nApproved by: {request.user.get_full_name() or request.user.email}\n'
                ),
                from_email=getattr(_s, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[log.user.email],
                fail_silently=True,
            )
        except Exception:
            pass

        return Response(DailyWorkLogSerializer(log).data)

    # ── Reject ────────────────────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        log = self.get_object()
        if not self._can_approve(request.user, log):
            return Response(
                {'error': 'You do not have permission to reject this activity.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if log.approval_status == DailyWorkLogApprovalStatus.REJECTED:
            return Response(
                {'error': 'Activity is already rejected.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        note = request.data.get('note', '')
        log.approval_status = DailyWorkLogApprovalStatus.REJECTED
        log.approved_by     = request.user
        log.approved_at     = timezone.now()
        log.approval_note   = note
        log.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note'])

        # ── Notify employee by email ───────────────────────────────────
        try:
            from django.core.mail import send_mail
            from django.conf import settings as _s
            role_label_r = (
                'Project Manager' if log.submitted_to_role == 'project_manager'
                else 'Reporting Manager' if log.submitted_to_role == 'reporting_manager'
                else 'Manager'
            )
            send_mail(
                subject=f'[RAD AI] Your activity log requires revision',
                message=(
                    f'Hi {log.user.first_name or log.user.email},\n\n'
                    f'Your activity "{log.task_title}" on {log.log_date} '
                    f'({log.hours_spent} hrs) was reviewed by your {role_label_r} and was not approved.\n\n'
                    f'Reason: {note or "No reason provided."}\n\n'
                    f'Please update the entry and resubmit for approval.\n\n'
                    f'Reviewed by: {request.user.get_full_name() or request.user.email}\n'
                ),
                from_email=getattr(_s, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[log.user.email],
                fail_silently=True,
            )
        except Exception:
            pass

        return Response(DailyWorkLogSerializer(log).data)

    # -- Summary: daily totals for heatmap + bar chart ---------------------
    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        qs = self.get_queryset()
        from django.db.models import Sum, Count  # noqa: F811
        rows = (
            qs
            .values('log_date')
            .annotate(total_hours=Sum('hours_spent'), task_count=Count('id'))
            .order_by('log_date')
        )
        data = [
            {
                'date':        str(r['log_date']),
                'total_hours': float(r['total_hours'] or 0),
                'task_count':  r['task_count'],
            }
            for r in rows
        ]
        return Response(data)

    # ── Export to S3 ───────────────────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='export-to-s3')
    def export_to_s3(self, request):
        from django.conf import settings as _settings  # noqa: F811
        bucket = getattr(_settings, 'AWS_STORAGE_BUCKET_NAME', '')
        if not bucket:
            return Response(
                {'error': 'S3 storage is not configured on this environment.'},
                status=503,
            )

        import boto3, json, tempfile  # noqa: E402
        from datetime import datetime as _dt  # noqa: F811

        qs   = self.get_queryset()
        data = DailyWorkLogSerializer(qs, many=True).data

        user       = request.user
        now        = _dt.utcnow()
        s3_key     = (
            f'daily-tracker/{user.id}/{now.year:04d}/{now.month:02d}/'
            f'export_{now.strftime("%Y%m%d_%H%M%S")}.json'
        )

        payload = json.dumps(list(data), default=str, ensure_ascii=False)

        region   = getattr(_settings, 'AWS_S3_REGION_NAME', 'us-east-1')
        endpoint = getattr(_settings, 'AWS_S3_ENDPOINT_URL', None)
        s3 = boto3.client(
            's3',
            region_name=region,
            endpoint_url=endpoint,
        )
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=payload.encode('utf-8'),
            ContentType='application/json',
        )

        # Mark exported logs with their S3 key
        qs.update(s3_export_key=s3_key)

        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': s3_key},
            ExpiresIn=3600,
        )

        return Response({'s3_key': s3_key, 'url': presigned_url, 'count': len(data)})

    # ── Team view: latest entries per user for a given date ────────────────
    @action(detail=False, methods=['get'], url_path='team')
    def team(self, request):
        if not request.user.is_staff:
            return Response({'error': 'Staff access required.'}, status=403)

        params = request.query_params
        date   = params.get('date', str(datetime.date.today()))

        from django.db.models import Max  # noqa: F811
        # Subquery: most recent entry per user on target date
        latest = (
            DailyWorkLog.objects
            .filter(log_date=date)
            .values('user')
            .annotate(latest=Max('created_at'))
            .values_list('latest', flat=True)
        )
        logs = (
            DailyWorkLog.objects
            .filter(log_date=date, created_at__in=latest)
            .select_related('user')
            .order_by('user__first_name', 'user__last_name')
        )
        return Response(DailyWorkLogSerializer(logs, many=True).data)


# =============================================================================
# AI ANALYTICS — GPT-4o powered HR intelligence for a payroll run
# POST /api/v1/payroll/ai-analytics/generate/
# Body: { run_id }
# Returns structured HR intelligence: health score, risk items,
# top recommendations, compliance flags, payroll forecast.
# =============================================================================

# Soft-coded thresholds for analytics
_AI_ANALYTICS_SALARY_SPIKE_PCT   = 25   # % change flagged as spike
_AI_ANALYTICS_MIN_SLIPS_REQUIRED = 1    # minimum slips to run analysis
_AI_ANALYTICS_CACHE_KEY_TTL      = 3600 # seconds — cache results for 1 hour


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ai_analytics_generate(request):
    """
    Builds an anonymized payroll summary, calls GPT-4o, and returns structured
    HR intelligence — health score, risk items, recommendations, and forecast.

    Employee data is anonymized before leaving the server: names are replaced
    with department+index codes and exact salaries are bucketed into ranges.
    """
    run_id = request.data.get('run_id')
    if not run_id:
        return Response({'error': 'run_id is required.'}, status=400)

    # ── Load payroll run ──────────────────────────────────────────────────────
    try:
        from apps.finance.salary_models import PayrollRun, SalarySlip
        from django.conf import settings as dj_settings

        payroll_run = PayrollRun.objects.get(pk=run_id)
    except PayrollRun.DoesNotExist:
        return Response({'error': 'Payroll run not found.'}, status=404)

    # ── Load slips for this run and previous run ──────────────────────────────
    curr_slips = list(
        SalarySlip.objects
        .filter(payroll_run=payroll_run)
        .select_related('employee_salary_info')
        .values(
            'slip_number', 'gross_salary', 'net_salary', 'total_deductions',
            'total_allowances', 'status',
            'employee_salary_info__department', 'employee_salary_info__job_title',
        )
    )

    # Previous run (same month-1 / year)
    prev_month = payroll_run.month - 1 if payroll_run.month > 1 else 12
    prev_year  = payroll_run.year if payroll_run.month > 1 else payroll_run.year - 1
    prev_slips = list(
        SalarySlip.objects
        .filter(payroll_run__month=prev_month, payroll_run__year=prev_year)
        .values('slip_number', 'net_salary')
    )

    if len(curr_slips) < _AI_ANALYTICS_MIN_SLIPS_REQUIRED:
        return Response({'error': 'Not enough payroll data to generate analytics.'}, status=400)

    # ── Build anonymized summary (NO PII sent to OpenAI) ─────────────────────
    import statistics

    net_values = [float(s['net_salary'] or 0) for s in curr_slips]
    gross_values = [float(s['gross_salary'] or 0) for s in curr_slips]
    deduction_values = [float(s['total_deductions'] or 0) for s in curr_slips]

    prev_net_map = {s['slip_number']: float(s['net_salary'] or 0) for s in prev_slips}

    # Department breakdown
    dept_map = {}
    for s in curr_slips:
        dept = s['employee_salary_info__department'] or 'Unassigned'
        dept_map.setdefault(dept, []).append(float(s['net_salary'] or 0))

    dept_summary = [
        {
            'dept': dept,
            'count': len(vals),
            'avg_net': round(statistics.mean(vals), 2),
            'min_net': round(min(vals), 2),
            'max_net': round(max(vals), 2),
        }
        for dept, vals in dept_map.items()
    ]

    # Status breakdown
    status_counts = {}
    for s in curr_slips:
        status_counts[s['status']] = status_counts.get(s['status'], 0) + 1

    # Month-over-month changes
    mom_changes = []
    for s in curr_slips:
        prev = prev_net_map.get(s['slip_number'])
        if prev and prev > 0:
            pct = ((float(s['net_salary'] or 0) - prev) / prev) * 100
            if abs(pct) >= _AI_ANALYTICS_SALARY_SPIKE_PCT:
                mom_changes.append({
                    'emp_code': s['slip_number'],
                    'dept':     s['employee_salary_info__department'] or 'Unassigned',
                    'change_pct': round(pct, 1),
                })

    new_employees   = len([s for s in curr_slips if s['slip_number'] not in prev_net_map and prev_slips])
    missing_employees = len([s for s in prev_slips if s['slip_number'] not in {c['slip_number'] for c in curr_slips}]) if prev_slips else 0

    summary = {
        'period':              f'{payroll_run.month:02d}/{payroll_run.year}',
        'run_code':            payroll_run.run_code,
        'total_employees':     len(curr_slips),
        'total_gross':         round(sum(gross_values), 2),
        'total_net':           round(sum(net_values), 2),
        'total_deductions':    round(sum(deduction_values), 2),
        'avg_net_salary':      round(statistics.mean(net_values), 2),
        'median_net_salary':   round(statistics.median(net_values), 2),
        'salary_std_dev':      round(statistics.stdev(net_values), 2) if len(net_values) > 1 else 0,
        'departments':         dept_summary,
        'status_breakdown':    status_counts,
        'mom_salary_spikes':   mom_changes,
        'new_employees':       new_employees,
        'missing_employees':   missing_employees,
        'prev_period_available': bool(prev_slips),
    }

    # ── Call GPT-4o ───────────────────────────────────────────────────────────
    api_key = getattr(dj_settings, 'OPENAI_API_KEY', '')
    model   = getattr(dj_settings, 'OPENAI_MODEL', 'gpt-4o')

    if not api_key:
        return Response({'error': 'OpenAI API key not configured. Set OPENAI_API_KEY in environment.'}, status=503)

    import json as _json
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        system_prompt = (
            "You are a senior HR analytics AI. You analyse payroll summaries and return "
            "actionable intelligence in strict JSON format. Be concise but specific. "
            "Never hallucinate employee names — use only the codes and department names provided. "
            "Base all insights on the data provided. Salary figures are in the organisation's local currency."
        )

        user_prompt = f"""Analyse this payroll summary and return ONLY valid JSON (no markdown, no explanation).

PAYROLL SUMMARY:
{_json.dumps(summary, indent=2)}

Return this exact JSON schema:
{{
  "health_score": <integer 0-100 representing overall payroll health>,
  "health_label": "<Excellent|Good|Fair|Needs Attention|Critical>",
  "executive_summary": "<2-3 sentence plain-English summary of this payroll run>",
  "risk_items": [
    {{
      "severity": "<critical|high|medium|low>",
      "category": "<salary|compliance|attendance|forecast|process>",
      "emp_code": "<employee code or department name>",
      "issue": "<concise issue title>",
      "root_cause": "<1 sentence explanation>",
      "recommendation": "<specific actionable fix>",
      "priority": <1-10>
    }}
  ],
  "top_recommendations": [
    {{
      "priority": <1-5>,
      "action": "<specific action to take>",
      "impact": "<expected outcome>",
      "effort": "<Low|Medium|High>"
    }}
  ],
  "compliance_flags": [
    {{
      "type": "<compliance category>",
      "description": "<what needs attention>",
      "urgency": "<Immediate|Soon|Monitor>"
    }}
  ],
  "forecast": {{
    "next_month_estimate": <number>,
    "trend": "<Increasing|Stable|Decreasing>",
    "confidence": "<High|Medium|Low>",
    "rationale": "<1 sentence>"
  }},
  "dept_health": [
    {{
      "dept": "<dept name>",
      "status": "<Healthy|Review|Concern>",
      "insight": "<1 sentence>"
    }}
  ]
}}"""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            response_format={'type': 'json_object'},
            temperature=0.2,
            max_tokens=2000,
        )

        ai_result = _json.loads(response.choices[0].message.content)

    except Exception as e:
        logger.error('ai_analytics_generate: OpenAI call failed: %s', e)
        return Response({'error': f'AI analysis failed: {str(e)}'}, status=502)

    return Response({
        'run_code': payroll_run.run_code,
        'period':   f'{payroll_run.month:02d}/{payroll_run.year}',
        'summary':  summary,
        'ai':       ai_result,
    })


# =============================================================================
# MASTER PAYROLL WORKFLOW — freeze / approve / finance / release
# =============================================================================
# Soft-coded role-permission map: each action defines which roles may perform it
# and what stage the record must be in.  Adding a new role just means editing
# _WORKFLOW_TRANSITIONS below — no view code changes needed.
#
# Super-admin (PAYROLL_WORKFLOW_SUPERADMIN_EMAIL in settings) can always unfreeze.
# Finance roles are identified by role code containing 'finance'.
# Accounts roles are identified by role code containing 'account'.

_WORKFLOW_TRANSITIONS = {
    # action:          (required_from_stage,           resulting_stage,              role_checker_name)
    'freeze':          (MasterPayrollWorkflowStage.DRAFT,            MasterPayrollWorkflowStage.FROZEN,           '_is_hr_manager'),
    'hr_approve':      (MasterPayrollWorkflowStage.FROZEN,           MasterPayrollWorkflowStage.HR_APPROVED,      '_is_hr_manager'),
    'finance_review':  (MasterPayrollWorkflowStage.HR_APPROVED,      MasterPayrollWorkflowStage.FINANCE_REVIEW,   '_is_finance'),
    'finance_approve': (MasterPayrollWorkflowStage.FINANCE_REVIEW,   MasterPayrollWorkflowStage.FINANCE_APPROVED, '_is_finance'),
    'release':         (MasterPayrollWorkflowStage.FINANCE_APPROVED, MasterPayrollWorkflowStage.RELEASED,         '_is_accounts'),
    # Unfreeze is special: superadmin only, can revert from any non-released stage
    'unfreeze':        (None, MasterPayrollWorkflowStage.DRAFT, '_is_superadmin_payroll'),
}


def _is_finance(user) -> bool:
    """User holds a Finance role."""
    if user.is_superuser or user.is_staff:
        return True
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if profile and profile.role:
            code = (profile.role.code or '').lower()
            return 'finance' in code or code in ('admin', 'superadmin')
    except Exception:
        pass
    return False


def _is_accounts(user) -> bool:
    """User holds an Accounts / Accounting role."""
    if user.is_superuser or user.is_staff:
        return True
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if profile and profile.role:
            code = (profile.role.code or '').lower()
            return 'account' in code or code in ('admin', 'superadmin')
    except Exception:
        pass
    return False


def _is_superadmin_payroll(user) -> bool:
    """Super-admin with payroll unfreeze permission.

    Matches the email set in PAYROLL_WORKFLOW_SUPERADMIN_EMAIL (settings)
    OR Django superuser status.
    """
    from django.conf import settings as dj_settings
    superadmin_email = getattr(dj_settings, 'PAYROLL_WORKFLOW_SUPERADMIN_EMAIL', '').lower()
    if user.is_superuser:
        return True
    if superadmin_email and (user.email or '').lower() == superadmin_email:
        return True
    return False


def _master_payroll_workflow_action(request, import_id: str, action: str):
    """
    Generic workflow-transition handler.  Shared logic for all action endpoints.

    - Loads the MasterPayrollImport
    - Checks permission via _WORKFLOW_TRANSITIONS role checker
    - Validates the current stage
    - Advances the stage
    - Creates an immutable MasterPayrollWorkflowLog entry
    - Returns updated workflow_info JSON
    """
    from django.utils import timezone
    from .models import MasterPayrollImport

    if action not in _WORKFLOW_TRANSITIONS:
        return Response({'error': f'Unknown workflow action: {action}'}, status=400)

    required_from, to_stage, checker_name = _WORKFLOW_TRANSITIONS[action]

    # Load record
    try:
        record = MasterPayrollImport.objects.get(pk=import_id)
    except MasterPayrollImport.DoesNotExist:
        return Response({'error': 'Master payroll import not found.'}, status=404)

    # Permission check
    checker_fn = {
        '_is_hr_manager':         _is_hr_manager,
        '_is_finance':            _is_finance,
        '_is_accounts':           _is_accounts,
        '_is_superadmin_payroll': _is_superadmin_payroll,
    }.get(checker_name)

    if not checker_fn or not checker_fn(request.user):
        return Response({'error': 'You do not have permission to perform this action.'}, status=403)

    # Stage guard (unfreeze is flexible — can revert from any non-released stage)
    if action == 'unfreeze':
        if record.workflow_stage == MasterPayrollWorkflowStage.RELEASED:
            return Response({'error': 'A released payroll record cannot be unfrozen.'}, status=400)
        if record.workflow_stage == MasterPayrollWorkflowStage.DRAFT:
            return Response({'error': 'Record is already in draft stage.'}, status=400)
    else:
        if record.workflow_stage != required_from:
            stage_label = dict(MasterPayrollWorkflowStage.choices).get(record.workflow_stage, record.workflow_stage)
            return Response({
                'error': f'Cannot perform "{action}" from current stage: {stage_label}.'
            }, status=400)

    note = (request.data.get('note') or '').strip()
    from_stage = record.workflow_stage
    now = timezone.now()

    # Apply the transition
    record.workflow_stage = to_stage

    if action == 'freeze':
        record.frozen_by = request.user
        record.frozen_at = now

    elif action == 'hr_approve':
        record.hr_approved_by   = request.user
        record.hr_approved_at   = now
        record.hr_approval_note = note

    elif action == 'finance_approve':
        record.finance_approved_by   = request.user
        record.finance_approved_at   = now
        record.finance_approval_note = note

    elif action == 'release':
        record.released_by  = request.user
        record.released_at  = now
        record.release_note = note

    elif action == 'unfreeze':
        # Revert all downstream tracking fields that came after freeze
        record.hr_approved_by = None
        record.hr_approved_at = None
        record.hr_approval_note = ''
        record.finance_approved_by = None
        record.finance_approved_at = None
        record.finance_approval_note = ''
        record.released_by = None
        record.released_at = None
        record.release_note = ''

    record.save()

    # Immutable log entry
    MasterPayrollWorkflowLog.objects.create(
        master_import=record,
        from_stage=from_stage,
        to_stage=to_stage,
        action=action,
        performed_by=request.user,
        note=note,
    )

    logger.info(
        'Payroll workflow: %s on import %s by user %s (from %s → %s)',
        action, import_id, request.user.email, from_stage, to_stage,
    )

    # ── Post-freeze notification ─────────────────────────────────────────────
    # Notify every HR Manager listed in PAYROLL_FREEZE_NOTIFY_EMAILS.
    # Runs synchronously but is lightweight (DB lookup + async email dispatch).
    if action == 'freeze':
        _notify_hr_managers_on_freeze(record, request.user)

    return Response(_build_workflow_info(record))


CALENDAR_MONTHS = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


def _notify_hr_managers_on_freeze(record, frozen_by_user):
    """
    Look up every email in PAYROLL_FREEZE_NOTIFY_EMAILS and create an in-app
    + email notification for each one using the existing NotificationService.
    Safe to call even if a recipient account does not exist — errors are
    logged and silently skipped so the workflow response is never affected.
    """
    try:
        from django.conf import settings as dj_settings
        from django.contrib.auth import get_user_model
        from apps.notifications.services import NotificationService

        notify_emails = getattr(dj_settings, 'PAYROLL_FREEZE_NOTIFY_EMAILS', [])
        if not notify_emails:
            return

        User = get_user_model()
        month_name  = CALENDAR_MONTHS[record.month] if 1 <= record.month <= 12 else str(record.month)
        period      = f'{month_name} {record.year}'
        frozen_name = (
            frozen_by_user.get_full_name() or
            frozen_by_user.email or
            str(frozen_by_user)
        )

        recipients = User.objects.filter(
            email__in=notify_emails,
            is_active=True,
        )

        for recipient in recipients:
            try:
                NotificationService.create_notification(
                    recipient=recipient,
                    template_key='PAYROLL_FROZEN',
                    sender=frozen_by_user,
                    period=period,
                    frozen_by=frozen_name,
                    total_rows=record.total_rows,
                    metadata={
                        'import_id':  str(record.pk),
                        'year':       record.year,
                        'month':      record.month,
                        'frozen_by':  frozen_by_user.email,
                    },
                )
                logger.info(
                    'Payroll freeze notification sent to %s for import %s',
                    recipient.email, record.pk,
                )
            except Exception as exc:
                logger.error(
                    'Failed to notify %s on payroll freeze: %s',
                    recipient.email, exc,
                )
    except Exception as exc:
        logger.error('_notify_hr_managers_on_freeze error: %s', exc)


# ─────────────────────────────────────────────────────────────────────────────
# Super-Admin Approval Tracker
# GET /api/v1/payroll/approval-tracker/
# Returns every MasterPayrollImport with per-stage actor + SLA status so the
# super-admin can monitor the entire approval pipeline in one view.
# ─────────────────────────────────────────────────────────────────────────────

# Stage order must match WORKFLOW_STAGE_ORDER in models.py
_TRACKER_STAGE_ORDER = [
    'draft', 'frozen', 'hr_approved', 'finance_review', 'finance_approved', 'released',
]

# Role label for who is expected to act at each stage
_TRACKER_PENDING_ROLE = {
    'draft':            'HR Manager',
    'frozen':           'HR Manager',
    'hr_approved':      'Finance Team',
    'finance_review':   'Finance Team',
    'finance_approved': 'Accounts Team',
    'released':         None,
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def master_payroll_approval_tracker(request):
    """
    Super-admin dashboard — approval progress for every master payroll file.

    Returns:
      summary   — aggregate counts per stage + overdue count
      results   — list of imports with per-stage actor/timestamp/SLA info
    Filters (query params): stage, year, month
    """
    from django.conf import settings as dj_settings
    from django.utils import timezone
    from .models import MasterPayrollImport

    if not _is_hr_manager(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('HR Manager or Super-Admin role required.')

    # Soft-coded SLA thresholds (days) — can be overridden per-stage via env
    sla_days_cfg = getattr(dj_settings, 'PAYROLL_TRACKER_SLA_DAYS', {
        'draft': 3, 'frozen': 2, 'hr_approved': 3,
        'finance_review': 3, 'finance_approved': 2, 'released': None,
    })

    now = timezone.now()

    qs = (
        MasterPayrollImport.objects
        .select_related('generated_by', 'frozen_by', 'hr_approved_by',
                        'finance_approved_by', 'released_by')
        .prefetch_related('workflow_logs__performed_by')
        .order_by('-year', '-month', '-generated_at')
    )

    # Optional filters
    for fld, cast in (('year', int), ('month', int)):
        val = request.query_params.get(fld)
        if val:
            try:
                qs = qs.filter(**{fld: cast(val)})
            except (ValueError, TypeError):
                pass

    stage_filter = request.query_params.get('stage', '').strip()
    if stage_filter and stage_filter in _TRACKER_STAGE_ORDER:
        qs = qs.filter(workflow_stage=stage_filter)

    def _actor(user):
        if not user:
            return None
        return {
            'name':  (user.get_full_name() or '').strip() or user.email,
            'email': user.email,
        }

    def _sla_status(entry_ts, stage_key):
        """Return 'ok' | 'warning' | 'overdue' for a stage entry timestamp."""
        sla = sla_days_cfg.get(stage_key)
        if not sla or not entry_ts:
            return 'ok'
        elapsed = (now - entry_ts).total_seconds() / 86400
        if elapsed > sla:
            return 'overdue'
        if elapsed > sla * 0.7:
            return 'warning'
        return 'ok'

    def _stage_entry(stage_key, actor_user, ts):
        sla = sla_days_cfg.get(stage_key)
        elapsed = round((now - ts).total_seconds() / 86400, 1) if ts else None
        return {
            'actor':        _actor(actor_user),
            'timestamp':    ts.isoformat() if ts else None,
            'days_elapsed': elapsed,
            'sla_days':     sla,
            'sla_status':   _sla_status(ts, stage_key) if ts else 'ok',
        }

    def _serialize(imp):
        month_name = CALENDAR_MONTHS[imp.month] if 1 <= imp.month <= 12 else str(imp.month)

        # Derive finance_review actor from immutable workflow logs
        finance_review_actor  = None
        finance_review_ts     = None
        for log in imp.workflow_logs.all():
            if log.action == 'finance_review':
                finance_review_actor = log.performed_by
                finance_review_ts    = log.performed_at
                break

        stages = {
            'draft':            _stage_entry('draft',            imp.generated_by,        imp.generated_at),
            'frozen':           _stage_entry('frozen',           imp.frozen_by,           imp.frozen_at),
            'hr_approved':      _stage_entry('hr_approved',      imp.hr_approved_by,      imp.hr_approved_at),
            'finance_review':   _stage_entry('finance_review',   finance_review_actor,    finance_review_ts),
            'finance_approved': _stage_entry('finance_approved', imp.finance_approved_by, imp.finance_approved_at),
            'released':         _stage_entry('released',         imp.released_by,         imp.released_at),
        }

        # Determine when the current stage was entered (for live SLA clock)
        current_stage_entry_ts_map = {
            'draft':            imp.generated_at,
            'frozen':           imp.frozen_at,
            'hr_approved':      imp.hr_approved_at,
            'finance_review':   finance_review_ts or imp.hr_approved_at,
            'finance_approved': imp.finance_approved_at,
            'released':         imp.released_at,
        }
        current_ts  = current_stage_entry_ts_map.get(imp.workflow_stage)
        current_sla = sla_days_cfg.get(imp.workflow_stage)
        days_in_stage = round((now - current_ts).total_seconds() / 86400, 1) if current_ts else None

        try:
            stage_idx = _TRACKER_STAGE_ORDER.index(imp.workflow_stage)
        except ValueError:
            stage_idx = 0

        return {
            'id':                    str(imp.id),
            'period':                f'{month_name} {imp.year}',
            'year':                  imp.year,
            'month':                 imp.month,
            'total_rows':            imp.total_rows,
            'generated_by':          _actor(imp.generated_by),
            'generated_at':          imp.generated_at.isoformat(),
            'workflow_stage':        imp.workflow_stage,
            'stage_index':           stage_idx,
            'pending_role':          _TRACKER_PENDING_ROLE.get(imp.workflow_stage),
            'days_in_current_stage': days_in_stage,
            'current_sla_days':      current_sla,
            'current_sla_status':    _sla_status(current_ts, imp.workflow_stage),
            'stages':                stages,
        }

    all_items  = list(qs)
    serialized = [_serialize(i) for i in all_items]

    # Aggregate counts per stage (unfiltered for the KPI bar)
    unfiltered_qs = MasterPayrollImport.objects.values('workflow_stage')
    stage_counts  = {}
    for row in unfiltered_qs:
        stage_counts[row['workflow_stage']] = stage_counts.get(row['workflow_stage'], 0) + 1

    overdue_count = sum(1 for s in serialized if s['current_sla_status'] == 'overdue')
    warning_count = sum(1 for s in serialized if s['current_sla_status'] == 'warning')

    return Response({
        'summary': {
            'total':         len(serialized),
            'by_stage':      stage_counts,
            'overdue_count': overdue_count,
            'warning_count': warning_count,
        },
        'results': serialized,
    })


def _build_workflow_info(record):
    """Build the JSON payload returned after every workflow action."""
    from .models import MasterPayrollImport  # local to avoid circular
    logs = record.workflow_logs.order_by('performed_at').values(
        'action', 'from_stage', 'to_stage',
        'performed_by__first_name', 'performed_by__last_name',
        'performed_by__email', 'performed_at', 'note',
    )

    def _user_name(log):
        fn = log.get('performed_by__first_name') or ''
        ln = log.get('performed_by__last_name') or ''
        return f'{fn} {ln}'.strip() or log.get('performed_by__email') or 'unknown'

    stage_labels = dict(MasterPayrollWorkflowStage.choices)

    return {
        'id':            str(record.id),
        'workflow_stage': record.workflow_stage,
        'stage_label':    stage_labels.get(record.workflow_stage, record.workflow_stage),
        'is_editable_by_hr': record.workflow_stage == MasterPayrollWorkflowStage.DRAFT,
        'frozen_at':     record.frozen_at.isoformat() if record.frozen_at else None,
        'hr_approved_at':record.hr_approved_at.isoformat() if record.hr_approved_at else None,
        'finance_approved_at': record.finance_approved_at.isoformat() if record.finance_approved_at else None,
        'released_at':   record.released_at.isoformat() if record.released_at else None,
        'workflow_log':  [
            {
                'action':     log['action'],
                'from_stage': log['from_stage'],
                'to_stage':   log['to_stage'],
                'actor':      _user_name(log),
                'email':      log.get('performed_by__email', ''),
                'at':         log['performed_at'].isoformat(),
                'note':       log['note'],
            }
            for log in logs
        ],
    }


# ── Individual workflow action endpoints ─────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def master_payroll_freeze(request, import_id):
    """HR Manager freezes the master payroll — one-time lock."""
    return _master_payroll_workflow_action(request, import_id, 'freeze')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def master_payroll_unfreeze(request, import_id):
    """Superadmin only — revert to draft stage."""
    return _master_payroll_workflow_action(request, import_id, 'unfreeze')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def master_payroll_hr_approve(request, import_id):
    """HR Manager approves the frozen payroll and sends to Finance."""
    return _master_payroll_workflow_action(request, import_id, 'hr_approve')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def master_payroll_finance_review(request, import_id):
    """Finance opens the file for review/modification."""
    return _master_payroll_workflow_action(request, import_id, 'finance_review')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def master_payroll_finance_approve(request, import_id):
    """Finance confirms the payroll and sends to Accounts."""
    return _master_payroll_workflow_action(request, import_id, 'finance_approve')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def master_payroll_release(request, import_id):
    """Accounts marks the salary as released."""
    return _master_payroll_workflow_action(request, import_id, 'release')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def master_payroll_workflow_status(request, import_id):
    """Return current workflow stage + full audit log for a master payroll import."""
    from .models import MasterPayrollImport
    try:
        record = MasterPayrollImport.objects.get(pk=import_id)
    except MasterPayrollImport.DoesNotExist:
        return Response({'error': 'Not found.'}, status=404)
    return Response(_build_workflow_info(record))


# =============================================================================
# Leave Encashment Views
# =============================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_encashment_status(request):
    """
    GET /api/v1/payroll/leave-encashment/status/?year=&month=
    Returns the LeaveEncashmentRun for the given period, or 404 if not run yet.
    """
    if not _is_hr_manager(request.user):
        return Response({'error': 'HR Manager role required.'}, status=403)

    from django.utils import timezone as tz
    now = tz.now()
    year  = int(request.query_params.get('year',  now.year))
    month = int(request.query_params.get('month', now.month))

    from apps.payroll.services.leave_encashment import get_encashment_status
    result = get_encashment_status(year=year, month=month)
    if result is None:
        return Response({'status': 'not_run', 'year': year, 'month': month}, status=404)
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_encashment_preview(request):
    """
    GET /api/v1/payroll/leave-encashment/preview/?year=&month=
    Dry-run: compute per-employee encashment without writing to DB.
    """
    if not _is_hr_manager(request.user):
        return Response({'error': 'HR Manager role required.'}, status=403)

    from django.utils import timezone as tz
    now = tz.now()
    year  = int(request.query_params.get('year',  now.year))
    month = int(request.query_params.get('month', now.month))

    from apps.payroll.services.leave_encashment import run_leave_encashment
    result = run_leave_encashment(year=year, month=month, triggered_by_user=request.user, dry_run=True)
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def leave_encashment_run(request):
    """
    POST /api/v1/payroll/leave-encashment/run/
    Body: { year: int, month: int }
    Triggers the leave encashment for the specified period.
    Idempotent guard: raises 409 if already run successfully.
    """
    if not _is_hr_manager(request.user):
        return Response({'error': 'HR Manager role required.'}, status=403)

    from django.utils import timezone as tz
    now = tz.now()
    year  = int(request.data.get('year',  now.year))
    month = int(request.data.get('month', now.month))

    from apps.payroll.services.leave_encashment import (
        run_leave_encashment,
        EncashmentAlreadyRunError,
    )
    try:
        result = run_leave_encashment(year=year, month=month, triggered_by_user=request.user)
        return Response(result, status=201)
    except EncashmentAlreadyRunError as exc:
        return Response({'error': str(exc)}, status=409)
    except Exception as exc:
        logger.exception('leave_encashment_run failed: %s', exc)
        return Response({'error': 'Encashment run failed. Check server logs.'}, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_encashment_list(request):
    """
    GET /api/v1/payroll/leave-encashment/
    List all LeaveEncashmentRun records (HR-only).
    """
    if not _is_hr_manager(request.user):
        return Response({'error': 'HR Manager role required.'}, status=403)

    from apps.payroll.models import LeaveEncashmentRun
    runs = LeaveEncashmentRun.objects.select_related('triggered_by').order_by('-year', '-month')
    data = [
        {
            'id':                   str(r.id),
            'year':                 r.year,
            'month':                r.month,
            'month_name':           r.get_month_display(),
            'status':               r.status,
            'executed_at':          r.executed_at.isoformat(),
            'triggered_by':         r.triggered_by.get_full_name() if r.triggered_by else 'System',
            'records_processed':    r.records_processed,
            'total_days_encashed':  float(r.total_days_encashed),
            'total_pay':            float(r.total_pay),
            'missing_salaries':     r.missing_salaries,
        }
        for r in runs
    ]
    return Response({'results': data, 'count': len(data)})
