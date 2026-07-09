"""
Payroll Intelligence Platform — Database Models
================================================
Intelligence layer on top of apps.finance payroll models.
Adds Validation Logs, Audit Alerts, Project Cost Allocation,
AI Insight Snapshots, and Chatbot Message history.

All monetary fields use Decimal (never float).
All PKs are UUID for portability.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

# Canonical identity normalisation — same helpers used by the timesheet
# ingest pipeline so employee_code, name and email are always stored in a
# consistent format across every table that holds employee identity.
from apps.timesheet.identity import norm_code, norm_email, norm_name

from django.contrib.auth import get_user_model
from django.db import models

from apps.finance.salary_models import EmployeeSalaryInfo, PayrollRun, SalarySlip

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Shared choices — soft-coded as TextChoices so they appear in the admin and
# are never magic strings scattered across the codebase.
# ─────────────────────────────────────────────────────────────────────────────

class Severity(models.TextChoices):
    ERROR   = 'error',    'Error'
    WARNING = 'warning',  'Warning'
    INFO    = 'info',     'Info'


class AlertType(models.TextChoices):
    SALARY_SPIKE       = 'salary_spike',       'Salary Spike'
    NEW_EMPLOYEE       = 'new_employee',        'New Employee'
    MISSING_EMPLOYEE   = 'missing_employee',    'Missing Employee'
    OVERTIME_EXCESS    = 'overtime_excess',     'Overtime Excess'
    NEGATIVE_SALARY    = 'negative_salary',     'Negative Salary'
    DUPLICATE_PAYMENT  = 'duplicate_payment',   'Duplicate Payment Risk'


class AlertSeverity(models.TextChoices):
    LOW      = 'low',      'Low'
    MEDIUM   = 'medium',   'Medium'
    HIGH     = 'high',     'High'
    CRITICAL = 'critical', 'Critical'


class AlertStatus(models.TextChoices):
    OPEN         = 'open',         'Open'
    ACKNOWLEDGED = 'acknowledged', 'Acknowledged'
    RESOLVED     = 'resolved',     'Resolved'


class InsightType(models.TextChoices):
    ATTENDANCE_ANOMALY  = 'attendance_anomaly',  'Attendance Anomaly'
    MISSING_TIMESHEET   = 'missing_timesheet',   'Missing Timesheet'
    OVERTIME_ALERT      = 'overtime_alert',      'Overtime Alert'
    PAYROLL_RISK        = 'payroll_risk',         'Payroll Risk Score'
    BURNOUT_RISK        = 'burnout_risk',         'Burnout Risk'
    PRODUCTIVITY_TREND  = 'productivity_trend',  'Productivity Trend'
    PAYROLL_FORECAST    = 'payroll_forecast',     'Payroll Forecast'


class ChatPersona(models.TextChoices):
    EMPLOYEE = 'employee', 'Employee'
    MANAGER  = 'manager',  'Manager'
    FINANCE  = 'finance',  'Finance'
    HR       = 'hr',       'HR'


class ChatRole(models.TextChoices):
    USER      = 'user',      'User'
    ASSISTANT = 'assistant', 'Assistant'


# ─────────────────────────────────────────────────────────────────────────────
# 1. PayrollValidationLog
# ─────────────────────────────────────────────────────────────────────────────

class PayrollValidationLog(models.Model):
    """
    Stores rule-engine findings for a given payroll run.
    Created by the ValidationEngine (both client-side rules surfaced via API
    and server-side aggregate checks). Resolved findings are kept for audit.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='validation_logs',
    )
    # Nullable — a finding may be run-level (e.g. "missing employees") rather
    # than per-employee.
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='validation_logs',
    )
    rule_id      = models.CharField(max_length=64)
    rule_label   = models.CharField(max_length=255)
    severity     = models.CharField(max_length=20, choices=Severity.choices, default=Severity.WARNING)
    description  = models.TextField()
    suggested_action = models.TextField(blank=True)
    is_resolved  = models.BooleanField(default=False)
    resolved_by  = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_validations',
    )
    resolved_at  = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table    = 'payroll_validation_log'
        ordering    = ['-created_at', 'severity']
        indexes     = [
            models.Index(fields=['payroll_run', 'severity']),
            models.Index(fields=['payroll_run', 'is_resolved']),
        ]

    def __str__(self):
        return f'{self.rule_label} [{self.severity}] — run {self.payroll_run_id}'


# ─────────────────────────────────────────────────────────────────────────────
# 2. PayrollAuditAlert
# ─────────────────────────────────────────────────────────────────────────────

class PayrollAuditAlert(models.Model):
    """
    Detected anomalies produced by the Payroll Auditor engine.
    Compares payroll_run with compared_to_run (previous cycle) and flags
    any change that exceeds soft-coded thresholds.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='audit_alerts',
    )
    compared_to_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='compared_alerts',
    )
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_alerts',
    )
    alert_type      = models.CharField(max_length=32, choices=AlertType.choices)
    severity        = models.CharField(max_length=20, choices=AlertSeverity.choices, default=AlertSeverity.MEDIUM)
    change_percent  = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    previous_value  = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    current_value   = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    root_cause      = models.TextField(blank=True)
    suggested_action = models.TextField(blank=True)
    status          = models.CharField(max_length=20, choices=AlertStatus.choices, default=AlertStatus.OPEN)
    acknowledged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acknowledged_alerts',
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payroll_audit_alert'
        ordering = ['-created_at', 'severity']
        indexes  = [
            models.Index(fields=['payroll_run', 'status']),
            models.Index(fields=['payroll_run', 'alert_type']),
        ]

    def __str__(self):
        return f'{self.get_alert_type_display()} [{self.severity}] — run {self.payroll_run_id}'


# ─────────────────────────────────────────────────────────────────────────────
# 3. ProjectCostAllocation
# ─────────────────────────────────────────────────────────────────────────────

class ProjectCostAllocation(models.Model):
    """
    Maps a fraction of a SalarySlip's cost to a project / cost center.
    Supports multi-project allocation per employee per period.
    allocation_percent values for one slip should sum to 100.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_slip = models.ForeignKey(
        SalarySlip,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='cost_allocations',
    )
    project_code   = models.CharField(max_length=64)
    project_name   = models.CharField(max_length=255, blank=True)
    cost_center    = models.CharField(max_length=64, blank=True)
    allocated_hours  = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    allocation_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text='Percentage of salary allocated to this project (0–100)',
    )
    allocated_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    currency       = models.CharField(max_length=3, default='AED')
    month          = models.PositiveSmallIntegerField()
    year           = models.PositiveSmallIntegerField()
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_project_cost_allocation'
        ordering = ['year', 'month', 'project_code']
        indexes  = [
            models.Index(fields=['project_code', 'year', 'month']),
            models.Index(fields=['salary_slip']),
        ]

    def __str__(self):
        return f'{self.project_code} — {self.allocation_percent}% — {self.month}/{self.year}'


