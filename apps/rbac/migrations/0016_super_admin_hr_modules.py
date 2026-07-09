"""
Assign hr_management, payroll, and timesheet modules to the super_admin role.

Migration 0015 added these modules and linked them only to hr_admin.
Super Administrator (level 1) should have access to ALL modules including
sensitive HR ones.  This migration backfills those RoleModule rows
idempotently (safe to run multiple times / on fresh Railway deployments).
"""
from django.db import migrations


HR_MODULE_CODES = ['hr_management', 'payroll', 'timesheet']


def assign_hr_modules_to_super_admin(apps, schema_editor):
    Role      = apps.get_model('rbac', 'Role')
    Module    = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')

    try:
        super_admin = Role.objects.get(code='super_admin')
    except Role.DoesNotExist:
        # Nothing to do on a completely empty DB — the seed command handles it
        return

    for code in HR_MODULE_CODES:
        try:
            module = Module.objects.get(code=code, is_active=True)
            RoleModule.objects.get_or_create(role=super_admin, module=module)
        except Module.DoesNotExist:
            pass  # module not seeded yet — safe to skip


def reverse_assign(apps, schema_editor):
    """Reverse: remove hr RoleModule rows from super_admin only."""
    Role      = apps.get_model('rbac', 'Role')
    Module    = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')

    try:
        super_admin = Role.objects.get(code='super_admin')
    except Role.DoesNotExist:
        return

    RoleModule.objects.filter(
        role=super_admin,
        module__code__in=HR_MODULE_CODES,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0015_seed_hr_admin_role'),
    ]

    operations = [
        migrations.RunPython(
            assign_hr_modules_to_super_admin,
            reverse_code=reverse_assign,
        ),
    ]
