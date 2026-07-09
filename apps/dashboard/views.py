"""
Personal Dashboard API Views
Returns role-scoped data bundles for each authenticated user.
Super Admin/Staff → handled by frontend (global dashboard).
All other roles → personalized data scoped to their context.
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

User = get_user_model()

# ─── Soft-coded configuration ────────────────────────────────────────────────
ACTIVITY_FEED_LIMIT = 10          # Recent activities to return
NOTIFICATION_PREVIEW_LIMIT = 5    # Recent notifications to preview
MODULES_LAST_USED_DAYS = 30       # Days to look back for last-used module stats
USAGE_CHART_DAYS = 30             # Days for usage sparkline
TEAM_ACTIVE_HOURS = 8             # Hours window for "active today" team count
PENDING_ACTIONS_LIMIT = 10        # Max pending actions to return
APPROVAL_CATEGORY_NAME = 'APPROVAL'   # Notification category name for approval requests

# ─── Persona detection (drives frontend widget selection) ────────────────────
# Departments (case-insensitive substring match) → persona code.
# Add new personas here and mirror them in frontend/src/config/personalDashboardPersona.config.js
PERSONA_DEPARTMENT_MAP = [
    ('project management',  'project_control'),
    ('project controls',    'project_control'),
    ('planning',            'project_control'),
    ('cost control',        'project_control'),
    ('engineering',         'engineer'),
    ('electrical',          'engineer'),
    ('instrument',          'engineer'),
    ('mechanical',          'engineer'),
    ('piping',              'engineer'),
    ('process',             'engineer'),
    ('civil',               'engineer'),
    ('procurement',         'procurement'),
    ('finance',             'finance'),
    ('accounts',            'finance'),
    ('quality',             'qhse'),
    ('qhse',                'qhse'),
    ('hse',                 'qhse'),
    ('safety',              'qhse'),
    ('hr',                  'hr'),
    ('human resource',      'hr'),
]
DEFAULT_PERSONA = 'default'

# Job-title fallback if department doesn't match
PERSONA_JOB_TITLE_MAP = [
    ('project control',     'project_control'),
    ('planner',             'project_control'),
    ('cost engineer',       'project_control'),
    ('scheduler',           'project_control'),
]

# Project-control bundle limits (soft-coded)
PC_PROJECT_LIMIT = 12
PC_MILESTONE_LIMIT = 8
PC_MILESTONE_WINDOW_DAYS = 60
PC_TASK_LIMIT = 8
PC_CHANGE_LIMIT = 6
# ─────────────────────────────────────────────────────────────────────────────


def _detect_persona(department, job_title):
    """Match department / job_title to a persona code. Case-insensitive substring match."""
    dep = (department or '').lower().strip()
    for keyword, persona in PERSONA_DEPARTMENT_MAP:
        if keyword in dep:
            return persona
    jt = (job_title or '').lower().strip()
    for keyword, persona in PERSONA_JOB_TITLE_MAP:
        if keyword in jt:
            return persona
    return DEFAULT_PERSONA


def _get_primary_role_code(user):
    """Return the lowest-level (most privileged) role code for the user."""
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.select_related().prefetch_related('roles').get(user=user)
        roles = profile.roles.filter(is_active=True).order_by('level')
        if roles.exists():
            return roles.first().code
    except Exception:
        pass
    if user.is_superuser or user.is_staff:
        return 'super_admin'
    return 'viewer'


def _get_user_module_codes(user):
    """Return list of module codes accessible to this user."""
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.prefetch_related('roles__modules').get(user=user)
        codes = set()
        for role in profile.roles.filter(is_active=True):
            for mod in role.modules.all():
                codes.add(mod.code)
        return list(codes)
    except Exception:
        return []


def _get_activity_feed(user, role_code):
    """Return user-scoped activity feed."""
    try:
        from apps.activity.models import SystemActivity
        if role_code in ('super_admin', 'administrator'):
            # Admins can see their org's activities
            qs = SystemActivity.objects.filter(user=user).order_by('-timestamp')[:ACTIVITY_FEED_LIMIT]
        else:
            qs = SystemActivity.objects.filter(user=user).order_by('-timestamp')[:ACTIVITY_FEED_LIMIT]

        return [
            {
                'id': a.id,
                'type': a.activity_type,
                'category': a.category,
                'description': a.description,
                'timestamp': a.timestamp.isoformat(),
                'success': a.success,
                'severity': a.severity,
            }
            for a in qs
        ]
    except Exception as e:
        logger.warning('activity feed error: %s', e)
        return []


def _get_notifications_summary(user):
    """Return unread count + recent notification previews."""
    try:
        from apps.notifications.models import Notification
        unread_qs = Notification.objects.filter(recipient=user, is_read=False)
        unread_count = unread_qs.count()
        recent = (
            Notification.objects
            .filter(recipient=user)
            .select_related('category')
            .order_by('-created_at')[:NOTIFICATION_PREVIEW_LIMIT]
        )
        return {
            'unread_count': unread_count,
            'recent': [
                {
                    'id': n.id,
                    'title': n.title,
                    'message': n.message[:120],
                    'priority': n.priority,
                    'category': n.category.name if n.category_id else None,
                    'is_read': n.is_read,
                    'created_at': n.created_at.isoformat(),
                }
                for n in recent
            ],
        }
    except Exception as e:
        logger.warning('notifications summary error: %s', e)
        return {'unread_count': 0, 'recent': []}


def _get_usage_stats(user):
    """Return user's personal 30-day usage aggregated by discipline."""
    try:
        from apps.usage_tracking.models import UsageLog
        cutoff = timezone.now() - timedelta(days=USAGE_CHART_DAYS)
        logs = (
            UsageLog.objects
            .filter(user_email=user.email, timestamp__gte=cutoff)
            .values('discipline_label', 'discipline_key')
            .annotate(
                count=__import__('django.db.models', fromlist=['Count']).Count('id')
            )
            .order_by('-count')
        )
        from apps.usage_tracking.models import UsageLog
        from django.db.models import Count as DjCount
        logs = (
            UsageLog.objects
            .filter(user_email=user.email, timestamp__gte=cutoff)
            .values('discipline_label', 'discipline_key')
            .annotate(count=DjCount('id'))
            .order_by('-count')
        )
        total = UsageLog.objects.filter(user_email=user.email, timestamp__gte=cutoff).count()
        today = UsageLog.objects.filter(
            user_email=user.email,
            timestamp__gte=timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ).count()
        return {
            'total_30d': total,
            'today': today,
            'by_discipline': list(logs[:8]),
        }
    except Exception as e:
        logger.warning('usage stats error: %s', e)
        return {'total_30d': 0, 'today': 0, 'by_discipline': []}


