"""
Cleanup Legacy `custom_<email>` Roles
=====================================
Historically the platform created a per-user Role with code
`custom_<email>` every time a user was created with module_ids. This
polluted the roles table with hundreds of one-off roles that never appear
in Admin › Roles & Access Management.

We now enforce strict role-based access
(MODULE_ASSIGNMENT_CONFIG['create_custom_roles'] is False). This command
migrates any leftover per-user roles.

For every legacy role it will:
  1. Compute the set of modules the role grants.
  2. Compare against modules the assigned users already receive from
     their OTHER active roles.
  3. Report the "exclusively-provided" modules — these are the ones
     that would disappear if the role were removed today.
  4. In --apply mode, delete the UserRole rows and then delete the Role
     itself (CASCADE removes RoleModule / RolePermission).

Usage:
    # Dry run (default): report what would happen, change nothing.
    python manage.py cleanup_custom_roles

    # Actually delete the legacy custom_* roles.
    python manage.py cleanup_custom_roles --apply

    # Restrict to a subset of roles by code prefix (defaults to the
    # value in MODULE_ASSIGNMENT_CONFIG['custom_role_prefix']).
    python manage.py cleanup_custom_roles --prefix custom_

    # Verbose (per-user module deltas).
    python manage.py cleanup_custom_roles --verbosity 2
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rbac.models import Role, UserRole, RoleModule
from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG

logger = logging.getLogger(__name__)

# SOFT-CODED: prefix is driven by MODULE_ASSIGNMENT_CONFIG so admins can
# rename or retire it without editing this command.
DEFAULT_CUSTOM_ROLE_PREFIX = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')


class Command(BaseCommand):
    help = (
        'Migrate legacy per-user "custom_<email>" roles into strict role-based '
        'access. Dry-run by default; pass --apply to delete.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Actually delete the legacy roles. Without this flag the command only reports.',
        )
        parser.add_argument(
            '--prefix',
            default=DEFAULT_CUSTOM_ROLE_PREFIX,
            help=f'Role code prefix to match (default: "{DEFAULT_CUSTOM_ROLE_PREFIX}").',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        prefix = options['prefix']
        verbosity = int(options.get('verbosity', 1))

        mode_label = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{mode_label}] Cleaning up legacy roles with prefix "{prefix}"'
        ))

        custom_roles = list(
            Role.objects.filter(code__startswith=prefix)
            .prefetch_related(
                'rolemodule_set__module',
                'userrole_set__user_profile__user',
            )
            .order_by('code')
        )

        if not custom_roles:
            self.stdout.write(self.style.SUCCESS('No legacy custom roles found. Nothing to do.'))
            return

        self.stdout.write(f'Found {len(custom_roles)} legacy role(s).\n')

        # Header row for the summary table
        header = f'{"role.code":<45} {"users":>6} {"modules":>8} {"exclusive":>10}  action'
        self.stdout.write(header)
        self.stdout.write('-' * len(header))

        total_users_affected = 0
        total_exclusive_modules = 0
        total_roles_processed = 0

        for role in custom_roles:
            role_module_codes = {rm.module.code for rm in role.rolemodule_set.all() if rm.module_id}
            user_assignments = list(role.userrole_set.all())
            user_count = len(user_assignments)

            # For each user, compute modules gained *exclusively* from this role.
            per_user_exclusive = {}
            for ua in user_assignments:
                profile = ua.user_profile
                # Modules from user's OTHER active roles (excluding this legacy role)
                other_module_codes = set(
                    RoleModule.objects.filter(
                        role__user_profiles=profile,
                        role__is_active=True,
                    )
                    .exclude(role_id=role.id)
                    .values_list('module__code', flat=True)
                )
                exclusive = role_module_codes - other_module_codes
                if exclusive:
                    per_user_exclusive[profile.user.email] = exclusive

            exclusive_all = set()
            for codes in per_user_exclusive.values():
                exclusive_all |= codes

            action = 'delete' if apply_changes else 'would delete'
            self.stdout.write(
                f'{role.code[:44]:<45} {user_count:>6} {len(role_module_codes):>8} {len(exclusive_all):>10}  {action}'
            )

            if verbosity >= 2 and per_user_exclusive:
                for email, codes in per_user_exclusive.items():
                    self.stdout.write(
                        self.style.WARNING(
                            f'    ! {email} loses access to: {", ".join(sorted(codes))}'
                        )
                    )

            total_users_affected += user_count
            total_exclusive_modules += len(exclusive_all)
            total_roles_processed += 1

            if apply_changes:
                with transaction.atomic():
                    # UserRole rows first (explicit for clarity — CASCADE would handle this too)
                    ur_deleted, _ = UserRole.objects.filter(role=role).delete()
                    role.delete()
                    logger.info(
                        '[cleanup_custom_roles] Deleted role %s (%s user assignments removed)',
                        role.code, ur_deleted,
                    )

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary'))
        self.stdout.write(f'  Roles processed:              {total_roles_processed}')
        self.stdout.write(f'  User assignments affected:    {total_users_affected}')
        self.stdout.write(f'  Modules exclusively provided: {total_exclusive_modules}')

        if not apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'This was a DRY RUN. Re-run with --apply to actually delete the legacy roles.'
            ))
            if total_exclusive_modules:
                self.stdout.write(self.style.WARNING(
                    'Some users would lose module access — assign them a shared role '
                    'in Admin > Roles & Access Management before applying.'
                ))
        else:
            self.stdout.write(self.style.SUCCESS('Cleanup complete.'))
