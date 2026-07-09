"""
Celery task: monthly report email to managers/HR.

Runs on a schedule defined in your celerybeat config. Soft-coded so the
recipient list, subject template, and report month are env-driven.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage

from . import config as ts_config
from . import exports as ts_exports

logger = logging.getLogger(__name__)


def _recipients() -> list[str]:
    raw = os.environ.get('TIMESHEET_REPORT_RECIPIENTS', '')
    out = [e.strip() for e in raw.split(',') if e.strip()]
    if out:
        return out
    fallback = getattr(settings, 'HR_ADMIN_EMAIL', None) or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    return [fallback] if fallback else []


@shared_task(name='timesheet.send_monthly_report')
def send_monthly_report(year: int | None = None, month: int | None = None) -> dict:
    """Generate the monthly Excel + PDF and email both to TIMESHEET_REPORT_RECIPIENTS."""
    if not ts_config.is_configured():
        return {'status': 'skipped', 'reason': 'not_configured'}

    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)

    to = _recipients()
    if not to:
        return {'status': 'skipped', 'reason': 'no_recipients'}

    xlsx_resp = ts_exports.export_monthly_excel(y, m)
    pdf_resp = ts_exports.export_monthly_pdf(y, m)

    subject = f"[RADAI] Time Sheet — {y}-{m:02d} monthly report"
    body = (
        f"Attached: monthly attendance summary for {y}-{m:02d}.\n"
        f"Generated automatically from {ts_config.SQLSERVER['host']} / "
        f"{ts_config.SQLSERVER['database']}.\n"
    )
    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=to,
    )
    msg.attach(f'timesheet_{y}_{m:02d}.xlsx',
               xlsx_resp.content,
               'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    msg.attach(f'timesheet_{y}_{m:02d}.pdf', pdf_resp.content, 'application/pdf')
    try:
        msg.send(fail_silently=False)
        return {'status': 'sent', 'recipients': to, 'year': y, 'month': m}
    except Exception as exc:
        logger.exception('[timesheet] monthly report email failed')
        return {'status': 'failed', 'error': str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Background Cache Refresh Tasks — Pre-warm timesheet data cache
# ─────────────────────────────────────────────────────────────────────────────
@shared_task(name='timesheet.refresh_cache')
def refresh_timesheet_cache(category: str, params: dict) -> dict:
    """Generic cache refresh task for any timesheet query.
    
    Args:
        category: 'live', 'daily', or 'monthly'
        params: Query parameters (date, year, month, etc.)
    """
    try:
        from . import config as ts_config
        from . import cache_service
        
        if not ts_config.is_configured():
            return {'status': 'skipped', 'reason': 'not_configured'}
            
        if not cache_service.CACHE_ENABLED or not cache_service.BACKGROUND_REFRESH:
            return {'status': 'skipped', 'reason': 'caching_disabled'}
        
        # Import appropriate service based on data source
        if ts_config.DATA_SOURCE == 'mirror':
            from . import mirror_services as svc
        else:
            from . import services as svc
        
        # Execute query and cache result
        if category == 'live':
            data = svc.live_status()
        elif category == 'daily':
            data = svc.daily_report(params.get('date'))
        elif category == 'monthly':
            data = svc.monthly_report(params.get('year'), params.get('month'))
        else:
            return {'status': 'failed', 'error': f'Unknown category: {category}'}
        
        # Cache the result
        ttl_map = {
            'live': cache_service.CACHE_LIVE_TTL,
            'daily': cache_service.CACHE_DAILY_TTL,
            'monthly': cache_service.CACHE_MONTHLY_TTL,
        }
        key = cache_service._make_key(category, **params)
        cache_service._cache_set_with_meta(key, data, ttl_map[category])
        
        logger.info(
            '[Timesheet Cache] Background refresh completed: %s %s',
            category, params
        )
        return {'status': 'success', 'category': category, 'params': params}
        
    except Exception as exc:
        logger.exception('[Timesheet Cache] Background refresh failed')
        return {'status': 'failed', 'error': str(exc)}


@shared_task(name='timesheet.refresh_live')
def refresh_timesheet_live() -> dict:
    """Refresh live attendance cache (highest priority, shortest TTL)."""
    return refresh_timesheet_cache('live', {})


@shared_task(name='timesheet.refresh_daily')
def refresh_timesheet_daily(date: str | None = None) -> dict:
    """Refresh daily attendance cache for today (or specified date)."""
    if not date:
        date = dt.date.today().isoformat()
    return refresh_timesheet_cache('daily', {'date': date})


@shared_task(name='timesheet.refresh_monthly')
def refresh_timesheet_monthly(year: int | None = None, month: int | None = None) -> dict:
    """Refresh monthly attendance cache for current month (or specified period)."""
    today = dt.date.today()
    year = year or today.year
    month = month or today.month
    return refresh_timesheet_cache('monthly', {'year': year, 'month': month})


@shared_task(name='timesheet.warm_all_caches')
def warm_all_timesheet_caches() -> dict:
    """Pre-populate all timesheet caches. Run after deploy or config change."""
    results = []
    try:
        # Live
        results.append(refresh_timesheet_live())
        # Today
        results.append(refresh_timesheet_daily())
        # Current month
        results.append(refresh_timesheet_monthly())
        # Previous month (often accessed for reports)
        today = dt.date.today()
        prev_month = today.month - 1 if today.month > 1 else 12
        prev_year = today.year if today.month > 1 else today.year - 1
        results.append(refresh_timesheet_monthly(prev_year, prev_month))
        
        logger.info('[Timesheet Cache] Warmed all caches')
        return {'status': 'success', 'tasks': len(results), 'results': results}
    except Exception as exc:
        logger.exception('[Timesheet Cache] Cache warming failed')
        return {'status': 'failed', 'error': str(exc), 'results': results}