def _get_my_modules(user):
    """Return accessible modules enriched with last-used timestamp."""
    try:
        from apps.rbac.models import UserProfile, Module
        from apps.usage_tracking.models import UsageLog
        from django.db.models import Max

        profile = UserProfile.objects.prefetch_related('roles__modules').get(user=user)
        modules = {}
        for role in profile.roles.filter(is_active=True):
            for mod in role.modules.all():
                if mod.code not in modules:
                    modules[mod.code] = {'code': mod.code, 'name': mod.name, 'last_used': None}

        # Enrich with last-used from usage logs
        cutoff = timezone.now() - timedelta(days=MODULES_LAST_USED_DAYS)
        for code, mod in modules.items():
            last = UsageLog.objects.filter(
                user_email=user.email,
                discipline_key=code,
                timestamp__gte=cutoff,
            ).aggregate(last=Max('timestamp'))['last']
            mod['last_used'] = last.isoformat() if last else None

        # Sort by last_used desc (None at end)
        result = sorted(
            modules.values(),
            key=lambda m: (m['last_used'] is None, m['last_used'] or ''),
            reverse=False,
        )
        # Reverse so most-recently-used comes first
        result = sorted(
            modules.values(),
            key=lambda m: m['last_used'] or '0000',
            reverse=True,
        )
        return result
    except Exception as e:
        logger.warning('my modules error: %s', e)
        return []


def _get_pending_actions(user):
    """Return items needing user's attention (approvals, reviews)."""
    actions = []
    try:
        from apps.notifications.models import Notification
        approvals = Notification.objects.filter(
            recipient=user,
            is_read=False,
            category__name=APPROVAL_CATEGORY_NAME,
        ).order_by('-created_at')[:PENDING_ACTIONS_LIMIT]
        for n in approvals:
            actions.append({
                'id': n.id,
                'type': 'approval',
                'title': n.title,
                'message': n.message[:100],
                'priority': n.priority,
                'created_at': n.created_at.isoformat(),
            })
    except Exception as e:
        logger.warning('pending actions error: %s', e)
    return actions


