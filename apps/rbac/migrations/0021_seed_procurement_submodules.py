"""
Migration 0021 — Seed Procurement Sub-Module Codes
===================================================
The Procurement section in the Sidebar has 5 distinct sub-features
(Dashboard, Vendors, Requisitions, Purchase Orders, Receipts) but
previously all shared the single 'procurement' module code.

This migration adds 4 granular sub-module codes so each sub-feature
can be independently toggled per role in the Role Management panel:

  procurement              (already exists) — Dashboard / root access
  procurement_vendors      (new) — Vendor Management (7.2)
  procurement_requisitions (new) — Purchase Requisitions (7.3)
  procurement_orders       (new) — Purchase Orders (7.4)
  procurement_receipts     (new) — Goods Receipt (7.5)

All new modules are idempotently created and granted to super_admin
and admin roles.
"""
from django.db import migrations

# ── Soft-coded catalogue for this migration ───────────────────────────────────
NEW_MODULES = [
    {
        'code':        'procurement_vendors',
        'name':        'Vendor Management',
        'icon':        'Users',
        'order':       84,
        'description': 'Manage vendors and supplier records',
    },
    {
        'code':        'procurement_requisitions',
        'name':        'Purchase Requisitions',
        'icon':        'DocumentText',
        'order':       85,
        'description': 'Purchase recommendations and requisitions',
    },
    {
        'code':        'procurement_orders',
        'name':        'Purchase Orders',
        'icon':        'DocumentPlus',
        'order':       86,
        'description': 'Create and manage purchase orders',
    },
    {
        'code':        'procurement_receipts',
        'name':        'Goods Receipt',
        'icon':        'Folder',
        'order':       87,
        'description': 'Goods receipt and delivery confirmation',
    },
]

# Roles that should receive all procurement sub-modules
GRANT_TO_ROLES = ['super_admin', 'admin']


def seed_procurement_submodules(apps, schema_editor):
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
            obj.save(using=db_alias)
        module_objs[m['code']] = obj
        action = 'created' if created else 'updated'
        print(f'  [0021] ✓ Module {action}: {m["code"]}')

    # 2. Grant to target roles
    for role_code in GRANT_TO_ROLES:
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
        except Role.DoesNotExist:
            print(f'  [0021] ⚠ Role not found: {role_code} — skipping')
            continue
        for mod in module_objs.values():
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role, module=mod,
            )
            if created:
                print(f'  [0021] ✓ Granted {mod.code} → {role_code}')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0020_seed_business_modules'),
    ]

    operations = [
        migrations.RunPython(seed_procurement_submodules, noop),
    ]
