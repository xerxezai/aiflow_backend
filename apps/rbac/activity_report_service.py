"""
Activity Report Service — Aggregated user engagement analytics for admins
=========================================================================

Generate weekly, monthly, daily, and custom-window reports on user activity
across the RADAI platform. All report types, metrics, grouping strategies,
and thresholds are SOFT-CODED in module-level constants so SuperAdmin can
rebalance without touching business logic.

Reports available:
  - Summary Report: totals per time window
  - User Report: per-user activity breakdown
  - Feature Report: adoption by feature/module
  - Department Report: by user's department
  - Custom Report: arbitrary (start, end, group_by)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum, Q
from django.utils import timezone

from .ai_champion_models import ActivityEvent, AIUsageLog

User = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED report configuration — module-level constants, overridable via
# Django settings.
# ---------------------------------------------------------------------------

# Report metric definitions: (metric_key, label, description, field, aggregation_fn)
DEFAULT_REPORT_METRICS = [
    ('total_actions', 'Total Actions', 'All user activities', 'id', 'count'),
    ('total_ai_requests', 'AI Requests', 'ChatGPT/Claude/etc calls', 'ai', 'count'),
    ('total_ai_cost_usd', 'AI Cost (USD)', 'Aggregate spend', 'ai', 'sum'),
    ('distinct_features', 'Distinct Features', 'Unique features used', 'feature', 'distinct'),
    ('distinct_modules', 'Distinct Modules', 'Unique modules used', 'module', 'distinct'),
    ('success_rate_pct', 'Success Rate (%)', 'Pct of successful actions', 'success', 'rate'),
    ('session_minutes', 'Session Time (min)', 'Aggregate dwell time', 'duration', 'sum_minutes'),
]

# Report time windows (days): human-readable label + delta
DEFAULT_TIME_WINDOWS = {
    'today': {'label': 'Today', 'days': 1},
    'week': {'label': 'This Week', 'days': 7},
    'month': {'label': 'This Month', 'days': 30},
    'quarter': {'label': 'This Quarter', 'days': 90},
    'ytd': {'label': 'Year to Date', 'days': 365},
}

# Report grouping strategies
DEFAULT_GROUP_BY_OPTIONS = {
    'user': 'Per-user activity',
    'feature': 'Per-feature adoption',
    'module': 'Per-module usage',
    'department': 'By user department',
    'application': 'By application area',
    'date': 'Daily breakdown',
}

# Minimum activity threshold to include in reports (suppresses noise)
MIN_ACTIONS_FOR_REPORT = 1
MIN_USERS_FOR_COHORT = 1


def _get_setting(name: str, default):
    from django.conf import settings
    return getattr(settings, name, default)


# ---------------------------------------------------------------------------
# Report aggregation helpers
# ---------------------------------------------------------------------------
def _activity_query_window(start: datetime, end: datetime):
    """Base queryset for ActivityEvent in a time window."""
    return ActivityEvent.objects.filter(
        timestamp__gte=start, timestamp__lt=end, user__is_active=True
    )


def _ai_usage_query_window(start: datetime, end: datetime):
    """Base queryset for AIUsageLog in a time window."""
    return AIUsageLog.objects.filter(
        timestamp__gte=start, timestamp__lt=end, user__is_active=True
    )


def _compute_metrics(activity_qs, ai_qs, metrics: List[str] = None) -> Dict[str, any]:
    """Compute a metric dict from activity + AI usage querysets."""
    if metrics is None:
        metrics = [m[0] for m in _get_setting(
            'ACTIVITY_REPORT_METRICS',
            DEFAULT_REPORT_METRICS,
        )]

    result = {}
    for metric in metrics:
        if metric == 'total_actions':
            result['total_actions'] = activity_qs.count()
        elif metric == 'total_ai_requests':
            result['total_ai_requests'] = ai_qs.count()
        elif metric == 'total_ai_cost_usd':
            agg = ai_qs.aggregate(total=Sum('cost_usd'))
            result['total_ai_cost_usd'] = float(agg['total'] or 0)
        elif metric == 'distinct_features':
            result['distinct_features'] = activity_qs.values('feature').distinct().count()
        elif metric == 'distinct_modules':
            result['distinct_modules'] = activity_qs.values('module').distinct().count()
        elif metric == 'success_rate_pct':
            total = activity_qs.count()
            success = activity_qs.filter(success=True).count()
            result['success_rate_pct'] = (success / total * 100) if total > 0 else 100.0
        elif metric == 'session_minutes':
            agg = activity_qs.aggregate(total=Sum('duration_ms'))
            result['session_minutes'] = int((agg['total'] or 0) / 60000)

    return result


# ---------------------------------------------------------------------------
# Public Report API
# ---------------------------------------------------------------------------
def generate_summary_report(
    window: str = 'month',
    custom_start: Optional[datetime] = None,
    custom_end: Optional[datetime] = None,
) -> Dict[str, any]:
    """
    Aggregate report for the entire user base in a time window.

    Returns:
      {
        'period': {'window': 'month', 'start': '...', 'end': '...'},
        'metrics': {'total_actions': 1000, ...},
        'cohort': {'active_users': 150, 'avg_actions_per_user': 6.7, ...}
      }
    """
    windows = _get_setting('ACTIVITY_REPORT_TIME_WINDOWS', DEFAULT_TIME_WINDOWS)
    if window not in windows and not (custom_start and custom_end):
        return {'error': f'Unknown time window: {window}'}

    if custom_start and custom_end:
        start, end = custom_start, custom_end
    else:
        days = windows[window]['days']
        end = timezone.now()
        start = end - timedelta(days=days)

    activity_qs = _activity_query_window(start, end)
    ai_qs = _ai_usage_query_window(start, end)
    metrics = _compute_metrics(activity_qs, ai_qs)

    # Cohort stats
    active_users = activity_qs.values('user_id').distinct().count()
    avg_actions = metrics['total_actions'] / active_users if active_users > 0 else 0

    return {
        'period': {
            'window': window,
            'start': start.isoformat(),
            'end': end.isoformat(),
        },
        'metrics': metrics,
        'cohort': {
            'active_users': active_users,
            'avg_actions_per_user': round(avg_actions, 2),
            'ai_adoption_rate_pct': (ai_qs.values('user_id').distinct().count() / active_users * 100)
            if active_users > 0 else 0,
        },
    }


def generate_user_report(
    window: str = 'month',
    custom_start: Optional[datetime] = None,
    custom_end: Optional[datetime] = None,
    limit: int = None,
) -> Dict[str, any]:
    """
    Per-user breakdown: sorted by total_actions (most active first).

    Returns:
      {
        'period': {...},
        'results': [
          {
            'user': {'id': 7, 'email': '...', 'name': '...'},
            'rank': 1,
            'metrics': {...},
          },
          ...
        ]
      }
    """
    windows = _get_setting('ACTIVITY_REPORT_TIME_WINDOWS', DEFAULT_TIME_WINDOWS)
    if window not in windows and not (custom_start and custom_end):
        return {'error': f'Unknown time window: {window}'}

    if custom_start and custom_end:
        start, end = custom_start, custom_end
    else:
        days = windows[window]['days']
        end = timezone.now()
        start = end - timedelta(days=days)

    # Per-user stats
    user_actions = (
        _activity_query_window(start, end)
        .values('user_id')
        .annotate(
            total_actions=Count('id'),
            distinct_features=Count('feature', distinct=True),
            distinct_modules=Count('module', distinct=True),
            total_duration_ms=Sum('duration_ms'),
            success_count=Count('id', filter=Q(success=True)),
        )
    )
    
    ai_per_user = (
        _ai_usage_query_window(start, end)
        .values('user_id')
        .annotate(
            total_ai_requests=Count('id'),
            total_cost=Sum('cost_usd'),
        )
    )
    ai_map = {r['user_id']: r for r in ai_per_user}

    results = []
    for idx, row in enumerate(
        sorted(user_actions, key=lambda r: r['total_actions'], reverse=True),
        start=1
    ):
        user_id = row['user_id']
        total_actions = row['total_actions'] or 0

        if total_actions < _get_setting('ACTIVITY_REPORT_MIN_ACTIONS', MIN_ACTIONS_FOR_REPORT):
            continue

        ai_data = ai_map.get(user_id, {})
        session_min = int((row['total_duration_ms'] or 0) / 60000)
        success_pct = (row['success_count'] or 0) / total_actions * 100 if total_actions else 100

        user = User.objects.filter(id=user_id).first()
        if not user:
            continue

        results.append({
            'rank': idx,
            'user': {
                'id': user.id,
                'email': user.email,
                'name': f"{user.first_name} {user.last_name}".strip() or user.email,
            },
            'metrics': {
                'total_actions': total_actions,
                'total_ai_requests': ai_data.get('total_ai_requests', 0),
                'total_ai_cost_usd': float(ai_data.get('total_cost') or 0),
                'distinct_features': row['distinct_features'] or 0,
                'distinct_modules': row['distinct_modules'] or 0,
                'session_minutes': session_min,
                'success_rate_pct': round(success_pct, 2),
            },
        })

    if limit:
        results = results[:limit]

    return {
        'period': {
            'window': window,
            'start': start.isoformat(),
            'end': end.isoformat(),
        },
        'count': len(results),
        'results': results,
    }


def generate_feature_report(
    window: str = 'month',
    custom_start: Optional[datetime] = None,
    custom_end: Optional[datetime] = None,
) -> Dict[str, any]:
    """
    Per-feature adoption: which features are most used.

    Returns:
      {
        'period': {...},
        'results': [
          {
            'feature': 'ocr-extract',
            'module': 'process',
            'metrics': {...}
          },
          ...
        ]
      }
    """
    windows = _get_setting('ACTIVITY_REPORT_TIME_WINDOWS', DEFAULT_TIME_WINDOWS)
    if window not in windows and not (custom_start and custom_end):
        return {'error': f'Unknown time window: {window}'}

    if custom_start and custom_end:
        start, end = custom_start, custom_end
    else:
        days = windows[window]['days']
        end = timezone.now()
        start = end - timedelta(days=days)

    feature_stats = (
        _activity_query_window(start, end)
        .values('feature', 'module')
        .annotate(
            total_actions=Count('id'),
            distinct_users=Count('user_id', distinct=True),
            total_duration_ms=Sum('duration_ms'),
            success_count=Count('id', filter=Q(success=True)),
        )
        .order_by('-total_actions')
    )

    results = []
    for row in feature_stats:
        total_actions = row['total_actions'] or 0
        if total_actions < MIN_ACTIONS_FOR_REPORT:
            continue

        session_min = int((row['total_duration_ms'] or 0) / 60000)
        success_pct = (row['success_count'] or 0) / total_actions * 100 if total_actions else 100

        results.append({
            'feature': row['feature'] or 'unknown',
            'module': row['module'] or 'unknown',
            'metrics': {
                'total_actions': total_actions,
                'distinct_users': row['distinct_users'] or 0,
                'session_minutes': session_min,
                'success_rate_pct': round(success_pct, 2),
            },
        })

    return {
        'period': {
            'window': window,
            'start': start.isoformat(),
            'end': end.isoformat(),
        },
        'count': len(results),
        'results': results,
    }


def generate_daily_breakdown(
    window: str = 'week',
    custom_start: Optional[datetime] = None,
    custom_end: Optional[datetime] = None,
) -> Dict[str, any]:
    """
    Daily activity heatmap: one row per day showing aggregate metrics.

    Returns:
      {
        'period': {...},
        'results': [
          {
            'date': '2026-05-02',
            'metrics': {...}
          },
          ...
        ]
      }
    """
    windows = _get_setting('ACTIVITY_REPORT_TIME_WINDOWS', DEFAULT_TIME_WINDOWS)
    if window not in windows and not (custom_start and custom_end):
        return {'error': f'Unknown time window: {window}'}

    if custom_start and custom_end:
        start, end = custom_start, custom_end
    else:
        days = windows[window]['days']
        end = timezone.now()
        start = end - timedelta(days=days)

    results = []
    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current < end:
        next_day = current + timedelta(days=1)
        day_qs = _activity_query_window(current, next_day)
        ai_qs = _ai_usage_query_window(current, next_day)
        
        if day_qs.count() > 0 or ai_qs.count() > 0:
            metrics = _compute_metrics(day_qs, ai_qs)
            results.append({
                'date': current.date().isoformat(),
                'metrics': metrics,
            })
        
        current = next_day

    return {
        'period': {
            'window': window,
            'start': start.isoformat(),
            'end': end.isoformat(),
        },
        'count': len(results),
        'results': results,
    }