def _get_team_snapshot(user):
    """Manager only — team members active today."""
    try:
        from apps.rbac.models import UserProfile
        from apps.activity.models import SystemActivity

        profile = UserProfile.objects.get(user=user)
        dept = profile.department or ''
        if not dept:
            return None

        cutoff = timezone.now() - timedelta(hours=TEAM_ACTIVE_HOURS)
        team_profiles = UserProfile.objects.filter(department=dept).exclude(user=user)
        team_count = team_profiles.count()

        # Active today = had a system activity in last TEAM_ACTIVE_HOURS hours
        active_user_ids = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff, user__in=team_profiles.values('user'))
            .values_list('user_id', flat=True)
            .distinct()
        )
        active_count = active_user_ids.count()

        return {
            'department': dept,
            'team_total': team_count,
            'active_today': active_count,
        }
    except Exception as e:
        logger.warning('team snapshot error: %s', e)
        return None


def _build_kpis(user, role_code, usage_stats, notifications_summary):
    """Build role-appropriate KPI list."""
    kpis = []

    # Universal KPIs
    kpis.append({
        'key': 'ai_calls_today',
        'label': 'AI Calls Today',
        'value': usage_stats.get('today', 0),
        'icon': 'sparkles',
        'color': 'blue',
    })
    kpis.append({
        'key': 'ai_calls_30d',
        'label': 'AI Calls (30 days)',
        'value': usage_stats.get('total_30d', 0),
        'icon': 'chart',
        'color': 'indigo',
    })
    kpis.append({
        'key': 'unread_notifications',
        'label': 'Unread Notifications',
        'value': notifications_summary.get('unread_count', 0),
        'icon': 'bell',
        'color': 'amber',
    })

    if role_code in ('manager', 'administrator'):
        kpis.append({
            'key': 'pending_approvals',
            'label': 'Pending Approvals',
            'value': 0,  # Populated below via pending_actions count
            'icon': 'check',
            'color': 'emerald',
        })

    return kpis


class PersonalDashboardView(APIView):
    """
    GET /api/v1/dashboard/personal/
    Returns a role-scoped data bundle for the authenticated user.
    Super Admin / is_superuser → 204 (frontend keeps global view).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Super admin / staff → frontend handles global view
        if user.is_superuser or user.is_staff:
            return Response({'redirect': 'global'}, status=204)

        role_code = _get_primary_role_code(user)

        # Also redirect super_admin role to global view
        if role_code == 'super_admin':
            return Response({'redirect': 'global'}, status=204)

        # Gather data in order
        usage_stats = _get_usage_stats(user)
        notifications_summary = _get_notifications_summary(user)
        activity_feed = _get_activity_feed(user, role_code)
        my_modules = _get_my_modules(user)
        pending_actions = _get_pending_actions(user)
        kpis = _build_kpis(user, role_code, usage_stats, notifications_summary)

        # Update pending approvals KPI with actual count
        for kpi in kpis:
            if kpi['key'] == 'pending_approvals':
                kpi['value'] = len(pending_actions)

        # Team snapshot only for managers
        team_snapshot = None
        if role_code in ('manager', 'administrator'):
            team_snapshot = _get_team_snapshot(user)

        # Build user context from profile
        try:
            from apps.rbac.models import UserProfile
            profile = UserProfile.objects.get(user=user)
            avatar_url = profile.profile_photo.url if profile.profile_photo else None
            department = profile.department or ''
            job_title = profile.job_title or ''
        except Exception:
            avatar_url = None
            department = ''
            job_title = ''

        persona = _detect_persona(department, job_title)

        payload = {
            'user_context': {
                'id': user.id,
                'name': user.get_full_name() or user.username,
                'email': user.email,
                'role_code': role_code,
                'persona': persona,
                'department': department,
                'job_title': job_title,
                'avatar_url': avatar_url,
            },
            'kpis': kpis,
            'activity_feed': activity_feed,
            'notifications': notifications_summary,
            'my_modules': my_modules,
            'pending_actions': pending_actions,
            'usage_stats': usage_stats,
            'team_snapshot': team_snapshot,
            'generated_at': timezone.now().isoformat(),
        }

        return Response(payload)


class PersonalInsightsView(APIView):
    """
    GET /api/v1/dashboard/personal/insights/
    Returns the latest AI-generated insights for the authenticated user.
    If no insights exist yet, returns empty list (Celery task will populate).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()

        try:
            from .models import UserDashboardInsight, INSIGHT_MAX_ACTIVE
            insights = (
                UserDashboardInsight.objects
                .filter(user=user, is_active=True, expires_at__gt=now)
                .order_by('-generated_at')[:INSIGHT_MAX_ACTIVE]
            )
            data = [
                {
                    'id': ins.id,
                    'title': ins.title,
                    'body': ins.body,
                    'insight_type': ins.insight_type,
                    'icon_key': ins.icon_key,
                    'generated_at': ins.generated_at.isoformat(),
                }
                for ins in insights
            ]
        except Exception as e:
            logger.warning('insights fetch error: %s', e)
            data = []

        return Response({'insights': data, 'count': len(data)})


