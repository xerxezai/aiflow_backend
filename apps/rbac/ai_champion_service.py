"""
AI Champion — Scoring Engine + Champion Selection Service
=========================================================

All weights / thresholds / badge tiers are SOFT-CODED in module-level
constants so SuperAdmin can rebalance the formula without touching business
logic. The scoring engine is pure-Python and side-effect-free; the selector
function is the only piece that writes to the database.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum, Avg, F, Q
from django.utils import timezone

from .ai_champion_models import (
    AIUsageLog,
    ActivityEvent,
    MonthlyChampion,
    AIPricingConfig,
)

User = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED scoring weights — must sum to 1.0
# Tunable via Django settings override `AI_CHAMPION_SCORING_WEIGHTS`.
# ---------------------------------------------------------------------------
SCORING_WEIGHTS = {
    'usage_frequency':    0.20,  # how often they act
    'feature_diversity':  0.15,  # breadth of the platform they touch
    'time_spent':         0.10,  # session minutes
    'ai_utilization':     0.25,  # AI requests count + tokens
    'cost_efficiency':    0.15,  # value-per-dollar (actions per $)
    'success_rate':       0.15,  # quality of work
}
assert abs(sum(SCORING_WEIGHTS.values()) - 1.0) < 1e-9, "SCORING_WEIGHTS must sum to 1.0"


# Soft-coded badge tiers (champion_score 0–100)
BADGE_TIERS = [
    {'id': 'diamond',  'min': 90, 'label': 'Diamond Champion'},
    {'id': 'platinum', 'min': 75, 'label': 'Platinum'},
    {'id': 'gold',     'min': 60, 'label': 'Gold'},
    {'id': 'silver',   'min': 40, 'label': 'Silver'},
    {'id': 'bronze',   'min': 20, 'label': 'Bronze'},
    {'id': 'rookie',   'min':  0, 'label': 'Rising Talent'},
]


def tier_for(score: float) -> dict:
    for tier in BADGE_TIERS:
        if score >= tier['min']:
            return tier
    return BADGE_TIERS[-1]


# ---------------------------------------------------------------------------
# Raw stats container — per-user aggregate over a date window
# ---------------------------------------------------------------------------
@dataclass
class UserStats:
    user_id: int
    total_actions: int = 0
    total_ai_requests: int = 0
    total_tokens: int = 0
    total_ai_cost_usd: Decimal = field(default_factory=lambda: Decimal('0'))
    distinct_features_used: int = 0
    total_session_minutes: int = 0
    success_rate: float = 100.0


def _gather_user_stats(start: datetime, end: datetime) -> Dict[int, UserStats]:
    """Aggregate per-user stats from ActivityEvent + AIUsageLog tables."""
    stats: Dict[int, UserStats] = {}

    # Activity-derived metrics
    activity_qs = (
        ActivityEvent.objects
        .filter(timestamp__gte=start, timestamp__lt=end)
        .values('user_id')
        .annotate(
            total_actions=Count('id'),
            distinct_features_used=Count('feature', distinct=True),
            total_duration_ms=Sum('duration_ms'),
            success_count=Count('id', filter=Q(success=True)),
        )
    )
    for row in activity_qs:
        uid = row['user_id']
        total = row['total_actions'] or 0
        s = stats.setdefault(uid, UserStats(user_id=uid))
        s.total_actions = total
        s.distinct_features_used = row['distinct_features_used'] or 0
        s.total_session_minutes = int((row['total_duration_ms'] or 0) / 60000)
        s.success_rate = (row['success_count'] / total * 100.0) if total else 100.0

    # AI usage metrics
    ai_qs = (
        AIUsageLog.objects
        .filter(timestamp__gte=start, timestamp__lt=end)
        .values('user_id')
        .annotate(
            total_ai_requests=Count('id'),
            total_tokens=Sum('total_tokens'),
            total_cost=Sum('cost_usd'),
        )
    )
    for row in ai_qs:
        uid = row['user_id']
        s = stats.setdefault(uid, UserStats(user_id=uid))
        s.total_ai_requests = row['total_ai_requests'] or 0
        s.total_tokens = row['total_tokens'] or 0
        s.total_ai_cost_usd = row['total_cost'] or Decimal('0')

    return stats


# ---------------------------------------------------------------------------
# Min-max normaliser (cohort-relative). Returns 0..100 for each user.
# ---------------------------------------------------------------------------
def _normalise(values: Dict[int, float], invert: bool = False) -> Dict[int, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi <= lo:
        return {uid: 50.0 for uid in values}  # neutral when no spread
    out = {}
    for uid, v in values.items():
        norm = (v - lo) / (hi - lo)
        if invert:
            norm = 1.0 - norm
        out[uid] = max(0.0, min(100.0, norm * 100.0))
    return out


# ---------------------------------------------------------------------------
# Public API: compute_scores
# ---------------------------------------------------------------------------
def compute_scores(start: datetime, end: datetime) -> List[dict]:
    """
    Returns ranked list of users for the [start, end) window.

    [
      {
        "user_id": 7,
        "champion_score": 87.4,
        "tier": "platinum",
        "breakdown": {"usage_frequency": 92.1, ...},
        "stats": {...},
      },
      ...
    ]
    """
    stats = _gather_user_stats(start, end)
    if not stats:
        return []

    # Cost efficiency = actions per $1 of AI spend (inverted hi-cost-low-output bad)
    cost_eff_raw: Dict[int, float] = {}
    for uid, s in stats.items():
        cost = float(s.total_ai_cost_usd) if s.total_ai_cost_usd else 0.0
        if cost > 0:
            cost_eff_raw[uid] = s.total_actions / cost
        else:
            # No spend = max efficiency (free tier user); treat as cohort top later
            cost_eff_raw[uid] = float(s.total_actions)

    norm = {
        'usage_frequency':   _normalise({u: s.total_actions for u, s in stats.items()}),
        'feature_diversity': _normalise({u: s.distinct_features_used for u, s in stats.items()}),
        'time_spent':        _normalise({u: s.total_session_minutes for u, s in stats.items()}),
        'ai_utilization':    _normalise({u: s.total_ai_requests for u, s in stats.items()}),
        'cost_efficiency':   _normalise(cost_eff_raw),
        'success_rate':      {u: s.success_rate for u, s in stats.items()},  # already 0-100
    }

    results = []
    for uid, s in stats.items():
        breakdown = {k: round(norm[k].get(uid, 0.0), 2) for k in SCORING_WEIGHTS}
        score = sum(breakdown[k] * w for k, w in SCORING_WEIGHTS.items())
        score = round(max(0.0, min(100.0, score)), 2)
        tier = tier_for(score)
        results.append({
            'user_id': uid,
            'champion_score': score,
            'tier': tier['id'],
            'tier_label': tier['label'],
            'breakdown': breakdown,
            'stats': {
                'total_actions': s.total_actions,
                'total_ai_requests': s.total_ai_requests,
                'total_tokens': s.total_tokens,
                'total_ai_cost_usd': float(s.total_ai_cost_usd),
                'distinct_features_used': s.distinct_features_used,
                'total_session_minutes': s.total_session_minutes,
                'success_rate': round(s.success_rate, 2),
            },
        })

    # Sort: champion_score DESC, then tie-breakers (more AI requests, then more features)
    results.sort(
        key=lambda r: (
            -r['champion_score'],
            -r['stats']['total_ai_requests'],
            -r['stats']['distinct_features_used'],
        )
    )
    return results


# ---------------------------------------------------------------------------
# Champion selection — the "AI Champion of the Month"
# ---------------------------------------------------------------------------
def select_monthly_champion(year: int, month: int, top_n: int = 3) -> List[MonthlyChampion]:
    """
    Computes scores for the calendar month and persists top-N as MonthlyChampion.
    Idempotent: replaces existing rows for that period.
    """
    start = timezone.make_aware(datetime(year, month, 1)) if timezone.is_naive(
        datetime(year, month, 1)
    ) else datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = start.replace(year=year + 1, month=1)
    else:
        end = start.replace(month=month + 1)

    ranked = compute_scores(start, end)
    if not ranked:
        logger.info("AI Champion: no activity for %s-%02d", year, month)
        return []

    # Wipe existing for idempotency
    MonthlyChampion.objects.filter(period_year=year, period_month=month).delete()

    created: List[MonthlyChampion] = []
    for rank_idx, entry in enumerate(ranked[:top_n], start=1):
        bd = entry['breakdown']
        st = entry['stats']
        citation = (
            f"Top performer with {st['total_ai_requests']} AI requests across "
            f"{st['distinct_features_used']} features. Champion score "
            f"{entry['champion_score']:.1f}/100."
        )
        mc = MonthlyChampion.objects.create(
            period_year=year,
            period_month=month,
            rank=rank_idx,
            user_id=entry['user_id'],
            champion_score=entry['champion_score'],
            usage_frequency_score=bd['usage_frequency'],
            feature_diversity_score=bd['feature_diversity'],
            time_spent_score=bd['time_spent'],
            ai_utilization_score=bd['ai_utilization'],
            cost_efficiency_score=bd['cost_efficiency'],
            success_rate_score=bd['success_rate'],
            total_actions=st['total_actions'],
            total_ai_requests=st['total_ai_requests'],
            total_ai_cost_usd=Decimal(str(st['total_ai_cost_usd'])),
            distinct_features_used=st['distinct_features_used'],
            total_session_minutes=st['total_session_minutes'],
            success_rate=st['success_rate'],
            badge_tier=entry['tier'],
            citation=citation,
        )
        created.append(mc)

    logger.info(
        "AI Champion: stored top-%d for %s-%02d (champion=user_id=%s, score=%.1f)",
        len(created), year, month, created[0].user_id, created[0].champion_score
    )
    return created


# ---------------------------------------------------------------------------
# Cost analytics aggregation
# ---------------------------------------------------------------------------
def cost_breakdown(start: datetime, end: datetime) -> dict:
    """High-level cost breakdown for the dashboard."""
    base_qs = AIUsageLog.objects.filter(timestamp__gte=start, timestamp__lt=end)

    by_provider = list(
        base_qs.values('provider')
        .annotate(requests=Count('id'), tokens=Sum('total_tokens'), cost=Sum('cost_usd'))
        .order_by('-cost')
    )
    by_application = list(
        base_qs.exclude(application='')
        .values('application')
        .annotate(requests=Count('id'), tokens=Sum('total_tokens'), cost=Sum('cost_usd'))
        .order_by('-cost')[:10]
    )
    by_model = list(
        base_qs.values('provider', 'model_name')
        .annotate(requests=Count('id'), tokens=Sum('total_tokens'), cost=Sum('cost_usd'))
        .order_by('-cost')[:10]
    )
    totals = base_qs.aggregate(
        requests=Count('id'),
        tokens=Sum('total_tokens'),
        cost=Sum('cost_usd'),
        avg_latency_ms=Avg('latency_ms'),
    )
    return {
        'window': {'start': start.isoformat(), 'end': end.isoformat()},
        'totals': {
            'requests': totals['requests'] or 0,
            'tokens': totals['tokens'] or 0,
            'cost_usd': float(totals['cost'] or 0),
            'avg_latency_ms': round(float(totals['avg_latency_ms'] or 0), 2),
        },
        'by_provider': [
            {**r, 'cost': float(r['cost'] or 0)} for r in by_provider
        ],
        'by_application': [
            {**r, 'cost': float(r['cost'] or 0)} for r in by_application
        ],
        'by_model': [
            {**r, 'cost': float(r['cost'] or 0)} for r in by_model
        ],
    }


def resolve_cost_for_request(provider: str, model_name: str,
                             tokens_input: int, tokens_output: int) -> Decimal:
    """Look up active pricing config and compute cost. Falls back to 0 if no config."""
    config = (
        AIPricingConfig.objects
        .filter(provider=provider, model_name=model_name, is_active=True,
                effective_from__lte=timezone.now())
        .order_by('-effective_from')
        .first()
    )
    if not config:
        return Decimal('0')
    return config.compute_cost(tokens_input, tokens_output)
