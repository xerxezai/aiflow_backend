"""
Migration 0018 — Default Access System

1. Add module 'hr_self_service' — personal leave/attendance/payslip only.
2. Assign hr_self_service to the viewer role and every other non-admin role.
3. Mark the viewer role as is_system_role=True (cannot be deleted).
4. Auto-assign the viewer role to every active user who currently has NO role.

All constants are soft-coded at the top — change values here only.
"""
from django.db import migrations

# ── Soft-coded constants ───────────────────────────────────────────────────────
DEFAULT_ROLE_CODE = 'viewer'

HR_SELF_SERVICE = {
    'code':        'hr_self_service',
    'name':        'HR Self-Service',
    'description': (
        'Personal HR self-service — view your own leave balance, attendance, '
        'timesheet and payslips. No access to other employees\' data.'
    ),
    'order': 51,
}

# These roles already have elevated access — no need to auto-assign hr_self_service
# (they are already super-set or should be managed manually)
SKIP_HR_SS_ROLE_CODES = set()  # Empty = assign to ALL roles

# Roles that will NOT be auto-assigned as the viewer default for new users
# (i.e. admin-class roles manage their own profile)
ELEVATED_ROLE_CODES = {'super_admin', 'admin', 'hr_admin'}
# ──────────────────────────────────────────────────────────────────────────────


def setup_default_access(apps, schema_editor):
    Module    = apps.get_model('rbac', 'Module')
    Role      = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole  = apps.get_model('rbac', 'UserRole')

    # 1. Create hr_self_service module (idempotent)
    hr_ss_module, _ = Module.objects.get_or_create(
        code=HR_SELF_SERVICE['code'],
        defaults={
            'name':        HR_SELF_SERVICE['name'],
            'description': HR_SELF_SERVICE['description'],
            'is_active':   True,
            'order':       HR_SELF_SERVICE['order'],
        },
    )

    # 2. Assign hr_self_service to EVERY active role so all users can
    #    always access their personal HR self-service area.
    for role in Role.objects.filter(is_active=True):
        if role.code in SKIP_HR_SS_ROLE_CODES:
            continue
        RoleModule.objects.get_or_create(
            role=role,
            module=hr_ss_module,
        )

    # 3. Lock the viewer (default) role as a system role so admins cannot delete it
    try:
        viewer_role = Role.objects.get(code=DEFAULT_ROLE_CODE)
        if not viewer_role.is_system_role:
            viewer_role.is_system_role = True
            viewer_role.save()
    except Role.DoesNotExist:
        return  # Viewer role doesn't exist — nothing more to do

    # 4. Backfill: give every ACTIVE user who has no role the viewer role
    #    This ensures all existing users immediately get the default access set.
    profiles_with_roles = UserRole.objects.values_list('user_profile_id', flat=True).distinct()
    orphan_profiles = UserProfile.objects.filter(
        is_deleted=False,
        status='active',
    ).exclude(id__in=profiles_with_roles)

    for profile in orphan_profiles:
        UserRole.objects.get_or_create(
            user_profile=profile,
            role=viewer_role,
            defaults={'is_primary': True},
        )


def reverse_setup(apps, schema_editor):
    # Intentionally a no-op — do not strip hr_self_service on rollback
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0017_ensure_tanzeem_super_admin'),
    ]

    operations = [
        migrations.RunPython(setup_default_access, reverse_code=reverse_setup),
    ]
