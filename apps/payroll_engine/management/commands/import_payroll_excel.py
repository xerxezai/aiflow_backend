"""Import the master Payroll Excel and optionally generate a Draft run.

Examples
--------
# Seed master roster only (no run)
python manage.py import_payroll_excel /tmp/payroll.xlsx --mode master

# Full import: master + adjustments + generate Draft PayrollRun
python manage.py import_payroll_excel /tmp/payroll.xlsx --year 2026 --month 4 --mode full

# Adjustments only (run must already exist)
python manage.py import_payroll_excel /tmp/adjustments.xlsx --year 2026 --month 4 --mode adjustments
"""
from django.core.management.base import BaseCommand, CommandError

from apps.payroll_engine.services import excel_import, run_generator


class Command(BaseCommand):
    help = 'Import master roster Excel into the Payroll Engine.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Path to the .xlsx file.')
        parser.add_argument('--year', type=int, default=None)
        parser.add_argument('--month', type=int, default=None)
        parser.add_argument('--mode', choices=['master', 'adjustments', 'full'],
                            default='master')
        parser.add_argument('--regenerate', action='store_true',
                            help='With --mode=full, overwrite an existing Draft run.')

    def handle(self, *args, **opts):
        path = opts['path']
        mode = opts['mode']
        year = opts.get('year')
        month = opts.get('month')

        if mode in ('full', 'adjustments') and not (year and month):
            raise CommandError('--year and --month are required for this mode.')

        if mode == 'master':
            summary = excel_import.import_master_roster(path)
        elif mode == 'adjustments':
            summary = excel_import.import_adjustments(path, year, month)
        elif mode == 'full':
            summary = excel_import.import_full_payroll(path, year, month)
            try:
                run = run_generator.generate_monthly_run(
                    year, month, overwrite=opts['regenerate'],
                    note=f'Imported from {path}',
                )
            except run_generator.GenerationError as exc:
                self.stdout.write(self.style.WARNING(
                    f'Import OK but run generation failed: {exc}. Pass --regenerate to overwrite.'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'PayrollRun {run.cycle_code} created — '
                    f'{run.employee_count} employees, net total {run.total_net}.'
                ))
        else:
            raise CommandError(f'Unknown mode: {mode}')

        for k, v in summary.as_dict().items():
            self.stdout.write(f'  {k}: {v}')
        self.stdout.write(self.style.SUCCESS('Done.'))
