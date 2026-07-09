"""
Celery task: nightly AI insight generation per active user.
Scheduled at 02:00 UAE time (Asia/Dubai) via Celery beat.
Uses GPT-4o to generate 3 personalized insights per user.
Failures are caught per-user so one bad user doesn't block others.
"""
import logging
import json
from datetime import timedelta

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)

User = get_user_model()

# ─── Soft-coded config ────────────────────────────────────────────────────────
INSIGHTS_PER_USER = 3
USAGE_LOOKBACK_DAYS = 30
ACTIVITY_LOOKBACK_DAYS = 7
INSIGHT_TTL_HOURS = 20
# Maps discipline_key to friendly label for the GPT prompt
DISCIPLINE_LABELS = {
    'pid_analysis':            'P&ID Quality Control',
    'pfd_to_pid':              'PFD Digitisation',
    'electrical_datasheet':    'Electrical Datasheets',
    'instrument_datasheet':    'Instrument Datasheets',
    'mechanical_datasheet':    'Mechanical Datasheets',
    'crs_documents':           'Change Request System',
    'project_control':         'Project Control',
    'qhse':                    'QHSE',
    'finance':                 'Finance',
    'procurement':             'Procurement',
    'sales':                   'Sales',
    'human_resource':          'Human Resources',
    'designiq':                'DesignIQ',
    'process_datasheet':       'Process Datasheets',
    'pid_verification':        'P&ID Verification',
    'timesheet':               'Timesheet & Attendance',
}
# ─────────────────────────────────────────────────────────────────────────────


def _build_gpt_prompt(user, role_code, department, job_title, used_disciplines, idle_disciplines, total_calls):
    """Build the GPT-4o prompt for personalised insight generation."""
    used_str = ', '.join(used_disciplines) if used_disciplines else 'none yet'
    idle_str = ', '.join(idle_disciplines) if idle_disciplines else 'none'

    return f"""You are an AI productivity coach for an engineering platform called RAD AI.
Generate exactly {INSIGHTS_PER_USER} personalised insights for this user. Each insight must be concise, practical, and relevant to their engineering work.

User profile:
- Role: {role_code}
- Department: {department or 'not specified'}
- Job Title: {job_title or 'not specified'}
- Total AI calls in last {USAGE_LOOKBACK_DAYS} days: {total_calls}
- Active features used: {used_str}
- Available features not yet used: {idle_str}

Return a JSON array of exactly {INSIGHTS_PER_USER} objects. Each object must have:
- "title": short title (max 60 chars)
- "body": actionable insight (max 200 chars)
- "insight_type": one of ["tip", "achievement", "alert", "suggestion"]
- "icon_key": one of ["lightbulb", "trophy", "bell", "sparkles", "chart", "rocket", "star", "check"]

Rules:
- If total_calls > 50 → include at least one "achievement"
- If idle_disciplines is not empty → include at least one "suggestion" about an unused feature
- Keep tone professional and specific to Oil & Gas engineering
- Do NOT mention competitor products
- Return ONLY the JSON array, no other text"""


def _parse_gpt_insights(content):
    """Parse GPT response into list of insight dicts. Returns [] on error."""
    try:
        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text
            text = text.rsplit('```', 1)[0]
        data = json.loads(text)
        if isinstance(data, list):
            return data[:INSIGHTS_PER_USER]
    except Exception as e:
        logger.warning('Failed to parse GPT insights: %s | raw: %s', e, content[:200])
    return []


@shared_task(bind=True, ignore_result=True, max_retries=0)
def generate_user_insights_task(self):
    """
    Nightly task: generate AI insights for all active users.
    Called by Celery beat at 02:00 Asia/Dubai.
    Also callable manually for testing.
    """
    from django.conf import settings
    import os

    logger.info('[DashboardInsights] Starting nightly insight generation')

    api_key = getattr(settings, 'OPENAI_API_KEY', None) or os.environ.get('OPENAI_API_KEY')
    model = getattr(settings, 'OPENAI_MODEL', 'gpt-4o')

    if not api_key:
        logger.error('[DashboardInsights] OPENAI_API_KEY not set — skipping')
        return

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    except ImportError:
        logger.error('[DashboardInsights] openai package not installed — skipping')
        return

    # Get all active non-staff users
    active_users = User.objects.filter(is_active=True).exclude(is_superuser=True)

    success_count = 0
    skip_count = 0
    error_count = 0

    for user in active_users:
        try:
            _generate_for_user(user, client, model)
            success_count += 1
        except Exception as e:
            logger.warning('[DashboardInsights] Failed for user %s: %s', user.email, e)
            error_count += 1

    logger.info(
        '[DashboardInsights] Done — success=%d skip=%d error=%d',
        success_count, skip_count, error_count,
    )


def _generate_for_user(user, openai_client, model):
    """Generate and save insights for a single user."""
    from .models import UserDashboardInsight, INSIGHT_TTL_HOURS
    from apps.usage_tracking.models import UsageLog
    from django.db.models import Count

    now = timezone.now()

    # Skip if user already has fresh insights
    fresh_count = UserDashboardInsight.objects.filter(
        user=user, is_active=True, expires_at__gt=now
    ).count()
    if fresh_count >= INSIGHTS_PER_USER:
        return  # Already fresh

    # Gather usage data
    cutoff = now - timedelta(days=USAGE_LOOKBACK_DAYS)
    usage_qs = (
        UsageLog.objects
        .filter(user_email=user.email, timestamp__gte=cutoff)
        .values('discipline_key')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    total_calls = UsageLog.objects.filter(user_email=user.email, timestamp__gte=cutoff).count()
    used_keys = {row['discipline_key'] for row in usage_qs if row['discipline_key']}
    used_disciplines = [DISCIPLINE_LABELS.get(k, k) for k in used_keys]

    # Get user's accessible modules
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.prefetch_related('roles__modules').get(user=user)
        all_module_codes = set()
        role_code = 'viewer'
        roles = profile.roles.filter(is_active=True).order_by('level')
        if roles.exists():
            role_code = roles.first().code
        department = profile.department or ''
        job_title = profile.job_title or ''
        for role in roles:
            for mod in role.modules.all():
                all_module_codes.add(mod.code)
    except Exception:
        role_code = 'viewer'
        department = ''
        job_title = ''
        all_module_codes = set()

    idle_keys = all_module_codes - used_keys
    idle_disciplines = [DISCIPLINE_LABELS.get(k, k) for k in idle_keys]

    # Build and call GPT
    prompt = _build_gpt_prompt(
        user, role_code, department, job_title,
        used_disciplines, idle_disciplines, total_calls,
    )

    response = openai_client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
        temperature=0.7,
        max_tokens=600,
    )
    raw_content = response.choices[0].message.content
    parsed = _parse_gpt_insights(raw_content)

    if not parsed:
        logger.warning('[DashboardInsights] No parseable insights for %s', user.email)
        return

    # Deactivate old insights
    UserDashboardInsight.objects.filter(user=user, is_active=True).update(is_active=False)

    # Create new insights
    expires_at = now + timedelta(hours=INSIGHT_TTL_HOURS)
    new_insights = []
    for item in parsed:
        new_insights.append(UserDashboardInsight(
            user=user,
            title=str(item.get('title', 'Insight'))[:120],
            body=str(item.get('body', ''))[:400],
            insight_type=item.get('insight_type', 'tip'),
            icon_key=item.get('icon_key', 'lightbulb'),
            expires_at=expires_at,
            is_active=True,
        ))

    UserDashboardInsight.objects.bulk_create(new_insights)
    logger.info('[DashboardInsights] Created %d insights for %s', len(new_insights), user.email)