# ─────────────────────────────────────────────────────────────────────────────
# 4. AIInsightSnapshot
# ─────────────────────────────────────────────────────────────────────────────

class AIInsightSnapshot(models.Model):
    """
    Cached result of a rule-based AI insight computation for one employee,
    one month/year, one insight type. Expires after TTL so the client knows
    to trigger a refresh.  The intelligence is computed in the frontend rule
    engine and POSTed here for persistence and manager visibility.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.CASCADE,
        related_name='ai_insights',
    )
    insight_type = models.CharField(max_length=32, choices=InsightType.choices)
    severity     = models.CharField(max_length=20, choices=Severity.choices, default=Severity.INFO)
    title        = models.CharField(max_length=255)
    description  = models.TextField()
    # Optional numeric value (e.g. risk score 0-100, hours, percentage)
    value        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    metadata     = models.JSONField(default=dict, blank=True)
    month        = models.PositiveSmallIntegerField()
    year         = models.PositiveSmallIntegerField()
    computed_at  = models.DateTimeField(auto_now_add=True)
    expires_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payroll_ai_insight_snapshot'
        unique_together = ('employee_salary_info', 'month', 'year', 'insight_type')
        ordering = ['-year', '-month', 'insight_type']
        indexes  = [
            models.Index(fields=['employee_salary_info', 'year', 'month']),
        ]

    def __str__(self):
        return f'{self.get_insight_type_display()} — {self.employee_salary_info_id} {self.month}/{self.year}'


# ─────────────────────────────────────────────────────────────────────────────
# 5. ChatbotMessage
# ─────────────────────────────────────────────────────────────────────────────

class ChatbotMessage(models.Model):
    """
    Payroll AI Assistant conversation history.
    Grouped by session_id (UUID generated client-side per conversation).
    Persona controls which data scope is available to the rule engine.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payroll_chat_messages')
    session_id = models.UUIDField(db_index=True)
    role       = models.CharField(max_length=16, choices=ChatRole.choices)
    content    = models.TextField()
    intent     = models.CharField(max_length=64, blank=True, help_text='Matched intent key from rule engine')
    data_payload = models.JSONField(default=dict, blank=True, help_text='Structured data returned with response')
    persona    = models.CharField(max_length=16, choices=ChatPersona.choices, default=ChatPersona.EMPLOYEE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payroll_chatbot_message'
        ordering = ['session_id', 'created_at']
        indexes  = [
            models.Index(fields=['user', 'session_id']),
            models.Index(fields=['session_id', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.role}] {self.content[:60]}'


# ─────────────────────────────────────────────────────────────────────────────
# 6. EmployeeLeaveRecord  — annual summary per employee per year
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeLeaveRecord(models.Model):
    """
    One row per employee per year imported from the HR leave Excel.
    Stores: employee identity, annual entitlement, and year-to-date totals.
    Uniqueness is on (employee_code, year) so re-importing updates in place.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Employee identity — kept as plain text because these employees may not
    # all have RAD AI user accounts.
    employee_code       = models.CharField(max_length=30, null=True, blank=True, db_index=True)
    employee_name       = models.CharField(max_length=255, db_index=True)
    department          = models.CharField(max_length=100, null=True, blank=True)
    job_title           = models.CharField(max_length=255, null=True, blank=True)
    joining_date        = models.DateField(null=True, blank=True)

    # Leave entitlement — configurable per company policy
    annual_entitlement  = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('22'))

    # Year this record covers
    year                = models.PositiveSmallIntegerField(default=2026)

    # Year-to-date aggregates (from the "Total Leave Balance" row in the Excel)
    total_earned        = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    total_taken         = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    total_encashed      = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))
    leave_balance       = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))

    # Carryforward from previous year
    carryforward        = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0'))

    # Branch / legal entity — RAD = Rejlers AB; RIN = Rejlers IN
    BRANCH_CHOICES      = [('RAD', 'Rejlers AB'), ('RIN', 'Rejlers IN')]
    branch              = models.CharField(
        max_length=10, choices=BRANCH_CHOICES, default='RAD', db_index=True,
    )

    # Source tracking
    source_file         = models.CharField(max_length=500, blank=True)
    imported_at         = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'payroll_employee_leave_record'
        unique_together = ('employee_code', 'year')
        ordering        = ['employee_name']
        indexes         = [
            models.Index(fields=['year', 'department']),
            models.Index(fields=['employee_code']),
        ]

    def __str__(self):
        return f'{self.employee_name} ({self.year}) — bal:{self.leave_balance}'

    def save(self, *args, **kwargs):
        # Normalise identity fields so lookups against timesheet data match.
        if self.employee_code is not None:
            self.employee_code = norm_code(self.employee_code) or None
        self.employee_name = norm_name(self.employee_name)
        super().save(*args, **kwargs)

# ─────────────────────────────────────────────────────────────────────────────
# 7. EmployeeLeaveMonthly  — per-month breakdown
# ─────────────────────────────────────────────────────────────────────────────
MONTH_CHOICES = [
    (1,'January'),(2,'February'),(3,'March'),(4,'April'),
    (5,'May'),(6,'June'),(7,'July'),(8,'August'),
    (9,'September'),(10,'October'),(11,'November'),(12,'December'),
]

class EmployeeLeaveMonthly(models.Model):
    """
    Monthly leave breakdown for one EmployeeLeaveRecord.
    One row per (record, month).
    """
    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record   = models.ForeignKey(
        EmployeeLeaveRecord,
        on_delete=models.CASCADE,
        related_name='monthly_breakdown',
    )
    month           = models.PositiveSmallIntegerField(choices=MONTH_CHOICES)
    earned          = models.DecimalField(max_digits=8,  decimal_places=4, default=Decimal('0'))
    taken           = models.DecimalField(max_digits=8,  decimal_places=4, default=Decimal('0'))
    encashed        = models.DecimalField(max_digits=8,  decimal_places=4, default=Decimal('0'))
    encashment_pay  = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'),
                          help_text='Monetary value of encashed days (days × daily_rate)')
    balance         = models.DecimalField(max_digits=8,  decimal_places=4, default=Decimal('0'))

    class Meta:
        db_table        = 'payroll_employee_leave_monthly'
        unique_together = ('record', 'month')
        ordering        = ['record', 'month']

    def __str__(self):
        return f'{self.record.employee_name} — {self.get_month_display()} {self.record.year}'


# ─────────────────────────────────────────────────────────────────────────────
# 7.1 MonthlyLeaveAccrualLog  — execution history for automated accruals
# ─────────────────────────────────────────────────────────────────────────────

class MonthlyLeaveAccrualLog(models.Model):
    """
    Tracks automated monthly leave accrual executions.
    One record per successful run — prevents duplicate processing.
    
    The scheduled task runs on the 1st of each month at 00:05 AM and:
      • Creates EmployeeLeaveMonthly records with earned=1.83 days
      • Updates existing records if needed
      • Logs execution here to prevent re-running
    
    Soft-coded values from leave_accrual.py:
      • MONTHLY_LEAVE_ACCRUAL (1.8333... days)
      • ANNUAL_LEAVE_DAYS (22 days)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Target period
    year = models.PositiveIntegerField(help_text='Year processed (e.g., 2026)')
    month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES,
        help_text='Month processed (1=January, 12=December)'
    )
    
    # Execution metadata
    executed_at = models.DateTimeField(auto_now_add=True, help_text='Timestamp of execution')
    triggered_by = models.CharField(
        max_length=50,
        default='celery_beat',
        help_text='Source: celery_beat (auto) | manual (admin) | api (HR Manager)'
    )
    
    # Results
    records_processed = models.PositiveIntegerField(default=0, help_text='Total employee records examined')
    records_created = models.PositiveIntegerField(default=0, help_text='New monthly records created')
    records_updated = models.PositiveIntegerField(default=0, help_text='Existing records updated')
    
    # Config snapshot (for audit trail)
    monthly_accrual_used = models.DecimalField(
        max_digits=8, decimal_places=4, default=Decimal('1.8333'),
        help_text='Monthly accrual value used for this run'
    )
    branch_filter = models.CharField(
        max_length=10, blank=True, null=True,
        help_text='Branch code filter (null = all branches)'
    )
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        default='success',
        choices=[
            ('success', 'Success'),
            ('partial', 'Partial Success'),
            ('failed', 'Failed'),
        ],
        help_text='Execution outcome'
    )
    error_message = models.TextField(blank=True, help_text='Error details if status=failed')
    
    class Meta:
        db_table = 'payroll_monthly_leave_accrual_log'
        ordering = ['-executed_at']
        indexes = [
            models.Index(fields=['year', 'month']),
            models.Index(fields=['executed_at']),
        ]
        # Prevent duplicate runs for same month (soft constraint - checked in task)
        unique_together = ('year', 'month', 'triggered_by')
    
    def __str__(self):
        return f'{MONTH_CHOICES[self.month-1][1]} {self.year} — {self.status} ({self.records_created} created)'


