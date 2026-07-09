"""
Finance App Configuration - Celery Beat Schedules
Automated payroll generation and monthly tasks
"""
from celery.schedules import crontab

# ═══════════════════════════════════════════════════════════════════════════
# CELERY BEAT SCHEDULE - Automated Monthly Payroll Generation
# ═══════════════════════════════════════════════════════════════════════════

BEAT_SCHEDULE = {
    # Auto-generate monthly payroll on the 25th of each month at 09:00 AM
    'auto-generate-monthly-payroll': {
        'task': 'finance.auto_generate_monthly_payroll',
        'schedule': crontab(day_of_month='25', hour='9', minute='0'),
        'args': [],  # Uses current year/month by default
        'kwargs': {'force': False},
        'options': {
            'expires': 3600,  # Task expires after 1 hour if not picked up
            'priority': 5,    # High priority
        },
    },
    
    # Optional: Run at the start of each month as well (for late processing)
    'auto-generate-payroll-month-start': {
        'task': 'finance.auto_generate_monthly_payroll',
        'schedule': crontab(day_of_month='1', hour='10', minute='0'),
        'args': [],
        'kwargs': {'force': False},
        'options': {
            'expires': 3600,
            'priority': 4,
        },
    },
}
