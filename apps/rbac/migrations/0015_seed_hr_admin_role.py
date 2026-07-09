"""
Seed HR & Payroll Administrator role and HR/Payroll/Timesheet modules.

hr_admin is a sensitive Level-2 system role.  Only Super Administrators
should grant it to users; it enables the hr_management, payroll, and
timesheet modules.

Follows the same idempotent pattern as 0014_seed_ai_pricing:
rows are only created if they do not already exist.
"""
from django.db import migrations


# ── New modules to add ──────────────────────────────────────────────────────
NEW_MODULES = [
    {
        'code': 'hr_management',
        'name': 'Human Resources',
        'icon': 'Users',
        'order': 70,
        'description': 'HR management — employee records, leave, and workforce planning',
        'is_active': True,
    },
    {
        'code': 'payroll',
        'name': 'Payroll Engine',
        'icon': 'DollarSign',
        'order': 71,
        'description': 'Payroll processing, salary slips, and compensation management',
        'is_active': True,
    },
    {
        'code': 'timesheet',
        'name': 'Timesheet & Attendance',
        'icon': 'Clock',
        'order': 72,
        'description': 'Employee timesheet tracking and biometric attendance reports',
        'is_active': True,
    },
]

# ── New role to add ─────────────────────────────────────────────────────────
HR_ADMIN_ROLE = {
    'code': 'hr_admin',
    'name': 'HR & Payroll Administrator',
    'level': 2,
    'description': (
        'Full access to HR, Payroll, and Timesheet data. '
        'Sensitive role — grant only via Super Administrator.'
    ),
    'is_system_role': True,
    'is_active': True,
}


def seed_hr_admin(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    Role   = apps.get_model('rbac', 'Role')

    # 1. Create/update modules
    created_module_ids = []
    for m in NEW_MODULES:
        obj, _ = Module.objects.get_or_create(
            code=m['code'],
            defaults={
                'name':        m['name'],
                'icon':        m['icon'],
                'order':       m['order'],
                'description': m['description'],
                'is_active':   m['is_active'],
            },
        )
        created_module_ids.append(obj.id)

    # 2. Create role (idempotent — skip if already present)
    role, created = Role.objects.get_or_create(
        code=HR_ADMIN_ROLE['code'],
        defaults={
            'name':           HR_ADMIN_ROLE['name'],
            'level':          HR_ADMIN_ROLE['level'],
            'description':    HR_ADMIN_ROLE['description'],
            'is_system_role': HR_ADMIN_ROLE['is_system_role'],
            'is_active':      HR_ADMIN_ROLE['is_active'],
        },
    )

    # 3. Assign the three new modules to the hr_admin role
    for module_id in created_module_ids:
        role.modules.add(module_id)


def unseed_hr_admin(apps, schema_editor):
    """Reverse migration — remove role and modules created above."""
    Module = apps.get_model('rbac', 'Module')
    Role   = apps.get_model('rbac', 'Role')
    Role.objects.filter(code='hr_admin', is_system_role=True).delete()
    Module.objects.filter(code__in=['hr_management', 'payroll', 'timesheet']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0014_seed_ai_pricing'),
    ]

    operations = [
        migrations.RunPython(seed_hr_admin, reverse_code=unseed_hr_admin),
    ]
