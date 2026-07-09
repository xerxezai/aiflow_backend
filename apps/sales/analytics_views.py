"""
Sales Intelligence Analytics Views
===================================
Generates sales-relevant intelligence from REAL platform data:
  - system_activity  (11k+ API request records)
  - users            (56 registered users)
  - rbac_user_profiles (department / job_title info)
  - user_session     (live session data)

No CRM tables are required — all insights are derived from
actual usage patterns, user registration data, and engagement metrics.

Soft-coded strategy:
  ENGAGEMENT_STAGES  — activity thresholds → pipeline stage assignment
  COMPANY_TIER_THRESHOLDS — user count → tier
  INSIGHT_RULES      — generates AI-style observations from metrics
"""
from datetime import timedelta, date
from collections import defaultdict
import hashlib
import json
import logging

from django.db.models import Count, Max, Avg, Min
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Maps request count → pipeline stage
ENGAGEMENT_STAGES = [
    (500, 'negotiation'),
    (100, 'proposal'),
    (20,  'qualified'),
    (1,   'lead'),
    (0,   'lead'),
]

# Maps user count per company → client tier
COMPANY_TIER_THRESHOLDS = [
    (10, 'enterprise'),
    (5,  'premium'),
    (2,  'standard'),
    (1,  'basic'),
]

# Maps email domain → industry label (soft-coded so teams can extend later)
DOMAIN_INDUSTRY_MAP = {
    'rejlers.ae':      'Engineering & Consulting',
    'radai.ae':        'AI Software Platform',
    'engineering.ae':  'Engineering Services',
    'consultant.ae':   'Consulting Services',
    'gmail.com':       'Individual User',
    'outlook.com':     'Individual User',
}

# Discipline key → compact label
DISCIPLINE_MAP = [
    ('pid',                  'Process (P&ID)'),
    ('pfd',                  'Digitization (PFD)'),
    ('process-datasheet',    'Process Datasheet'),
    ('electrical-datasheet', 'Electrical Datasheet'),
    ('crs',                  'CRS Documents'),
    ('designiq',             'DesignIQ'),
    ('finance',              'Finance'),
    ('procurement',          'Procurement'),
    ('qhse',                 'QHSE'),
    ('projects',             'Project Control'),
    ('sales',                'Sales'),
    ('rbac',                 'Admin / RBAC'),
    ('users',                'User Management'),
    ('notifications',        'Notifications'),
]

# Stage display info (mirrors frontend CONFIG.pipelineStages).
# Soft-coded per-stage AED value ranges + win probability ranges + close-date
# horizons. All numeric output is derived from these maps so a tweak here
# uniformly rebalances the synthesised pipeline.
#
# Ranges reflect realistic Oil & Gas / Engineering EPC services contracts
# in the UAE (AED). Values inside a range are picked deterministically per
# company via a stable hash so KPIs don't flicker between requests.
STAGE_META = {
    'lead':        {'label': 'Lead',        'order': 1, 'value_min':    60_000, 'value_max':   180_000, 'win_min':  5, 'win_max': 15, 'days_min':  90, 'days_max': 180},
    'qualified':   {'label': 'Qualified',   'order': 2, 'value_min':   180_000, 'value_max':   600_000, 'win_min': 20, 'win_max': 35, 'days_min':  60, 'days_max': 120},
    'proposal':    {'label': 'Proposal',    'order': 3, 'value_min':   600_000, 'value_max': 2_500_000, 'win_min': 45, 'win_max': 65, 'days_min':  30, 'days_max':  90},
    'negotiation': {'label': 'Negotiation', 'order': 4, 'value_min': 2_500_000, 'value_max': 8_000_000, 'win_min': 70, 'win_max': 85, 'days_min':  15, 'days_max':  45},
    'closed_won':  {'label': 'Won ✓',       'order': 5, 'value_min': 1_200_000, 'value_max':15_000_000, 'win_min':100, 'win_max':100, 'days_min':   0, 'days_max':   0},
    'closed_lost': {'label': 'Lost',        'order': 6, 'value_min':         0, 'value_max':         0, 'win_min':  0, 'win_max':  0, 'days_min':   0, 'days_max':   0},
}

