"""
Time Sheet — Postgres mirror of biometric attendance events.

Filled by the office-side agent (`scripts/timesheet_mirror_sync.py`) which
reads from the on-prem SQL Server and POSTs batches to the ingest endpoint.
Read by `mirror_services.py` when `TIMESHEET_DATA_SOURCE=mirror` (Railway/
production), so the API serves attendance data without ever needing a route
to the office LAN.

`source_event_id` is a deterministic hash of (employee_code, event_time,
event_type) — guarantees idempotent upserts even if the agent re-uploads.
"""
from django.db import models
from .identity import norm_code, norm_email, norm_name


class TimesheetEvent(models.Model):
    EVENT_IN = 'IN'
    EVENT_OUT = 'OUT'
    EVENT_CHOICES = [(EVENT_IN, 'IN'), (EVENT_OUT, 'OUT')]

    source_event_id = models.CharField(max_length=128, unique=True, db_index=True)
    employee_code = models.CharField(max_length=64, db_index=True)
    employee_name = models.CharField(max_length=255, blank=True, default='')
    employee_email = models.CharField(max_length=255, blank=True, default='')
    department = models.CharField(max_length=255, blank=True, default='')
    event_time = models.DateTimeField(db_index=True)
    event_type = models.CharField(max_length=8, choices=EVENT_CHOICES, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event_time']
        indexes = [
            models.Index(fields=['employee_code', '-event_time']),
            models.Index(fields=['-event_time', 'event_type']),
        ]

    def __str__(self):
        return f'{self.employee_code} {self.event_type} @ {self.event_time:%Y-%m-%d %H:%M}'

    def save(self, *args, **kwargs):
        # Normalise identity fields at every write — prevents whitespace/case
        # variants from creating duplicate logical rows in analytics.
        self.employee_code  = norm_code(self.employee_code)
        self.employee_email = norm_email(self.employee_email)
        self.employee_name  = norm_name(self.employee_name)
        super().save(*args, **kwargs)


class BiometricUserMaster(models.Model):
    """Postgres mirror of the Matrix `Mx_VEW_UserDetails` user-master view.

    Populated by the office-side agent via the `/timesheet/mirror/ingest-users/`
    endpoint so Railway/production can enrich attendance rows with `Card1`,
    `OfficeEmail`, etc. without needing a route to the on-prem SQL Server.

    All columns are optional except `employee_code` — keeps the schema soft
    so new Matrix fields can be added by extending `extra` (JSON) instead of
    requiring a migration each time.
    """
    employee_code  = models.CharField(max_length=64, unique=True, db_index=True)
    full_name      = models.CharField(max_length=255, blank=True, default='')
    card1          = models.CharField(max_length=64,  blank=True, default='')
    card2          = models.CharField(max_length=64,  blank=True, default='')
    office_email   = models.CharField(max_length=255, blank=True, default='')
    personal_email = models.CharField(max_length=255, blank=True, default='')
    designation    = models.CharField(max_length=255, blank=True, default='')
    department     = models.CharField(max_length=255, blank=True, default='')
    # Forward-compat bucket for any extra columns the agent decides to push.
    extra          = models.JSONField(default=dict, blank=True)
    synced_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee_code']

    def __str__(self):
        return f'{self.employee_code} — {self.full_name or "?"}'

    def save(self, *args, **kwargs):
        # Canonical identity normalization — every write goes through norm_*.
        self.employee_code  = norm_code(self.employee_code)
        self.full_name      = norm_name(self.full_name)
        self.office_email   = norm_email(self.office_email)
        self.personal_email = norm_email(self.personal_email)
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# DailyAttendanceSummary — materialised per-employee per-day work-hours record
#
# Computed (and upserted) by mirror_services._compute_and_save_day() whenever:
#   1. New events are ingested via the mirror ingest endpoint.
#   2. The daily_report() API is called (on-demand recompute for the day).
#
# Hours calculation mode is governed by TIMESHEET_HOURS_MODE:
#   'paired'  — only paired IN→OUT segments count (default, anti-abuse)
#   'elapsed' — first_in to last_out (legacy)
#
# This table is the single source of truth consumed by:
#   • Payroll Attendance Summary
#   • Employee Self-Service hours display
#   • Monthly roll-up (avoids re-pairing for past days on every request)
# ─────────────────────────────────────────────────────────────────────────────
class DailyAttendanceSummary(models.Model):
    employee_code       = models.CharField(max_length=64, db_index=True)
    date                = models.DateField(db_index=True)

    # ── Paired-hours mode (anti-coffee-break abuse) ──
    paired_hours        = models.FloatField(
        default=0.0,
        help_text='Sum of completed IN→OUT pair durations (hours). '
                  'Only changes when a matching OUT punch arrives.',
    )
    # ── Legacy elapsed mode ──
    elapsed_hours       = models.FloatField(
        default=0.0,
        help_text='first_in to last_out regardless of interim punches.',
    )
    # ── Effective hours used by payroll / reports ──
    # Set to `paired_hours` or `elapsed_hours` depending on TIMESHEET_HOURS_MODE.
    # Kept as a separate column so the payroll engine reads one field and the
    # mode can be changed retroactively by re-running the summary recompute.
    effective_hours     = models.FloatField(
        default=0.0,
        db_index=True,
        help_text='Hours field consumed by payroll and attendance reports. '
                  'Equals paired_hours when mode=paired, else elapsed_hours.',
    )

    first_in            = models.DateTimeField(null=True, blank=True)
    last_out            = models.DateTimeField(null=True, blank=True)

    punch_count_in      = models.PositiveSmallIntegerField(default=0)
    punch_count_out     = models.PositiveSmallIntegerField(default=0)
    paired_segments     = models.PositiveSmallIntegerField(
        default=0,
        help_text='Number of matched IN→OUT pairs.',
    )

    # ── Open-shift tracking ──
    open_shift          = models.BooleanField(
        default=False,
        db_index=True,
        help_text='True when the last IN punch has no matching OUT yet.',
    )
    open_shift_since    = models.DateTimeField(
        null=True, blank=True,
        help_text='Timestamp of the unmatched IN punch, if open_shift=True.',
    )
    open_shift_credited = models.FloatField(
        default=0.0,
        help_text='Hours credited for the open shift (capped by TIMESHEET_OPEN_SHIFT_MAX_HOURS).',
    )

    is_late             = models.BooleanField(default=False)
    is_full_day         = models.BooleanField(default=False)

    # Audit
    computed_at         = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('employee_code', 'date')]
        indexes = [
            models.Index(fields=['employee_code', '-date'], name='ts_daily_sum_code_date'),
            models.Index(fields=['-date', 'open_shift'],    name='ts_daily_sum_open'),
        ]
        ordering = ['-date', 'employee_code']

    def __str__(self):
        return f'{self.employee_code} {self.date} {self.effective_hours:.2f}h'

    def save(self, *args, **kwargs):
        # Always store normalised employee_code so the unique_together
        # constraint on (employee_code, date) matches lookups correctly.
        self.employee_code = norm_code(self.employee_code)
        super().save(*args, **kwargs)
