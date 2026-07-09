"""
Migration 0028 — Replace Viewer with Default role for existing users
====================================================================
Fixes historical role assignment where every new UserProfile received the
'viewer' role as their baseline. The system-wide default is now the
'default' role (rbac_config.DEFAULT_ROLE_CONFIG). This migration reassigns
every existing user accordingly:

  • Super Administrators (is_superuser=True) — remove their 'viewer'
    UserRole entirely. They bypass all access checks and never needed it.

  • Everyone else — replace their 'viewer' UserRole with a 'default'
    UserRole, preserving the is_primary flag. If they already have the
    'default' role, just remove the redundant 'viewer' entry.

Safety:
  1. Idempotent — safe to re-run.
  2. Never grants any new permissions to super admins.
  3. Uses soft-coded role codes read from rbac_config.
  4. If the 'default' role has not been seeded yet, the migration exits
     cleanly without changing anything.

Reverse: no-op (we never restore 'viewer' as the baseline).
"""
import uuid
from django.db import migrations


def replace_viewer_with_default(apps, schema_editor):
    Role = apps.get_model('rbac', 'Role')
    UserRole = apps.get_model('rbac', 'UserRole')
    db_alias = schema_editor.connection.alias

    # Import inside the function so migration doesn't fail if the module
    # is temporarily unavailable during initial migrate.
    try:
        from apps.rbac.rbac_config import DEFAULT_ROLE_CONFIG
        default_code = DEFAULT_ROLE_CONFIG.get('code', 'default')
    except Exception:
        default_code = 'default'

    viewer_code = 'viewer'
    super_admin_code = 'super_admin'

    try:
        viewer_role = Role.objects.using(db_alias).get(code=viewer_code)
    except Role.DoesNotExist:
        print(f"\n[0028] Viewer role not found — nothing to migrate.")
        return

    try:
        default_role = Role.objects.using(db_alias).get(
            code=default_code, is_active=True
        )
    except Role.DoesNotExist:
        print(
            f"\n[0028] Default role (code='{default_code}') not seeded yet. "
            "Run migration 0027 first. Skipping."
        )
        return

    # Every UserRole pointing at 'viewer'
    viewer_assignments = UserRole.objects.using(db_alias).filter(
        role=viewer_role
    ).select_related('user_profile__user')

    removed_from_super = 0
    replaced = 0
    already_default = 0

    for ur in viewer_assignments:
        profile = ur.user_profile
        user = getattr(profile, 'user', None)
        is_super_django = bool(getattr(user, 'is_superuser', False))

        # Also treat users with an active super_admin role as super admins
        has_super_role = UserRole.objects.using(db_alias).filter(
            user_profile=profile,
            role__code=super_admin_code,
            role__is_active=True,
        ).exists()

        if is_super_django or has_super_role:
            # Super admin — never needed viewer; remove it outright.
            ur.delete()
            removed_from_super += 1
            continue

        # Regular user: does this profile already have the default role?
        already_has_default = UserRole.objects.using(db_alias).filter(
            user_profile=profile,
            role=default_role,
        ).exists()

        if already_has_default:
            # Just drop the redundant viewer entry.
            ur.delete()
            already_default += 1
            continue

        # Replace viewer with default, preserving the is_primary flag.
        was_primary = bool(ur.is_primary)
        assigned_by = getattr(ur, 'assigned_by', None)
        ur.delete()

        UserRole.objects.using(db_alias).create(
            id=uuid.uuid4(),
            user_profile=profile,
            role=default_role,
            is_primary=was_primary,
            assigned_by=assigned_by,
        )
        replaced += 1

    print(
        f"\n[0028] Viewer -> Default migration complete. "
        f"Replaced: {replaced} | Already had default: {already_default} | "
        f"Removed from super admins: {removed_from_super}"
    )


def noop_reverse(apps, schema_editor):
    """No reverse — we do not restore 'viewer' as the baseline role."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0027_seed_default_role'),
    ]

    operations = [
        migrations.RunPython(
            replace_viewer_with_default,
            reverse_code=noop_reverse,
        ),
    ]
