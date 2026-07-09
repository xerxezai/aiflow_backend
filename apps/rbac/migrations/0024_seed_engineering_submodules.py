"""
Migration 0024 — Seed Granular Engineering Sub-Module Codes
============================================================
Four engineering sidebar features were sharing module codes with other
features, preventing independent access control per sub-feature.

BEFORE this migration:
  - Process > Line List       → moduleCode: pid_analysis   (shared with P&ID QC)
  - Process > Equipment List  → moduleCode: pid_analysis   (shared with P&ID QC)
  - Piping  > Critical Line List → moduleCode: designiq    (wrong discipline)
  - Instrument > IO List      → moduleCode: instrument_datasheet (shared with Datasheets)

AFTER this migration:
  - Process > Line List       → pid_line_list
  - Process > Equipment List  → pid_equipment_list
  - Piping  > Critical Line List → piping_critical_line_list
  - Instrument > IO List      → instrument_io_list

Backward-compatibility strategy:
  Any role that already had the *parent* module code will automatically
  receive the new granular code — so no user loses access on deploy.
  Parent mappings:
    pid_analysis        → pid_line_list, pid_equipment_list
    designiq            → piping_critical_line_list
    instrument_datasheet → instrument_io_list

All operations are idempotent — safe to re-run.
"""
from django.db import migrations

# ── New module definitions ────────────────────────────────────────────────────
NEW_MODULES = [
    {
        'code':        'pid_line_list',
        'name':        'Line List',
        'icon':        'TableCells',
        'order':       22,
        'description': 'Extract 8 base columns from P&ID (P&ID-only, no enrichment)',
        'parent_code': 'pid_analysis',
    },
    {
        'code':        'pid_equipment_list',
        'name':        'Equipment List',
        'icon':        'TableCells',
        'order':       23,
        'description': 'Extract equipment tags and type classification from P&ID',
        'parent_code': 'pid_analysis',
    },
    {
        'code':        'piping_critical_line_list',
        'name':        'Critical Line List',
        'icon':        'GitBranch',
        'order':       24,
        'description': '5-document critical line list with full 35-column enrichment',
        'parent_code': 'designiq',
    },
    {
        'code':        'instrument_io_list',
        'name':        'Instrument IO List',
        'icon':        'CircleStack',
        'order':       25,
        'description': 'Generate or QC an Input/Output list from the instrument register',
        'parent_code': 'instrument_datasheet',
    },
]

# Roles that should always receive all new engineering sub-modules
ALWAYS_GRANT_ROLES = [
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


def seed_engineering_submodules(apps, schema_editor):
    Module     = apps.get_model('rbac', 'Module')
    Role       = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias   = schema_editor.connection.alias

    for m in NEW_MODULES:
        # 1. Create / update the new module
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
        print(f'  [0024] ✓ Module {"created" if created else "updated"}: {m["code"]}')

        # 2. Grant to always-grant roles
        granted_codes = set()
        for role_code in ALWAYS_GRANT_ROLES:
            try:
                role = Role.objects.using(db_alias).get(code=role_code, is_active=True)
            except Role.DoesNotExist:
                continue
            _, c = RoleModule.objects.using(db_alias).get_or_create(role=role, module=obj)
            if c:
                print(f'  [0024]   ✓ Granted {m["code"]} → {role_code}')
            granted_codes.add(role_code)

        # 3. Auto-grant to any role that already has the parent module code
        #    (covers custom org roles and any future roles seeded outside this list)
        try:
            parent = Module.objects.using(db_alias).get(code=m['parent_code'])
        except Module.DoesNotExist:
            print(f'  [0024]   ⚠ Parent module "{m["parent_code"]}" not found — skipping auto-grant')
            continue

        roles_with_parent = Role.objects.using(db_alias).filter(
            modules=parent,
            is_active=True,
        ).exclude(code__startswith='custom_')

        for role in roles_with_parent:
            if role.code in granted_codes:
                continue
            _, c = RoleModule.objects.using(db_alias).get_or_create(role=role, module=obj)
            if c:
                print(f'  [0024]   ✓ Auto-granted {m["code"]} → {role.code} (inherited from {m["parent_code"]})')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0023_seed_qhse_submodules'),
    ]

    operations = [
        migrations.RunPython(seed_engineering_submodules, noop),
    ]
