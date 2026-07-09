"""Payroll Engine models.

Fresh schema, independent of apps.finance.salary_models. All choice
strings are sourced from apps.payroll_engine.catalog so they stay
soft-coded.
"""
from __future__ import annotations
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import catalog
from .config import DEFAULT_EMPLOYEE_HOURS


def _choices_from(catalog_list, code_key='code', label_key='label'):
    """Convert a catalog list-of-dicts into Django choices tuple."""
    return [(item[code_key], item[label_key]) for item in catalog_list]


ZERO = Decimal('0.00')


# ════════════════════════════════════════════════════════════════════
# Employee master roster
# ════════════════════════════════════════════════════════════════════
class PayrollEmployee(models.Model):
    """One row per employee on the payroll. Source of truth for the
    monthly run generator. Decoupled from auth.User so contractors and
    historical employees can also be tracked.
    """
    employee_no = models.CharField(max_length=32, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='payroll_profile',
    )

    full_name = models.CharField(max_length=255, db_index=True)
    emirates_id = models.CharField(max_length=32, blank=True, default='')
    mol_no = models.CharField(max_length=32, blank=True, default='')

    # Banking
    iban = models.CharField(max_length=64, blank=True, default='')
    bank_name = models.CharField(max_length=128, blank=True, default='')
    routing_code = models.CharField(max_length=64, blank=True, default='')

    # Org
    department = models.CharField(max_length=128, blank=True, default='', db_index=True)
    discipline = models.CharField(max_length=128, blank=True, default='')
    designation = models.CharField(max_length=128, blank=True, default='')
    grade = models.CharField(max_length=128, blank=True, default='')
    nationality_group = models.CharField(max_length=64, blank=True, default='')

    joining_date = models.DateField(null=True, blank=True)
    leaving_date = models.DateField(null=True, blank=True)

    # Contracted hours per month (soft-coded default in config.DEFAULT_EMPLOYEE_HOURS)
    hours = models.DecimalField(max_digits=8, decimal_places=2, default=DEFAULT_EMPLOYEE_HOURS)

    # Fixed earnings (monthly defaults)
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    housing = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    transport = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    home_leave = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    default_payment_mode = models.CharField(
        max_length=32,
        choices=_choices_from(catalog.PAYMENT_MODES),
        default=catalog.DEFAULT_PAYMENT_MODE,
    )

    is_active = models.BooleanField(default=True, db_index=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)

    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='payroll_employees_created',
    )

    class Meta:
        db_table = 'payroll_engine_employee'
        verbose_name = 'Payroll Employee'
        ordering = ('full_name',)
        indexes = [
            models.Index(fields=['department', 'is_active']),
            models.Index(fields=['is_active', 'employee_no']),
        ]

    def __str__(self) -> str:
        return f'{self.employee_no} — {self.full_name}'

    @property
    def default_gross(self) -> Decimal:
        return (self.basic + self.housing + self.transport + self.home_leave)


