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

ONBOARDING_ACTIVE_STATUSES = (
    STATUS_INITIATED,
    STATUS_DOCUMENTATION,
    STATUS_EQUIPMENT,
    STATUS_ACCESS_PROVISIONING,
    STATUS_TRAINING,
)

# Offboarding Status
OFFBOARDING_STATUS_INITIATED = 'initiated'
OFFBOARDING_STATUS_ACCESS_REVOCATION = 'access_revocation'
OFFBOARDING_STATUS_EQUIPMENT_RETURN = 'equipment_return'
OFFBOARDING_STATUS_EXIT_INTERVIEW = 'exit_interview'
OFFBOARDING_STATUS_FINAL_SETTLEMENT = 'final_settlement'
OFFBOARDING_STATUS_COMPLETED = 'completed'
OFFBOARDING_STATUS_CANCELLED = 'cancelled'
OFFBOARDING_STATUS_REJECTED = 'rejected'

OFFBOARDING_STATUS_CHOICES = [
    (OFFBOARDING_STATUS_INITIATED, 'Initiated'),
    (OFFBOARDING_STATUS_ACCESS_REVOCATION, 'Access Revocation'),
    (OFFBOARDING_STATUS_EQUIPMENT_RETURN, 'Equipment Return'),
    (OFFBOARDING_STATUS_EXIT_INTERVIEW, 'Exit Interview'),
    (OFFBOARDING_STATUS_FINAL_SETTLEMENT, 'Final Settlement'),
    (OFFBOARDING_STATUS_COMPLETED, 'Completed'),
    (OFFBOARDING_STATUS_CANCELLED, 'Cancelled'),
    (OFFBOARDING_STATUS_REJECTED, 'Rejected'),
]

OFFBOARDING_ACTIVE_STATUSES = (
    OFFBOARDING_STATUS_INITIATED,
    OFFBOARDING_STATUS_ACCESS_REVOCATION,
    OFFBOARDING_STATUS_EQUIPMENT_RETURN,
    OFFBOARDING_STATUS_EXIT_INTERVIEW,
    OFFBOARDING_STATUS_FINAL_SETTLEMENT,
)

CHECKLIST_STAGE_GENERAL = 'general'
CHECKLIST_STAGE_PRE_HIRE = 'pre_hire'
CHECKLIST_STAGE_IT_PROVISIONING = 'it_provisioning'
CHECKLIST_STAGE_FIRST_DAY = 'first_day'
CHECKLIST_STAGE_FINAL_VALIDATION = 'final_validation'
CHECKLIST_STAGE_EXIT_INITIATION = 'exit_initiation'
CHECKLIST_STAGE_ACCESS_REVOCATION = 'access_revocation'
CHECKLIST_STAGE_ASSET_RETURN = 'asset_return'
CHECKLIST_STAGE_EXIT_CLEARANCE = 'exit_clearance'
CHECKLIST_STAGE_FINAL_SETTLEMENT = 'final_settlement'