# ─────────────────────────────────────────────────────────────────────────────
# Project Control persona bundle
# ─────────────────────────────────────────────────────────────────────────────
def _project_health(project, snapshot):
    """Compute health traffic light from CPI/SPI + schedule progress vs elapsed."""
    # CPI/SPI take precedence when available (1.0 = on target)
    if snapshot and snapshot.cpi is not None and snapshot.spi is not None:
        worst = min(snapshot.cpi, snapshot.spi)
        if worst >= 0.95:
            return 'green'
        if worst >= 0.85:
            return 'amber'
        return 'red'
    # Fallback: compare progress to elapsed time
    if project.start_date and project.end_date and project.progress is not None:
        total = (project.end_date - project.start_date).days or 1
        elapsed = (timezone.now().date() - project.start_date).days
        elapsed_pct = max(0, min(100, (elapsed / total) * 100))
        gap = (project.progress or 0) - elapsed_pct
        if gap >= -5:
            return 'green'
        if gap >= -15:
            return 'amber'
        return 'red'
    return 'grey'


def _days_between(d1, d2):
    if not d1 or not d2:
        return None
    return (d1 - d2).days


def _serialise_project(project, latest_snapshot):
    today = timezone.now().date()
    budget = float(project.budget or 0)
    spent = float(project.spent or 0)
    contract = float(project.contract_value or 0)
    return {
        'id':               project.id,
        'code':             project.code,
        'name':             project.name,
        'status':           project.status,
        'priority':         project.priority,
        'progress':         project.progress or 0,
        'client_name':      project.client_name or '',
        'start_date':       project.start_date.isoformat() if project.start_date else None,
        'end_date':         project.end_date.isoformat() if project.end_date else None,
        'days_remaining':   _days_between(project.end_date, today) if project.end_date else None,
        'is_overdue':       project.is_overdue,
        'budget':           budget,
        'spent':            spent,
        'contract_value':   contract,
        'currency':         project.currency or 'AED',
        'budget_utilisation': round(project.budget_utilization, 1) if budget else 0,
        'cpi':              round(latest_snapshot.cpi, 2) if latest_snapshot and latest_snapshot.cpi is not None else None,
        'spi':              round(latest_snapshot.spi, 2) if latest_snapshot and latest_snapshot.spi is not None else None,
        'eac':              float(latest_snapshot.eac) if latest_snapshot and latest_snapshot.eac is not None else None,
        'health':           _project_health(project, latest_snapshot),
    }


def _get_user_projects(user):
    """Projects where user is owner OR team member (active)."""
    try:
        from apps.core.project_models import Project
        from django.db.models import Q
        qs = (
            Project.objects
            .filter(is_deleted=False)
            .filter(Q(owner=user) | Q(memberships__user=user, memberships__is_active=True))
            .distinct()
            .order_by('-updated_at')[:PC_PROJECT_LIMIT]
        )
        return list(qs)
    except Exception as e:
        logger.warning('project fetch error: %s', e)
        return []


def _get_latest_snapshots(projects):
    """Return dict {project_id: latest CostSnapshot} for the given projects."""
    if not projects:
        return {}
    try:
        from apps.project_control.models import CostSnapshot
        result = {}
        for snap in (
            CostSnapshot.objects
            .filter(project__in=projects, is_deleted=False)
            .order_by('project_id', '-period_end')
        ):
            result.setdefault(snap.project_id, snap)
        return result
    except Exception as e:
        logger.warning('cost snapshot fetch error: %s', e)
        return {}


def _get_upcoming_milestones(projects):
    """Upcoming (not completed) milestones for user's projects."""
    if not projects:
        return []
    try:
        from apps.core.project_models import ProjectMilestone
        today = timezone.now().date()
        horizon = today + timedelta(days=PC_MILESTONE_WINDOW_DAYS)
        qs = (
            ProjectMilestone.objects
            .filter(
                project__in=projects,
                is_completed=False,
                is_deleted=False,
                target_date__gte=today - timedelta(days=7),  # include just-overdue
                target_date__lte=horizon,
            )
            .select_related('project')
            .order_by('target_date')[:PC_MILESTONE_LIMIT]
        )
        return [
            {
                'id':            m.id,
                'name':          m.name,
                'project_code':  m.project.code,
                'project_name':  m.project.name,
                'target_date':   m.target_date.isoformat(),
                'days_out':      (m.target_date - today).days,
                'is_overdue':    m.target_date < today,
            }
            for m in qs
        ]
    except Exception as e:
        logger.warning('milestone fetch error: %s', e)
        return []


