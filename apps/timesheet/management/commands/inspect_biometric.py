"""Quick read-only inspector for the biometric mirror table.

Usage on Railway shell:
    python manage.py inspect_biometric --email sanket.kondare@rejlers.ae
    python manage.py inspect_biometric --name kondare
    python manage.py inspect_biometric --code 22974

Prints what the resolver would see + a sample of matching rows so we can tell
whether a user is missing from the mirror, mis-spelled, or simply uncached.
"""
from django.core.management.base import BaseCommand

from apps.timesheet.models import TimesheetEvent
from apps.timesheet import mirror_services as ms


class Command(BaseCommand):
    help = 'Inspect what the biometric mirror has for a given email/name/code.'

    def add_arguments(self, parser):
        parser.add_argument('--email', default='')
        parser.add_argument('--name', default='', help='Substring match against employee_name')
        parser.add_argument('--code', default='')
        parser.add_argument('--limit', type=int, default=10)

    def handle(self, *args, **opts):
        email = (opts['email'] or '').strip()
        name  = (opts['name']  or '').strip()
        code  = (opts['code']  or '').strip()
        limit = max(1, int(opts['limit']))

        self.stdout.write(self.style.MIGRATE_HEADING('Inputs'))
        self.stdout.write(f'  email={email!r} name={name!r} code={code!r}')

        if email:
            qs = TimesheetEvent.objects.filter(employee_email__iexact=email)
            rows = list(qs.values('employee_code', 'employee_name', 'employee_email').distinct()[:limit])
            self.stdout.write(self.style.MIGRATE_HEADING(f'employee_email__iexact \u2192 {qs.count()} events / {len(rows)} distinct'))
            for r in rows:
                self.stdout.write(f'  {r}')

        if name:
            qs = TimesheetEvent.objects.filter(employee_name__icontains=name)
            rows = list(qs.values('employee_code', 'employee_name', 'employee_email').distinct()[:limit])
            self.stdout.write(self.style.MIGRATE_HEADING(f'employee_name__icontains={name!r} \u2192 {qs.count()} events / {len(rows)} distinct'))
            for r in rows:
                self.stdout.write(f'  {r}')

        if code:
            qs = TimesheetEvent.objects.filter(employee_code=code)
            rows = list(qs.values('employee_code', 'employee_name', 'employee_email').distinct()[:limit])
            self.stdout.write(self.style.MIGRATE_HEADING(f'employee_code={code!r} \u2192 {qs.count()} events / {len(rows)} distinct'))
            for r in rows:
                self.stdout.write(f'  {r}')

        self.stdout.write(self.style.MIGRATE_HEADING('Resolver output (mirror)'))
        codes = ms._resolve_biometric_codes_mirror(profile=None, email=email or None, employee_code=code or None)
        self.stdout.write(f'  _resolve_biometric_codes_mirror \u2192 {sorted(codes)}')
        emails_set, codes_set = ms._resolve_user_aliases_mirror(code or None, email or None)
        self.stdout.write(f'  _resolve_user_aliases_mirror   \u2192 emails={sorted(emails_set)} codes={sorted(codes_set)}')
