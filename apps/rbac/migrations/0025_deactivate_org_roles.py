"""
Migration 0025 — Deactivate Org-Specific Roles from Production Role List
=========================================================================
The following roles were found in the production Role Management panel
and are no longer needed:

  • "Super Admin"               — duplicate/org-created level-1 role
                                  (canonical is "Super Administrator" code=super_admin)
  • "Finance Invoice Management" — manually created org role
  • "QHSE Full Access"           — manually created org role

These roles are DEACTIVATED (is_active=False), NOT hard-deleted, to
preserve referential integrity in audit logs, UserRole history, etc.

Safety rules applied before deactivation:
  1. The canonical super_admin role (code='super_admin') is NEVER touched,
     even if its display name happens to be "Super Admin".
  2. System roles (is_system_role=True) are NEVER deactivated here.
  3. Any user whose ONLY role is being deactivated gets automatically
     assigned the canonical 'viewer' role so they retain a safe default
     access level.  Admins can re-assign them as needed.
  4. All operations are idempotent — safe to re-run.

To RE-ACTIVATE any of these roles manually (Django shell or admin):
    Role.objects.filter(name='Finance Invoice Management').update(is_active=True)
"""
from django.db import migrations

# ── Soft-coded target list ────────────────────────────────────────────────────
# Change this list if role names ever differ in production.
# Matching is EXACT (case-insensitive) on the `name` field.
TARGET_ROLE_NAMES = [
    'Super Admin',
    'Finance Invoice Management',
    'QHSE Full Access',
]

# Canonical role that MUST NEVER be deactivated regardless of name match
PROTECTED_CODES = {'super_admin', 'admin'}

# Fallback role code for orphaned users (role assigned if user has no other active role)
FALLBACK_ROLE_CODE = 'viewer'


def deactivate_org_roles(apps, schema_editor):
    Role       = apps.get_model('rbac', 'Role')
    UserRole   = apps.get_model('rbac', 'UserRole')
    db_alias   = schema_editor.connection.alias

    # Resolve fallback role once
    try:
        fallback_role = Role.objects.using(db_alias).get(
            code=FALLBACK_ROLE_CODE, is_active=True
        )
    except Role.DoesNotExist:
        fallback_role = None
        print(f'  [0025]   ⚠ Fallback role "{FALLBACK_ROLE_CODE}" not found — orphaned users will NOT be auto-assigned')

    for target_name in TARGET_ROLE_NAMES:
        candidates = Role.objects.using(db_alias).filter(
            name__iexact=target_name,
        )

        if not candidates.exists():
            print(f'  [0025] ℹ Role not found (already absent or renamed): "{target_name}"')
            continue

        for role in candidates:
            # ── Safety guard: never touch protected system roles ──────────
            if role.code in PROTECTED_CODES or role.is_system_role:
                print(f'  [0025]   ⚠ Skipping protected/system role: "{role.name}" (code={role.code})')
                continue

            if not role.is_active:
                print(f'  [0025] ℹ Already inactive: "{role.name}" (code={role.code})')
                continue

            # ── Migrate user assignments ──────────────────────────────────
            affected_user_roles = UserRole.objects.using(db_alias).filter(role=role)
            orphan_count = 0
            for ur in affected_user_roles:
                # Check if this user has any other active role besides the one being deactivated
                other_active = UserRole.objects.using(db_alias).filter(
                    user_profile=ur.user_profile,
                    role__is_active=True,
                ).exclude(role=role).exists()

                if not other_active and fallback_role:
                    UserRole.objects.using(db_alias).get_or_create(
                        user_profile=ur.user_profile,
                        role=fallback_role,
                        defaults={'is_primary': True},
                    )
                    orphan_count += 1

            if orphan_count:
                print(f'  [0025]   ✓ {orphan_count} user(s) assigned fallback role "{FALLBACK_ROLE_CODE}"')

            # ── Deactivate the role ───────────────────────────────────────
            role.is_active = False
            role.save(using=db_alias, update_fields=['is_active'])
            print(f'  [0025] ✓ Deactivated role: "{role.name}" (code={role.code})')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0024_seed_engineering_submodules'),
    ]

    operations = [
        migrations.RunPython(deactivate_org_roles, noop),
    ]
