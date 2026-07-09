"""
Salary Slip Automation System - Database Models
Enterprise-grade payroll management system
SOFT-CODED for easy customization
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal
import uuid

User = get_user_model()


class SalaryStatus(models.TextChoices):
    """Status choices for salary slips"""
    DRAFT = 'draft', 'Draft'
    GENERATED = 'generated', 'Generated'
    PENDING_APPROVAL = 'pending_approval', 'Pending Approval'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'
    SENT = 'sent', 'Sent to Employee'
    ARCHIVED = 'archived', 'Archived'


class ApprovalStatus(models.TextChoices):
    """Approval workflow statuses"""
    PENDING = 'pending', 'Pending'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class EmailStatus(models.TextChoices):
    """Email delivery statuses"""
    PENDING = 'pending', 'Pending'
    QUEUED = 'queued', 'Queued'
    SENT = 'sent', 'Sent'
    FAILED = 'failed', 'Failed'
    BOUNCED = 'bounced', 'Bounced'


# ===========================
# EMPLOYEE & SALARY STRUCTURE
# ===========================

class EmployeeSalaryInfo(models.Model):
    """
    Employee salary structure and payroll information
    Extends User model with payroll-specific fields
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='salary_info'
    )
    
    # Employee identification
    employee_id = models.CharField(max_length=50, unique=True, db_index=True)
    department = models.CharField(max_length=100, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    join_date = models.DateField(null=True, blank=True)
    
    # Bank details (encrypted in production)
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=50, blank=True)
    iban = models.CharField(max_length=50, blank=True)
    swift_code = models.CharField(max_length=20, blank=True)
    
    # Tax information
    tax_id = models.CharField(max_length=50, blank=True)
    tax_exemption = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00')
    )
    
    # Salary structure
    basic_salary = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    currency = models.CharField(max_length=10, default='AED')
    
    # Employment status
    is_active = models.BooleanField(default=True)
    termination_date = models.DateField(null=True, blank=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='created_salary_infos'
    )
    
    class Meta:
        db_table = 'finance_employee_salary_info'
        verbose_name = 'Employee Salary Information'
        verbose_name_plural = 'Employee Salary Information'
        ordering = ['employee_id']
        indexes = [
            models.Index(fields=['employee_id']),
            models.Index(fields=['user']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.employee_id} - {self.user.get_full_name() or self.user.email}"


class SalaryComponent(models.Model):
    """
    Master table for salary components (allowances and deductions)
    SOFT-CODED for easy customization
    """
    COMPONENT_TYPE_CHOICES = [
        ('allowance', 'Allowance'),
        ('deduction', 'Deduction'),
    ]
    
    CALCULATION_TYPE_CHOICES = [
        ('fixed', 'Fixed Amount'),
        ('percentage', 'Percentage of Basic'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    component_type = models.CharField(max_length=20, choices=COMPONENT_TYPE_CHOICES)
    calculation_type = models.CharField(max_length=20, choices=CALCULATION_TYPE_CHOICES)
    
    # Default value (can be overridden per employee)
    default_value = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    
    # Configuration
    is_taxable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'finance_salary_components'
        verbose_name = 'Salary Component'
        verbose_name_plural = 'Salary Components'
        ordering = ['component_type', 'display_order', 'name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['component_type']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_component_type_display()})"


class EmployeeSalaryComponent(models.Model):
    """
    Employee-specific salary components (allowances/deductions)
    Links employees to their specific salary breakdown
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.CASCADE,
        related_name='salary_components'
    )
    component = models.ForeignKey(
        SalaryComponent,
        on_delete=models.CASCADE,
        related_name='employee_components'
    )
    
    # Employee-specific value (overrides default)
    value = models.DecimalField(
        max_digits=12, 
        decimal_places=2
    )
    
    # Effective dates
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'finance_employee_salary_components'
        verbose_name = 'Employee Salary Component'
        verbose_name_plural = 'Employee Salary Components'
        ordering = ['employee_salary_info', 'component__display_order']
        unique_together = [['employee_salary_info', 'component', 'effective_from']]
        indexes = [
            models.Index(fields=['employee_salary_info']),
            models.Index(fields=['component']),
            models.Index(fields=['is_active']),
            models.Index(fields=['effective_from', 'effective_to']),
        ]
    
    def __str__(self):
        return f"{self.employee_salary_info.employee_id} - {self.component.name}"


# ===========================
# PAYROLL RUN MANAGEMENT
# ===========================

class PayrollRun(models.Model):
    """
    Monthly payroll processing run
    Tracks each payroll cycle
    """
    RUN_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_code = models.CharField(max_length=50, unique=True, db_index=True)  # e.g., PAY-2024-01
    
    # Period
    month = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    year = models.IntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    
    # Payroll details
    total_employees = models.IntegerField(default=0)
    processed_employees = models.IntegerField(default=0)
    total_gross_salary = models.DecimalField(
        max_digits=15, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    total_deductions = models.DecimalField(
        max_digits=15, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    total_net_salary = models.DecimalField(
        max_digits=15, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    
    # Status
    status = models.CharField(max_length=20, choices=RUN_STATUS_CHOICES, default='draft')
    
    # Processing metadata
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    error_log = models.TextField(blank=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_payroll_runs'
    )
    
    class Meta:
        db_table = 'finance_payroll_runs'
        verbose_name = 'Payroll Run'
        verbose_name_plural = 'Payroll Runs'
        ordering = ['-year', '-month']
        unique_together = [['month', 'year']]
        indexes = [
            models.Index(fields=['run_code']),
            models.Index(fields=['year', 'month']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.run_code} - {self.month}/{self.year}"


# ===========================
# SALARY SLIP
# ===========================

class SalarySlip(models.Model):
    """
    Individual employee salary slip
    Core entity for payroll automation
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slip_number = models.CharField(max_length=50, unique=True, db_index=True)
    
    # Relationships
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='salary_slips'
    )
    employee_salary_info = models.ForeignKey(
        EmployeeSalaryInfo,
        on_delete=models.CASCADE,
        related_name='salary_slips'
    )
    
    # Period
    month = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    year = models.IntegerField()
    
    # Salary calculations
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    total_allowances = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    
    total_deductions = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    tax_deduction = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        default=Decimal('0.00')
    )
    
    net_salary = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='AED')
    
    # Component breakdown (stored as JSON for flexibility)
    allowances_breakdown = models.JSONField(default=dict, blank=True)
    deductions_breakdown = models.JSONField(default=dict, blank=True)
    
    # Working days
    working_days = models.IntegerField(default=30)
    present_days = models.IntegerField(default=30)
    absent_days = models.IntegerField(default=0)
    
    # Status
    status = models.CharField(
        max_length=30,
        choices=SalaryStatus.choices,
        default=SalaryStatus.DRAFT
    )
    
    # PDF storage — local path (legacy) + S3 key for cloud-hosted PDFs
    pdf_file_path = models.CharField(max_length=500, blank=True)
    pdf_generated_at = models.DateTimeField(null=True, blank=True)
    pdf_s3_key = models.CharField(max_length=600, blank=True,
        help_text='S3 object key for the uploaded PDF (payroll/slips/YYYY/MM/slip.pdf)')
    pdf_s3_uploaded_at = models.DateTimeField(null=True, blank=True)
    
    # Approval tracking
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_salary_slips'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    
    # Notes
    remarks = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    generated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='generated_salary_slips'
    )
    
    class Meta:
        db_table = 'finance_salary_slips'
        verbose_name = 'Salary Slip'
        verbose_name_plural = 'Salary Slips'
        ordering = ['-year', '-month', 'employee_salary_info__employee_id']
        unique_together = [['employee_salary_info', 'month', 'year']]
        indexes = [
            models.Index(fields=['slip_number']),
            models.Index(fields=['payroll_run']),
            models.Index(fields=['employee_salary_info']),
            models.Index(fields=['year', 'month']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.slip_number} - {self.employee_salary_info.employee_id} ({self.month}/{self.year})"


# ===========================
# APPROVAL WORKFLOW
# ===========================

class SalarySlipApproval(models.Model):
    """
    Multi-level approval workflow for salary slips
    Tracks approval chain (HR → Finance → Final Approval)
    """
    APPROVAL_ROLE_CHOICES = [
        ('hr', 'HR'),
        ('finance', 'Finance'),
        ('management', 'Management'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_slip = models.ForeignKey(
        SalarySlip,
        on_delete=models.CASCADE,
        related_name='approvals'
    )
    
    # Approval details
    approval_level = models.IntegerField(default=1)
    approval_role = models.CharField(max_length=20, choices=APPROVAL_ROLE_CHOICES)
    approver = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='salary_approvals'
    )
    
    # Status and decision
    status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING
    )
    decision_date = models.DateTimeField(null=True, blank=True)
    comments = models.TextField(blank=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'finance_salary_slip_approvals'
        verbose_name = 'Salary Slip Approval'
        verbose_name_plural = 'Salary Slip Approvals'
        ordering = ['salary_slip', 'approval_level']
        unique_together = [['salary_slip', 'approval_level']]
        indexes = [
            models.Index(fields=['salary_slip']),
            models.Index(fields=['approver']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.salary_slip.slip_number} - Level {self.approval_level} ({self.get_status_display()})"


# ===========================
# EMAIL DELIVERY TRACKING
# ===========================

class SalarySlipEmail(models.Model):
    """
    Email delivery tracking for salary slips
    Ensures accountability and delivery confirmation
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_slip = models.ForeignKey(
        SalarySlip,
        on_delete=models.CASCADE,
        related_name='email_deliveries'
    )
    
    # Email details
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=500)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=EmailStatus.choices,
        default=EmailStatus.PENDING
    )
    
    # Delivery tracking
    email_provider_id = models.CharField(max_length=200, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    
    # Error handling
    retry_count = models.IntegerField(default=0)
    last_error = models.TextField(blank=True)
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'finance_salary_slip_emails'
        verbose_name = 'Salary Slip Email'
        verbose_name_plural = 'Salary Slip Emails'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['salary_slip']),
            models.Index(fields=['status']),
            models.Index(fields=['recipient_email']),
        ]
    
    def __str__(self):
        return f"{self.salary_slip.slip_number} → {self.recipient_email} ({self.get_status_display()})"


# ===========================
# AUDIT LOG
# ===========================

class SalarySlipAuditLog(models.Model):
    """
    Comprehensive audit trail for all salary slip operations
    Ensures compliance and traceability
    """
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('generated', 'Generated'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('sent', 'Sent to Employee'),
        ('downloaded', 'Downloaded'),
        ('archived', 'Archived'),
        ('deleted', 'Deleted'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_slip = models.ForeignKey(
        SalarySlip,
        on_delete=models.CASCADE,
        related_name='audit_logs'
    )
    
    # Action details
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='salary_slip_actions'
    )
    performed_at = models.DateTimeField(auto_now_add=True)
    
    # Change tracking
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    
    # Context
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    
    class Meta:
        db_table = 'finance_salary_slip_audit_logs'
        verbose_name = 'Salary Slip Audit Log'
        verbose_name_plural = 'Salary Slip Audit Logs'
        ordering = ['-performed_at']
        indexes = [
            models.Index(fields=['salary_slip']),
            models.Index(fields=['performed_by']),
            models.Index(fields=['action']),
            models.Index(fields=['performed_at']),
        ]
    
    def __str__(self):
        return f"{self.action} - {self.salary_slip.slip_number} by {self.performed_by}"


# ===========================
# PAYROLL AUTO-SCHEDULE
# ===========================

class PayrollSchedule(models.Model):
    """
    Configuration for automated monthly payroll generation.
    One global singleton row (id=1) controls the schedule.
    Celery Beat reads this on each tick to decide whether to fire.

    Workflow triggered when enabled:
      1. Create PayrollRun for (target_month, target_year)
      2. Process run → generate SalarySlip records
      3. Generate PDF for each slip via SalarySlipPDFService
      4. Upload PDF to S3 via PayrollSlipStorage
      5. Store S3 key on SalarySlip.pdf_s3_key
      6. (Optional) send email to each employee
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Toggle
    enabled = models.BooleanField(
        default=False,
        help_text='When True, the Celery Beat task will auto-generate payroll on schedule.',
    )

    # Schedule — day of month to run (1-28)
    day_of_month = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text='Day of month on which the task fires (1-28). Default = 1st.',
    )

    # How many calendar days after month-end to generate (0 = same day, 1 = next day…)
    days_after_month_end = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(15)],
        help_text='How many days after month-end to wait before generating. 0 = generate immediately.',
    )

    # Whether to auto-send emails after generating PDFs
    auto_send_emails = models.BooleanField(
        default=False,
        help_text='When True, emails are queued for all approved slips after generation.',
    )

    # Notification recipients (comma-separated emails)
    notify_emails = models.TextField(
        blank=True,
        help_text='Comma-separated email addresses to notify on success / failure.',
    )

    # Last execution metadata
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(
        max_length=20,
        blank=True,
        choices=[('success', 'Success'), ('failed', 'Failed'), ('skipped', 'Skipped')],
    )
    last_run_details = models.TextField(blank=True)

    # Audit
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payroll_schedule_changes',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'finance_payroll_schedule'
        verbose_name = 'Payroll Auto-Schedule'
        verbose_name_plural = 'Payroll Auto-Schedule'

    def __str__(self):
        state = 'ENABLED' if self.enabled else 'DISABLED'
        return f'PayrollSchedule [{state}] day={self.day_of_month}'

    @classmethod
    def get_or_create_singleton(cls):
        """Return the single schedule config row, creating defaults if absent."""
        obj, _ = cls.objects.get_or_create(
            pk=cls.objects.values_list('id', flat=True).first() or uuid.uuid4(),
        )
        return obj
