"""
DEV-ONLY: quick user reset for local Docker development.

Usage (inside container):
    python manage.py dev_reset_user jamal.ayoub@rejlers.ae
    python manage.py dev_reset_user jamal.ayoub@rejlers.ae --password "MyPass123"
    python manage.py dev_reset_user jamal.ayoub@rejlers.ae --department "Project Management" --title "Project Control Engineer"

Or from the host (PowerShell):
    docker exec aiflow_backend_local python manage.py dev_reset_user jamal.ayoub@rejlers.ae

What it does (all in one shot):
  1. activates the account (is_active=True)
  2. resets the password (default: "Password123")
  3. clears the RBAC must_change_password flag
  4. optionally sets department + job_title (drives dashboard persona detection)
  5. clears any failed-login lock counters if present

REFUSES to run when DEBUG=False (safety — never touches production).
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

# ─── Soft-coded defaults ────────────────────────────────────────────────────
DEFAULT_PASSWORD = 'Password123'
DEFAULT_DEPARTMENT = None      # only touch profile.department when explicitly passed
DEFAULT_JOB_TITLE  = None      # same rule
# ─────────────────────────────────────────────────────────────────────────────

User = get_user_model()


class Command(BaseCommand):
    help = 'DEV-ONLY: activate a local user, reset password, clear must_change_password. Refuses in production.'

    def add_arguments(self, parser):
        parser.add_argument('email', help='Email of the user to reset')
        parser.add_argument('--password',   default=DEFAULT_PASSWORD, help=f'New password (default: {DEFAULT_PASSWORD})')
        parser.add_argument('--department', default=DEFAULT_DEPARTMENT, help='Set UserProfile.department')
        parser.add_argument('--title',      default=DEFAULT_JOB_TITLE,  help='Set UserProfile.job_title')
        parser.add_argument('--force',      action='store_true', help='Bypass the DEBUG safety check (still refuses when ENVIRONMENT=production)')

    def handle(self, *args, **opts):
        # ─── Safety: refuse in production ───────────────────────────────────
        env = (getattr(settings, 'ENVIRONMENT', '') or '').lower()
        if env == 'production':
            raise CommandError('Refusing to run: ENVIRONMENT=production. This command is DEV-ONLY.')
        if not settings.DEBUG and not opts['force']:
            raise CommandError('Refusing to run: DEBUG=False. Pass --force to override (not recommended).')

        email = opts['email'].strip()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f'No user with email={email}')

        # 1. activate + password
        user.is_active = True
        user.set_password(opts['password'])
        user.save()

        # 2. clear must_change_password + optional profile fields
        profile_msg = ''
        try:
            from apps.rbac.models import UserProfile
            profile, _ = UserProfile.objects.get_or_create(user=user)
            changed_fields = []
            if profile.must_change_password:
                profile.must_change_password = False
                changed_fields.append('must_change_password')
            if opts['department']:
                profile.department = opts['department']
                changed_fields.append('department')
            if opts['title']:
                profile.job_title = opts['title']
                changed_fields.append('job_title')
            if changed_fields:
                profile.save(update_fields=changed_fields)
            profile_msg = f'department={profile.department!r} · title={profile.job_title!r}'
        except Exception as e:
            profile_msg = f'(profile update skipped: {e})'

        # 3. clear failed-login lock counters if middleware uses cache keys
        try:
            from django.core.cache import cache
            for k in [f'login_fail_{email}', f'login_lock_{email}']:
                cache.delete(k)
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(
            f'\n[dev_reset_user] OK'
            f'\n  email     : {user.email}'
            f'\n  username  : {user.username}'
            f'\n  is_active : {user.is_active}'
            f'\n  password  : {opts["password"]}   (works: {user.check_password(opts["password"])})'
            f'\n  profile   : {profile_msg}\n'
        ))