# Tier multiplier applied on top of the per-stage range (enterprise prospects
# negotiate larger contracts).
TIER_VALUE_MULTIPLIER = {
    'enterprise': 1.40,
    'premium':    1.15,
    'standard':   1.00,
    'basic':      0.85,
}

# Discipline-coverage bonus: each engineering discipline a company actively
# uses signals broader scope, so add up to +30% based on coverage breadth.
DISCIPLINE_BREADTH_BONUS_PCT = 0.06   # +6% per discipline used (capped at +30%)
DISCIPLINE_BREADTH_BONUS_CAP = 0.30

# Currency (frontend reads this back from API to keep formatting consistent).
CURRENCY_CODE   = 'AED'
CURRENCY_LOCALE = 'en-AE'

# Closed_won / closed_lost detection thresholds (soft-coded).
CLOSED_WON_MIN_REQUESTS  = 1500   # very-high engagement
CLOSED_WON_MIN_USERS     = 5      # broad team adoption
CLOSED_LOST_INACTIVE_DAYS = 60    # stale lead with prior activity
CLOSED_LOST_MIN_PRIOR_REQUESTS = 50

# S3 cache for the heavy aggregation (re-used by /pipeline + /clients /
# /forecast endpoints). TTL keeps the dashboard snappy without showing
# stale numbers.
S3_CACHE_PREFIX     = 'sales-analytics'
S3_CACHE_TTL_SECONDS = 600        # 10 minutes

ANALYSIS_WINDOW_DAYS = 30   # default lookback for analytics


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_activity_model():
    from apps.activity.models import SystemActivity
    return SystemActivity


def _get_user_model():
    from django.contrib.auth import get_user_model
    return get_user_model()


def _classify_discipline(description):
    """Extract (key, label) from a SystemActivity description string."""
    if not description:
        return 'other', 'Other'
    # description format: 'GET /api/v1/{key}/...'
    parts = description.split(' ', 1)
    path = parts[1] if len(parts) > 1 else parts[0]
    path_lower = path.lower()
    for key, label in DISCIPLINE_MAP:
        if f'/{key}/' in path_lower or f'/{key}' == path_lower.rstrip('/').split('?')[0][-len(key)-1:]:
            return key, label
    return 'other', 'Other'


def _engagement_stage(request_count):
    """Map total request count to pipeline stage key."""
    for threshold, stage in ENGAGEMENT_STAGES:
        if request_count >= threshold:
            return stage
    return 'lead'


def _company_tier(user_count):
    for threshold, tier in COMPANY_TIER_THRESHOLDS:
        if user_count >= threshold:
            return tier
    return 'basic'