# ─────────────────────────────────────────────────────────────────────────────
# 7.2 LeaveEncashmentRun  — audit log for HR-triggered monthly encashment runs
# ─────────────────────────────────────────────────────────────────────────────

class LeaveEncashmentRun(models.Model):
    """
    One record per HR-triggered encashment run for a given (year, month).
    Unique on (year, month) — only one encashment run allowed per period.

    Encashment formula (soft-coded in services/leave_encashment.py):
      encashment_pay = days_encashed × (monthly_salary ÷ ENCASHMENT_WORKING_DAYS)
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    year             = models.PositiveIntegerField()
    month            = models.PositiveSmallIntegerField(choices=MONTH_CHOICES)
    triggered_by     = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='encashment_runs',
        help_text='HR Manager who triggered this run',
    )
    executed_at      = models.DateTimeField(auto_now_add=True)
    status           = models.CharField(
        max_length=20, default='success',
        choices=[('success','Success'),('partial','Partial'),('failed','Failed')],
    )
    records_processed    = models.PositiveIntegerField(default=0)
    total_days_encashed  = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('0'),
                               help_text='Sum of all encashed days across all employees')
    total_pay            = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'),
                               help_text='Sum of all encashment pay amounts (AED)')
    missing_salaries     = models.JSONField(default=list, blank=True,
                               help_text='employee_codes with no salary on record (encashment_pay=0)')
    branch_filter        = models.CharField(max_length=10, blank=True, null=True)
    notes                = models.TextField(blank=True)

    class Meta:
        db_table        = 'payroll_leave_encashment_run'
        ordering        = ['-year', '-month']
        unique_together = ('year', 'month')
        indexes         = [models.Index(fields=['year', 'month'])]

    def __str__(self):
        return f'Encashment {MONTH_CHOICES[self.month-1][1]} {self.year} — {self.status}'


# ─────────────────────────────────────────────────────────────────────────────
# 8. LeaveType  — master list of leave type codes
# ─────────────────────────────────────────────────────────────────────────────

class LeaveCategory(models.TextChoices):
    """
    Canonical category keys — must match ESS_LEAVE_TYPE_CONFIG keys in
    frontend/src/config/hrLeave.config.js so the ESS portal can filter
    enabled leave types without hardcoded code comparisons.
    """
    ANNUAL         = 'annual',         'Annual Leave'
    SICK           = 'sick',           'Sick Leave'
    EMERGENCY      = 'emergency',      'Emergency Leave'
    UNPAID         = 'unpaid',         'Unpaid Leave'
    MATERNITY      = 'maternity',      'Maternity Leave'
    PATERNITY      = 'paternity',      'Paternity Leave'
    COMPENSATORY   = 'compensatory',   'Compensatory Leave'
    PUBLIC_HOLIDAY = 'public_holiday', 'Public Holiday'
    WORK_OFF       = 'work_off',       'Work Off'
    OTHER          = 'other',          'Other'


class LeaveType(models.Model):
    """
    Master list of leave type codes.  Seeded with defaults in migration 0003;
    HR admins can add custom types via the Django admin without code changes.
    The Tailwind badge classes are stored as plain strings so PurgeCSS on the
    backend does not strip them; the frontend reads them directly from the API.
    """
    code              = models.CharField(max_length=10, unique=True, db_index=True)
    name              = models.CharField(max_length=100)
    color_hex         = models.CharField(
        max_length=7, default='#6b7280',
        help_text='Hex colour for charts and calendar heatmap',
    )
    # Tailwind utility classes — stored as strings (safe from PurgeCSS)
    badge_bg          = models.CharField(max_length=60, blank=True, default='bg-slate-100')
    badge_text        = models.CharField(max_length=60, blank=True, default='text-slate-700')
    badge_border      = models.CharField(max_length=60, blank=True, default='border-slate-300')
    # Category — maps to ESS_LEAVE_TYPE_CONFIG keys in hrLeave.config.js
    category          = models.CharField(
        max_length=20, choices=LeaveCategory.choices,
        default=LeaveCategory.OTHER, blank=True, db_index=True,
        help_text='Canonical category key — must match ESS_LEAVE_TYPE_CONFIG key in hrLeave.config.js',
    )
    # Policy flags
    is_paid           = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=True)
    requires_document = models.BooleanField(default=False)
    is_active         = models.BooleanField(default=True)
    display_order     = models.PositiveSmallIntegerField(default=99)

    class Meta:
        db_table = 'payroll_leave_type'
        ordering = ['display_order', 'code']

    def __str__(self):
        return f'{self.code} — {self.name}'


# ─────────────────────────────────────────────────────────────────────────────
# 9. LeaveRequest  — individual employee leave application
# ─────────────────────────────────────────────────────────────────────────────

class LeaveRequestStatus(models.TextChoices):
    PENDING     = 'PENDING',     'Pending'           # Waiting for Reporting Manager
    RM_APPROVED = 'RM_APPROVED', 'Pending HR Approval'  # RM approved → waiting HR
    RM_REJECTED = 'RM_REJECTED', 'Rejected by Manager'  # RM rejected
    APPROVED    = 'APPROVED',    'Approved'           # HR final approval
    REJECTED    = 'REJECTED',    'Rejected'           # HR final rejection
    CANCELLED   = 'CANCELLED',   'Cancelled'


class LeaveRequest(models.Model):
    """
    One leave application covering a contiguous date range.
    Supports both RAD AI system users (employee FK) and non-system employees
    (employee_code / employee_name plain text).  HR can submit on behalf of
    any employee by supplying employee_code + employee_name directly.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Employee identity — RAD AI user account (optional)
    employee         = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leave_requests',
    )
    # Denormalised fields — auto-populated from the User FK on save; also
    # allow HR to create requests for employees without RAD AI accounts.
    employee_code    = models.CharField(max_length=30, null=True, blank=True, db_index=True)
    employee_name    = models.CharField(max_length=255, db_index=True)
    department       = models.CharField(max_length=100, blank=True)

    # Leave details
    leave_type       = models.ForeignKey(
        LeaveType, on_delete=models.PROTECT, related_name='requests',
    )
    start_date       = models.DateField()
    end_date         = models.DateField()
    # Computed Mon–Fri count stored for fast balance checks
    days_requested   = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('0'))
    reason           = models.TextField(blank=True)

    # Approval workflow
    status           = models.CharField(
        max_length=20, choices=LeaveRequestStatus.choices,
        default=LeaveRequestStatus.PENDING, db_index=True,
    )
    reviewed_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_leave_requests',
    )
    reviewed_at      = models.DateTimeField(null=True, blank=True)
    reviewer_note    = models.TextField(blank=True)

    # Reporting Manager (Stage 1) approval
    rm_reviewed_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='rm_reviewed_leave_requests',
    )
    rm_reviewed_at   = models.DateTimeField(null=True, blank=True)
    rm_note          = models.TextField(blank=True)

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_leave_request'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['employee_code', 'start_date', 'end_date'],
                         name='payroll_lr_code_dates_idx'),
            models.Index(fields=['status', 'start_date'],
                         name='payroll_lr_status_date_idx'),
        ]

    def __str__(self):
        lt = getattr(self.leave_type, 'code', self.leave_type_id or '?')
        return (
            f'{self.employee_name} · {lt} · '
            f'{self.start_date}→{self.end_date} [{self.status}]'
        )

    def save(self, *args, **kwargs):
        import datetime as _dt
        # Normalise identity fields so lookups and dedup work cross-table.
        if self.employee_code:
            self.employee_code = norm_code(self.employee_code) or None
        if self.employee_name:
            self.employee_name = norm_name(self.employee_name)
        # Auto-populate identity fields from the User FK
        if self.employee:
            u = self.employee
            if not self.employee_name:
                self.employee_name = (
                    f'{u.first_name} {u.last_name}'.strip() or u.email
                )
            if not self.employee_code or not self.department:
                try:
                    from apps.rbac.models import UserProfile
                    p = UserProfile.objects.filter(
                        user=u, is_deleted=False,
                    ).first()
                    if p:
                        if not self.employee_code and p.employee_id:
                            self.employee_code = str(p.employee_id)
                        if not self.department and getattr(p, 'department', None):
                            self.department = p.department
                except Exception:
                    pass
        # Compute days_requested — working days (Mon–Fri) only
        if self.start_date and self.end_date:
            days = 0
            cur = self.start_date
            while cur <= self.end_date:
                if cur.weekday() < 5:
                    days += 1
                cur += _dt.timedelta(days=1)
            self.days_requested = Decimal(str(days))
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# PublicHoliday — Abu Dhabi / UAE calendar (editable by HR Manager)
# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded region choices — add new regions without migrations (label-only).
REGION_CHOICES = [
    ('AE-AZ', 'Abu Dhabi (UAE)'),
    ('AE',    'UAE-wide'),
    ('SA',    'Saudi Arabia'),
    ('QA',    'Qatar'),
    ('KW',    'Kuwait'),
    ('BH',    'Bahrain'),
    ('OM',    'Oman'),
    ('COMPANY', 'Company-specific'),
]

