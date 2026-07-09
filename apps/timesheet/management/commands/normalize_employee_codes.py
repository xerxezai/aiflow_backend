"""
Management command: normalize_employee_codes
=============================================
Applies the canonical identity normalization (identity.norm_*) to all existing
rows across every table that stores employee_code, employee_name, or email.

Run this once on production after deploying to clean up historical data.
The DB migration (0004 / 0007) runs it automatically via RunPython, but this
command can be re-run any time to verify or re-clean data.

Usage:
    python manage.py normalize_employee_codes [--dry-run] [--table TABLE]

Options:
    --dry-run       Print counts of dirty records without modifying DB.
    --table TABLE   Only process one table: events|master|summary|leave|request|profile
                    (default: all)

Soft-coded: the normalization strategy is controlled by env vars in
apps.timesheet.identity (TIMESHEET_CODE_NORM, TIMESHEET_EMAIL_NORM, etc.).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Normalize employee_code / employee_name / email across all identity tables'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', default=False,
            help='Show counts of dirty records without modifying.',
        )
        parser.add_argument(
            '--table', choices=['events', 'master', 'summary', 'leave', 'request', 'profile', 'all'],
            default='all', help='Restrict to one table.',
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        table = options['table']
        mode = '[DRY RUN]' if dry else '[LIVE]'
        self.stdout.write(f'{mode} normalize_employee_codes starting...\n')

        results = {}
        if table in ('events', 'all'):
            results['TimesheetEvent'] = self._normalize_events(dry)
        if table in ('master', 'all'):
            results['BiometricUserMaster'] = self._normalize_master(dry)
        if table in ('summary', 'all'):
            results['DailyAttendanceSummary'] = self._normalize_summary(dry)
        if table in ('leave', 'all'):
            results['EmployeeLeaveRecord'] = self._normalize_leave(dry)
        if table in ('request', 'all'):
            results['LeaveRequest'] = self._normalize_requests(dry)
        if table in ('profile', 'all'):
            results['UserProfile'] = self._normalize_profiles(dry)

        self.stdout.write('\nSummary:')
        for tbl, (dirty, fixed, merged) in results.items():
            self.stdout.write(
                f'  {tbl}: {dirty} dirty rows, '
                f'{fixed} normalised, {merged} duplicates merged'
            )
        self.stdout.write(f'\n{mode} Done.\n')

    # ─────────────────────────────────────────────────────────────────────────
    def _count_dirty_events(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM timesheet_timesheetevent "
                "WHERE employee_code != TRIM(employee_code) "
                "   OR employee_name != TRIM(employee_name) "
                "   OR employee_email != LOWER(TRIM(employee_email))"
            )
            return cur.fetchone()[0]

    def _normalize_events(self, dry: bool) -> tuple:
        dirty = self._count_dirty_events()
        if dry or dirty == 0:
            return dirty, 0, 0
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE timesheet_timesheetevent "
                "SET employee_code  = TRIM(employee_code), "
                "    employee_name  = TRIM(employee_name), "
                "    employee_email = LOWER(TRIM(employee_email)) "
                "WHERE employee_code  != TRIM(employee_code) "
                "   OR employee_name  != TRIM(employee_name) "
                "   OR employee_email != LOWER(TRIM(employee_email))"
            )
            fixed = cur.rowcount
        self.stdout.write(f'  ✓ TimesheetEvent: {fixed} rows normalised')
        return dirty, fixed, 0

    def _normalize_master(self, dry: bool) -> tuple:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM timesheet_biometricusermaster "
                "WHERE employee_code != TRIM(employee_code) "
                "   OR full_name     != TRIM(full_name) "
                "   OR office_email  != LOWER(TRIM(office_email)) "
                "   OR personal_email != LOWER(TRIM(personal_email))"
            )
            dirty = cur.fetchone()[0]
        if dry or dirty == 0:
            return dirty, 0, 0
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE timesheet_biometricusermaster "
                "SET employee_code  = TRIM(employee_code), "
                "    full_name      = TRIM(full_name), "
                "    office_email   = LOWER(TRIM(office_email)), "
                "    personal_email = LOWER(TRIM(personal_email)) "
                "WHERE employee_code  != TRIM(employee_code) "
                "   OR full_name     != TRIM(full_name) "
                "   OR office_email  != LOWER(TRIM(office_email)) "
                "   OR personal_email != LOWER(TRIM(personal_email))"
            )
            fixed = cur.rowcount
        self.stdout.write(f'  ✓ BiometricUserMaster: {fixed} rows normalised')
        return dirty, fixed, 0

    def _normalize_summary(self, dry: bool) -> tuple:
        """Merge duplicates then normalise DailyAttendanceSummary."""
        from apps.timesheet.models import DailyAttendanceSummary
        merged_total = 0

        # Find duplicate (norm_code, date) groups
        with connection.cursor() as cur:
            cur.execute("""
                SELECT TRIM(employee_code) AS nc, date, COUNT(*) AS cnt
                FROM timesheet_dailyattendancesummary
                GROUP BY TRIM(employee_code), date
                HAVING COUNT(*) > 1
            """)
            dupes = cur.fetchall()

        for norm_code, day, _ in dupes:
            variants = list(
                DailyAttendanceSummary.objects.filter(
                    employee_code__in=[norm_code, norm_code + ' ', ' ' + norm_code],
                    date=day,
                ).order_by('-effective_hours')
            )
            if len(variants) <= 1:
                continue
            merged_total += len(variants) - 1
            if not dry:
                keep = variants[0]
                for dup in variants[1:]:
                    dup.delete()
                if keep.employee_code != norm_code:
                    keep.employee_code = norm_code
                    keep.save(update_fields=['employee_code'])

        # Now bulk-normalise remaining rows
        with connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM timesheet_dailyattendancesummary "
                "WHERE employee_code != TRIM(employee_code)"
            )
            dirty = cur.fetchone()[0]

        fixed = 0
        if not dry and dirty > 0:
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE timesheet_dailyattendancesummary "
                    "SET employee_code = TRIM(employee_code) "
                    "WHERE employee_code != TRIM(employee_code)"
                )
                fixed = cur.rowcount
            self.stdout.write(f'  ✓ DailyAttendanceSummary: {fixed} rows normalised, {merged_total} duplicates merged')
        return dirty, fixed, merged_total

    def _normalize_leave(self, dry: bool) -> tuple:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM payroll_employee_leave_record "
                "WHERE (employee_code IS NOT NULL AND employee_code != TRIM(employee_code)) "
                "   OR employee_name != TRIM(employee_name)"
            )
            dirty = cur.fetchone()[0]
        if dry or dirty == 0:
            return dirty, 0, 0
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE payroll_employee_leave_record "
                "SET employee_code = TRIM(employee_code), "
                "    employee_name = TRIM(employee_name) "
                "WHERE (employee_code IS NOT NULL AND employee_code != TRIM(employee_code)) "
                "   OR employee_name != TRIM(employee_name)"
            )
            fixed = cur.rowcount
        self.stdout.write(f'  ✓ EmployeeLeaveRecord: {fixed} rows normalised')
        return dirty, fixed, 0

    def _normalize_requests(self, dry: bool) -> tuple:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM payroll_leave_request "
                "WHERE (employee_code IS NOT NULL AND employee_code != TRIM(employee_code)) "
                "   OR employee_name != TRIM(employee_name)"
            )
            dirty = cur.fetchone()[0]
        if dry or dirty == 0:
            return dirty, 0, 0
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE payroll_leave_request "
                "SET employee_code = TRIM(employee_code), "
                "    employee_name = TRIM(employee_name) "
                "WHERE (employee_code IS NOT NULL AND employee_code != TRIM(employee_code)) "
                "   OR employee_name != TRIM(employee_name)"
            )
            fixed = cur.rowcount
        self.stdout.write(f'  ✓ LeaveRequest: {fixed} rows normalised')
        return dirty, fixed, 0

    def _normalize_profiles(self, dry: bool) -> tuple:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM rbac_user_profiles "
                "WHERE employee_id IS NOT NULL "
                "  AND employee_id != '' "
                "  AND employee_id != TRIM(employee_id)"
            )
            dirty = cur.fetchone()[0]
        if dry or dirty == 0:
            return dirty, 0, 0
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE rbac_user_profiles "
                "SET employee_id = TRIM(employee_id) "
                "WHERE employee_id IS NOT NULL "
                "  AND employee_id != '' "
                "  AND employee_id != TRIM(employee_id)"
            )
            fixed = cur.rowcount
        self.stdout.write(f'  ✓ UserProfile.employee_id: {fixed} rows normalised')
        return dirty, fixed, 0
