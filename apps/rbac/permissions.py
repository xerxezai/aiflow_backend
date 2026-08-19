"""
RBAC Permissions - Django Rest Framework Permission Classes
Enterprise-grade permission enforcement
"""
from rest_framework import permissions
from .models import UserProfile


class IsSuperAdmin(permissions.BasePermission):
    """
    Permission class to check if user is super admin
    Also allows Django superuser as fallback
    """
    message = "You must be a super admin to perform this action."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Allow Django superuser as fallback
        if request.user.is_superuser:
            return True
        
        # Check RBAC role
        try:
            profile = request.user.rbac_profile
            return profile.roles.filter(code='super_admin', is_active=True).exists()
        except UserProfile.DoesNotExist:
            return False


class IsAdmin(permissions.BasePermission):
    """
    Permission class to check if user is admin (includes super admin and ICT admin)
    SECURITY: Only is_superuser bypasses (emergency access), is_staff does NOT bypass
    """
    message = "You must be an admin to perform this action."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # ONLY Django superuser bypasses (emergency access)
        # is_staff does NOT bypass - it only grants Django admin panel access
        if request.user.is_superuser:
            return True
        
        # Check RBAC roles (soft-coded from rbac_config.py)
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(
                code__in=['super_admin', 'admin', 'ict_admin', 'hr_admin'],
                is_active=True
            ).exists():
                return True
            # Module-based check for custom roles
            if profile.has_module_access('user_mgmt') or profile.has_module_access('hr_management'):
                return True
            return False
        except UserProfile.DoesNotExist:
            return False


class HasPermission(permissions.BasePermission):
    """
    Permission class to check if user has specific permission
    Usage: permission_classes = [HasPermission]
           permission_required = 'pid_analysis.create'
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin has all permissions
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Check for specific permission
        permission_required = getattr(view, 'permission_required', None)
        if not permission_required:
            return True  # No specific permission required
        
        return profile.has_permission(permission_required)


class HasModuleAccess(permissions.BasePermission):
    """
    Permission class to check if user has access to specific module
    Usage: permission_classes = [HasModuleAccess]
           module_required = 'pid_analysis'
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin has all module access
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Check for specific module access
        module_required = getattr(view, 'module_required', None)
        if not module_required:
            return True  # No specific module required
        
        return profile.has_module_access(module_required)


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Permission class to check if user is owner of object or admin
    """
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin can access everything
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Check if user is owner
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'user_profile'):
            return obj.user_profile.user == request.user
        elif hasattr(obj, 'created_by'):
            return obj.created_by == request.user
        
        return False


class SameOrganization(permissions.BasePermission):
    """
    Permission class to ensure users can only access resources from their organization
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin can access all organizations
        try:
            user_profile = request.user.rbac_profile
            if user_profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
            
            # Check if object belongs to same organization
            if hasattr(obj, 'organization'):
                return obj.organization == user_profile.organization
            elif hasattr(obj, 'user_profile'):
                return obj.user_profile.organization == user_profile.organization
        except UserProfile.DoesNotExist:
            return False
        
        return False


class CanManageUsers(permissions.BasePermission):
    """
    Permission class for user management operations
    SECURITY: Only is_superuser bypasses, is_staff requires user_mgmt module
    """
    message = "You don't have permission to manage users."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # ONLY Django superuser bypasses (emergency access)
        if request.user.is_superuser:
            return True
        
        try:
            profile = request.user.rbac_profile
            
            # Super admin, admin, and ICT admin can manage users (soft-coded)
            if profile.roles.filter(
                code__in=['super_admin', 'admin', 'ict_admin', 'hr_admin'],
                is_active=True
            ).exists():
                return True

            # Module-based check for custom roles
            if profile.has_module_access('hr_management') or profile.has_module_access('user_mgmt'):
                return True
            
            # Check specific permission
            return profile.has_permission('users.manage')
        except UserProfile.DoesNotExist:
            return False


class CanManageRoles(permissions.BasePermission):
    """
    Permission class for role management operations
    Allows Django superuser as fallback
    """
    message = "You don't have permission to manage roles."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Allow Django superuser as fallback
        if request.user.is_superuser:
            return True
        
        try:
            profile = request.user.rbac_profile
            
            # Only super admin can manage roles
            return profile.roles.filter(code='super_admin', is_active=True).exists()
        except UserProfile.DoesNotExist:
            return False


class ReadOnly(permissions.BasePermission):
    """
    Permission class to allow read-only access
    """
    def has_permission(self, request, view):
        return request.method in permissions.SAFE_METHODS


class HasDisciplineAccess(permissions.BasePermission):
    """
    Permission class to check if user has access to a module based on discipline/department
    Soft-coded configuration for scalability with 300+ users
    
    Uses UserProfile.department field mapped to module access via DisciplineAccessConfig
    
    Usage in ViewSet:
        permission_classes = [IsAuthenticated, HasDisciplineAccess]
        module_required = 'pid_verification'  # or 'pfd_quality', etc.
    
    Configuration:
        - No code changes needed to allow new disciplines
        - Update DisciplineAccessConfig in apps.rbac.discipline_config
        - Add discipline to accessible_by_disciplines list for module
        - Cache automatically invalidates (1-hour TTL)
    """
    message = "Your discipline does not have access to this module."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        try:
            user_profile = request.user.rbac_profile
        except UserProfile.DoesNotExist:
            return False
        
        # Get module requirement from view
        module_required = getattr(view, 'module_required', None)
        if not module_required:
            return True  # No specific module required
        
        # Import here to avoid circular imports
        from .discipline_config import DisciplineAccessConfig
        
        return DisciplineAccessConfig.user_has_module_access(user_profile, module_required)
