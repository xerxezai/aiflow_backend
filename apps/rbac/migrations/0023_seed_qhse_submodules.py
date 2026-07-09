"""
Migration 0023 — Seed QHSE Sub-Module Codes
============================================
The QHSE section in the Sidebar has 6 distinct sub-features
(Overview, Details, Quality, Health & Safety, Environmental, Energy)
but previously ALL shared the single 'qhse' module code.

This migration adds 5 granular sub-module codes so each sub-feature
can be independently toggled per role in the Role Management panel:

  qhse                 (already exists) — Overview / root access  (8.1)
  qhse_detailed        (new) — Project Quality Details             (8.2)
  qhse_quality         (new) — Quality Management                  (8.3)
  qhse_health_safety   (new) — Health & Safety                     (8.4)
  qhse_environmental   (new) — Environmental                       (8.5)
  qhse_energy          (new) — Energy Management                   (8.6)

All new modules are idempotently created and granted to super_admin,
admin, and all engineering-discipline roles that already have 'qhse'.
"""
from django.db import migrations

# ── Soft-coded catalogue for this migration ───────────────────────────────────
NEW_MODULES = [
    {
        'code':        'qhse_detailed',
        'name':        'QHSE Project Details',
        'icon':        'TableCells',
        'order':       61,
        'description': 'Detailed project quality view and drill-down',
    },
    {
        'code':        'qhse_quality',
        'name':        'Quality Management',
        'icon':        'ChartBar',
        'order':       62,
        'description': 'Quality metrics, audits and non-conformance tracking',
    },
    {
        'code':        'qhse_health_safety',
        'name':        'Health & Safety',
        'icon':        'Shield',
        'order':       63,
        'description': 'Health and safety incident management',
    },
    {
        'code':        'qhse_environmental',
        'name':        'Environmental',
        'icon':        'DocumentText',
        'order':       64,
        'description': 'Environmental compliance and impact management',
    },
    {
        'code':        'qhse_energy',
        'name':        'Energy Management',
        'icon':        'ChartBar',
        'order':       65,
        'description': 'Energy consumption tracking and efficiency reporting',
    },
]

# Roles that should receive the new QHSE sub-modules.
# Any role that already has 'qhse' should also get the sub-features.
GRANT_TO_ROLES = [
    'super_admin',
    'admin',
    'process_engineer',
    'electrical_engineer',
    'instrument_engineer',
    'mechanical_engineer',
    'civil_engineer',
    'piping_engineer',
    'qhse_engineer',
    'design_engineer',
    'project_manager',
    'viewer',
    'engineering_common_access',
]


def seed_qhse_submodules(apps, schema_editor):
    Module     = apps.get_model('rbac', 'Module')
    Role       = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias   = schema_editor.connection.alias

    # 1. Create / update each sub-module (idempotent)
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
            obj.name        = m['name']
            obj.description = m['description']
            obj.order       = m['order']
            obj.is_active   = True
            obj.save(using=db_alias, update_fields=['name', 'description', 'order', 'is_active'])
        module_objs[m['code']] = obj
        status = 'created' if created else 'updated'
        print(f'  [0023] ✓ Module {status}: {m["code"]}')

    # 2. Grant all new sub-modules to the target roles
    for role_code in GRANT_TO_ROLES:
        try:
            role = Role.objects.using(db_alias).get(code=role_code, is_active=True)
        except Role.DoesNotExist:
            print(f'  [0023]   ⚠ Role not found (skipping): {role_code}')
            continue
        for module in module_objs.values():
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role, module=module,
            )
            if created:
                print(f'  [0023]   ✓ Granted {module.code} → {role_code}')

    # 3. Also grant to any role that already has the parent 'qhse' module
    #    (covers custom org roles not listed in GRANT_TO_ROLES)
    try:
        parent_module = Module.objects.using(db_alias).get(code='qhse')
    except Module.DoesNotExist:
        print('  [0023]   ⚠ Parent module "qhse" not found — skipping auto-grant')
        return

    already_granted_roles = set(GRANT_TO_ROLES)
    roles_with_qhse = Role.objects.using(db_alias).filter(
        modules__code='qhse',
        is_active=True,
    ).exclude(code__startswith='custom_')

    for role in roles_with_qhse:
        if role.code in already_granted_roles:
            continue
        for module in module_objs.values():
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role, module=module,
            )
            if created:
                print(f'  [0023]   ✓ Auto-granted {module.code} → {role.code}')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0022_fix_super_admin_duplicate'),
    ]

    operations = [
        migrations.RunPython(seed_qhse_submodules, noop),
    ]
