"""
Migration 0020 — Seed Business & HR Self-Service Modules
=========================================================
Seeds the following module codes that are referenced in the Sidebar
but were missing from the DB:

  Business modules (new):
    - finance          Finance
    - sales            Sales / Business Development
    - project_control  Project Control
    - procurement      Procurement (vendors, POs, receipts)

  HR module (backfill — seeded in 0018 but not in rbac_config.py):
    - hr_self_service  HR Self-Service

All modules are created idempotently (get_or_create) and assigned
to the super_admin and admin roles.  Finance, sales, project_control,
and procurement are also listed in the ROLE_MODULE_POLICY for admin
going forward via rbac_config.py.
"""
from django.db import migrations

# ── Soft-coded catalogue for this migration ───────────────────────────────────
NEW_MODULES = [
    {
        'code':        'hr_self_service',
        'name':        'HR Self-Service',
        'icon':        'User',
        'order':       73,
        'description': 'Personal leave requests, attendance records and payslip access',
    },
    {
        'code':        'finance',
        'name':        'Finance',
        'icon':        'CreditCard',
        'order':       80,
        'description': 'Invoice tracking, billing and financial management',
    },
    {
        'code':        'sales',
        'name':        'Sales',
        'icon':        'TrendingUp',
        'order':       81,
        'description': 'Internal sales pipeline and business development',
    },
    {
        'code':        'project_control',
        'name':        'Project Control',
        'icon':        'Briefcase',
        'order':       82,
        'description': 'Project planning, tracking and schedule control',
    },
    {
        'code':        'procurement',
        'name':        'Procurement',
        'icon':        'ShoppingCart',
        'order':       83,
        'description': 'Vendors, purchase orders, requisitions and goods receipts',
    },
]

# Roles that should receive all these new modules
GRANT_TO_ROLES = ['super_admin', 'admin']


def seed_business_modules(apps, schema_editor):
    Module    = apps.get_model('rbac', 'Module')
    Role      = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias  = schema_editor.connection.alias

    # 1. Create / update every module
    module_objs = {}
    for m in NEW_MODULES:
        obj, created = Module.objects.using(db_alias).get_or_create(
            code=m['code'],
            defaults={
                'name':        m['name'],
                'description': m['description'],
                'icon':        m['icon'],
                'order':       m['order'],
                'is_active':   True,
            },
        )
        if not created:
            # Keep name/description/order up-to-date
            obj.name        = m['name']
            obj.description = m['description']
            obj.order       = m['order']
            obj.is_active   = True
            obj.save(using=db_alias)
        module_objs[m['code']] = obj
        action = 'created' if created else 'updated'
        print(f'  [0020] ✓ Module {action}: {m["code"]}')

    # 2. Assign to target roles
    for role_code in GRANT_TO_ROLES:
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
        except Role.DoesNotExist:
            print(f'  [0020] ⚠ Role not found: {role_code} — skipping')
            continue
        for mod in module_objs.values():
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role,
                module=mod,
            )
            if created:
                print(f'  [0020] ✓ Granted {mod.code} → {role_code}')


def noop(apps, schema_editor):
    """Intentional no-op reverse — do not strip modules on rollback."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0019_access_request_model'),
    ]

    operations = [
        migrations.RunPython(seed_business_modules, noop),
    ]
