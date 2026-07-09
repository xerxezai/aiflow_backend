"""
Assign Default Role to Role-less Users
=======================================
Idempotent management command that finds every active UserProfile with no
active role assignment and assigns the 'default' system role to them.

The target role code is read from rbac_config.DEFAULT_ROLE_CONFIG so that
a single config-file change is all that's needed to redirect the fallback.

Usage:
    # Assign default role to all role-less users:
    python manage.py assign_default_role

    # Dry-run (report only, no DB changes):
    python manage.py assign_default_role --dry-run

    # Verbose output (show every affected user):
    python manage.py assign_default_role --verbosity 2
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from apps.rbac.models import Role, UserRole, UserProfile
from apps.rbac.rbac_config import DEFAULT_ROLE_CONFIG

User = get_user_model()
logger = logging.getLogger(__name__)

# SOFT-CODED: role code is driven by DEFAULT_ROLE_CONFIG — never hardcode here
DEFAULT_ROLE_CODE = DEFAULT_ROLE_CONFIG['code']


class Command(BaseCommand):
    help = (
        'Assign the Default role to every active UserProfile that has no active '
        'role. Idempotent and safe to re-run. Use --dry-run to preview.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Report changes without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run  = options['dry_run']
        verbose  = options['verbosity'] >= 2

        # ── 1. Resolve the Default role ───────────────────────────────────
        try:
            default_role = Role.objects.get(code=DEFAULT_ROLE_CODE, is_active=True)
        except Role.DoesNotExist:
            self.stderr.write(
                self.style.ERROR(
                    f"Default role with code='{DEFAULT_ROLE_CODE}' not found or inactive. "
                    "Run migration 0027 first: python manage.py migrate rbac"
                )
            )
            return

        # ── 2. Find role-less profiles ────────────────────────────────────
        # UserProfiles that have NO UserRole pointing to an active role
        profiles_with_role_ids = set(
            UserRole.objects.filter(role__is_active=True)
                            .values_list('user_profile_id', flat=True)
                            .distinct()
        )
        roleless_profiles = UserProfile.objects.exclude(
            id__in=profiles_with_role_ids
        ).select_related('user')

        total = roleless_profiles.count()

        if total == 0:
            self.stdout.write(
                self.style.SUCCESS('✅ All users already have at least one active role. Nothing to do.')
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"Found {total} profile(s) with no active role. "
                f"{'[DRY-RUN — no changes will be made]' if dry_run else 'Assigning Default role...'}"
            )
        )

        # ── 3. Assign Default role ────────────────────────────────────────
        assigned = 0
        skipped  = 0

        with transaction.atomic():
            for profile in roleless_profiles.iterator():
                email = (profile.user.email if profile.user else '') or str(profile.id)

                if verbose:
                    self.stdout.write(f'  → {email}')

                if dry_run:
                    assigned += 1
                    continue

                _, created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=default_role,
                    defaults={'is_primary': True, 'assigned_by': None},
                )
                if created:
                    assigned += 1
                else:
                    skipped += 1

            if dry_run:
                # Roll back to ensure nothing was written
                transaction.set_rollback(True)

        label = 'Would assign' if dry_run else 'Assigned'
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ {label} Default role to {assigned} user(s). "
                f"{f'({skipped} already had it)' if skipped else ''}"
            )
        )
