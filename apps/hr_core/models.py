"""
HR Core Models - Unified Employee Master System

This module consolidates employee data from:
- apps.users.models.User
- apps.users.models.UserProfile
- apps.finance.models.EmployeeSalaryInfo
- apps.onboarding.models.OnboardingRecord

DESIGN PRINCIPLE: Single Source of Truth
- All employee data lives here
- Other tables reference this via Foreign Key
- Legacy identifiers (employee_code, emp_code) indexed for backward compatibility
"""
import uuid
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


class EmployeeMaster(TimeStampedModel):
    """
    Central Employee Master Record - Single Source of Truth
    
    Consolidates employee data from multiple legacy tables while maintaining
    backward compatibility through indexed legacy identifiers.
    
    Migration Strategy:
    - Phase 1: Create this table (parallel to existing tables)
    - Phase 2: Dual-write (update both old + new tables)
    - Phase 3: Migrate historical data
    - Phase 4: Switch reads to this table
    - Phase 5: Deprecate old tables
    """
    
    # ========================================
    # PRIMARY IDENTITY (UUID for global uniqueness)
    # ========================================
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Globally unique employee identifier'
    )
    
    # ========================================
    # LEGACY IDENTIFIERS (Indexed for backward compatibility)
    # ========================================
    employee_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text='Employee number from user_profiles table (primary legacy ID)'
    )
    
    employee_code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text='Employee code from finance tables (salary, payroll)'
    )
    
    emp_code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text='Employee code from timesheet/biometric system (truncated)'
    )
    
    # ========================================
    # AUTHENTICATION LINK
    # ========================================
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='employee_master',
        help_text='Link to Django authentication user'
    )
    
    email = models.EmailField(
        unique=True,
        db_index=True,
        help_text='Employee email (denormalized for performance)'
    )
    
    # ========================================
    # PERSONAL INFORMATION
    # ========================================
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_given_name = models.CharField(
        max_length=100,
        blank=True,
        help_text='Preferred first name for daily use'
    )
    initials = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    
    # ========================================
    # PHOTO (Single S3 source - replaces avatar + photo_url duplication)
    # ========================================
    photo_file_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='S3 key: media/employee_photos/{uuid}.{ext}'
    )
    
    photo_url = models.URLField(
        max_length=1000,
        blank=True,
        help_text='Presigned S3 URL (auto-refreshed every 7 days)'
    )
    
    photo_file_size = models.IntegerField(
        null=True,
        blank=True,
        help_text='File size in bytes'
    )
    
    photo_mime_type = models.CharField(
        max_length=100,
        blank=True,
        help_text='MIME type (image/jpeg, image/png)'
    )
    
    photo_uploaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When photo was last uploaded'
    )
    
    # ========================================
    # ORGANIZATIONAL HIERARCHY
    # ========================================
    manager = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='direct_reports',
        help_text='Direct reporting manager'
    )
    
    department = models.CharField(max_length=100, blank=True, db_index=True)
    division = models.CharField(max_length=100, blank=True, db_index=True)
    business_unit = models.CharField(max_length=100, blank=True)
    business_area = models.CharField(max_length=100, blank=True)
    office = models.CharField(max_length=100, blank=True)
    
    BRANCH_CHOICES = [
        ('RAD', 'Rejlers Abu Dhabi (RAD)'),
        ('RIN', 'Rejlers India (RIN)'),
    ]
    
    branch = models.CharField(
        max_length=20,
        choices=BRANCH_CHOICES,
        blank=True,
        db_index=True,
        help_text='Operating branch/entity'
    )
    
    # ========================================
    # JOB INFORMATION
    # ========================================
    job_title_uae = models.CharField(
        max_length=200,
        blank=True,
        help_text='Job title for UAE operations'
    )
    
    job_title_finland = models.CharField(
        max_length=200,
        blank=True,
        help_text='Job title for Finland operations'
    )
    
    designation = models.CharField(
        max_length=100,
        blank=True,
        help_text='Official designation/position'
    )
    
    # ========================================
    # EMPLOYMENT LIFECYCLE
    # ========================================
    join_date = models.DateField(
        help_text='Date of joining the organization'
    )
    
    probation_end_date = models.DateField(
        null=True,
        blank=True,
        help_text='Expected end of probation period'
    )
    
    confirmation_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date of employment confirmation'
    )
    
    exit_date = models.DateField(
        null=True,
        blank=True,
        help_text='Last working day (if exited)'
    )
    
    EMPLOYMENT_STATUS_CHOICES = [
        ('active', 'Active'),
        ('probation', 'On Probation'),
        ('notice_period', 'Notice Period'),
        ('exited', 'Exited'),
        ('suspended', 'Suspended'),
    ]
    
    employment_status = models.CharField(
        max_length=20,
        choices=EMPLOYMENT_STATUS_CHOICES,
        default='active',
        db_index=True,
        help_text='Current employment status'
    )
    
    # ========================================
    # SALARY (Basic info - detailed records in EmployeeSalary model)
    # ========================================
    current_base_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Current base salary (denormalized for quick access)'
    )
    
    currency = models.CharField(
        max_length=3,
        default='AED',
        help_text='Salary currency code (ISO 4217)'
    )
    
    # ========================================
    # CONTACT INFORMATION
    # ========================================
    phone_number = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    
    # ========================================
    # BANKING & PAYROLL
    # ========================================
    bank_account_number = models.CharField(
        max_length=50,
        blank=True,
        help_text='Bank account number for salary transfer'
    )
    
    bank_name = models.CharField(max_length=100, blank=True)
    
    iban = models.CharField(
        max_length=34,
        blank=True,
        help_text='International Bank Account Number'
    )
    
    swift_code = models.CharField(
        max_length=11,
        blank=True,
        help_text='SWIFT/BIC code'
    )
    
    # ========================================
    # TAX & COMPLIANCE
    # ========================================
    pan_number = models.CharField(
        max_length=20,
        blank=True,
        help_text='Permanent Account Number (India tax ID)'
    )
    
    uan_number = models.CharField(
        max_length=20,
        blank=True,
        help_text='Universal Account Number (India PF)'
    )
    
    tax_id = models.CharField(
        max_length=50,
        blank=True,
        help_text='Tax identification number'
    )
    
    # ========================================
    # HR SYSTEM INTEGRATION IDs
    # ========================================
    employment_id = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text='Employment ID from external HRM system'
    )
    
    candidate_id = models.CharField(
        max_length=50,
        blank=True,
        help_text='Candidate ID from recruitment system'
    )
    
    account_name = models.CharField(
        max_length=100,
        blank=True,
        help_text='Active Directory account name'
    )
    
    # ========================================
    # METADATA & AUDIT
    # ========================================
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='employees_created',
        help_text='User who created this employee record'
    )
    
    last_updated_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='employees_updated',
        help_text='User who last updated this record'
    )
    
    # ========================================
    # FLAGS
    # ========================================
    is_test_person = models.BooleanField(
        default=False,
        help_text='Test/demo employee flag (exclude from reports)'
    )
    
    protected_identity = models.BooleanField(
        default=False,
        help_text='Protected identity flag (restrict access)'
    )
    
    not_signed = models.BooleanField(
        default=False,
        help_text='Contract not yet signed flag'
    )
    
    # ========================================
    # ENGINEERING PROFILE (JSON)
    # ========================================
    engineer_profile = models.JSONField(
        null=True,
        blank=True,
        default=dict,
        help_text='Engineering expertise data: disciplines, skills, certifications, projects, availability'
    )
    
    class Meta:
        db_table = 'hr_employee_master'
        verbose_name = 'Employee Master Record'
        verbose_name_plural = 'Employee Master Records'
        ordering = ['-created_at']
        
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['employee_number']),
            models.Index(fields=['employee_code']),
            models.Index(fields=['emp_code']),
            models.Index(fields=['employment_status', 'join_date']),
            models.Index(fields=['department', 'division']),
            models.Index(fields=['branch', 'employment_status']),
        ]
    
    def __str__(self):
        return f"{self.employee_number} - {self.first_name} {self.last_name}"
    
    def get_full_name(self):
        """Return full name."""
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_display_name(self):
        """Return preferred name or full name."""
        if self.preferred_given_name:
            return f"{self.preferred_given_name} {self.last_name}".strip()
        return self.get_full_name()
    
    def is_active_employee(self):
        """Check if employee is currently active."""
        return self.employment_status == 'active'
    
    def days_employed(self):
        """Calculate days of employment."""
        end_date = self.exit_date or timezone.now().date()
        return (end_date - self.join_date).days
    
    def refresh_photo_url(self):
        """
        Refresh presigned S3 URL for photo.
        Called by Celery task daily to maintain valid URLs.
        """
        if self.photo_file_path:
            from apps.core.s3_service import S3Service
            s3_service = S3Service()
            self.photo_url = s3_service.generate_presigned_url(
                self.photo_file_path,
                expiration=604800  # 7 days
            )
            self.save(update_fields=['photo_url'])