CHECKLIST_STAGE_CHOICES = [
    (CHECKLIST_STAGE_GENERAL, 'General'),
    (CHECKLIST_STAGE_PRE_HIRE, 'Pre-Hire Initiation'),
    (CHECKLIST_STAGE_IT_PROVISIONING, 'IT Provisioning'),
    (CHECKLIST_STAGE_FIRST_DAY, 'First Day Orientation'),
    (CHECKLIST_STAGE_FINAL_VALIDATION, 'Final Checklist Validation'),
    (CHECKLIST_STAGE_EXIT_INITIATION, 'Exit Initiation'),
    (CHECKLIST_STAGE_ACCESS_REVOCATION, 'Access Revocation'),
    (CHECKLIST_STAGE_ASSET_RETURN, 'Asset Return'),
    (CHECKLIST_STAGE_EXIT_CLEARANCE, 'Exit Interview & Clearance'),
    (CHECKLIST_STAGE_FINAL_SETTLEMENT, 'Final Settlement'),
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
    rejection_reason = models.TextField(blank=True, null=True)
    rejected_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='offboarding_rejected'
    )
    rejected_at = models.DateTimeField(null=True, blank=True)

    # Project-manager clearance for employees assigned to active projects.
    # DEPRECATED: Legacy single project manager approval (kept for backwards compatibility)
    project_manager_approval_status = models.CharField(
        max_length=20,
        choices=[
            ('not_required', 'Not Required'),
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='not_required',
    )
    project_manager_decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='offboarding_project_manager_decisions'
    )
    project_manager_decided_at = models.DateTimeField(null=True, blank=True)
    project_manager_decision_note = models.TextField(blank=True, null=True)
    
    # ✨ NEW: Three-step approval workflow
    # Step 1: Multiple Project Managers (many-to-many relationship via ExitApproval model)
    # Step 2: HR Coordinator
    hr_coordinator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='offboarding_hr_coordinator_assignments',
        help_text='HR Coordinator responsible for processing exit documentation'
    )
    hr_coordinator_approval_status = models.CharField(
        max_length=20,
        choices=[
            ('not_required', 'Not Required'),
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='pending',
    )
    hr_coordinator_approved_at = models.DateTimeField(null=True, blank=True)
    hr_coordinator_note = models.TextField(blank=True, null=True)
    
    # Step 3: HR Approver (final approval)
    hr_approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='offboarding_hr_approver_assignments',
        help_text='HR Manager/Approver for final exit approval'
    )
    hr_approver_approval_status = models.CharField(
        max_length=20,
        choices=[
            ('not_required', 'Not Required'),
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='pending',
    )
    hr_approver_approved_at = models.DateTimeField(null=True, blank=True)
    hr_approver_note = models.TextField(blank=True, null=True)
    
    # Overall approval workflow status
    approval_workflow_status = models.CharField(
        max_length=30,
        choices=[
            ('not_started', 'Not Started'),
            ('project_managers_pending', 'Awaiting Project Manager(s) Approval'),
            ('hr_coordinator_pending', 'Awaiting HR Coordinator Approval'),
            ('hr_approver_pending', 'Awaiting HR Approver'),
            ('approved', 'Fully Approved'),
            ('rejected', 'Rejected'),
        ],
        default='not_started',
        db_index=True,
        help_text='Current stage in the three-step approval workflow'
    )
    
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
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_documents_verified')
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
    stage = models.CharField(
        max_length=30,
        choices=CHECKLIST_STAGE_CHOICES,
        default=CHECKLIST_STAGE_GENERAL,
        db_index=True,
    )
    
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


class ExitApproval(models.Model):
    """
    ✨ Three-step approval workflow for offboarding
    Tracks individual approvals from Project Managers (Step 1), HR Coordinator (Step 2), HR Approver (Step 3)
    Soft-coded approval statuses and decision tracking with project assignment support
    """
    offboarding_record = models.ForeignKey(
        OffboardingRecord,
        on_delete=models.CASCADE,
        related_name='exit_approvals',
        help_text='The offboarding record requiring approval'
    )
    
    # Approver details
    approver = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exit_approval_assignments',
        help_text='User assigned to approve this exit (Project Manager, HR Coordinator, or HR Approver)'
    )
    
    # Approval step/role
    approval_step = models.CharField(
        max_length=30,
        choices=[
            ('project_manager', 'Project Manager'),
            ('hr_coordinator', 'HR Coordinator'),
            ('hr_approver', 'HR Approver'),
        ],
        db_index=True,
        help_text='Which step in the approval workflow this record represents'
    )
    
    # ✨ NEW: Project assignment for multi-project scenario
    # Soft-coded: Links the approval to a specific project when employee works on multiple projects
    project_number = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text='Project number/code this approval is for (when employee is assigned to multiple projects)'
    )
    project_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Project name for better readability'
    )
    
    # Approval status
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='pending',
        db_index=True,
    )
    
    # Decision details
    decision_note = models.TextField(
        blank=True,
        null=True,
        help_text='Optional note/reason from the approver'
    )
    decided_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the approval decision was made'
    )
    
    # Notification tracking
    notification_sent = models.BooleanField(
        default=False,
        help_text='Whether approval request notification was sent to approver'
    )
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_count = models.IntegerField(
        default=0,
        help_text='Number of reminder notifications sent'
    )
    last_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'exit_approval'
        ordering = ['offboarding_record', 'approval_step', 'created_at']
        indexes = [
            models.Index(fields=['offboarding_record', 'approval_step']),
            models.Index(fields=['approver', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]
        # Ensure one approval record per approver per offboarding
        unique_together = [['offboarding_record', 'approver', 'approval_step']]
    
    def __str__(self):
        approver_name = self.approver.get_full_name() if self.approver else 'Unassigned'
        return f"{self.get_approval_step_display()} - {approver_name} ({self.status})"