def _stable_unit(seed: str) -> float:
    """Return a stable float in [0, 1) derived from `seed`. Same seed always
    yields the same number, so deal values and close dates don't flicker
    between requests for a given company."""
    h = hashlib.sha256(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _realistic_deal_value(*, domain: str, stage: str, tier: str, discipline_count: int, requests: int) -> float:
    """Compute a realistic AED deal value from the soft-coded STAGE_META range,
    with a deterministic per-company variance, tier multiplier, and
    discipline-breadth bonus.
    """
    meta = STAGE_META.get(stage, STAGE_META['lead'])
    lo, hi = meta['value_min'], meta['value_max']
    if hi <= lo:
        return float(lo)

    # 1. Base value: deterministic point inside the stage range.
    base = lo + _stable_unit(f'{domain}|value|{stage}') * (hi - lo)

    # 2. Tier multiplier (enterprise prospects = larger contracts).
    base *= TIER_VALUE_MULTIPLIER.get(tier, 1.0)

    # 3. Discipline-breadth bonus (capped).
    bonus = min(discipline_count * DISCIPLINE_BREADTH_BONUS_PCT, DISCIPLINE_BREADTH_BONUS_CAP)
    base *= (1.0 + bonus)

    # 4. Engagement nudge: very-active accounts skew toward the upper part of
    #    the band (caps at +10% to avoid double-counting tier).
    nudge = min(requests / 5000.0, 0.10)
    base *= (1.0 + nudge)

    # Round to nearest AED 5,000 for clean dashboard numbers.
    return round(base / 5000.0) * 5000.0


def _realistic_win_probability(*, domain: str, stage: str) -> int:
    """Pick a stable win probability inside the stage's band."""
    meta = STAGE_META.get(stage, STAGE_META['lead'])
    lo, hi = meta['win_min'], meta['win_max']
    if hi <= lo:
        return int(lo)
    return int(lo + _stable_unit(f'{domain}|winprob|{stage}') * (hi - lo))


def _realistic_close_date(*, domain: str, stage: str):
    """Pick a stable expected close date inside the stage's day-horizon band."""
    meta = STAGE_META.get(stage, STAGE_META['lead'])
    lo, hi = meta['days_min'], meta['days_max']
    if hi <= lo:
        days = lo
    else:
        days = int(lo + _stable_unit(f'{domain}|close|{stage}') * (hi - lo))
    return (timezone.now() + timedelta(days=days)).date().isoformat()


def _resolve_pipeline_stage(*, requests: int, recent_requests: int, user_count: int, last_seen, now) -> str:
    """Pick the pipeline stage including closed_won / closed_lost detection.

    Soft-coded rules (override the simple ENGAGEMENT_STAGES ladder):
      - closed_won  : sustained high engagement + broad team adoption
      - closed_lost : significant prior activity but inactive for too long
      - else        : fall back to engagement-count ladder
    """
    if last_seen and (now - last_seen).days >= CLOSED_LOST_INACTIVE_DAYS and requests >= CLOSED_LOST_MIN_PRIOR_REQUESTS:
        return 'closed_lost'
    if requests >= CLOSED_WON_MIN_REQUESTS and user_count >= CLOSED_WON_MIN_USERS and recent_requests > 0:
        return 'closed_won'
    return _engagement_stage(requests)


def _cache_to_s3(kind: str, payload: dict) -> None:
    """Persist an analytics snapshot to S3 for caching + audit history.

    Reuses the existing smart_storage S3 client (region/SigV4 already
    configured). The API response is not blocked on S3 success — caller
    wraps in a try/except.
    """
    try:
        from apps.electrical_datasheet.smart_storage import smart_storage
    except Exception:
        return
    if not getattr(smart_storage, 'is_enabled', lambda: False)():
        return
    ts = timezone.now().strftime('%Y%m%dT%H%M%S')
    key = f'{S3_CACHE_PREFIX}/{kind}/{ts}.json'
    try:
        smart_storage.client.put_object(
            Bucket=getattr(__import__('apps.electrical_datasheet.smart_storage', fromlist=['S3_BUCKET']), 'S3_BUCKET'),
            Key=key,
            Body=json.dumps(payload, default=str).encode('utf-8'),
            ContentType='application/json',
            Metadata={'kind': kind, 'cached-at': timezone.now().isoformat()},
        )
    except Exception as exc:
        logger.warning(f'[sales-analytics] put_object failed: {exc}')


def _domain_from_email(email):
    """Extract domain from email address."""
    return email.split('@')[-1] if '@' in email else 'unknown'


def _company_name_from_domain(domain):
    """Convert email domain to a human-readable company name."""
    name_map = {
        'rejlers.ae':     'Rejlers Engineering',
        'radai.ae':       'RAD AI Platform',
        'engineering.ae': 'Engineering Services AE',
        'consultant.ae':  'Consultant AE',
    }
    if domain in name_map:
        return name_map[domain]
    parts = domain.split('.')
    return ' '.join(w.capitalize() for w in parts[:-1]) or domain


# ─────────────────────────────────────────────────────────────────────────────
# VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class SalesPipelineAnalyticsView(APIView):
    """
    GET /api/v1/sales/analytics/pipeline/
    Returns pipeline summary synthesised from real platform usage data.
    Groups users by email domain as "client companies".
    Assigns pipeline stage based on engagement depth.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity = _get_activity_model()
        User = _get_user_model()

        cutoff = timezone.now() - timedelta(days=ANALYSIS_WINDOW_DAYS)
        week_ago = timezone.now() - timedelta(days=7)

        # ── 1. Get all users grouped by email domain ──────────────────────
        users = list(User.objects.values('id', 'email', 'first_name', 'last_name', 'is_active', 'date_joined', 'last_login'))

        # ── 2. Activity counts per email ──────────────────────────────────
        activity_qs = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff)
            .exclude(user_email='')
            .values('user_email')
            .annotate(
                total=Count('id'),
                last_seen=Max('timestamp'),
                first_seen=Min('timestamp'),
                avg_ms=Avg('duration_ms'),
            )
        )
        activity_map = {r['user_email']: r for r in activity_qs}

        # ── 3. Discipline coverage per email (last 30 days) ───────────────
        disc_by_email = defaultdict(set)
        for row in SystemActivity.objects.filter(timestamp__gte=cutoff).exclude(user_email='').values('user_email', 'description'):
            key, _ = _classify_discipline(row['description'])
            disc_by_email[row['user_email']].add(key)

        # ── 4. Recent activity (last 7 days) per email ────────────────────
        recent_map = {}
        for r in SystemActivity.objects.filter(timestamp__gte=week_ago).exclude(user_email='').values('user_email').annotate(cnt=Count('id')):
            recent_map[r['user_email']] = r['cnt']

        # ── 5. Group users by domain → companies ─────────────────────────
        companies = defaultdict(lambda: {
            'users': [], 'total_requests': 0, 'last_seen': None,
            'disciplines': set(), 'recent_requests': 0, 'first_seen': None,
        })

        for u in users:
            email = u['email']
            domain = _domain_from_email(email)
            act = activity_map.get(email, {})
            companies[domain]['users'].append(u)
            companies[domain]['total_requests'] += act.get('total', 0)
            companies[domain]['recent_requests'] += recent_map.get(email, 0)
            companies[domain]['disciplines'].update(disc_by_email.get(email, set()))
            ls = act.get('last_seen')
            if ls and (not companies[domain]['last_seen'] or ls > companies[domain]['last_seen']):
                companies[domain]['last_seen'] = ls
            fs = act.get('first_seen') or u.get('date_joined')
            if fs and (not companies[domain]['first_seen'] or fs < companies[domain]['first_seen']):
                companies[domain]['first_seen'] = fs

        # ── 6. Build deals (one per company) ──────────────────────────────
        deals = []
        stage_counts = defaultdict(lambda: {'deal_count': 0, 'total_value': 0})
        total_pipeline = 0
        won_value = 0

        for i, (domain, data) in enumerate(companies.items()):
            company_name = _company_name_from_domain(domain)
            requests = data['total_requests']
            tier = _company_tier(len(data['users']))

            # Stage with closed_won / closed_lost detection.
            stage = _resolve_pipeline_stage(
                requests=requests,
                recent_requests=data['recent_requests'],
                user_count=len(data['users']),
                last_seen=data['last_seen'],
                now=timezone.now(),
            )

            disc_list = sorted(data['disciplines'] - {'other'})

            # Realistic AED deal value (stable per-company hash + tier + breadth).
            est_value = _realistic_deal_value(
                domain=domain,
                stage=stage,
                tier=tier,
                discipline_count=len(disc_list),
                requests=requests,
            )

            # Realistic win probability + close date inside the stage band.
            win_prob = _realistic_win_probability(domain=domain, stage=stage)
            expected_close = _realistic_close_date(domain=domain, stage=stage)

            # Days since first engagement
            first_seen = data['first_seen']
            days_in_pipeline = 0
            if first_seen:
                fs_aware = first_seen if hasattr(first_seen, 'tzinfo') and first_seen.tzinfo else timezone.make_aware(first_seen) if not hasattr(first_seen, 'tzinfo') else first_seen
                try:
                    days_in_pipeline = (timezone.now() - fs_aware).days
                except Exception:
                    days_in_pipeline = 30

            # Priority based on recent activity
            if data['recent_requests'] > 100:
                priority = 'high'
            elif data['recent_requests'] > 20:
                priority = 'medium'
            else:
                priority = 'low'

            deal = {
                'id':                 i + 1,
                'title':              f'{company_name} — Platform Engagement',
                'client':             company_name,
                'client_name':        company_name,
                'stage':              stage,
                'value':              est_value,
                'deal_value':         est_value,
                'currency':           CURRENCY_CODE,
                'win_probability':    win_prob,
                'win_prob':           win_prob,
                'priority':           priority,
                'expected_close_date': expected_close,
                'domains_active':     list(disc_list)[:5],
                'user_count':         len(data['users']),
                'total_requests':     requests,
                'recent_requests':    data['recent_requests'],
                'days_in_pipeline':   days_in_pipeline,
                'tier':               tier,
            }

            deals.append(deal)

            stage_counts[stage]['deal_count'] += 1
            stage_counts[stage]['total_value'] += est_value

            if stage not in ('closed_lost',):
                total_pipeline += est_value
            if stage == 'closed_won':
                won_value += est_value

        # ── 7. Stage summary list (sorted by pipeline order) ──────────────
        by_stage = []
        for stage_key, meta in sorted(STAGE_META.items(), key=lambda x: x[1]['order']):
            sc = stage_counts.get(stage_key, {'deal_count': 0, 'total_value': 0})
            by_stage.append({
                'stage':       stage_key,
                'stage_label': meta['label'],
                'deal_count':  sc['deal_count'],
                'total_value': round(sc['total_value'], -3),
            })

        total_closed = stage_counts.get('closed_won', {}).get('deal_count', 0) + \
                       stage_counts.get('closed_lost', {}).get('deal_count', 0)
        win_rate = round(stage_counts.get('closed_won', {}).get('deal_count', 0) / max(total_closed, 1) * 100, 1)

        # Avg days in pipeline across all deals (non-zero)
        avg_days = round(sum(d['days_in_pipeline'] for d in deals) / max(len(deals), 1))

        payload = {
            'total_deals':          len(deals),
            'total_pipeline_value': round(total_pipeline, -3),
            'total_value':          round(total_pipeline, -3),
            'won_value':            round(won_value, -3),
            'won_deals':            stage_counts.get('closed_won', {}).get('deal_count', 0),
            'win_rate':             win_rate,
            'avg_deal_days':        avg_days,
            'avg_cycle_days':       avg_days,
            'currency':             CURRENCY_CODE,
            'currency_locale':      CURRENCY_LOCALE,
            'by_stage':             by_stage,
            'deals':                sorted(deals, key=lambda d: -d['total_requests']),
            'generated_at':         timezone.now().isoformat(),
        }
        # Best-effort S3 snapshot (cache + audit history). Failures are logged
        # but never break the API response.
        try:
            _cache_to_s3('pipeline', payload)
        except Exception as exc:
            logger.warning(f'[sales-analytics] S3 cache write failed: {exc}')

        return Response(payload)


class SalesClientsAnalyticsView(APIView):
    """
    GET /api/v1/sales/analytics/clients/
    Returns top clients + at-risk clients derived from usage patterns.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity = _get_activity_model()
        User = _get_user_model()

        cutoff = timezone.now() - timedelta(days=ANALYSIS_WINDOW_DAYS)
        week_ago = timezone.now() - timedelta(days=7)

        users = list(User.objects.values('id', 'email', 'first_name', 'last_name', 'is_active', 'date_joined'))

        activity_qs = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff)
            .exclude(user_email='')
            .values('user_email')
            .annotate(total=Count('id'), last_seen=Max('timestamp'))
        )
        activity_map = {r['user_email']: r for r in activity_qs}

        recent_map = {}
        for r in SystemActivity.objects.filter(timestamp__gte=week_ago).exclude(user_email='').values('user_email').annotate(cnt=Count('id')):
            recent_map[r['user_email']] = r['cnt']

        # Group by domain
        companies = defaultdict(lambda: {'users': [], 'total': 0, 'recent': 0, 'last_seen': None})
        for u in users:
            e = u['email']
            d = _domain_from_email(e)
            act = activity_map.get(e, {})
            companies[d]['users'].append(u)
            companies[d]['total'] += act.get('total', 0)
            companies[d]['recent'] += recent_map.get(e, 0)
            ls = act.get('last_seen')
            if ls and (not companies[d]['last_seen'] or ls > companies[d]['last_seen']):
                companies[d]['last_seen'] = ls

        clients = []
        for domain, data in companies.items():
            total_req = data['total']
            recent_req = data['recent']
            user_count = len(data['users'])
            name = _company_name_from_domain(domain)
            industry = DOMAIN_INDUSTRY_MAP.get(domain, 'Engineering')
            tier = _company_tier(user_count)

            # Health score: based on recent activity relative to total
            if total_req == 0:
                health = 0
            elif recent_req == 0:
                health = 20  # dormant
            else:
                health = min(int(recent_req / max(total_req, 1) * 100 * 3), 100)
                health = max(health, 30)

            # Churn probability: inverse of recent engagement
            if recent_req > 50:
                churn = 10
            elif recent_req > 10:
                churn = 30
            elif recent_req > 0:
                churn = 55
            else:
                churn = 80

            est_revenue = STAGE_META[_engagement_stage(total_req)]['value_multiplier'] * user_count

            clients.append({
                'id':               domain,
                'name':             name,
                'company_name':     name,
                'domain':           domain,
                'industry':         industry,
                'tier':             tier,
                'total_revenue':    round(est_revenue, -2),
                'annual_revenue':   round(est_revenue, -2),
                'health_score':     health,
                'churn_probability': churn,
                'churn_risk':       churn,
                'user_count':       user_count,
                'total_requests':   total_req,
                'recent_requests':  recent_req,
                'last_seen':        data['last_seen'].isoformat() if data['last_seen'] else None,
                'primary_contact':  next(
                    (f"{u['first_name']} {u['last_name']}".strip() for u in data['users'] if u['first_name']),
                    data['users'][0]['email'] if data['users'] else '—'
                ),
            })

        clients.sort(key=lambda c: -c['total_requests'])

        mode = request.query_params.get('mode', 'top')
        if mode == 'at_risk':
            at_risk = [c for c in clients if c['churn_probability'] >= 55]
            return Response({'success': True, 'count': len(at_risk), 'clients': at_risk})

        limit = int(request.query_params.get('limit', 20))
        top = clients[:limit]
        return Response({'success': True, 'count': len(top), 'clients': top})


