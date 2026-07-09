# Migration 0031 — Seed HR Onboarding | Offboarding Module
# =============================================================
# Adds the `hr_onboarding` module code so it appears as a
# standalone checkbox under Role & Access Management → Human Resources.
# Previously the sidebar item "4.4 Onboarding | Offboarding" reused
# the `hr_management` module code, which prevented granular access
# control from Role Management.
#
# Assignment scope (soft-coded ROLES_WITH_HR_ONBOARDING below):
#   - super_admin           (always)
#   - admin                 (always)
#   - hr_admin              (sensitive HR administrator)
#
# Uses soft-coded configuration and is idempotent.

from django.db import migrations


# ── Soft-coded module configuration ──────────────────────────────────────
HR_ONBOARDING_MODULE = {
    'code':        'hr_onboarding',
    'name':        'Onboarding | Offboarding',
    'icon':        'UserPlus',
    'order':       74,
    'description': 'Employee lifecycle management — onboarding pipeline and offboarding exits',
    'is_active':   True,
}

# Soft-coded list of role codes that should receive the new module.
# HR-sensitive: keep this list minimal.
ROLES_WITH_HR_ONBOARDING = [
    'super_admin',
    'admin',
    'hr_admin',
]


def seed_hr_onboarding_module(apps, schema_editor):
    """
    Create the hr_onboarding module (idempotent) and assign it to
    the roles listed in ROLES_WITH_HR_ONBOARDING.
    """
    Module = apps.get_model('rbac', 'Module')
    Role   = apps.get_model('rbac', 'Role')

    # 1. Create or update module
    module, created = Module.objects.update_or_create(
        code=HR_ONBOARDING_MODULE['code'],
        defaults={
            'name':        HR_ONBOARDING_MODULE['name'],
            'icon':        HR_ONBOARDING_MODULE['icon'],
            'order':       HR_ONBOARDING_MODULE['order'],
            'description': HR_ONBOARDING_MODULE['description'],
            'is_active':   HR_ONBOARDING_MODULE['is_active'],
        },
    )
    action = "Created" if created else "Updated"
    print(f"✅ {action} module: {module.name} (code={module.code})")

    # 2. Assign module to configured roles (idempotent)
    assigned = 0
    for role_code in ROLES_WITH_HR_ONBOARDING:
        try:
            role = Role.objects.get(code=role_code)
        except Role.DoesNotExist:
            print(f"  ⚠️  Role not found (skipping): {role_code}")
            continue

        if role.modules.filter(code=HR_ONBOARDING_MODULE['code']).exists():
            print(f"  ✓ Already assigned to: {role.name} ({role.code})")
        else:
            role.modules.add(module)
            assigned += 1
            print(f"  → Assigned hr_onboarding to: {role.name} ({role.code})")

    print("=" * 60)
    print(f"HR Onboarding Module Migration Complete — assigned to {assigned} new role(s)")
    print("=" * 60)


def reverse_noop(apps, schema_editor):
    """No-op reverse — never delete role-module links; other data may depend on them."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0030_seed_data_mining_module'),
    ]

    operations = [
        migrations.RunPython(seed_hr_onboarding_module, reverse_noop),
    ]
