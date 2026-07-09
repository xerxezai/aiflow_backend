"""
Onboarding & Offboarding Models
Tracks employee lifecycle: joining, exit, equipment, documents, access provisioning
All soft-coded: status choices, equipment types, document types via config
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

# ═══════════════════════════════════════════════════════════════════════════
# Soft-Coded Configuration Constants
# ═══════════════════════════════════════════════════════════════════════════

# Onboarding Status
STATUS_INITIATED = 'initiated'
STATUS_DOCUMENTATION = 'documentation'
STATUS_EQUIPMENT = 'equipment'
STATUS_ACCESS_PROVISIONING = 'access_provisioning'
STATUS_TRAINING = 'training'
STATUS_COMPLETED = 'completed'
STATUS_CANCELLED = 'cancelled'

ONBOARDING_STATUS_CHOICES = [
    (STATUS_INITIATED, 'Initiated'),
    (STATUS_DOCUMENTATION, 'Documentation Collection'),
    (STATUS_EQUIPMENT, 'Equipment Assignment'),
    (STATUS_ACCESS_PROVISIONING, 'Access Provisioning'),
    (STATUS_TRAINING, 'Training & Orientation'),
    (STATUS_COMPLETED, 'Completed'),
    (STATUS_CANCELLED, 'Cancelled'),
]

# Offboarding Status
OFFBOARDING_STATUS_INITIATED = 'initiated'
OFFBOARDING_STATUS_ACCESS_REVOCATION = 'access_revocation'
OFFBOARDING_STATUS_EQUIPMENT_RETURN = 'equipment_return'
OFFBOARDING_STATUS_EXIT_INTERVIEW = 'exit_interview'
OFFBOARDING_STATUS_FINAL_SETTLEMENT = 'final_settlement'
OFFBOARDING_STATUS_COMPLETED = 'completed'
OFFBOARDING_STATUS_CANCELLED = 'cancelled'

OFFBOARDING_STATUS_CHOICES = [
    (OFFBOARDING_STATUS_INITIATED, 'Initiated'),
    (OFFBOARDING_STATUS_ACCESS_REVOCATION, 'Access Revocation'),
    (OFFBOARDING_STATUS_EQUIPMENT_RETURN, 'Equipment Return'),
    (OFFBOARDING_STATUS_EXIT_INTERVIEW, 'Exit Interview'),
    (OFFBOARDING_STATUS_FINAL_SETTLEMENT, 'Final Settlement'),
    (OFFBOARDING_STATUS_COMPLETED, 'Completed'),
    (OFFBOARDING_STATUS_CANCELLED, 'Cancelled'),
]

# Equipment Types
EQUIPMENT_TYPES = [
    ('laptop', 'Laptop'),
    ('desktop', 'Desktop Computer'),
    ('monitor', 'Monitor'),
    ('keyboard', 'Keyboard'),
    ('mouse', 'Mouse'),
    ('headset', 'Headset'),
    ('mobile', 'Mobile Phone'),
    ('access_card', 'Access Card'),
    ('badge', 'ID Badge'),
    ('keys', 'Office Keys'),
    ('other', 'Other'),
]

# Document Types
DOCUMENT_TYPES = [
    ('passport', 'Passport Copy'),
    ('visa', 'Visa'),
    ('emirates_id', 'Emirates ID'),
    ('driving_license', 'Driving License'),
    ('degree', 'Educational Certificates'),
    ('certificate', 'Professional Certificate'),
    ('experience', 'Experience Letters'),
    ('offer_letter', 'Signed Offer Letter'),
    ('contract', 'Employment Contract'),
    ('confidentiality', 'Confidentiality Agreement'),
    ('policy_acknowledgment', 'Policy Acknowledgment'),
    ('bank_details', 'Bank Account Details'),
    ('emergency_contact', 'Emergency Contact Form'),
    ('medical', 'Medical/Insurance Forms'),
    ('vaccination', 'Vaccination Certificate'),
    ('police_clearance', 'Police Clearance Certificate'),
    ('resignation', 'Resignation Letter'),
    ('clearance', 'Exit Clearance Form'),
    ('other', 'Other'),
]

# Access Types
ACCESS_TYPES = [
    ('email', 'Email Account'),
    ('active_directory', 'Active Directory'),
    ('erp', 'ERP System'),
    ('hr_system', 'HR System'),
    ('project_tools', 'Project Management Tools'),
    ('cloud_storage', 'Cloud Storage'),
    ('vpn', 'VPN Access'),
    ('building_access', 'Building Access'),
    ('parking', 'Parking Access'),
    ('other', 'Other'),
]

# Exit Reasons
EXIT_REASONS = [
    ('resignation', 'Voluntary Resignation'),
    ('termination', 'Termination'),
    ('contract_end', 'Contract Completion'),
    ('retirement', 'Retirement'),
    ('relocation', 'Relocation'),
    ('health', 'Health Reasons'),
    ('performance', 'Performance Issues'),
    ('redundancy', 'Redundancy'),
    ('other', 'Other'),
]


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════

class OnboardingRecord(models.Model):
    """
    Onboarding tracker for new joiners
    Workflow: Initiated → Documentation → Equipment → Access → Training → Completed
    """
    # Employee Info
    employee_name = models.CharField(max_length=255)
    employee_email = models.EmailField(unique=True)
    employee_id = models.CharField(max_length=100, blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_records')
    
    # Passport Photo (stored in S3)
    photo_file_path = models.CharField(max_length=500, blank=True, null=True, help_text='S3 key for passport photo')
    photo_url = models.URLField(max_length=1000, blank=True, null=True, help_text='Presigned S3 URL for photo')
    photo_file_size = models.IntegerField(null=True, blank=True, help_text='Photo file size in bytes')
    photo_mime_type = models.CharField(max_length=100, blank=True, null=True, help_text='Photo MIME type')
    photo_original_filename = models.CharField(max_length=255, blank=True, null=True, help_text='Original photo filename')
    
    # Position Info
    position = models.CharField(max_length=255)
    department = models.CharField(max_length=255)
    reporting_manager = models.CharField(max_length=255, blank=True, null=True)
    branch = models.CharField(max_length=50, default='RAD', choices=[('RAD', 'Rejlers Abu Dhabi'), ('RIN', 'Rejlers India')])
    
    # Timeline
    joining_date = models.DateField()
    initiated_date = models.DateTimeField(default=timezone.now)
    target_completion_date = models.DateField()
    actual_completion_date = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=50, choices=ONBOARDING_STATUS_CHOICES, default=STATUS_INITIATED)
    progress_percentage = models.IntegerField(default=0)
    
    # Tracking
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='onboarding_created')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_assigned')
    
    # Notes
    notes = models.TextField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_record'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['joining_date']),
            models.Index(fields=['employee_email']),
        ]
    
    def __str__(self):
        return f"{self.employee_name} - {self.position} ({self.status})"


class OffboardingRecord(models.Model):
    """
    Offboarding tracker for exiting employees
    Workflow: Initiated → Access Revocation → Equipment Return → Exit Interview → Settlement → Completed
    """
    # Employee Info
    employee_name = models.CharField(max_length=255)
    employee_email = models.EmailField()
    employee_id = models.CharField(max_length=100, blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='offboarding_records')
    
    # Position Info
    position = models.CharField(max_length=255)
    department = models.CharField(max_length=255)
    reporting_manager = models.CharField(max_length=255, blank=True, null=True)
    branch = models.CharField(max_length=50, default='RAD', choices=[('RAD', 'Rejlers Abu Dhabi'), ('RIN', 'Rejlers India')])
    
    # Exit Info
    exit_reason = models.CharField(max_length=50, choices=EXIT_REASONS)
    exit_reason_detail = models.TextField(blank=True, null=True)
    last_working_day = models.DateField()
    notice_period_days = models.IntegerField(default=30)
    
    # Timeline
    initiated_date = models.DateTimeField(default=timezone.now)
    target_completion_date = models.DateField()
    actual_completion_date = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=50, choices=OFFBOARDING_STATUS_CHOICES, default=OFFBOARDING_STATUS_INITIATED)
    progress_percentage = models.IntegerField(default=0)
    
    # Tracking
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='offboarding_created')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='offboarding_assigned')
    
    # Notes
    notes = models.TextField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'offboarding_record'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['last_working_day']),
            models.Index(fields=['employee_email']),
        ]
    
    def __str__(self):
        return f"{self.employee_name} - Exit on {self.last_working_day} ({self.status})"


class Equipment(models.Model):
    """
    Equipment assigned to employees (onboarding) or returned (offboarding)
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='equipment')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='equipment')
    
    equipment_type = models.CharField(max_length=50, choices=EQUIPMENT_TYPES)
    item_name = models.CharField(max_length=255)
    serial_number = models.CharField(max_length=255, blank=True, null=True)
    asset_tag = models.CharField(max_length=100, blank=True, null=True)
    
    assigned_date = models.DateField(null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
    
    condition = models.CharField(max_length=50, choices=[
        ('new', 'New'),
        ('good', 'Good'),
        ('fair', 'Fair'),
        ('damaged', 'Damaged'),
    ], default='good')
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_equipment'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.equipment_type} - {self.item_name}"


class Document(models.Model):
    """
    Documents collected during onboarding or offboarding
    Supports file uploads to AWS S3 (certificates, Emirates ID, driving license, etc.)
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    document_name = models.CharField(max_length=255)
    
    # S3 storage fields
    file_path = models.CharField(max_length=500, blank=True, null=True, help_text="S3 key path for the uploaded file")
    file_url = models.URLField(max_length=1000, blank=True, null=True, help_text="Presigned URL or public URL")
    file_size = models.IntegerField(null=True, blank=True, help_text="File size in bytes")
    file_mime_type = models.CharField(max_length=100, blank=True, null=True)
    original_filename = models.CharField(max_length=255, blank=True, null=True)
    
    submitted = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_documents')
    verified_date = models.DateTimeField(null=True, blank=True)
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_document'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.document_type} - {self.document_name}"


class AccessProvisioning(models.Model):
    """
    IT/System access provisioned or revoked
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='access_records')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='access_records')
    
    access_type = models.CharField(max_length=50, choices=ACCESS_TYPES)
    access_name = models.CharField(max_length=255)
    account_username = models.CharField(max_length=255, blank=True, null=True)
    
    provisioned = models.BooleanField(default=False)
    provisioned_date = models.DateTimeField(null=True, blank=True)
    
    revoked = models.BooleanField(default=False)
    revoked_date = models.DateTimeField(null=True, blank=True)
    
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='access_assigned')
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_access'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.access_type} - {self.access_name}"


class Checklist(models.Model):
    """
    Custom checklist items for onboarding/offboarding
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='checklist_items')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='checklist_items')
    
    task_name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    
    completed = models.BooleanField(default=False)
    completed_date = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='checklist_completed')
    
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=20, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], default='medium')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_checklist'
        ordering = ['due_date', '-priority', 'task_name']
    
    def __str__(self):
        return f"{self.task_name} ({'Completed' if self.completed else 'Pending'})"
