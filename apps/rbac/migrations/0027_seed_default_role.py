"""
Migration 0027 — Seed Default Role & Assign Role-less Users
=============================================================
Creates the 'Default' system role (code='default', level=4) and grants it
all module codes defined in DEFAULT_ROLE_MODULES (rbac_config.py).

Then every UserProfile that currently has NO active UserRole is assigned
the Default role as their primary role.

Safety rules:
  1. Idempotent — safe to re-run (uses get_or_create everywhere).
  2. Never touches users who already have at least one active role.
  3. Module codes that do not yet exist in the DB are silently skipped.
  4. System roles (is_system_role=True) are flagged so they cannot be
     accidentally deleted through the UI.

The module list is the authoritative definition from rbac_config.py at the
time this migration is written.  If you change DEFAULT_ROLE_MODULES later,
run a new data migration to sync the DB.
"""
import uuid
from django.db import migrations

# ── Soft-coded constants (mirrors rbac_config.DEFAULT_ROLE_MODULES) ───────────
DEFAULT_ROLE_CODE        = 'default'
DEFAULT_ROLE_NAME        = 'Default'
DEFAULT_ROLE_LEVEL       = 4          # 'Engineer' in ROLE_LEVEL_CHOICES
DEFAULT_ROLE_DESCRIPTION = (
    'Default access for all users — standard engineering modules plus HR self-service.'
)

# SOFT-CODED: keep in sync with rbac_config.DEFAULT_ROLE_MODULES
DEFAULT_ROLE_MODULE_CODES = [
    # Process Engineering
    'pid_analysis',
    'pfd_quality',
    'process_datasheet',
    'pid_line_list',
    'pid_equipment_list',
    # Piping Engineering
    'piping_critical_line_list',
    'piping_pms',
    'piping_datasheet',
    # Electrical Engineering
    'electrical_sld',
    'electrical_datasheet',
    # Civil Engineering
    'civil_datasheet',
    # Mechanical Engineering
    'mechanical_datasheet',
    # Digital Transformation
    'spec_customization',
    'non_teff_metadata',
    # Common & Integration
    'crs_documents',
    'pfd_to_pid',
    'designiq',
    # HR Self-Service ONLY
    'hr_self_service',
]


# ─────────────────────────────────────────────────────────────────────────────
def seed_default_role(apps, schema_editor):
    Role      = apps.get_model('rbac', 'Role')
    Module    = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole  = apps.get_model('rbac', 'UserRole')
    db_alias  = schema_editor.connection.alias

    # 1. Create (or fetch) the Default role
    role, created = Role.objects.using(db_alias).get_or_create(
        code=DEFAULT_ROLE_CODE,
        defaults={
            'id':            uuid.uuid4(),
            'name':          DEFAULT_ROLE_NAME,
            'level':         DEFAULT_ROLE_LEVEL,
            'description':   DEFAULT_ROLE_DESCRIPTION,
            'is_active':     True,
            'is_system_role': True,
        },
    )

    # Ensure existing row is consistent even if get_or_create found it
    if not created:
        updated = False
        if role.name != DEFAULT_ROLE_NAME:
            role.name = DEFAULT_ROLE_NAME
            updated = True
        if not role.is_active:
            role.is_active = True
            updated = True
        if not role.is_system_role:
            role.is_system_role = True
            updated = True
        if role.level != DEFAULT_ROLE_LEVEL:
            role.level = DEFAULT_ROLE_LEVEL
            updated = True
        if updated:
            role.save(using=db_alias)

    # 2. Assign module codes to the role
    for code in DEFAULT_ROLE_MODULE_CODES:
        try:
            module = Module.objects.using(db_alias).get(code=code, is_active=True)
        except Module.DoesNotExist:
            continue  # skip codes not yet in the DB
        RoleModule.objects.using(db_alias).get_or_create(
            role=role,
            module=module,
            defaults={'id': uuid.uuid4(), 'granted_by': None},
        )

    # 3. Assign the Default role as primary to every role-less UserProfile
    assigned_count = 0
    for profile in UserProfile.objects.using(db_alias).all():
        # Check for any active role assignment
        has_role = UserRole.objects.using(db_alias).filter(
            user_profile=profile,
            role__is_active=True,
        ).exists()
        if has_role:
            continue

        _, ur_created = UserRole.objects.using(db_alias).get_or_create(
            user_profile=profile,
            role=role,
            defaults={
                'id':         uuid.uuid4(),
                'is_primary': True,
                'assigned_by': None,
            },
        )
        if ur_created:
            assigned_count += 1

    # Print summary (visible in migrate --verbosity 2)
    print(
        f"\n[0027] Default role {'created' if created else 'already existed'}. "
        f"Assigned to {assigned_count} previously role-less user(s)."
    )


def reverse_seed_default_role(apps, schema_editor):
    """Reverse: deactivate the Default role (do not hard-delete)."""
    Role = apps.get_model('rbac', 'Role')
    db_alias = schema_editor.connection.alias
    Role.objects.using(db_alias).filter(code=DEFAULT_ROLE_CODE).update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0026_deactivate_per_user_named_roles'),
    ]

    operations = [
        migrations.RunPython(
            seed_default_role,
            reverse_code=reverse_seed_default_role,
        ),
    ]
