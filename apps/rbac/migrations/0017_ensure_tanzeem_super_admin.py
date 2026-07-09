"""
Migration 0017 — Ensure tanzeem.agra@rejlers.ae has Super Administrator access.

Idempotent: safe to run multiple times.
Sets Django-level is_superuser=True + is_staff=True AND assigns the super_admin RBAC role.
"""
from django.db import migrations

# Soft-coded — change here only if the target email or role code ever changes
TARGET_EMAIL = 'tanzeem.agra@rejlers.ae'
SUPER_ADMIN_ROLE_CODE = 'super_admin'


def assign_super_admin(apps, schema_editor):
    User = apps.get_model('users', 'User')  # custom User model (AUTH_USER_MODEL = 'users.User')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole = apps.get_model('rbac', 'UserRole')
    Role = apps.get_model('rbac', 'Role')

    try:
        user = User.objects.get(email=TARGET_EMAIL)
    except User.DoesNotExist:
        # User not yet created — migration is idempotent, nothing to do
        return

    # Ensure Django-level superuser / staff flags
    changed = False
    if not user.is_superuser:
        user.is_superuser = True
        changed = True
    if not user.is_staff:
        user.is_staff = True
        changed = True
    if changed:
        user.save()

    try:
        role = Role.objects.get(code=SUPER_ADMIN_ROLE_CODE)
    except Role.DoesNotExist:
        return

    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        return

    # Idempotent role assignment — is_primary=True for super_admin
    UserRole.objects.get_or_create(
        user_profile=profile,
        role=role,
        defaults={'is_primary': True},
    )


def reverse_assign(apps, schema_editor):
    # Intentionally irreversible — never strip super admin on rollback
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0016_super_admin_hr_modules'),
    ]

    operations = [
        migrations.RunPython(assign_super_admin, reverse_code=reverse_assign),
    ]
