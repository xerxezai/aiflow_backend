"""
Migration 0022 — Deduplicate Super Admin Role & Fix Level Label
===============================================================
Fixes two issues found in the production Role Management panel:

ISSUE 1 — Duplicate "Super Admin" role
  Production may contain a legacy role named "Super Admin" alongside
  the canonical "Super Administrator" (code=super_admin).  This migration:
    a) Renames any role with code='super_admin' and name='Super Admin'
       to the canonical name 'Super Administrator'.
    b) Finds any OTHER level-1 role that is a structural duplicate
       (different code, same level), re-assigns its users to the
       canonical role, then deactivates (not deletes) the duplicate
       to preserve referential integrity.

ISSUE 2 — Level choice label
  ROLE_LEVEL_CHOICES had (1, 'Super Admin') which Django displays
  in get_FOO_display().  This migration runs a no-op SQL for the
  label (it lives in Python, not the DB), but the models.py fix
  already corrects it for all future serializer output.

All operations are idempotent — safe to re-run.
"""
from django.db import migrations

# ── Soft-coded constants ───────────────────────────────────────────────────────
CANONICAL_CODE  = 'super_admin'
CANONICAL_NAME  = 'Super Administrator'
CANONICAL_LEVEL = 1

# Custom role code prefix (mirrors rbac_config.MODULE_ASSIGNMENT_CONFIG)
CUSTOM_PREFIX = 'custom_'


def fix_super_admin_duplicates(apps, schema_editor):
    Role      = apps.get_model('rbac', 'Role')
    UserRole  = apps.get_model('rbac', 'UserRole')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias  = schema_editor.connection.alias

    # ── Step 1: Ensure the canonical role has the right name ─────────────────
    try:
        canonical = Role.objects.using(db_alias).get(code=CANONICAL_CODE)
    except Role.DoesNotExist:
        print(f'  [0022] ⚠ Canonical role code={CANONICAL_CODE} not found — skipping')
        return

    if canonical.name != CANONICAL_NAME:
        old_name      = canonical.name
        canonical.name = CANONICAL_NAME
        canonical.save(using=db_alias, update_fields=['name'])
        print(f'  [0022] ✓ Renamed canonical role: "{old_name}" → "{CANONICAL_NAME}"')
    else:
        print(f'  [0022] ✓ Canonical role name is already correct: "{CANONICAL_NAME}"')

    # ── Step 2: Find duplicate level-1 roles (different code, not custom_) ───
    duplicates = Role.objects.using(db_alias).filter(
        level=CANONICAL_LEVEL,
        is_active=True,
    ).exclude(
        code=CANONICAL_CODE,
    ).exclude(
        code__startswith=CUSTOM_PREFIX,
    )

    for dup in duplicates:
        print(f'  [0022] Found duplicate level-1 role: "{dup.name}" (code={dup.code})')

        # Re-assign all users from duplicate → canonical role
        user_roles = UserRole.objects.using(db_alias).filter(role=dup)
        moved = 0
        for ur in user_roles:
            _, created = UserRole.objects.using(db_alias).get_or_create(
                user_profile=ur.user_profile,
                role=canonical,
                defaults={'is_primary': ur.is_primary},
            )
            if created:
                moved += 1
        if moved:
            print(f'  [0022]   ✓ Moved {moved} user assignment(s) to canonical role')

        # Copy any modules that canonical role is missing
        dup_modules = RoleModule.objects.using(db_alias).filter(role=dup)
        for rm in dup_modules:
            RoleModule.objects.using(db_alias).get_or_create(
                role=canonical, module=rm.module,
            )

        # Deactivate (not delete) the duplicate to preserve FK integrity
        dup.is_active = False
        dup.save(using=db_alias, update_fields=['is_active'])
        print(f'  [0022]   ✓ Deactivated duplicate role: "{dup.name}" (code={dup.code})')

    if not duplicates.exists():
        print('  [0022] ✓ No duplicate level-1 roles found — nothing to merge')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0021_seed_procurement_submodules'),
    ]

    operations = [
        migrations.RunPython(fix_super_admin_duplicates, noop),
    ]
