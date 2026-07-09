"""Generate a monthly Draft PayrollRun from existing PayrollEmployee data
and pending PayrollAdjustment entries.

Example
-------
python manage.py generate_payroll_run --year 2026 --month 4
python manage.py generate_payroll_run --year 2026 --month 4 --overwrite
"""
from django.core.management.base import BaseCommand, CommandError

from apps.payroll_engine.services import run_generator


class Command(BaseCommand):
    help = 'Generate a Draft PayrollRun for a given (year, month).'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--overwrite', action='store_true')
        parser.add_argument('--note', default='')

    def handle(self, *args, **opts):
        try:
            run = run_generator.generate_monthly_run(
                opts['year'], opts['month'],
                overwrite=opts['overwrite'],
                note=opts['note'],
            )
        except run_generator.GenerationError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f'PayrollRun {run.cycle_code} — {run.employee_count} payslips, '
            f'gross {run.total_gross}, deductions {run.total_deductions}, net {run.total_net}.'
        ))
