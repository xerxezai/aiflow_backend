"""
Celery beat schedule for apps.dashboard.
Imported by config/celery.py at startup.
"""
from celery.schedules import crontab

# Run nightly at 02:00 UAE time (Asia/Dubai = UTC+4)
# Celery beat uses UTC by default; 02:00 Dubai = 22:00 UTC previous day
BEAT_SCHEDULE = {
    'generate-user-dashboard-insights': {
        'task': 'apps.dashboard.tasks.generate_user_insights_task',
        'schedule': crontab(hour=22, minute=0),  # 22:00 UTC = 02:00 Dubai
    },
}