# Source choices — whether the holiday was seeded from official calendar or
# added manually by HR (important for audit trail).
HOLIDAY_SOURCE_CHOICES = [
    ('government',  'Abu Dhabi Government Official Calendar'),
    ('hr_added',    'Added by HR Manager'),
]


class PublicHoliday(models.Model):
    """
    Public holiday calendar entry.

    Seeded with official Abu Dhabi / UAE government holidays for the current
    and next year.  HR Managers can add, edit, and deactivate individual entries
    without affecting the seed data (which is re-applied only when the `seed`
    management command is run with `--force`).

    Displayed on the Summary tab as a colour-coded column overlay so that
    HR can see at a glance why an employee shows absent on a given day.
    """
    date        = models.DateField(db_index=True)
    name        = models.CharField(
        max_length=255,
        help_text='Official name in English, e.g. "UAE National Day".',
    )
    name_ar     = models.CharField(
        max_length=255,
        blank=True,
        help_text='Arabic name (optional — for display purposes only).',
    )
    region      = models.CharField(
        max_length=20,
        choices=REGION_CHOICES,
        default='AE-AZ',
        db_index=True,
        help_text='Geographic scope of this holiday.',
    )
    source      = models.CharField(
        max_length=20,
        choices=HOLIDAY_SOURCE_CHOICES,
        default='government',
        help_text='Whether this entry came from the official calendar or was added by HR.',
    )
    # HR-editable note — does not affect logic
    note        = models.TextField(
        blank=True,
        help_text='HR note, e.g. confirmed date, subject to moon sighting, etc.',
    )
    is_active   = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Deactivate without deleting — keeps audit trail intact.',
    )
    created_by  = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_holidays',
    )
    updated_by  = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_holidays',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table   = 'payroll_public_holiday'
        ordering   = ['date']
        unique_together = [('date', 'region')]
        indexes = [
            models.Index(fields=['date', 'is_active'], name='payroll_ph_date_active_idx'),
        ]

    def __str__(self):
        return f'{self.date}  {self.name} [{self.region}]'