# ════════════════════════════════════════════════════════════════════
# PayrollRun (one per month)
# ════════════════════════════════════════════════════════════════════
class PayrollRun(models.Model):
    """One PayrollRun per (year, month). Aggregates Payslip rows."""
    year = models.PositiveSmallIntegerField(db_index=True)
    month = models.PositiveSmallIntegerField(db_index=True)
    cycle_code = models.CharField(max_length=10, db_index=True)  # 'YYYY-MM'

    status = models.CharField(
        max_length=32,
        choices=_choices_from(catalog.WORKFLOW_STATUSES),
        default=catalog.Status.DRAFT,
        db_index=True,
    )

    # Aggregated totals (denormalised for fast dashboards)
    employee_count = models.PositiveIntegerField(default=0)
    total_gross = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_net = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    # Sum of live biometric hours across every payslip in this run, and
    # the same expressed as days (hours ÷ HOURS_PER_WORKDAY, default 9).
    total_hours = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total_days = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)

    # Total working days in this calendar month — set by HR at generation time.
    # Defaults to catalog.DEFAULT_WORKING_DAYS_PER_MONTH (22 UAE standard).
    # Stored on the run so it is available for salary computations and the
    # Excel master export without recalculation.
    working_days_in_month = models.PositiveSmallIntegerField(
        default=22,
        help_text='Total working days in this payroll month (HR-entered at generation). Default 22.',
    )

    # How this run was created — soft-coded; drives the UI badge.
    # 'system' = generated from live employee/timesheet data via the engine
    # 'import' = generated by uploading a master Excel file
    source_type = models.CharField(
        max_length=20,
        choices=[('system', 'System Generated'), ('import', 'Imported from File')],
        default='system', db_index=True,
        help_text='Origin of this run (system-generated vs imported from Excel).',
    )

    # Count of official public holidays for this calendar month, auto-computed
    # at run generation from the PublicHoliday register (apps.payroll).
    # Region filtered by catalog.DEFAULT_PH_REGION (default: 'AE').
    # Same value for every payslip in the run — displayed in the payslip table
    # so reviewers can verify salary computations at a glance.
    public_holidays_in_month = models.PositiveSmallIntegerField(
        default=0,
        help_text='Official public holidays in this payroll month (auto-computed from PH register at generation).',
    )

    # Timestamps for each transition
    generated_at = models.DateTimeField(null=True, blank=True)
    hr_approved_at = models.DateTimeField(null=True, blank=True)
    finance_approved_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    hr_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_runs_hr_approved',
    )
    finance_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_runs_finance_approved',
    )
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_runs_released',
    )

    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_runs_created',
    )

    class Meta:
        db_table = 'payroll_engine_run'
        verbose_name = 'Payroll Run'
        ordering = ('-year', '-month')
        constraints = [
            models.UniqueConstraint(fields=['year', 'month'], name='uq_payroll_run_year_month'),
        ]

    def __str__(self) -> str:
        return f'PayrollRun {self.cycle_code} [{self.status}]'

    def save(self, *args, **kwargs):
        if not self.cycle_code:
            self.cycle_code = f'{self.year:04d}-{self.month:02d}'
        super().save(*args, **kwargs)

    @property
    def is_editable(self) -> bool:
        return self.status == catalog.Status.DRAFT

    @property
    def is_terminal(self) -> bool:
        return catalog.is_terminal(self.status)


# ════════════════════════════════════════════════════════════════════
# Payslip (one per run × employee)
# ════════════════════════════════════════════════════════════════════
class Payslip(models.Model):
    run = models.ForeignKey(
        PayrollRun, on_delete=models.CASCADE, related_name='payslips',
    )
    employee = models.ForeignKey(
        PayrollEmployee, on_delete=models.PROTECT, related_name='payslips',
    )

    # Snapshot of contracted hours at run time
    hours = models.DecimalField(max_digits=8, decimal_places=2, default=DEFAULT_EMPLOYEE_HOURS)
    # Live work-days derived from hours (hours ÷ HOURS_PER_WORKDAY).
    # Recomputed whenever ``hours`` changes via the calculator service.
    days = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))

    # Leave days sourced from the Leave module (apps.payroll.LeaveRequest) for
    # the payroll month.  Populated at run generation from approved leave requests
    # linked by employee_code.  Soft-coded categories live in catalog.py.
    annual_leave_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
        help_text='Approved Annual Leave days taken in this payroll month.',
    )
    unpaid_leave_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
        help_text='Approved Unpaid Leave days taken in this payroll month.',
    )
    # Per-payslip public holiday count — seeded from run.public_holidays_in_month
    # at generation time.  Editable per-payslip so HR can correct individual records
    # without regenerating the entire run.
    public_holiday_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
        help_text='Public holiday days for this employee in this payroll month.',
    )
    
    # Total worked days (for payroll calculation) — auto-computed from formula:
    # Total Worked Days = (Hours ÷ Hours per Day) + Public Holidays + Annual Leave
    # Hours per Day depends on nationality: Emirates (basic only) = 8h, Others = 9h
    # This represents total credited days for salary purposes (excludes unpaid leave)
    total_worked_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0.00'),
        help_text='Total worked days = (Hours ÷ Hours/Day) + Public Holidays + Annual Leave. Auto-calculated.',
    )

    # Fixed earnings (4 standard columns)
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    housing = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    transport = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    home_leave = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    # Aggregates (recomputed on save / by calculator)
    other_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    gross_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    net_payable = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    payment_mode = models.CharField(
        max_length=32,
        choices=_choices_from(catalog.PAYMENT_MODES),
        default=catalog.DEFAULT_PAYMENT_MODE,
    )

    # Snapshot fields (preserve employee state at run time)
    snapshot_full_name = models.CharField(max_length=255, blank=True, default='')
    snapshot_department = models.CharField(max_length=128, blank=True, default='')
    snapshot_designation = models.CharField(max_length=128, blank=True, default='')
    snapshot_iban = models.CharField(max_length=64, blank=True, default='')
    snapshot_joining_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=32,
        choices=_choices_from(catalog.WORKFLOW_STATUSES),
        default=catalog.Status.DRAFT,
        db_index=True,
    )

    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payroll_engine_payslip'
        verbose_name = 'Payslip'
        ordering = ('snapshot_full_name',)
        constraints = [
            models.UniqueConstraint(fields=['run', 'employee'], name='uq_payslip_run_employee'),
        ]
        indexes = [
            models.Index(fields=['run', 'status']),
        ]

    def __str__(self) -> str:
        return f'Payslip {self.run.cycle_code} — {self.snapshot_full_name}'


