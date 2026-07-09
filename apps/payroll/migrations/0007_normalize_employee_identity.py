"""
Migration 0007 — Normalize employee_code / employee_name identity fields in payroll

Applies the same normalisation logic as apps.timesheet.identity.norm_* to all
existing rows in EmployeeLeaveRecord and LeaveRequest so cross-table lookups
(e.g. annual-leave-balance API matching biometric code → HR leave record) always
resolve correctly even when the source Excel or biometric device used different
whitespace conventions.
"""
from django.db import migrations


def normalize_payroll_employee_codes(apps, schema_editor):
    """Strip + collapse whitespace from employee_code and employee_name."""
    from django.db import connection

    # EmployeeLeaveRecord
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE payroll_employee_leave_record "
            "SET employee_code = TRIM(employee_code), "
            "    employee_name = TRIM(employee_name) "
            "WHERE (employee_code IS NOT NULL AND employee_code != TRIM(employee_code)) "
            "   OR employee_name != TRIM(employee_name)"
        )

    # LeaveRequest — uses payroll_leave_request table
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE payroll_leave_request "
            "SET employee_code = TRIM(employee_code), "
            "    employee_name = TRIM(employee_name) "
            "WHERE (employee_code IS NOT NULL AND employee_code != TRIM(employee_code)) "
            "   OR employee_name != TRIM(employee_name)"
        )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0006_salary_management'),
    ]

    operations = [
        migrations.RunPython(normalize_payroll_employee_codes, reverse_noop),
    ]