def _get_my_tasks(user):
    """Tasks assigned to this user (not completed), ordered by due date."""
    try:
        from apps.core.project_models import ProjectTask
        today = timezone.now().date()
        qs = (
            ProjectTask.objects
            .filter(assigned_to=user, is_deleted=False)
            .exclude(status='completed')
            .select_related('project')
            .order_by('due_date', '-created_at')[:PC_TASK_LIMIT]
        )
        return [
            {
                'id':           t.id,
                'title':        t.title,
                'project_code': t.project.code,
                'status':       t.status,
                'priority':     t.priority,
                'due_date':     t.due_date.isoformat() if t.due_date else None,
                'days_out':     (t.due_date - today).days if t.due_date else None,
                'is_overdue':   bool(t.due_date and t.due_date < today and t.status != 'completed'),
            }
            for t in qs
        ]
    except Exception as e:
        logger.warning('task fetch error: %s', e)
        return []


def _get_recent_changes(projects):
    if not projects:
        return []
    try:
        from apps.project_control.models import ChangeEvent
        qs = (
            ChangeEvent.objects
            .filter(project__in=projects, is_deleted=False)
            .select_related('project')
            .order_by('-detected_at')[:PC_CHANGE_LIMIT]
        )
        return [
            {
                'id':            c.id,
                'summary':       c.summary,
                'severity':      c.severity,
                'status':        c.status,
                'project_code':  c.project.code,
                'delta_amount':  float(c.delta_amount) if c.delta_amount is not None else None,
                'currency':      c.delta_currency,
                'detected_at':   c.detected_at.isoformat(),
            }
            for c in qs
        ]
    except Exception as e:
        logger.warning('change event fetch error: %s', e)
        return []


def _portfolio_summary(projects_data):
    """Aggregate portfolio KPIs across the user's projects."""
    total_budget = sum(p['budget'] for p in projects_data)
    total_spent = sum(p['spent'] for p in projects_data)
    total_contract = sum(p['contract_value'] for p in projects_data)

    cpis = [p['cpi'] for p in projects_data if p['cpi'] is not None]
    spis = [p['spi'] for p in projects_data if p['spi'] is not None]

    health_counts = {'green': 0, 'amber': 0, 'red': 0, 'grey': 0}
    for p in projects_data:
        health_counts[p['health']] = health_counts.get(p['health'], 0) + 1

    active = sum(1 for p in projects_data if p['status'] == 'active')
    at_risk = health_counts['red'] + health_counts['amber']
    overdue = sum(1 for p in projects_data if p['is_overdue'])

    return {
        'project_count':    len(projects_data),
        'active_count':     active,
        'at_risk_count':    at_risk,
        'overdue_count':    overdue,
        'health_counts':    health_counts,
        'total_budget':     round(total_budget, 2),
        'total_spent':      round(total_spent, 2),
        'total_contract':   round(total_contract, 2),
        'budget_utilisation': round((total_spent / total_budget) * 100, 1) if total_budget else 0,
        'avg_cpi':          round(sum(cpis) / len(cpis), 2) if cpis else None,
        'avg_spi':          round(sum(spis) / len(spis), 2) if spis else None,
    }


class ProjectControlBundleView(APIView):
    """
    GET /api/v1/dashboard/personal/project-control/
    Returns project-portfolio bundle for the Project Control Engineer persona.
    Any authenticated user may call this; the bundle is filtered to the user's projects.
    Missing tables / apps → empty bundle (no error).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        projects = _get_user_projects(user)
        snapshots = _get_latest_snapshots(projects) if projects else {}
        projects_data = [_serialise_project(p, snapshots.get(p.id)) for p in projects]

        payload = {
            'portfolio':          _portfolio_summary(projects_data),
            'projects':           projects_data,
            'upcoming_milestones': _get_upcoming_milestones(projects),
            'my_tasks':           _get_my_tasks(user),
            'recent_changes':     _get_recent_changes(projects),
            'generated_at':       timezone.now().isoformat(),
        }
        return Response(payload)