# ════════════════════════════════════════════════════════════════════
# PayslipLineItem (free-form earning / deduction rows)
# ════════════════════════════════════════════════════════════════════
class PayslipLineItem(models.Model):
    payslip = models.ForeignKey(
        Payslip, on_delete=models.CASCADE, related_name='line_items',
    )

    kind = models.CharField(
        max_length=16,
        choices=_choices_from(catalog.LINE_ITEM_KINDS),
        db_index=True,
    )
    component_code = models.CharField(max_length=64, db_index=True)
    label = models.CharField(max_length=128)
    description = models.TextField(blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    source = models.CharField(
        max_length=16,
        choices=_choices_from(catalog.LINE_ITEM_SOURCES),
        default=catalog.LineItemSource.MANUAL,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_line_items_created',
    )

    class Meta:
        db_table = 'payroll_engine_line_item'
        verbose_name = 'Payslip Line Item'
        ordering = ('kind', 'component_code')
        indexes = [
            models.Index(fields=['payslip', 'kind']),
        ]

    def __str__(self) -> str:
        return f'{self.kind}:{self.component_code} {self.amount}'


# ════════════════════════════════════════════════════════════════════
# PayrollAdjustment (pre-staged for an upcoming run)
# ════════════════════════════════════════════════════════════════════
class PayrollAdjustment(models.Model):
    """Pending earning/deduction queued for a future PayrollRun.
    Materialised into PayslipLineItem by run_generator.
    """
    employee = models.ForeignKey(
        PayrollEmployee, on_delete=models.CASCADE, related_name='adjustments',
    )
    target_year = models.PositiveSmallIntegerField(db_index=True)
    target_month = models.PositiveSmallIntegerField(db_index=True)

    kind = models.CharField(
        max_length=16,
        choices=_choices_from(catalog.LINE_ITEM_KINDS),
    )
    component_code = models.CharField(max_length=64)
    label = models.CharField(max_length=128)
    description = models.TextField(blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    status = models.CharField(
        max_length=16,
        choices=_choices_from(catalog.ADJUSTMENT_STATUSES),
        default=catalog.AdjustmentStatus.PENDING,
        db_index=True,
    )
    applied_to = models.ForeignKey(
        Payslip, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='applied_adjustments',
    )
    applied_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_adjustments_created',
    )

    class Meta:
        db_table = 'payroll_engine_adjustment'
        verbose_name = 'Payroll Adjustment'
        ordering = ('-target_year', '-target_month', 'employee')
        indexes = [
            models.Index(fields=['target_year', 'target_month', 'status']),
            models.Index(fields=['employee', 'status']),
        ]

    def __str__(self) -> str:
        return f'Adj {self.employee.employee_no} {self.target_year}-{self.target_month:02d} {self.kind} {self.amount}'


# ════════════════════════════════════════════════════════════════════
# WorkflowLog (immutable audit trail)
# ════════════════════════════════════════════════════════════════════
class PayrollWorkflowLog(models.Model):
    run = models.ForeignKey(
        PayrollRun, on_delete=models.CASCADE, related_name='workflow_logs',
    )
    from_status = models.CharField(max_length=32, blank=True, default='')
    to_status = models.CharField(max_length=32)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_engine_workflow_actions',
    )
    note = models.TextField(blank=True, default='')
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'payroll_engine_workflow_log'
        verbose_name = 'Payroll Workflow Log'
        ordering = ('-at',)

    def __str__(self) -> str:
        return f'{self.run.cycle_code} {self.from_status}→{self.to_status} @ {self.at:%Y-%m-%d %H:%M}'