# ─────────────────────────────────────────────────────────────────────────────
# AttendanceOverride — HR Manager manual correction of a single day cell
# ─────────────────────────────────────────────────────────────────────────────
# Reason choices — why the biometric value is being overridden.
OVERRIDE_REASON_CHOICES = [
    ('biometric_error',   'Biometric device error'),
    ('system_outage',     'System / network outage'),
    ('forgot_punch',      'Employee forgot to punch'),
    ('site_visit',        'On-site client visit (no biometric access)'),
    ('wfh',               'Work from home (WFH approved)'),
    ('travel',            'Business travel'),
    ('training',          'Approved external training'),
    ('hr_correction',     'HR administrative correction'),
    ('other',             'Other (see note)'),
]


class AttendanceOverride(models.Model):
    """
    HR Manager manual override for a single employee × date attendance record.

    When a cell in the Summary pivot table has an incorrect biometric value
    (device error, forgotten punch, WFH day, site visit, etc.) HR can record
    the corrected hours and reason here.

    The frontend overlay logic: if an override exists for (employee_code, date),
    display the override_hours instead of the biometric value, and show a
    visual indicator (pencil icon) so the correction is transparent.

    Overrides are NEVER deleted — they are deactivated to maintain an audit
    trail.  The `is_active` flag controls which override is currently applied;
    only the most-recent active record for a given (employee_code, date) is used.
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_code = models.CharField(max_length=30, db_index=True)
    employee_name = models.CharField(max_length=255, blank=True)
    date          = models.DateField(db_index=True)

    # Original biometric value at the time of override (stored for audit).
    original_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Biometric hours recorded before this correction.',
    )
    # HR-corrected value.
    override_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text='Corrected hours to display instead of the biometric value.',
    )
    reason        = models.CharField(
        max_length=30,
        choices=OVERRIDE_REASON_CHOICES,
        default='hr_correction',
    )
    note          = models.TextField(
        blank=True,
        help_text='Free-text HR note explaining the correction.',
    )
    is_active     = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Only the most-recent active override for a given (employee_code, date) is applied.',
    )
    created_by    = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attendance_overrides_created',
    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_attendance_override'
        ordering = ['-created_at']
        indexes  = [
            models.Index(
                fields=['employee_code', 'date', 'is_active'],
                name='payroll_ao_cd_active_idx',
            ),
        ]

    def __str__(self):
        return (
            f'{self.employee_name or self.employee_code}  '
            f'{self.date}  {self.original_hours}->{self.override_hours}h'
        )


# =============================================================================
# Salary Management -- TextChoices
# =============================================================================

class SalaryComponentCategory(models.TextChoices):
    ALLOWANCE = 'allowance', 'Allowance'
    DEDUCTION = 'deduction', 'Deduction'
    GROSS     = 'gross',     'Gross Component'


class SalaryStructureStatus(models.TextChoices):
    DRAFT            = 'DRAFT',            'Draft'
    PENDING_APPROVAL = 'PENDING_APPROVAL', 'Pending Approval'
    APPROVED         = 'APPROVED',         'Approved'
    REJECTED         = 'REJECTED',         'Rejected'


CURRENCY_SALARY_CHOICES = [
    ('AED', 'UAE Dirham (AED)'),
    ('USD', 'US Dollar (USD)'),
    ('EUR', 'Euro (EUR)'),
    ('SAR', 'Saudi Riyal (SAR)'),
    ('GBP', 'British Pound (GBP)'),
    ('INR', 'Indian Rupee (INR)'),
]


# =============================================================================
# 12. SalaryComponent -- master catalogue of reusable salary components
# =============================================================================

class SalaryComponent(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code        = models.CharField(max_length=30, unique=True, db_index=True)
    name        = models.CharField(max_length=100)
    category    = models.CharField(
        max_length=20,
        choices=SalaryComponentCategory.choices,
        default=SalaryComponentCategory.ALLOWANCE,
    )
    is_taxable  = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    is_active   = models.BooleanField(default=True)
    created_by  = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_salary_components',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_salary_component'
        ordering = ['category', 'code']

    def __str__(self):
        return f'{self.code} -- {self.name} ({self.category})'


# =============================================================================
# 13. EmployeeSalaryStructure -- per-employee salary with approval workflow
# =============================================================================

class EmployeeSalaryStructure(models.Model):
    """
    DRAFT -> PENDING_APPROVAL -> APPROVED | REJECTED

    components: [{"code":"HRA","name":"Housing Allowance","category":"allowance","amount":"3000.00"}]
    Approval auto-deactivates the prior active structure and writes a SalaryHistory row.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_code    = models.CharField(max_length=30, db_index=True)
    employee_name    = models.CharField(max_length=255, db_index=True)
    department       = models.CharField(max_length=100, blank=True)
    effective_date   = models.DateField()
    currency         = models.CharField(max_length=3, choices=CURRENCY_SALARY_CHOICES, default='AED')
    basic_salary     = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    components       = models.JSONField(default=list, blank=True)
    total_gross      = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    net_salary       = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    status           = models.CharField(
        max_length=20, choices=SalaryStructureStatus.choices,
        default=SalaryStructureStatus.DRAFT, db_index=True,
    )
    submitted_by     = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='submitted_salary_structures',
    )
    submitted_at     = models.DateTimeField(null=True, blank=True)
    reviewed_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_salary_structures',
    )
    reviewed_at      = models.DateTimeField(null=True, blank=True)
    reviewer_note    = models.TextField(blank=True)
    is_active        = models.BooleanField(default=True, db_index=True)
    superseded_by    = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supersedes',
    )
    created_by       = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_salary_structures',
    )
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_salary_structure'
        ordering = ['-effective_date', '-created_at']
        indexes  = [
            models.Index(fields=['employee_code', 'status'],    name='payroll_ss_code_status'),
            models.Index(fields=['employee_code', 'is_active'], name='payroll_ss_code_active'),
        ]

    def _recompute_totals(self):
        gross      = Decimal(str(self.basic_salary or 0))
        deductions = Decimal('0')
        for c in (self.components or []):
            amt = Decimal(str(c.get('amount', 0) or 0))
            cat = c.get('category', '')
            if cat in ('allowance', 'gross'):
                gross += amt
            elif cat == 'deduction':
                deductions += amt
        self.total_gross      = gross
        self.total_deductions = deductions
        self.net_salary       = gross - deductions

    def save(self, *args, **kwargs):
        self._recompute_totals()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.employee_name} ({self.employee_code}) eff {self.effective_date} [{self.status}]'


