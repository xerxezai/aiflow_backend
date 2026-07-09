"""
Migration 0004 — Normalize employee identity fields + add lookup indexes

Applies the same normalisation logic as identity.norm_* to all existing rows
in TimesheetEvent, BiometricUserMaster, DailyAttendanceSummary.

Handles the trickiest case: after stripping whitespace from employee_code,
two DailyAttendanceSummary rows for the same logical employee+date may become
duplicates.  The migration merges them (keeps the row with higher hours) before
normalising so the unique_together constraint is never violated.

Also adds db_index=True on:
  - TimesheetEvent.employee_name  (powers name-based resolver)
  - TimesheetEvent.employee_email (powers email-based resolver)
"""
from django.db import migrations, models


def _norm_code(s):
    return str(s or '').strip()


def _norm_email(s):
    return str(s or '').strip().lower()


def _norm_name(s):
    import re
    return re.sub(r'\s+', ' ', str(s or '').strip())


def normalize_timesheet_events(apps, schema_editor):
    """Strip whitespace from employee_code / name / email in TimesheetEvent."""
    from django.db import connection
    db = schema_editor.connection.alias
    # Use a direct SQL UPDATE for speed — avoids ORM overhead on large tables.
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


def normalize_biometric_user_master(apps, schema_editor):
    """Normalise employee_code, full_name, office_email, personal_email."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE timesheet_biometricusermaster "
            "SET employee_code  = TRIM(employee_code), "
            "    full_name      = TRIM(full_name), "
            "    office_email   = LOWER(TRIM(office_email)), "
            "    personal_email = LOWER(TRIM(personal_email)) "
            "WHERE employee_code  != TRIM(employee_code) "
            "   OR full_name      != TRIM(full_name) "
            "   OR office_email   != LOWER(TRIM(office_email)) "
            "   OR personal_email != LOWER(TRIM(personal_email))"
        )


def normalize_daily_attendance_summary(apps, schema_editor):
    """
    Deduplicate then normalise DailyAttendanceSummary.employee_code.

    After stripping, records with codes '22393' and '22393 ' become the same
    canonical key ('22393').  We must merge before normalising to avoid
    violating the unique_together(employee_code, date) constraint.

    Merge strategy: keep the record with the highest effective_hours.
    """
    DailyAttendanceSummary = apps.get_model('timesheet', 'DailyAttendanceSummary')

    # Find all (stripped_code, date) groups that have more than one record.
    from django.db.models import Count
    from django.db import connection

    # Step 1: collect duplicates using raw SQL for efficiency.
    with connection.cursor() as cur:
        cur.execute("""
            SELECT TRIM(employee_code) AS norm_code, date, COUNT(*) AS cnt
            FROM timesheet_dailyattendancesummary
            GROUP BY TRIM(employee_code), date
            HAVING COUNT(*) > 1
        """)
        dupes = cur.fetchall()  # [(norm_code, date, count), ...]

    for norm_code, day, _ in dupes:
        # Fetch all variants for this (norm_code, date).
        variants = list(
            DailyAttendanceSummary.objects.filter(
                employee_code__in=[norm_code, norm_code + ' ', ' ' + norm_code],
                date=day,
            ).order_by('-effective_hours')
        )
        if len(variants) <= 1:
            continue
        # Keep the one with highest effective_hours; delete the rest.
        keep = variants[0]
        for dup in variants[1:]:
            dup.delete()
        # Normalise the survivor's code.
        if keep.employee_code != norm_code:
            keep.employee_code = norm_code
            keep.save(update_fields=['employee_code'])

    # Step 2: bulk-normalise remaining rows (those that didn't have duplicates).
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE timesheet_dailyattendancesummary "
            "SET employee_code = TRIM(employee_code) "
            "WHERE employee_code != TRIM(employee_code)"
        )


def reverse_noop(apps, schema_editor):
    pass  # Normalization is irreversible by design.


class Migration(migrations.Migration):

    dependencies = [
        ('timesheet', '0003_daily_attendance_summary'),
    ]

    operations = [
        # 1. Normalise existing data first (before adding indexes that depend on clean data).
        migrations.RunPython(normalize_timesheet_events,        reverse_noop),
        migrations.RunPython(normalize_biometric_user_master,   reverse_noop),
        migrations.RunPython(normalize_daily_attendance_summary, reverse_noop),

        # 2. Add lookup indexes for name and email — powers the resolver strategies
        #    without requiring a table scan on every ingest/lookup cycle.
        migrations.AddIndex(
            model_name='timesheetevent',
            index=models.Index(
                fields=['employee_name'],
                name='ts_event_emp_name_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='timesheetevent',
            index=models.Index(
                fields=['employee_email'],
                name='ts_event_emp_email_idx',
            ),
        ),
    ]
