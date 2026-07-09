"""
Compute Leave Accrual — Django Management Command
==================================================
Recomputes the "earned" and "balance" columns for every EmployeeLeaveRecord
using the authoritative formula in apps.payroll.services.leave_accrual.

The command does NOT touch "taken", "encashed", or "carryforward" — those
values come from the HR Excel import and are the source of truth.

Usage:
    # Compute accruals for all employees for 2026
    python manage.py compute_leave_accrual

    # Specific year
    python manage.py compute_leave_accrual --year 2025

    # Single employee (by employee code)
    python manage.py compute_leave_accrual --employee-code 10954

    # Dry run (print what would change, no DB writes)
    python manage.py compute_leave_accrual --dry-run

    # Limit to a branch
    python manage.py compute_leave_accrual --branch RAD
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Recompute annual leave earned + balance using the 22-days accrual formula'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year', type=int, default=2026,
            help='Leave year to recompute (default: 2026)',
        )
        parser.add_argument(
            '--employee-code', default=None,
            help='Limit to a single employee code',
        )
        parser.add_argument(
            '--branch', default=None, choices=['RAD', 'RIN'],
            help='Limit to a branch (RAD or RIN)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print changes without writing to the database',
        )

    def handle(self, *args, **options):
        # Lazy imports — avoid circular imports at module load
        from apps.payroll.models import EmployeeLeaveRecord
        from apps.payroll.services.leave_accrual import compute_accrual_for_record

        year     = options['year']
        dry      = options['dry_run']
        emp_code = options['employee_code']
        branch   = options['branch']

        qs = (
            EmployeeLeaveRecord.objects
            .prefetch_related('monthly_breakdown')
            .filter(year=year)
        )
        if emp_code:
            qs = qs.filter(employee_code=emp_code)
        if branch:
            qs = qs.filter(branch__iexact=branch)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING(
                f'No EmployeeLeaveRecord rows found for year={year}. '
                f'Run import_leave_excel first.'
            ))
            return

        mode_label = '(DRY RUN)' if dry else ''
        self.stdout.write(
            f'🔄  Computing accruals for {total} records  year={year}  {mode_label}'
        )

        processed = errors = 0
        for record in qs.iterator(chunk_size=100):
            try:
                summary = compute_accrual_for_record(record, target_year=year, dry_run=dry)
                self.stdout.write(
                    f'  {"DRY" if dry else "OK "}  '
                    f'{summary["employee_name"]!r:45s}  '
                    f'code={summary["employee_code"] or "—":8s}  '
                    f'joined={summary["joining_date"]}  '
                    f'cf={summary["carryforward"]:7.4f}  '
                    f'earned={summary["total_earned"]:7.4f}  '
                    f'taken={summary["total_taken"]:6.2f}  '
                    f'bal={summary["leave_balance"]:7.4f}'
                )
                processed += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f'  ERROR  {record.employee_name!r}: {exc}'
                )

        if dry:
            self.stdout.write(self.style.WARNING(
                f'\n🔍 DRY RUN — {processed} would be updated, {errors} errors'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\n✅ Done — {processed} records updated, {errors} errors'
            ))