# =============================================================================
# 14. SalaryHistory -- immutable audit trail of every approval
# =============================================================================

class SalaryHistory(models.Model):
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_code  = models.CharField(max_length=30, db_index=True)
    employee_name  = models.CharField(max_length=255)
    change_date    = models.DateField()
    previous_basic = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    new_basic      = models.DecimalField(max_digits=14, decimal_places=2)
    previous_net   = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    new_net        = models.DecimalField(max_digits=14, decimal_places=2)
    change_percent = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    change_reason  = models.TextField(blank=True)
    structure      = models.ForeignKey(
        EmployeeSalaryStructure,
        on_delete=models.CASCADE,
        related_name='history_entries',
    )
    approved_by    = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_salary_histories',
    )
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payroll_salary_history'
        ordering = ['-change_date', '-created_at']
        indexes  = [
            models.Index(fields=['employee_code', 'change_date'], name='payroll_sh_code_date'),
        ]

    def __str__(self):
        return f'{self.employee_name} ({self.employee_code}) {self.change_date} net {self.previous_net}->{self.new_net}'


# ─────────────────────────────────────────────────────────────────────────────
# 12. DailyWorkLog — individual employee daily activity tracker
#     Stored in PostgreSQL; optionally exported to AWS S3 as JSON archive.
# ─────────────────────────────────────────────────────────────────────────────

class DailyWorkLogPriority(models.TextChoices):
    LOW      = 'low',      'Low'
    MEDIUM   = 'medium',   'Medium'
    HIGH     = 'high',     'High'
    CRITICAL = 'critical', 'Critical'


class DailyWorkLogStatus(models.TextChoices):
    IN_PROGRESS = 'in_progress', 'In Progress'
    DONE        = 'done',        'Done'
    BLOCKED     = 'blocked',     'Blocked'
    DEFERRED    = 'deferred',    'Deferred'


class DailyWorkLogApprovalStatus(models.TextChoices):
    PENDING  = 'pending',  'Pending Approval'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class DailyWorkLogSubmitTo(models.TextChoices):
    PROJECT_MANAGER   = 'project_manager',   'Project Manager'
    REPORTING_MANAGER = 'reporting_manager', 'Reporting Manager'


