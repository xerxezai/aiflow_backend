"""
AI Champion — Celery tasks
==========================

Schedule reference (Celery beat — analogous to AWS EventBridge cron):

    celery -A config beat
    # In settings.CELERY_BEAT_SCHEDULE register:
    #   'select-monthly-ai-champion': {
    #       'task': 'apps.rbac.ai_champion_tasks.select_previous_month_champion',
    #       'schedule': crontab(day_of_month=1, hour=0, minute=5),
    #   }
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.utils import timezone

from .ai_champion_service import select_monthly_champion

logger = logging.getLogger(__name__)


@shared_task(name='apps.rbac.ai_champion_tasks.select_previous_month_champion')
def select_previous_month_champion(top_n: int = 3) -> dict:
    """Compute champion for the *previous* calendar month (run on day-1 of new month)."""
    now = timezone.now()
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_of_prev = first_of_this_month - timedelta(seconds=1)
    year, month = last_of_prev.year, last_of_prev.month
    created = select_monthly_champion(year, month, top_n=top_n)
    return {
        'period': {'year': year, 'month': month},
        'created_count': len(created),
        'champion_user_id': created[0].user_id if created else None,
    }


@shared_task(name='apps.rbac.ai_champion_tasks.recompute_month')
def recompute_month(year: int, month: int, top_n: int = 3) -> dict:
    """Manual recompute (admin-triggered)."""
    created = select_monthly_champion(year, month, top_n=top_n)
    return {
        'period': {'year': year, 'month': month},
        'created_count': len(created),
        'champion_user_id': created[0].user_id if created else None,
    }
