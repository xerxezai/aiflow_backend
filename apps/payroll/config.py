"""
Payroll App Configuration
==========================
Soft-coded settings for the payroll module.

BEAT_SCHEDULE: Celery periodic task definitions.
"""
from celery.schedules import crontab

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED SCHEDULE CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Monthly leave accrual schedule
# Default: 1st of every month at 00:05 AM (5 minutes after midnight)
# Runs after day rollover to ensure correct month processing
MONTHLY_ACCRUAL_DAY_OF_MONTH = 1
MONTHLY_ACCRUAL_HOUR = 0
MONTHLY_ACCRUAL_MINUTE = 5

# ─────────────────────────────────────────────────────────────────────────────
# CELERY BEAT SCHEDULE
# Exported to config/celery.py for registration
# ─────────────────────────────────────────────────────────────────────────────

BEAT_SCHEDULE = {
    # Automated monthly leave accrual — adds 1.83 days to all employees on the 1st of each month
    'payroll-monthly-leave-accrual': {
        'task': 'payroll.run_monthly_leave_accrual',
        'schedule': crontab(
            day_of_month=str(MONTHLY_ACCRUAL_DAY_OF_MONTH),
            hour=MONTHLY_ACCRUAL_HOUR,
            minute=MONTHLY_ACCRUAL_MINUTE,
        ),
        'kwargs': {
            'triggered_by': 'celery_beat',
        },
        'options': {
            'expires': 3600 * 12,  # Task expires after 12 hours if not executed
        },
    },
}
