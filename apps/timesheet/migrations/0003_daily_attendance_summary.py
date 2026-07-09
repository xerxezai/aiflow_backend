"""
Migration 0003 — DailyAttendanceSummary

Materialised per-employee per-day work-hours summary.
Computed by the paired-hours engine whenever events are ingested.
Acts as the single source of truth for payroll and self-service hours.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('timesheet', '0002_biometricusermaster'),
    ]

    operations = [
        migrations.CreateModel(
            name='DailyAttendanceSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee_code',       models.CharField(db_index=True, max_length=64)),
                ('date',                models.DateField(db_index=True)),
                ('paired_hours',        models.FloatField(default=0.0,
                    help_text='Sum of completed IN-OUT pair durations (hours).')),
                ('elapsed_hours',       models.FloatField(default=0.0,
                    help_text='first_in to last_out regardless of interim punches.')),
                ('effective_hours',     models.FloatField(db_index=True, default=0.0,
                    help_text='Hours used by payroll/reports. equals paired_hours or elapsed_hours per mode.')),
                ('first_in',            models.DateTimeField(blank=True, null=True)),
                ('last_out',            models.DateTimeField(blank=True, null=True)),
                ('punch_count_in',      models.PositiveSmallIntegerField(default=0)),
                ('punch_count_out',     models.PositiveSmallIntegerField(default=0)),
                ('paired_segments',     models.PositiveSmallIntegerField(default=0,
                    help_text='Number of matched IN-OUT pairs.')),
                ('open_shift',          models.BooleanField(db_index=True, default=False,
                    help_text='True when the last IN punch has no matching OUT yet.')),
                ('open_shift_since',    models.DateTimeField(blank=True, null=True,
                    help_text='Timestamp of the unmatched IN punch, if open_shift=True.')),
                ('open_shift_credited', models.FloatField(default=0.0,
                    help_text='Hours credited for the open shift (capped by TIMESHEET_OPEN_SHIFT_MAX_HOURS).')),
                ('is_late',             models.BooleanField(default=False)),
                ('is_full_day',         models.BooleanField(default=False)),
                ('computed_at',         models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-date', 'employee_code'],
            },
        ),
        migrations.AddConstraint(
            model_name='dailyattendancesummary',
            constraint=models.UniqueConstraint(
                fields=['employee_code', 'date'],
                name='ts_daily_sum_unique_code_date',
            ),
        ),
        migrations.AddIndex(
            model_name='dailyattendancesummary',
            index=models.Index(
                fields=['employee_code', '-date'],
                name='ts_daily_sum_code_date',
            ),
        ),
        migrations.AddIndex(
            model_name='dailyattendancesummary',
            index=models.Index(
                fields=['-date', 'open_shift'],
                name='ts_daily_sum_open',
            ),
        ),
    ]
