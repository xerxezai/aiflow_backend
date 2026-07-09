"""
Celery configuration for RADAI project.
Smart task queue configuration for background processing.
"""
import os
from celery import Celery

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('radai')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# ─────────────────────────────────────────────────────────────────────────────
# Periodic tasks (soft-coded; per-app dicts merged at import time so adding a
# new feature is one-line, never edits to celery.py).
# ─────────────────────────────────────────────────────────────────────────────
from celery.schedules import crontab

_beat_schedule: dict = {}

try:
    from apps.timesheet.config import BEAT_SCHEDULE as _timesheet_beat
    _beat_schedule.update(_timesheet_beat)
except Exception:
    pass

try:
    from apps.project_control.config import BEAT_SCHEDULE as _project_control_beat
    _beat_schedule.update(_project_control_beat)
except Exception:
    pass

try:
    from apps.finance.config import BEAT_SCHEDULE as _finance_beat
    _beat_schedule.update(_finance_beat)
except Exception:
    pass

try:
    from apps.dashboard.config import BEAT_SCHEDULE as _dashboard_beat
    _beat_schedule.update(_dashboard_beat)
except Exception:
    pass

try:
    from apps.payroll.config import BEAT_SCHEDULE as _payroll_beat
    _beat_schedule.update(_payroll_beat)
except Exception:
    pass

if _beat_schedule:
    app.conf.beat_schedule = _beat_schedule


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery configuration."""
    print(f'Request: {self.request!r}')
