"""
RBAC Signals
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.core.cache import cache
from .models import UserProfile, Organization, UserRole
from .utils import create_audit_log

User = get_user_model()


@receiver(post_save, sender=User)
def log_user_login(sender, instance, created, **kwargs):
    """
    Log user login events
    """
    if not created and instance.last_login:
        try:
            profile = instance.rbac_profile
            profile.last_login_at = instance.last_login
            profile.failed_login_attempts = 0  # Reset on successful login
            profile.save(update_fields=['last_login_at', 'failed_login_attempts'])
        except UserProfile.DoesNotExist:
            pass


@receiver(post_save, sender=UserRole)
@receiver(post_delete, sender=UserRole)
def clear_user_permissions_cache(sender, instance, **kwargs):
    """
    Clear user permissions and modules cache when roles are assigned/removed
    """
    profile_id = instance.user_profile_id
    cache.delete(f'user_permissions_{profile_id}')
    cache.delete(f'user_modules_{profile_id}')
    print(f"[Cache] Cleared permissions and modules cache for user {profile_id}")


# Soft-coded: read the default role code from rbac_config so a single config
# change flips the baseline role for the whole system. Was previously hardcoded
# to 'viewer' — now points to the Default role defined in DEFAULT_ROLE_CONFIG.
def _get_default_role_code():
    from .rbac_config import DEFAULT_ROLE_CONFIG
    return DEFAULT_ROLE_CONFIG.get('code', 'default')


@receiver(post_save, sender=UserProfile)
def assign_default_role_on_profile_creation(sender, instance, created, **kwargs):
    """
    Auto-assign the system default role to every new UserProfile so that all
    users have baseline access (engineering modules, common tools, HR
    self-service) without manual intervention.

    Super Administrators (Django is_superuser=True) are excluded — they
    bypass every module check and do not need the Default role.

    The role code is soft-coded via DEFAULT_ROLE_CONFIG in rbac_config.py.
    """
    if not created:
        return

    # Skip Super Administrators — they already bypass all access checks.
    if getattr(instance.user, 'is_superuser', False):
        return

    from .models import Role, UserRole  # local import to avoid circular
    default_code = _get_default_role_code()
    try:
        default_role = Role.objects.get(code=default_code, is_active=True)
        UserRole.objects.get_or_create(
            user_profile=instance,
            role=default_role,
            defaults={'is_primary': True},
        )
    except Role.DoesNotExist:
        # Default role not yet seeded (e.g. fresh migrations) — skip silently.
        pass
