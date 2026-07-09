"""
Serializers for user models.
Smart data validation and transformation.
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import UserProfile  # ⚠️ DEPRECATED - migrating to hr_core.EmployeeMaster

# New employee management
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.services import EmployeeService

User = get_user_model()

# ---------------------------------------------------------------------------
# SOFT-CODED: Controls whether self-registered accounts are immediately active.
# False (default) = account is disabled until a super-administrator activates
#   it via the Django admin or User Management panel.
# True            = accounts become active immediately on registration (legacy
#   behaviour — only use in trusted internal-only deployments).
# ---------------------------------------------------------------------------
SELF_REGISTRATION_ACTIVE = False


class UserProfileSerializer(serializers.ModelSerializer):
    """
    ⚠️ DEPRECATED - Serializer for old user profile model.
    
    This serializer is deprecated. New code should use EmployeeMasterSerializer
    from apps.hr_core.serializers instead.
    
    Kept for backward compatibility with existing API endpoints.
    """
    
    class Meta:
        model = UserProfile
        fields = ['date_of_birth', 'address', 'city', 'country', 'postal_code']


class UserSerializer(serializers.ModelSerializer):
    """Serializer for user model."""
    profile = UserProfileSerializer(read_only=True)
    roles = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                  'phone_number', 'avatar', 'bio', 'is_verified', 'is_staff', 
                  'is_superuser', 'profile', 'roles']
        read_only_fields = ['id', 'is_verified', 'is_staff', 'is_superuser']
    
    def get_roles(self, obj):
        """Get user's RBAC roles."""
        try:
            from apps.rbac.models import UserProfile as RBACUserProfile
            rbac_profile = RBACUserProfile.objects.filter(user=obj, is_deleted=False).first()
            if rbac_profile:
                roles = rbac_profile.roles.all()
                return [{'id': str(role.id), 'code': role.code, 'name': role.name, 'level': role.level} for role in roles]
        except Exception as e:
            # Log error for debugging
            import traceback
            print(f"[ERROR] UserSerializer.get_roles failed: {str(e)}")
            print(traceback.format_exc())
        return []


class UserRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm', 
                  'first_name', 'last_name']
    
    def validate(self, attrs):
        """Validate password confirmation."""
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs
    
    def create(self, validated_data):
        """Create new user with hashed password."""
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        # SOFT-CODED: new accounts are inactive until approved by super admin.
        # Controlled by the SELF_REGISTRATION_ACTIVE module-level constant.
        user = User.objects.create_user(**validated_data)
        user.is_active = SELF_REGISTRATION_ACTIVE
        user.set_password(password)
        user.last_password_change = timezone.now()
        user.must_reset_password = False
        user.save()
        
        # Create associated employee record using new EmployeeService
        try:
            EmployeeService.create_employee(
                user=user,
                email=user.email,
                first_name=user.first_name or '',
                last_name=user.last_name or ''
            )
        except Exception as e:
            # Log error but don't fail registration if employee creation fails
            import traceback
            print(f"[WARNING] Failed to create EmployeeMaster record during registration: {str(e)}")
            print(traceback.format_exc())
            # Fallback to old UserProfile for backward compatibility
            UserProfile.objects.create(user=user)
        
        return user