class DailyWorkLog(models.Model):
    """
    One work activity entry per user per day.

    Personal scope — each user owns their own entries.  HR/staff users may
    query other users' logs via the `?user_id=` or `?all=true` params on the
    ViewSet.  S3 export is user-triggered: the ViewSet action serialises a
    date range to JSON, uploads to S3 under
    ``daily-tracker/{user_id}/{YYYY}/{MM}/export_{timestamp}.json`` and
    returns a 1-hour presigned URL.

    Monetary fields are intentionally absent; ``hours_spent`` is Decimal for
    precision without floating-point artefacts.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user             = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='daily_work_logs',
        db_index=True,
    )

    log_date         = models.DateField(db_index=True)
    task_title       = models.CharField(max_length=255)
    project_category = models.CharField(max_length=100, blank=True)

    # Decimal — never float per global coding rules
    hours_spent      = models.DecimalField(max_digits=4, decimal_places=2)

    priority         = models.CharField(
        max_length=10,
        choices=DailyWorkLogPriority.choices,
        default=DailyWorkLogPriority.MEDIUM,
        db_index=True,
    )
    status           = models.CharField(
        max_length=15,
        choices=DailyWorkLogStatus.choices,
        default=DailyWorkLogStatus.IN_PROGRESS,
        db_index=True,
    )
    notes            = models.TextField(blank=True)

    # Populated once the user exports this entry to S3 — blank until then.
    s3_export_key    = models.CharField(max_length=500, blank=True)

    # ── Approval workflow ─────────────────────────────────────────────────
    # New logs start as PENDING; manager or HR staff approves / rejects.
    # Approved logs feed into project cost allocations and payroll KPIs.
    approval_status  = models.CharField(
        max_length=10,
        choices=DailyWorkLogApprovalStatus.choices,
        default=DailyWorkLogApprovalStatus.PENDING,
        db_index=True,
    )
    approved_by      = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_work_logs',
    )
    approved_at      = models.DateTimeField(null=True, blank=True)
    approval_note    = models.TextField(blank=True)

    # Which manager role the employee routed this entry to for approval.
    # Blank = not specified (legacy entries / optional selection).
    submitted_to_role = models.CharField(
        max_length=20,
        choices=DailyWorkLogSubmitTo.choices,
        blank=True,
        default='',
        help_text='Manager role type the employee directed this log to.',
    )

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_daily_work_log'
        ordering = ['-log_date', '-created_at']
        indexes  = [
            models.Index(fields=['user', 'log_date'],       name='payroll_dwl_user_date'),
            models.Index(fields=['log_date'],               name='payroll_dwl_date'),
            models.Index(fields=['status'],                 name='payroll_dwl_status'),
            models.Index(fields=['approval_status'],        name='payroll_dwl_approval'),
        ]

    def __str__(self):
        return f'{self.user_id} | {self.log_date} | {self.task_title[:40]} [{self.status}]'


# =============================================================================
# 13. MasterPayrollImport — one record per Sympa+ValueFrame generation session
#     Stores metadata + aggregate stats.  Individual employee rows are in
#     MasterPayrollRow.  The generated Excel is uploaded to S3 asynchronously
#     via a Celery task; s3_key is populated once the upload completes.
# =============================================================================

class MasterPayrollImportStatus(models.TextChoices):
    PROCESSING = 'processing', 'Processing'
    READY      = 'ready',      'Ready'
    UPLOADED   = 'uploaded',   'Uploaded to S3'
    FAILED     = 'failed',     'Failed'


# Soft-coded workflow stage progression (order matters for guard checks)
# draft → frozen → hr_approved → finance_review → finance_approved → released
class MasterPayrollWorkflowStage(models.TextChoices):
    DRAFT            = 'draft',            'Draft — HR Editing'
    FROZEN           = 'frozen',           'Frozen — Awaiting HR Approval'
    HR_APPROVED      = 'hr_approved',      'HR Approved — Finance Review'
    FINANCE_REVIEW   = 'finance_review',   'Finance Review — In Progress'
    FINANCE_APPROVED = 'finance_approved', 'Finance Approved — Awaiting Release'
    RELEASED         = 'released',         'Released — Salary Disbursed'


# Ordered list for stage-progression guard
WORKFLOW_STAGE_ORDER = [
    MasterPayrollWorkflowStage.DRAFT,
    MasterPayrollWorkflowStage.FROZEN,
    MasterPayrollWorkflowStage.HR_APPROVED,
    MasterPayrollWorkflowStage.FINANCE_REVIEW,
    MasterPayrollWorkflowStage.FINANCE_APPROVED,
    MasterPayrollWorkflowStage.RELEASED,
]


class MasterPayrollImport(models.Model):
    """
    One import session = one HR master payroll generation.
    Created synchronously when the HR manager clicks 'Generate Master'.
    S3 upload happens asynchronously (Celery task updates s3_key).

    Workflow: draft → frozen (HR locks) → hr_approved → finance_review
              → finance_approved → released
    Only the super-admin email (PAYROLL_WORKFLOW_SUPERADMIN_EMAIL in settings)
    can unfreeze a frozen/approved record.
    """
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    year           = models.PositiveSmallIntegerField(db_index=True)
    month          = models.PositiveSmallIntegerField(db_index=True)
    generated_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='master_payroll_imports',
    )
    generated_at   = models.DateTimeField(auto_now_add=True)

    # Source file names (informational — actual files are not stored locally)
    sympa_filename      = models.CharField(max_length=255, blank=True)
    valueframe_filename = models.CharField(max_length=255, blank=True)
    other_filename      = models.CharField(max_length=255, blank=True)

    # S3 key for the generated Excel — blank until Celery task completes
    s3_key         = models.CharField(max_length=500, blank=True)
    status         = models.CharField(
        max_length=20, choices=MasterPayrollImportStatus.choices,
        default=MasterPayrollImportStatus.PROCESSING, db_index=True,
    )

    # Aggregate stats (sympa_rows, vf_employees, radai_rows, matched)
    stats          = models.JSONField(default=dict, blank=True)
    # Any merge warnings generated during processing
    warnings       = models.JSONField(default=list, blank=True)
    # Total employee rows saved
    total_rows     = models.PositiveIntegerField(default=0)

    # ── Approval Workflow (soft-coded stage machine) ──────────────────────────
    workflow_stage = models.CharField(
        max_length=20,
        choices=MasterPayrollWorkflowStage.choices,
        default=MasterPayrollWorkflowStage.DRAFT,
        db_index=True,
    )
    # Freeze metadata
    frozen_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='frozen_master_payrolls',
    )
    frozen_at      = models.DateTimeField(null=True, blank=True)
    # HR approval
    hr_approved_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='hr_approved_master_payrolls',
    )
    hr_approved_at   = models.DateTimeField(null=True, blank=True)
    hr_approval_note = models.TextField(blank=True)
    # Finance approval
    finance_approved_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='finance_approved_master_payrolls',
    )
    finance_approved_at   = models.DateTimeField(null=True, blank=True)
    finance_approval_note = models.TextField(blank=True)
    # Salary release
    released_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='released_master_payrolls',
    )
    released_at   = models.DateTimeField(null=True, blank=True)
    release_note  = models.TextField(blank=True)

    class Meta:
        db_table = 'payroll_master_import'
        ordering = ['-year', '-month', '-generated_at']
        indexes  = [
            models.Index(fields=['year', 'month'], name='payroll_mi_year_month'),
            models.Index(fields=['generated_by'],  name='payroll_mi_generated_by'),
            models.Index(fields=['status'],        name='payroll_mi_status'),
            models.Index(fields=['workflow_stage'], name='payroll_mi_workflow_stage'),
        ]

    def __str__(self):
        return f'MasterPayroll {self.year}-{self.month:02d} by {self.generated_by_id} [{self.status}]'

    @property
    def is_editable_by_hr(self):
        """HR can only edit rows when in draft stage."""
        return self.workflow_stage == MasterPayrollWorkflowStage.DRAFT

    def s3_url(self):
        """Return a presigned download URL if the file has been uploaded."""
        if not self.s3_key:
            return None
        try:
            from apps.payroll.storage import PayrollExportStorage, S3_AVAILABLE
            if not S3_AVAILABLE:
                return None
            storage = PayrollExportStorage()
            return storage.url(self.s3_key.split(f'{storage.location}/', 1)[-1])
        except Exception:
            return None


# =============================================================================
# 14. MasterPayrollRow — one employee row per MasterPayrollImport session
# =============================================================================

class MasterPayrollRow(models.Model):
    """
    One row per employee per MasterPayrollImport session.
    Mirrors exactly the 15-column master output so the data can be queried,
    diffed across months, and fed into future automation pipelines.
    All monetary values use Decimal (never float).
    """
    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    import_session  = models.ForeignKey(
        MasterPayrollImport, on_delete=models.CASCADE, related_name='rows',
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    employee_code   = models.CharField(max_length=60, db_index=True)
    employee_name   = models.CharField(max_length=255)
    joining_date    = models.CharField(max_length=50, blank=True)   # kept as text; source may vary

    # ── Time & Attendance ─────────────────────────────────────────────────────
    total_hours     = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))

    # ── Salary Components ─────────────────────────────────────────────────────
    employee_salary     = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))   # gross
    basic_salary        = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total_allowances    = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    transport_allowance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    housing_allowance   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    other_allowances    = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    other_pay           = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))

    # ── Notes / Details ───────────────────────────────────────────────────────
    details             = models.TextField(blank=True)

    # ── Deductions ────────────────────────────────────────────────────────────
    total_deductions    = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    deduction_details   = models.TextField(blank=True)

    # ── Final ─────────────────────────────────────────────────────────────────
    final_salary            = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))

    # ── Leave Encashment (populated when HR runs monthly encashment) ──────────
    leave_encashment_days   = models.DecimalField(max_digits=6,  decimal_places=2, default=Decimal('0'),
                                  help_text='Encashed leave days for this payroll period')
    leave_encashment_pay    = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'),
                                  help_text='Monetary value of encashed leave (AED)')

    # ── Metadata (for audit / debugging) ─────────────────────────────────────
    sources     = models.JSONField(default=list, blank=True)   # ['sympa','valueframe','radai']
    row_warnings= models.JSONField(default=list, blank=True)   # per-employee merge warnings
    raw_data    = models.JSONField(default=dict, blank=True)   # full original row snapshot

    class Meta:
        db_table = 'payroll_master_row'
        ordering = ['import_session', 'employee_name']
        indexes  = [
            models.Index(fields=['import_session', 'employee_code'], name='payroll_mr_session_code'),
            models.Index(fields=['employee_code'],                   name='payroll_mr_emp_code'),
        ]
        # Prevent duplicate employee per session
        unique_together = [('import_session', 'employee_code')]

    def save(self, *args, **kwargs):
        """
        Always cascade the salary formula before persisting.
        This guarantees total_allowances, employee_salary, and final_salary
        are always consistent with their source components — even when a row
        is updated directly via the Django admin or a PATCH endpoint.

        Formula (mirrors frontend recomputeMasterRow):
          total_allowances = transport + housing + other_allowances
          employee_salary  = basic + total_allowances + other_pay
          final_salary     = max(0, employee_salary - total_deductions)
        """
        self.total_allowances = (
            self.transport_allowance + self.housing_allowance + self.other_allowances
        )
        self.employee_salary = self.basic_salary + self.total_allowances + self.other_pay
        self.final_salary    = max(Decimal('0'), self.employee_salary - self.total_deductions)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.employee_code} — {self.import_session}'


# =============================================================================
# MasterPayrollWorkflowLog — immutable audit trail of every workflow transition
# =============================================================================

class MasterPayrollWorkflowLog(models.Model):
    """
    Immutable record of every freeze / approve / release action on a
    MasterPayrollImport.  Never delete rows from this table.

    Action codes (soft-coded):
      freeze           HR Manager locks the file
      unfreeze         Superadmin reverts to draft
      hr_approve       HR Manager approves → Finance
      finance_review   Finance opens the file for modification
      finance_approve  Finance confirms → Accounts
      release          Accounts marks salary as released
    """
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    master_import  = models.ForeignKey(
        MasterPayrollImport, on_delete=models.CASCADE,
        related_name='workflow_logs',
    )
    from_stage     = models.CharField(max_length=20, blank=True)
    to_stage       = models.CharField(max_length=20)
    action         = models.CharField(max_length=30)   # freeze | unfreeze | hr_approve | …
    performed_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payroll_workflow_actions',
    )
    performed_at   = models.DateTimeField(auto_now_add=True, db_index=True)
    note           = models.TextField(blank=True)

    class Meta:
        db_table = 'payroll_workflow_log'
        ordering = ['performed_at']
        indexes  = [
            models.Index(fields=['master_import', 'performed_at'], name='payroll_wfl_import_at'),
        ]

    def __str__(self):
        actor = self.performed_by.get_full_name() if self.performed_by else 'system'
        return f'{self.action} by {actor} at {self.performed_at:%Y-%m-%d %H:%M}'