# ════════════════════════════════════════════════════════════════════# RunUpload — store imported external files (ValueFrame / Sympa) per run
# ╕══════════════════════════════════════════════════════════════════
class PayrollRunUpload(models.Model):
    """
    Tracks every external file (ValueFrame / Sympa / Generic) uploaded to a run.
    Parsing results are applied directly to Draft payslips.
    Files are stored in S3 for audit purposes.
    Field mapping is controlled by catalog.EXTERNAL_IMPORT_FIELD_MAP.
    """
    FILE_TYPE_CHOICES = [
        ('valueframe', 'ValueFrame (hours + leave)'),
        ('sympa',      'Sympa (salary components)'),
        ('generic',    'Generic XLSX'),
    ]
    STATUS_CHOICES = [
        ('applied', 'Applied'),
        ('failed',  'Failed'),
    ]

    run               = models.ForeignKey(PayrollRun, on_delete=models.CASCADE,
                            related_name='uploads')
    file_type         = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES,
                            default='valueframe')
    original_filename = models.CharField(max_length=255)
    s3_key            = models.CharField(max_length=500, blank=True)
    uploaded_by       = models.ForeignKey(
                            settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                            null=True, blank=True, related_name='payroll_run_uploads')
    uploaded_at       = models.DateTimeField(auto_now_add=True)
    rows_matched      = models.PositiveIntegerField(default=0)
    rows_updated      = models.PositiveIntegerField(default=0)
    unmatched         = models.JSONField(default=list, blank=True,
                            help_text='employee_no / names not found in this run')
    updated_fields    = models.JSONField(default=list, blank=True,
                            help_text='list of payslip field names that were written')
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES,
                            default='applied')
    error_message     = models.TextField(blank=True)

    class Meta:
        db_table = 'payroll_engine_run_upload'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.run.cycle_code} — {self.file_type} — {self.original_filename}'


# ╒══════════════════════════════════════════════════════════════════# Comparison — reconcile a Run against an external HR file
# (e.g. ValueFrame timesheet, Sympa salary master)
# ════════════════════════════════════════════════════════════════════
class PayrollComparison(models.Model):
    """One upload = one comparison job. Diff rows live in the related
    ``rows`` reverse relation so the summary can stay tabular while the
    individual variances are paginated.
    """
    run = models.ForeignKey(
        PayrollRun, on_delete=models.CASCADE, related_name='comparisons',
    )
    source_label = models.CharField(
        max_length=64,
        help_text='Human-readable source name e.g. "ValueFrame", "Sympa".',
    )
    source_profile = models.CharField(
        max_length=32, default='auto',
        help_text='Parser profile key from catalog.COMPARISON_PROFILES.',
    )
    source_filename = models.CharField(max_length=255, blank=True, default='')

    # Resolved {canonical_field: external_header_string} chosen by the parser.
    column_mapping = models.JSONField(default=dict, blank=True)

    # Aggregates: counts + per-field totals + per-status counts.
    summary = models.JSONField(default=dict, blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payroll_comparisons',
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'payroll_engine_comparison'
        verbose_name = 'Payroll Comparison'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['run', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'Cmp {self.run.cycle_code} ← {self.source_label} ({self.created_at:%Y-%m-%d})'


class PayrollComparisonRow(models.Model):
    """One row per matched/unmatched employee in a comparison."""

    STATUS_CHOICES = [
        (catalog.ComparisonStatus.MATCH,         'Match'),
        (catalog.ComparisonStatus.VARIANCE,      'Variance'),
        (catalog.ComparisonStatus.EXTERNAL_ONLY, 'External-only'),
        (catalog.ComparisonStatus.PAYROLL_ONLY,  'Missing from external'),
    ]

    comparison = models.ForeignKey(
        PayrollComparison, on_delete=models.CASCADE, related_name='rows',
    )
    payroll_employee = models.ForeignKey(
        PayrollEmployee, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+',
    )

    # Echoes from the external row (kept even when matched, for traceability).
    external_employee_no = models.CharField(max_length=64, blank=True, default='')
    external_name = models.CharField(max_length=255, blank=True, default='')

    # 'employee_no', 'name', 'fuzzy:0.92', or '' when status is *_only.
    matched_by = models.CharField(max_length=32, blank=True, default='')

    our_values = models.JSONField(default=dict, blank=True)
    external_values = models.JSONField(default=dict, blank=True)
    # [{field, our, external, diff, pct, severity, recommendation}]
    variances = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES,
        default=catalog.ComparisonStatus.MATCH, db_index=True,
    )

    class Meta:
        db_table = 'payroll_engine_comparison_row'
        verbose_name = 'Payroll Comparison Row'
        ordering = ('status', 'external_name')
        indexes = [
            models.Index(fields=['comparison', 'status']),
        ]

    def __str__(self) -> str:
        who = self.external_name or self.external_employee_no or 'unknown'
        return f'{who} [{self.status}]'
