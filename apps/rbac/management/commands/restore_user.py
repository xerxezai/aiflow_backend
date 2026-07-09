"""
Restore User (Un-Soft-Delete)
=============================
Config-driven, idempotent management command that restores a soft-deleted
UserProfile and re-activates the underlying User, so the account becomes
visible again in the RBAC list and can log in.

Behaviour is driven by ``rbac_config.USER_PROFILE_CONFIG`` and
``rbac_config.DEFAULT_ROLE_CONFIG`` — no thresholds, statuses, or role
codes are hardcoded in this file.

Restore steps (each guarded and idempotent):
  1. Clear soft-delete on the UserProfile
     (``is_deleted = False``, ``deleted_at = None``, ``deleted_by = None``)
  2. If ``USER_PROFILE_CONFIG['default_status'] == 'active'``:
       - ``user.is_active = True``
       - ``profile.status = 'active'``
  3. Ensure the baseline role from ``DEFAULT_ROLE_CONFIG`` is assigned
     (skipped for Django superusers to match signal behaviour).

Works against any environment — point ``DATABASE_URL`` at the target DB
before running (local / staging / Railway production).

Usage:
    python manage.py restore_user --email jamal.ayoub@rejlers.ae
    python manage.py restore_user --email user@example.com --dry-run
    python manage.py restore_user --username jamal.ayoub
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.rbac.models import Role, UserProfile, UserRole
from apps.rbac.rbac_config import DEFAULT_ROLE_CONFIG, USER_PROFILE_CONFIG

User = get_user_model()

ACTIVE_STATUS = 'active'


class Command(BaseCommand):
    help = (
        'Restore a soft-deleted user profile and re-activate the account. '
        'Driven by rbac_config.USER_PROFILE_CONFIG and DEFAULT_ROLE_CONFIG. '
        'Idempotent — safe to re-run.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--email', help='User email (case-insensitive).')
        parser.add_argument('--username', help='Username (case-insensitive).')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report changes without writing to the DB.',
        )

    def handle(self, *args, **options):
        email = (options.get('email') or '').strip()
        username = (options.get('username') or '').strip()
        dry_run = options['dry_run']

        if not email and not username:
            raise CommandError('Provide --email or --username.')

        qs = User.objects.all()
        if email:
            qs = qs.filter(email__iexact=email)
        if username:
            qs = qs.filter(username__iexact=username)

        user = qs.first()
        if not user:
            raise CommandError(
                f'User not found for email={email!r} username={username!r}'
            )

        profile = UserProfile.objects.filter(user=user).first()
        if not profile:
            raise CommandError(
                f'No UserProfile exists for user_id={user.id} ({user.email}). '
                'Restore requires an existing profile.'
            )

        default_status = USER_PROFILE_CONFIG.get('default_status', ACTIVE_STATUS)
        should_activate = default_status == ACTIVE_STATUS
        default_role_code = DEFAULT_ROLE_CONFIG.get('code', 'default')

        # Snapshot for reporting
        before = {
            'user.is_active': user.is_active,
            'profile.is_deleted': profile.is_deleted,
            'profile.deleted_at': profile.deleted_at,
            'profile.deleted_by_id': profile.deleted_by_id,
            'profile.status': profile.status,
        }

        self.stdout.write(self.style.NOTICE(
            f'Target: user_id={user.id} email={user.email} username={user.username}'
        ))
        self.stdout.write(f'Before: {before}')

        planned_changes = []
        if profile.is_deleted:
            planned_changes.append('clear profile.is_deleted (+ deleted_at, deleted_by)')
        if should_activate and not user.is_active:
            planned_changes.append('user.is_active = True')
        if should_activate and profile.status != ACTIVE_STATUS:
            planned_changes.append(f'profile.status = {ACTIVE_STATUS!r}')

        # Baseline role check (skip for Django superusers, matching signal policy)
        role_action = None
        if not user.is_superuser:
            try:
                default_role = Role.objects.get(code=default_role_code, is_active=True)
                has_role = UserRole.objects.filter(
                    user_profile=profile, role=default_role,
                ).exists()
                if not has_role:
                    role_action = f'assign baseline role {default_role_code!r}'
                    planned_changes.append(role_action)
            except Role.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'Baseline role {default_role_code!r} not found — skipping role assignment.'
                ))
                default_role = None
        else:
            default_role = None

        if not planned_changes:
            self.stdout.write(self.style.SUCCESS(
                'No changes required — user already restored and active.'
            ))
            return

        self.stdout.write('Planned changes:')
        for c in planned_changes:
            self.stdout.write(f'  - {c}')

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: no DB changes made.'))
            return

        with transaction.atomic():
            if profile.is_deleted:
                profile.is_deleted = False
                profile.deleted_at = None
                profile.deleted_by = None
            if should_activate:
                if not user.is_active:
                    user.is_active = True
                    user.save(update_fields=['is_active'])
                if profile.status != ACTIVE_STATUS:
                    profile.status = ACTIVE_STATUS
            profile.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'status'])

            if default_role is not None and role_action:
                UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=default_role,
                    defaults={'is_primary': not UserRole.objects.filter(
                        user_profile=profile, is_primary=True,
                    ).exists()},
                )

        # Reload for reporting
        user.refresh_from_db()
        profile.refresh_from_db()
        after = {
            'user.is_active': user.is_active,
            'profile.is_deleted': profile.is_deleted,
            'profile.deleted_at': profile.deleted_at,
            'profile.deleted_by_id': profile.deleted_by_id,
            'profile.status': profile.status,
        }
        self.stdout.write(f'After:  {after}')
        self.stdout.write(self.style.SUCCESS(
            f'Restored user_id={user.id} ({user.email}).'
        ))
