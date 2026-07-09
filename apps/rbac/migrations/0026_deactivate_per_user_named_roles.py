"""
Migration 0026 — Deactivate Per-User & Org-Level Named Roles
=============================================================
The following role groups were found in the production Role Management
panel and are no longer needed:

  • "Engineering & Common Features Access"           — org-level role
  • "Finance Invoice Management Full Access - *"     — per-user named roles
  • "QHSE Full Access - *"                           — per-user named roles

Matching strategy (soft-coded, no hard-coded names):
  EXACT_NAMES   — matched verbatim (case-insensitive)
  NAME_PREFIXES — any role whose name starts with this prefix is matched
                  (covers all user-specific variants like "... - aleksi.murtomaki")

Safety rules (same as migration 0025):
  1. Roles with protected codes (super_admin, admin) are NEVER touched.
  2. System roles (is_system_role=True) are NEVER deactivated.
  3. Orphaned users (sole role being deactivated) get the 'viewer' fallback.
  4. All operations are idempotent — safe to re-run.

Data is DEACTIVATED, never hard-deleted, for audit trail integrity.
"""
from django.db import migrations
from django.db.models import Q

# ── Soft-coded target configuration ──────────────────────────────────────────

# Exact role names to deactivate (case-insensitive match)
EXACT_NAMES = [
    'Engineering & Common Features Access',
]

# Name prefixes — any role starting with these strings is deactivated.
# This covers all "Finance Invoice Management Full Access - <username>" and
# "QHSE Full Access - <username>" variants without listing each user.
NAME_PREFIXES = [
    'Finance Invoice Management Full Access',   # catches standalone + " - user" variants
    'QHSE Full Access',                          # catches standalone + " - user" variants
]

# Roles that must never be deactivated regardless of name match
PROTECTED_CODES = {'super_admin', 'admin'}

# Fallback role assigned to users who would otherwise be left with no active role
FALLBACK_ROLE_CODE = 'viewer'


def deactivate_named_org_roles(apps, schema_editor):
    Role     = apps.get_model('rbac', 'Role')
    UserRole = apps.get_model('rbac', 'UserRole')
    db_alias = schema_editor.connection.alias

    # Resolve fallback role
    try:
        fallback_role = Role.objects.using(db_alias).get(
            code=FALLBACK_ROLE_CODE, is_active=True
        )
    except Role.DoesNotExist:
        fallback_role = None
        print(f'  [0026]   ⚠ Fallback role "{FALLBACK_ROLE_CODE}" not found')

    # Build combined queryset: exact names + prefix matches
    q = Q()
    for name in EXACT_NAMES:
        q |= Q(name__iexact=name)
    for prefix in NAME_PREFIXES:
        q |= Q(name__istartswith=prefix)

    candidates = Role.objects.using(db_alias).filter(q).exclude(
        code__in=PROTECTED_CODES
    ).exclude(
        is_system_role=True
    )

    if not candidates.exists():
        print('  [0026] ℹ No matching roles found (already absent or renamed)')
        return

    for role in candidates:
        if not role.is_active:
            print(f'  [0026] ℹ Already inactive: "{role.name}"')
            continue

        # Migrate orphaned users to fallback role
        affected = UserRole.objects.using(db_alias).filter(role=role)
        orphan_count = 0
        for ur in affected:
            has_other_active = UserRole.objects.using(db_alias).filter(
                user_profile=ur.user_profile,
                role__is_active=True,
            ).exclude(role=role).exists()

            if not has_other_active and fallback_role:
                UserRole.objects.using(db_alias).get_or_create(
                    user_profile=ur.user_profile,
                    role=fallback_role,
                    defaults={'is_primary': True},
                )
                orphan_count += 1

        if orphan_count:
            print(f'  [0026]   ✓ {orphan_count} user(s) assigned fallback "{FALLBACK_ROLE_CODE}"')

        role.is_active = False
        role.save(using=db_alias, update_fields=['is_active'])
        print(f'  [0026] ✓ Deactivated: "{role.name}" (code={role.code})')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0025_deactivate_org_roles'),
    ]

    operations = [
        migrations.RunPython(deactivate_named_org_roles, noop),
    ]