class SalesAIInsightsView(APIView):
    """
    GET /api/v1/sales/analytics/insights/
    Generates AI-style insights from real usage + user data.
    All logic is soft-coded through INSIGHT_RULES patterns.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity = _get_activity_model()
        User = _get_user_model()

        now = timezone.now()
        cutoff_30 = now - timedelta(days=30)
        cutoff_7  = now - timedelta(days=7)
        cutoff_15m = now - timedelta(minutes=15)

        total_users = User.objects.filter(is_active=True).count()
        total_requests_7d = SystemActivity.objects.filter(timestamp__gte=cutoff_7).count()
        total_requests_30d = SystemActivity.objects.filter(timestamp__gte=cutoff_30).count()
        active_now = SystemActivity.objects.filter(timestamp__gte=cutoff_15m).values('user_email').exclude(user_email='').distinct().count()
        active_7d = SystemActivity.objects.filter(timestamp__gte=cutoff_7).values('user_email').exclude(user_email='').distinct().count()

        # Top discipline
        from django.db.models import Case, When, Value, CharField
        disc_cases = [
            When(description__icontains=f'/{key}/', then=Value(label))
            for key, label in DISCIPLINE_MAP
        ]
        disc_annotation = Case(*disc_cases, default=Value('Other'), output_field=CharField())

        top_discs = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff_7)
            .annotate(disc=disc_annotation)
            .values('disc')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')[:3]
        )
        top_disc_list = [r['disc'] for r in top_discs]
        top_disc = top_disc_list[0] if top_disc_list else 'API'

        # Top user
        top_user_qs = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff_7)
            .exclude(user_email='')
            .values('user_email', 'user_full_name')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')
            .first()
        )

        # Domains active
        domains_active = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff_7)
            .exclude(user_email='')
            .values('user_email')
            .distinct()
        )
        domains = set(_domain_from_email(r['user_email']) for r in domains_active)
        total_domains = len([d for d in domains if d != 'unknown'])

        # Dormant users (registered but no activity in 30 days)
        active_emails = set(
            r['user_email'] for r in
            SystemActivity.objects.filter(timestamp__gte=cutoff_30).exclude(user_email='').values('user_email').distinct()
        )
        all_emails = set(User.objects.values_list('email', flat=True))
        dormant_count = len(all_emails - active_emails)

        # Avg requests per active day (last 7 days)
        days_with_activity = SystemActivity.objects.filter(timestamp__gte=cutoff_7).annotate(d=TruncDate('timestamp')).values('d').distinct().count()
        avg_daily = round(total_requests_7d / max(days_with_activity, 1))

        # Success rate
        success_rate = round(SystemActivity.objects.filter(timestamp__gte=cutoff_7, success=True).count() / max(total_requests_7d, 1) * 100, 1)

        insights = []

        # ── Insight 1: Platform health ────────────────────────────────────
        insights.append({
            'type':        'recommendation' if success_rate > 95 else 'warning',
            'priority':    'low' if success_rate > 95 else 'high',
            'title':       f'Platform Success Rate: {success_rate}%',
            'description': f'{total_requests_7d:,} API requests in last 7 days with {success_rate}% success rate across {active_7d} active users.',
            'action':      'Continue monitoring. Consider performance reviews if rate drops below 95%.' if success_rate > 95 else 'Investigate and resolve recurring errors immediately.',
        })

        # ── Insight 2: Top discipline usage ───────────────────────────────
        if top_disc_list:
            disc_str = ', '.join(top_disc_list[:2])
            insights.append({
                'type':        'opportunity',
                'priority':    'medium',
                'title':       f'Most Active Feature: {top_disc}',
                'description': f'Top disciplines this week: {disc_str}. These represent the strongest engagement areas for upsell and expansion.',
                'action':      f'Focus demo and proposal content on {top_disc} — highest engagement signal.',
            })

        # ── Insight 3: Active user engagement ─────────────────────────────
        engagement_pct = round(active_7d / max(total_users, 1) * 100)
        insights.append({
            'type':        'opportunity' if engagement_pct > 50 else 'warning',
            'priority':    'medium' if engagement_pct > 50 else 'high',
            'title':       f'{engagement_pct}% Users Active This Week ({active_7d}/{total_users})',
            'description': f'{active_7d} out of {total_users} registered users were active in the last 7 days. Daily average: {avg_daily:,} requests.',
            'action':      f'Reach out to the {dormant_count} dormant users with personalised onboarding emails.' if dormant_count > 0 else 'Maintain engagement with regular feature updates.',
        })

        # ── Insight 4: Top power user ──────────────────────────────────────
        if top_user_qs:
            name = top_user_qs['user_full_name'] or top_user_qs['user_email'].split('@')[0]
            insights.append({
                'type':        'opportunity',
                'priority':    'low',
                'title':       f'Power User Identified: {name}',
                'description': f'{name} made {top_user_qs["cnt"]:,} requests this week — the most active user on the platform.',
                'action':      f'Engage {name} as a platform champion. Request case study or testimonial.',
            })

        # ── Insight 5: Multi-company presence ─────────────────────────────
        if total_domains > 1:
            insights.append({
                'type':        'opportunity',
                'priority':    'medium',
                'title':       f'{total_domains} Companies Using the Platform',
                'description': f'Users from {total_domains} different organisations are actively using the platform, indicating strong market pull.',
                'action':      'Identify highest-value domain and prioritise enterprise contract negotiation.',
            })

        # ── Insight 6: Dormant users ──────────────────────────────────────
        if dormant_count > 0:
            insights.append({
                'type':        'warning',
                'priority':    'high',
                'title':       f'{dormant_count} Dormant Users — Re-engagement Opportunity',
                'description': f'{dormant_count} registered users have had no activity in the last 30 days.',
                'action':      'Send targeted re-engagement campaign with a highlight of the latest features.',
            })

        # ── Insight 7: Activity trend ──────────────────────────────────────
        if total_requests_30d > 0:
            week_pct = round(total_requests_7d / max(total_requests_30d, 1) * 100 * 4)  # weekly proportion
            trend = 'accelerating' if week_pct > 130 else ('stable' if week_pct > 70 else 'declining')
            insights.append({
                'type':        'recommendation' if trend == 'stable' else ('opportunity' if trend == 'accelerating' else 'warning'),
                'priority':    'low' if trend == 'stable' else 'medium',
                'title':       f'Platform Activity Trend: {trend.capitalize()}',
                'description': f'Last 7 days had {total_requests_7d:,} requests vs {total_requests_30d:,} in last 30 days — trend is {trend}.',
                'action':      'Good momentum! Plan your next feature release to maintain growth.' if trend == 'accelerating' else 'Review onboarding flow to re-activate users.' if trend == 'declining' else 'Maintain current engagement strategy.',
            })

        return Response({
            'success':      True,
            'insights':     insights,
            'generated_at': now.isoformat(),
            'metrics': {
                'total_users':        total_users,
                'active_now':         active_now,
                'active_7d':          active_7d,
                'requests_7d':        total_requests_7d,
                'requests_30d':       total_requests_30d,
                'dormant_users':      dormant_count,
                'companies_active':   total_domains,
                'success_rate':       success_rate,
                'top_discipline':     top_disc,
            },
        })


class SalesActivitiesView(APIView):
    """
    GET /api/v1/sales/analytics/activities/
    Returns upcoming follow-up activities synthesised from dormant/at-risk users.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        User = _get_user_model()
        SystemActivity = _get_activity_model()

        days = int(request.query_params.get('days', 7))
        cutoff_30 = timezone.now() - timedelta(days=30)

        # Users with no activity in 30 days = follow-up candidates
        active_emails = set(
            r['user_email'] for r in
            SystemActivity.objects.filter(timestamp__gte=cutoff_30).exclude(user_email='').values('user_email').distinct()
        )

        dormant_users = (
            User.objects
            .filter(is_active=True)
            .exclude(email__in=active_emails)
            .values('email', 'first_name', 'last_name')[:10]
        )

        activities = []
        for i, u in enumerate(dormant_users):
            name = f"{u['first_name']} {u['last_name']}".strip() or u['email']
            domain = _domain_from_email(u['email'])
            company = _company_name_from_domain(domain)
            due_date = (timezone.now() + timedelta(days=(i % days) + 1)).date().isoformat()

            activities.append({
                'id':          i + 1,
                'type':        'email' if i % 3 != 0 else 'call',
                'title':       f'Re-engagement: Contact {name}',
                'description': f'Follow up with {name} from {company} — no platform activity in 30 days.',
                'client_name': company,
                'due_date':    due_date,
                'priority':    'medium',
            })

        # Also add a review activity for top users
        top_user = (
            SystemActivity.objects
            .filter(timestamp__gte=timezone.now() - timedelta(days=7))
            .exclude(user_email='')
            .values('user_email', 'user_full_name')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')
            .first()
        )
        if top_user:
            name = top_user['user_full_name'] or top_user['user_email']
            company = _company_name_from_domain(_domain_from_email(top_user['user_email']))
            activities.insert(0, {
                'id':          0,
                'type':        'meeting',
                'title':       f'Champion Review: {name}',
                'description': f'Quarterly review meeting with {name} ({company}) — top power user.',
                'client_name': company,
                'due_date':    (timezone.now() + timedelta(days=3)).date().isoformat(),
                'priority':    'high',
            })

        return Response(activities)
