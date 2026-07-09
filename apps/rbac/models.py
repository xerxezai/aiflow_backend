"""
RBAC Models - Enterprise Role-Based Access Control
Designed for regulated Oil & Gas environment
"""
import os
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from apps.core.models import TimeStampedModel


def get_profile_photo_storage():
    """Return AvatarStorage (S3) when USE_S3 is enabled, otherwise local FileSystemStorage.
    Used as a callable for the profile_photo ImageField so the correct backend is
    selected at runtime without a hard dependency on boto3.
    """
    if os.environ.get('USE_S3', 'False').lower() == 'true':
        try:
            from apps.core.storage_backends import AvatarStorage
            return AvatarStorage()
        except Exception:
            pass
    from django.core.files.storage import FileSystemStorage
    from django.conf import settings
    return FileSystemStorage(location=str(getattr(settings, 'MEDIA_ROOT', 'media')))

User = get_user_model()


class Organization(TimeStampedModel):
    """
    Multi-tenant organization model
    Each user belongs to one organization
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    
    # Contact information
    primary_contact_name = models.CharField(max_length=255, blank=True)
    primary_contact_email = models.EmailField(blank=True)
    primary_contact_phone = models.CharField(max_length=20, blank=True)
    
    # Address
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    
    # S3 storage configuration
    s3_bucket_name = models.CharField(max_length=255, blank=True)
    s3_region = models.CharField(max_length=50, default='us-east-1')
    
    class Meta:
        db_table = 'rbac_organizations'
        verbose_name = 'Organization'
        verbose_name_plural = 'Organizations'
        ordering = ['name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name


class Module(TimeStampedModel):
    """
    Application modules/features that can be enabled/disabled per role
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    icon = models.CharField(max_length=50, blank=True)
    order = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'rbac_modules'
        verbose_name = 'Module'
        verbose_name_plural = 'Modules'
        ordering = ['order', 'name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name


class Permission(TimeStampedModel):
    """
    Granular permissions for actions within modules
    """
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('read', 'Read'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('approve', 'Approve'),
        ('export', 'Export'),
        ('import', 'Import'),
        ('execute', 'Execute'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='permissions')
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'rbac_permissions'
        verbose_name = 'Permission'
        verbose_name_plural = 'Permissions'
        ordering = ['module', 'action']
        unique_together = ['module', 'code']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['module', 'action']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.module.name}: {self.name}"


class Role(TimeStampedModel):
    """
    User roles with hierarchical structure
    """
    ROLE_LEVEL_CHOICES = [
        (1, 'Super Administrator'),
        (2, 'Admin'),
        (3, 'Manager'),
        (4, 'Engineer'),
        (5, 'Reviewer'),
        (6, 'Viewer'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    level = models.IntegerField(choices=ROLE_LEVEL_CHOICES, default=6)
    is_active = models.BooleanField(default=True)
    is_system_role = models.BooleanField(default=False)  # Cannot be deleted
    
    # Permissions
    permissions = models.ManyToManyField(
        Permission,
        through='RolePermission',
        related_name='roles'
    )
    
    # Module access
    modules = models.ManyToManyField(
        Module,
        through='RoleModule',
        related_name='roles'
    )
    
    class Meta:
        db_table = 'rbac_roles'
        verbose_name = 'Role'
        verbose_name_plural = 'Roles'
        ordering = ['level', 'name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['level']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name
    
    def has_permission(self, permission_code):
        """Check if role has specific permission"""
        return self.permissions.filter(code=permission_code, is_active=True).exists()
    
    def has_module_access(self, module_code):
        """Check if role has access to module"""
        return self.modules.filter(code=module_code, is_active=True).exists()


class RolePermission(TimeStampedModel):
    """
    Many-to-many relationship between roles and permissions
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'rbac_role_permissions'
        unique_together = ['role', 'permission']
        indexes = [
            models.Index(fields=['role', 'permission']),
        ]
    
    def __str__(self):
        return f"{self.role.name} - {self.permission.name}"


class RoleModule(TimeStampedModel):
    """
    Many-to-many relationship between roles and modules
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    module = models.ForeignKey(Module, on_delete=models.CASCADE)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'rbac_role_modules'
        unique_together = ['role', 'module']
        indexes = [
            models.Index(fields=['role', 'module']),
        ]
    
    def __str__(self):
        return f"{self.role.name} - {self.module.name}"


class UserProfile(TimeStampedModel):
    """
    Extended user profile with organization and RBAC
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
        ('pending', 'Pending'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='rbac_profile')
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name='users'
    )
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    is_mfa_enabled = models.BooleanField(default=False)
    
    # Roles
    roles = models.ManyToManyField(
        Role,
        through='UserRole',
        related_name='user_profiles'
    )
    
    # Metadata
    employee_id = models.CharField(max_length=50, blank=True)
    department = models.CharField(max_length=100, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)  # For email verification tokens, etc.
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subordinates'
    )
    
    # Login tracking
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    failed_login_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    
    # Password policy
    must_change_password = models.BooleanField(
        default=False, 
        help_text="User must change password on next login"
    )
    
    # Profile customization
    profile_photo = models.ImageField(
        upload_to='profile_photos/',
        storage=get_profile_photo_storage,
        null=True,
        blank=True,
        help_text="User profile photo — stored in S3 (production) or local media (dev)"
    )
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    location = models.CharField(max_length=100, blank=True)
    
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deleted_profiles'
    )
    
    class Meta:
        db_table = 'rbac_user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['employee_id']),
            models.Index(fields=['is_deleted']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.organization.name}"

    def save(self, *args, **kwargs):
        # Normalise employee_id so it matches the biometric employee_code
        # stored in TimesheetEvent / DailyAttendanceSummary / EmployeeLeaveRecord.
        # Prevents lookup misses caused by trailing spaces entered via the UI.
        if self.employee_id:
            try:
                from apps.timesheet.identity import norm_code
                self.employee_id = norm_code(self.employee_id)
            except Exception:
                self.employee_id = str(self.employee_id).strip()
        super().save(*args, **kwargs)
    
    def has_permission(self, permission_code):
        """Check if user has specific permission through any active role"""
        from apps.rbac.models import UserRole
        user_role_ids = UserRole.objects.filter(
            user_profile=self,
            role__is_active=True
        ).values_list('role_id', flat=True)
        return Permission.objects.filter(
            roles__id__in=user_role_ids,
            code=permission_code,
            is_active=True
        ).exists()
    
    def has_module_access(self, module_code):
        """Check if user has access to module through any active role"""
        from apps.rbac.models import UserRole
        user_role_ids = UserRole.objects.filter(
            user_profile=self,
            role__is_active=True
        ).values_list('role_id', flat=True)
        return Module.objects.filter(
            roles__id__in=user_role_ids,
            code=module_code,
            is_active=True
        ).exists()
    
    def get_all_permissions(self):
        """Get all permissions from all assigned roles (with caching)"""
        from django.core.cache import cache
        cache_key = f'user_permissions_{self.id}'
        permissions = cache.get(cache_key)
        
        if permissions is None:
            permissions = list(Permission.objects.filter(
                roles__in=self.roles.filter(is_active=True),
                is_active=True
            ).distinct())
            # Cache for 5 minutes
            cache.set(cache_key, permissions, 300)
        
        return permissions
    
    def get_all_modules(self):
        """Get all accessible modules from all assigned roles (with caching)"""
        from django.core.cache import cache
        cache_key = f'user_modules_{self.id}'
        modules = cache.get(cache_key)
        
        if modules is None:
            # Get role IDs through UserRole relationship — only active roles
            user_role_ids = UserRole.objects.filter(
                user_profile=self,
                role__is_active=True
            ).values_list('role_id', flat=True)
            
            # Get modules linked to these roles through RoleModule
            modules = list(Module.objects.filter(
                rolemodule__role_id__in=user_role_ids,
                is_active=True
            ).distinct())

            # Soft-coded global access modules (for all authenticated users)
            try:
                from apps.rbac.discipline_config import DisciplineAccessConfig
                global_codes = DisciplineAccessConfig.get_globally_enabled_module_codes()
                if global_codes:
                    global_modules = list(Module.objects.filter(code__in=global_codes, is_active=True))
                    existing_ids = {m.id for m in modules}
                    for mod in global_modules:
                        if mod.id not in existing_ids:
                            modules.append(mod)
            except Exception:
                # Non-fatal: keep role-based modules if config resolution fails
                pass

            # Cache for 60 seconds — short TTL so role changes propagate quickly
            cache.set(cache_key, modules, 60)
        
        return modules


class UserRole(TimeStampedModel):
    """
    Many-to-many relationship between users and roles
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    is_primary = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'rbac_user_roles'
        unique_together = ['user_profile', 'role']
        indexes = [
            models.Index(fields=['user_profile', 'role']),
            models.Index(fields=['is_primary']),
        ]
    
    def __str__(self):
        return f"{self.user_profile.user.email} - {self.role.name}"


class UserStorage(TimeStampedModel):
    """
    Track user file storage in S3
    """
    FILE_TYPE_CHOICES = [
        ('document', 'Document'),
        ('image', 'Image'),
        ('drawing', 'P&ID Drawing'),
        ('report', 'Report'),
        ('model', 'AI Model'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='files')
    
    # File metadata
    filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES)
    file_size = models.BigIntegerField()  # Size in bytes
    mime_type = models.CharField(max_length=100)
    
    # S3 path
    s3_bucket = models.CharField(max_length=255)
    s3_key = models.CharField(max_length=1024)  # Full S3 path
    s3_region = models.CharField(max_length=50)
    
    # Checksum for integrity
    md5_checksum = models.CharField(max_length=32, blank=True)
    
    # Access tracking
    download_count = models.IntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'rbac_user_storage'
        verbose_name = 'User Storage'
        verbose_name_plural = 'User Storage'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user_profile', 'file_type']),
            models.Index(fields=['s3_bucket', 's3_key']),
            models.Index(fields=['is_deleted']),
        ]
    
    def __str__(self):
        return f"{self.filename} - {self.user_profile.user.email}"
    
    @property
    def s3_path(self):
        """Full S3 path"""
        return f"s3://{self.s3_bucket}/{self.s3_key}"


class EngineerProfile(TimeStampedModel):
    """
    Dedicated engineering competency & project-assignment profile for each user.
    One-to-one with UserProfile — stored in its own DB table (rbac_engineer_profiles).
    """
    EXPERTISE_CHOICES = [
        ('junior',    'Junior'),
        ('mid',       'Mid-Level'),
        ('senior',    'Senior'),
        ('principal', 'Principal'),
        ('lead',      'Lead'),
        ('manager',   'Engineering Manager'),
    ]
    AVAILABILITY_CHOICES = [
        ('available',  'Available'),
        ('partial',    'Partially Available'),
        ('busy',       'Fully Committed'),
        ('on_leave',   'On Leave'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.OneToOneField(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='engineer_profile',
    )

    # Competency
    expertise_level           = models.CharField(max_length=20, choices=EXPERTISE_CHOICES, blank=True)
    years_experience          = models.PositiveIntegerField(default=0)
    engineering_disciplines   = models.JSONField(default=list, blank=True)   # ["Process", "Piping", …]
    technical_skills          = models.JSONField(default=list, blank=True)   # [{"name": "HYSYS", "proficiency": 4}, …]
    languages                 = models.JSONField(default=list, blank=True)   # ["English", "Arabic"]
    certifications            = models.JSONField(default=list, blank=True)   # [{name, issuer, year, expiry_date, id}, …]

    # Availability
    availability_status       = models.CharField(max_length=20, choices=AVAILABILITY_CHOICES, default='available')
    availability_percentage   = models.PositiveIntegerField(default=100)
    next_available_date       = models.DateField(null=True, blank=True)
    max_concurrent_projects   = models.PositiveIntegerField(default=2)
    preferred_project_types   = models.JSONField(default=list, blank=True)  # ["FEED", "Greenfield …"]

    # Current project assignments (management visibility)
    current_projects          = models.JSONField(default=list, blank=True)  # [{name, client, role, allocation, …}, …]

    class Meta:
        db_table = 'rbac_engineer_profiles'
        verbose_name = 'Engineer Profile'
        verbose_name_plural = 'Engineer Profiles'
        indexes = [
            models.Index(fields=['expertise_level']),
            models.Index(fields=['availability_status']),
        ]

    def __str__(self):
        return f"EngineerProfile({self.user_profile.user.email})"

    def to_dict(self):
        """Serialise to the same shape the frontend expects."""
        return {
            'expertise_level':          self.expertise_level,
            'years_experience':         self.years_experience,
            'engineering_disciplines':  self.engineering_disciplines,
            'technical_skills':         self.technical_skills,
            'languages':                self.languages,
            'certifications':           self.certifications,
            'availability_status':      self.availability_status,
            'availability_percentage':  self.availability_percentage,
            'next_available_date':      str(self.next_available_date) if self.next_available_date else '',
            'max_concurrent_projects':  self.max_concurrent_projects,
            'preferred_project_types':  self.preferred_project_types,
            'current_projects':         self.current_projects,
        }


class AuditLog(TimeStampedModel):
    """
    Comprehensive audit logging for compliance
    """
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('create', 'Create'),
        ('read', 'Read'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('role_assign', 'Role Assign'),
        ('role_revoke', 'Role Revoke'),
        ('permission_grant', 'Permission Grant'),
        ('permission_revoke', 'Permission Revoke'),
        ('file_upload', 'File Upload'),
        ('file_download', 'File Download'),
        ('file_delete', 'File Delete'),
        ('password_change', 'Password Change'),
        ('password_reset', 'Password Reset'),
        ('mfa_enable', 'MFA Enable'),
        ('mfa_disable', 'MFA Disable'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Who
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    user_email = models.EmailField()  # Denormalized for historical record
    
    # What
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    resource_type = models.CharField(max_length=100)  # Model name
    resource_id = models.UUIDField(null=True, blank=True)
    resource_repr = models.CharField(max_length=255, blank=True)  # String representation
    
    # When & Where
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    # Details
    changes = models.JSONField(default=dict, blank=True)  # Before/after values
    metadata = models.JSONField(default=dict, blank=True)  # Additional context
    
    # Result
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    
    class Meta:
        db_table = 'rbac_audit_logs'
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
            models.Index(fields=['resource_type', 'resource_id']),
            models.Index(fields=['ip_address']),
            models.Index(fields=['-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.user_email} - {self.action} - {self.timestamp}"


class AccessRequest(TimeStampedModel):
    """
    User-initiated request for additional module access.

    Workflow:
      1. User submits a request (any authenticated user, via POST /rbac/access-requests/)
      2. Super Administrator reviews the pending list (/admin/access-requests)
      3. Admin approves → UserRole for the corresponding module's role is assigned
         OR Admin denies → status set to 'denied' with an optional admin_note

    All status values are soft-coded in STATUS_CHOICES below.
    """

    # Soft-coded status labels — update here only
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DENIED   = 'denied'

    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_DENIED,   'Denied'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='access_requests',
    )
    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name='access_requests',
    )
    reason = models.TextField(
        blank=True,
        help_text="Why does the user need access to this module?",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    # Review tracking
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_access_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_note  = models.TextField(blank=True, help_text="Admin response or reason for denial")

    class Meta:
        db_table    = 'rbac_access_requests'
        ordering    = ['-created_at']
        verbose_name = 'Access Request'
        verbose_name_plural = 'Access Requests'
        indexes = [
            models.Index(fields=['user_profile', 'status']),
            models.Index(fields=['module',        'status']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user_profile.user.email} → {self.module.name} ({self.status})"


# Re-export models from sibling modules so Django auto-discovery picks them up
# (analytics_models is already discovered via admin.py import on startup, but explicit is safer)
from .ai_champion_models import (  # noqa: F401,E402
    AIPricingConfig,
    AIUsageLog,
    ActivityEvent,
    MonthlyChampion,
)
