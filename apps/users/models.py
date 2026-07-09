"""
User models for authentication and profile management.
Smart user management with custom fields.
"""
import warnings
from django.contrib.auth.models import AbstractUser
from django.db import models
from apps.core.models import TimeStampedModel


class User(AbstractUser):
    """
    Custom user model extending Django's AbstractUser.
    """
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    
    # First-time login and password reset tracking
    is_first_login = models.BooleanField(default=True, help_text='True if user has not logged in yet')
    must_reset_password = models.BooleanField(default=False, help_text='True if user must reset password')
    temp_password_created_at = models.DateTimeField(null=True, blank=True, help_text='When temporary password was set')
    last_password_change = models.DateTimeField(null=True, blank=True, help_text='Last time password was changed')
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return self.email


class UserProfile(TimeStampedModel):
    """
    ⚠️ DEPRECATED as of 2026-07-03
    
    This model is deprecated and will be removed in a future version.
    All employee data has been migrated to hr_core.EmployeeMaster.
    
    MIGRATION PATH:
    - OLD: from apps.users.models import UserProfile
    - NEW: from apps.hr_core.models import EmployeeMaster
    - SERVICE: from apps.hr_core.services import EmployeeService
    
    NOTE: This is NOT the same as apps.rbac.models.UserProfile (RBAC system).
          For RBAC user profiles, continue using apps.rbac.models.UserProfile.
    
    Extended user profile information.
    Includes fields from Sympa HR onboarding form.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    # Basic profile fields
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    
    # HR Onboarding fields (from Sympa HR)
    preferred_given_name = models.CharField(max_length=100, blank=True, null=True, help_text='Preferred first name')
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_employees', help_text='Direct manager')
    initials = models.CharField(max_length=10, blank=True, null=True)
    employee_number = models.CharField(max_length=50, blank=True, null=True, db_index=True, help_text='Employee number from HR system')
    account_name = models.CharField(max_length=100, blank=True, null=True, help_text='Active Directory account name')
    employment_id = models.CharField(max_length=50, blank=True, null=True, db_index=True, help_text='Employment ID from HRM system')
    candidate_id = models.CharField(max_length=50, blank=True, null=True, help_text='Candidate ID from recruitment')
    
    # Organization fields
    company = models.CharField(max_length=100, blank=True, null=True)
    business_unit = models.CharField(max_length=100, blank=True, null=True)
    division = models.CharField(max_length=100, blank=True, null=True)
    business_area = models.CharField(max_length=100, blank=True, null=True)
    office = models.CharField(max_length=100, blank=True, null=True)
    job_title_finland = models.CharField(max_length=200, blank=True, null=True, help_text='Job title for Finland')
    job_title_uae = models.CharField(max_length=200, blank=True, null=True, help_text='Job title for UAE')
    
    # Additional flags
    protected_identity = models.BooleanField(default=False, help_text='Protected identity flag')
    is_test_person = models.BooleanField(default=False, help_text='Test person flag')
    not_signed = models.BooleanField(default=False, help_text='Not signed flag')
    
    # Implementation and testing fields
    implementation_test = models.CharField(max_length=100, blank=True, null=True)
    hrm_test = models.CharField(max_length=100, blank=True, null=True)
    process_testing = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False  # ⚠️ DEPRECATED - Django will not manage this table
        db_table = 'user_profiles'
        verbose_name = 'User Profile (Deprecated)'
        verbose_name_plural = 'User Profiles (Deprecated)'

    def __str__(self):
        return f"Profile of {self.user.email}"
    
    def save(self, *args, **kwargs):
        """Override save to emit deprecation warning."""
        warnings.warn(
            "UserProfile (apps.users.models) is deprecated. "
            "Use EmployeeMaster from apps.hr_core instead. "
            "For RBAC profiles, use apps.rbac.models.UserProfile.",
            DeprecationWarning,
            stacklevel=2
        )
        super().save(*args, **kwargs)
