"""
Analytics Collectors — Real-time data ingestion for the Admin Dashboard.

Populates the analytics_models tables (SystemMetrics, SystemHealthCheck,
UserActivityAnalytics, SecurityAlert, FeatureUsageAnalytics,
PredictiveInsight) from REAL sources (psutil, AuditLog, DB pings, cache)
so the dashboard surfaces live numbers instead of zeros.

Design principles:
- TTL-gated: each collector only runs if its latest snapshot is older than
  a soft-coded interval, so calling `ensure_fresh()` on every admin page
  load is cheap.
- Non-invasive: this module READS existing models and WRITES analytics
  rows. It never modifies business logic, RBAC decisions, or any other
  app's behaviour.
- Soft-coded: all thresholds, intervals, and limits come from module-level
  constants or environment variables via python-decouple.
- Graceful: every collector swallows its own exceptions and logs them so a
  failing collector never breaks the dashboard request.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, time, timedelta

from decouple import config
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone

from .models import AuditLog, UserProfile
from .analytics_models import (
    SystemMetrics,
    SystemHealthCheck,
    UserActivityAnalytics,
    SecurityAlert,
    FeatureUsageAnalytics,
    PredictiveInsight,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded freshness intervals (seconds). Override via environment vars.
# ---------------------------------------------------------------------------
SYSTEM_METRICS_TTL_SEC = config('ADMIN_METRICS_TTL_SEC', default=30, cast=int)
HEALTH_CHECK_TTL_SEC = config('ADMIN_HEALTH_TTL_SEC', default=60, cast=int)
USER_ACTIVITY_TTL_SEC = config('ADMIN_USER_ACTIVITY_TTL_SEC', default=120, cast=int)
SECURITY_ALERT_TTL_SEC = config('ADMIN_SECURITY_TTL_SEC', default=120, cast=int)
FEATURE_USAGE_TTL_SEC = config('ADMIN_FEATURE_USAGE_TTL_SEC', default=300, cast=int)
INSIGHT_TTL_SEC = config('ADMIN_INSIGHT_TTL_SEC', default=600, cast=int)

# Soft-coded thresholds used by the rule-based collectors
FAILED_LOGIN_ALERT_THRESHOLD = config('ADMIN_FAILED_LOGIN_THRESHOLD', default=5, cast=int)
FAILED_LOGIN_WINDOW_MIN = config('ADMIN_FAILED_LOGIN_WINDOW_MIN', default=15, cast=int)
HIGH_ERROR_RATE_PCT = config('ADMIN_HIGH_ERROR_RATE_PCT', default=5.0, cast=float)
HEALTH_DEGRADED_SCORE = config('ADMIN_HEALTH_DEGRADED_SCORE', default=80.0, cast=float)
HEALTH_CRITICAL_SCORE = config('ADMIN_HEALTH_CRITICAL_SCORE', default=60.0, cast=float)
DISK_PATH = config('ADMIN_DISK_PATH', default='/')
SECURITY_ALERT_LOOKBACK_HOUR = config('ADMIN_SECURITY_LOOKBACK_HOUR', default=24, cast=int)
INSIGHT_RETENTION_DAYS = config('ADMIN_INSIGHT_RETENTION_DAYS', default=7, cast=int)

# Auth audit actions that count as failed authentication attempts
AUTH_ACTIONS = ('login', 'logout', 'password_change', 'password_reset')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _latest_age_seconds(model, ts_field: str) -> float:
    """Return age in seconds of the most recent row, or +inf if empty."""
    latest = model.objects.order_by(f'-{ts_field}').values_list(ts_field, flat=True).first()
    if latest is None:
        return float('inf')
    return (timezone.now() - latest).total_seconds()


def _safe_psutil():
    """Import psutil lazily so a missing package never breaks the dashboard."""
    try:
        import psutil  # noqa: WPS433  (runtime optional dep)
        return psutil
    except Exception as exc:  # pragma: no cover
        logger.debug('psutil unavailable: %s', exc)
        return None


def _ping_database() -> bool:
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        return True
    except Exception as exc:
        logger.warning('DB ping failed: %s', exc)
        return False


def _ping_redis() -> bool:
    try:
        from django.core.cache import cache
        cache.set('__admin_health_ping__', '1', timeout=10)
        return cache.get('__admin_health_ping__') == '1'
    except Exception as exc:
        logger.debug('Redis ping failed: %s', exc)
        return False


def _ping_celery() -> bool:
    try:
        from celery import current_app
        if getattr(current_app.conf, 'task_always_eager', False):
            # In EAGER mode the worker is the web process itself.
            return True
        inspector = current_app.control.inspect(timeout=1.0)
        return bool(inspector.ping())
    except Exception as exc:
        logger.debug('Celery ping failed: %s', exc)
        return False


# ---------------------------------------------------------------------------
# Individual collectors
# ---------------------------------------------------------------------------
def collect_system_metrics(force: bool = False) -> SystemMetrics | None:
    """Capture a snapshot of resource usage + API success rate."""
    if not force and _latest_age_seconds(SystemMetrics, 'timestamp') < SYSTEM_METRICS_TTL_SEC:
        return None
    try:
        psutil = _safe_psutil()
        cpu_pct = float(psutil.cpu_percent(interval=0.1)) if psutil else 0.0
        memory_mb = float(psutil.virtual_memory().used / (1024 * 1024)) if psutil else 0.0
        disk_gb = float(psutil.disk_usage(DISK_PATH).used / (1024 ** 3)) if psutil else 0.0
        active_conn = int(len(psutil.net_connections(kind='inet'))) if psutil else 0

        # API success rate derived from recent AuditLog rows
        window_start = timezone.now() - timedelta(minutes=SYSTEM_METRICS_TTL_SEC // 60 + 1)
        recent = AuditLog.objects.filter(timestamp__gte=window_start)
        total = recent.count()
        failed = recent.filter(success=False).count()
        success_rate = ((total - failed) / total * 100.0) if total else 100.0

        return SystemMetrics.objects.create(
            timestamp=timezone.now(),
            avg_response_time_ms=0,  # populated by external APM if available
            peak_response_time_ms=0,
            api_requests_count=total,
            failed_requests_count=failed,
            success_rate_percentage=round(success_rate, 2),
            cpu_usage_percentage=round(cpu_pct, 2),
            memory_usage_mb=round(memory_mb, 2),
            disk_usage_gb=round(disk_gb, 2),
            active_connections=active_conn,
        )
    except Exception as exc:
        logger.exception('collect_system_metrics failed: %s', exc)
        return None


def collect_health_check(force: bool = False) -> SystemHealthCheck | None:
    """Run lightweight component pings and persist an overall health row."""
    if not force and _latest_age_seconds(SystemHealthCheck, 'check_time') < HEALTH_CHECK_TTL_SEC:
        return None
    try:
        db_ok = _ping_database()
        redis_ok = _ping_redis()
        celery_ok = _ping_celery()

        # Storage check via shutil (cross-platform, no extra dep required)
        storage_ok = True
        storage_pct = 0.0
        try:
            total, used, _free = shutil.disk_usage(DISK_PATH)
            storage_pct = (used / total) * 100 if total else 0
            storage_ok = storage_pct < 95
        except Exception:
            storage_ok = False

        components = {
            'database_status': 'healthy' if db_ok else 'critical',
            'redis_status': 'healthy' if redis_ok else 'degraded',
            'celery_status': 'healthy' if celery_ok else 'degraded',
            'storage_status': 'healthy' if storage_ok else 'critical',
            'api_status': 'healthy',  # serving this request implies API healthy
        }
        weights = {'database_status': 35, 'redis_status': 15, 'celery_status': 15,
                   'storage_status': 15, 'api_status': 20}
        score = sum(
            weights[k] * (1.0 if v == 'healthy' else 0.5 if v == 'degraded' else 0.0)
            for k, v in components.items()
        )

        if score >= HEALTH_DEGRADED_SCORE:
            overall = 'healthy'
        elif score >= HEALTH_CRITICAL_SCORE:
            overall = 'degraded'
        else:
            overall = 'critical'

        issues = [k.replace('_status', '') for k, v in components.items() if v != 'healthy']

        return SystemHealthCheck.objects.create(
            check_time=timezone.now(),
            **components,
            overall_status=overall,
            health_score=round(score, 2),
            resource_usage={'storage_pct': round(storage_pct, 2)},
            issues_found=issues,
            warnings=[],
        )
    except Exception as exc:
        logger.exception('collect_health_check failed: %s', exc)
        return None


def collect_user_activity_today(force: bool = False) -> int:
    """Upsert per-user UserActivityAnalytics rows for today from AuditLog."""
    today = timezone.now().date()
    if not force:
        latest = (
            UserActivityAnalytics.objects.filter(date=today)
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )
        if latest and (timezone.now() - latest).total_seconds() < USER_ACTIVITY_TTL_SEC:
            return 0
    try:
        start = datetime.combine(today, time.min)
        if timezone.is_naive(start):
            start = timezone.make_aware(start)

        # Aggregate actions per user for today
        rows = (
            AuditLog.objects.filter(timestamp__gte=start, user__isnull=False)
            .values('user_id')
            .annotate(
                login_count=Count('id', filter=Q(action='login', success=True)),
                actions=Count('id'),
                failed=Count('id', filter=Q(success=False)),
            )
        )
        written = 0
        for row in rows:
            uid = row['user_id']
            login_count = row['login_count']
            actions = row['actions']
            failed = row['failed']
            # Engagement = action volume capped at 100. Risk = failed-action ratio.
            engagement = min(100.0, actions * 2.0)
            risk = min(100.0, (failed / actions * 100.0) if actions else 0.0)
            UserActivityAnalytics.objects.update_or_create(
                user_id=uid,
                date=today,
                defaults={
                    'login_count': login_count,
                    'features_used_count': actions,
                    'engagement_score': round(engagement, 2),
                    'productivity_score': round(engagement * 0.8, 2),
                    'risk_score': round(risk, 2),
                    'anomaly_detected': risk >= 50,
                    'usage_pattern': 'power_user' if actions > 50 else 'normal',
                },
            )
            written += 1
        return written
    except Exception as exc:
        logger.exception('collect_user_activity_today failed: %s', exc)
        return 0


def collect_security_alerts(force: bool = False) -> int:
    """Detect failed-login bursts and locked accounts; emit dedup'd alerts."""
    if not force:
        latest = (
            SecurityAlert.objects.order_by('-detection_time')
            .values_list('detection_time', flat=True)
            .first()
        )
        if latest and (timezone.now() - latest).total_seconds() < SECURITY_ALERT_TTL_SEC:
            return 0
    try:
        created = 0
        # 1) Failed-login bursts in the last N minutes per IP
        window_start = timezone.now() - timedelta(minutes=FAILED_LOGIN_WINDOW_MIN)
        bursts = (
            AuditLog.objects.filter(
                action='login',
                success=False,
                timestamp__gte=window_start,
            )
            .values('ip_address', 'user_email')
            .annotate(count=Count('id'))
            .filter(count__gte=FAILED_LOGIN_ALERT_THRESHOLD)
        )
        for b in bursts:
            ip = b['ip_address']
            email = b['user_email'] or 'unknown'
            # Dedup: skip if an open alert for this IP exists in lookback
            dedup_start = timezone.now() - timedelta(hours=SECURITY_ALERT_LOOKBACK_HOUR)
            exists = SecurityAlert.objects.filter(
                alert_type='failed_login_burst',
                ip_address=ip,
                detection_time__gte=dedup_start,
                status__in=['new', 'investigating'],
            ).exists()
            if exists:
                continue
            SecurityAlert.objects.create(
                alert_type='failed_login_burst',
                severity='high' if b['count'] >= FAILED_LOGIN_ALERT_THRESHOLD * 2 else 'medium',
                status='new',
                title=f'{b["count"]} failed logins from {ip or "unknown IP"}',
                description=(
                    f'{b["count"]} failed login attempts against "{email}" '
                    f'in the last {FAILED_LOGIN_WINDOW_MIN} minutes.'
                ),
                detection_time=timezone.now(),
                ip_address=ip,
                ai_confidence=0.85,
                threat_indicators=['brute_force_pattern'],
                recommended_actions=['Lock account', 'Block IP', 'Notify user'],
            )
            created += 1

        # 2) Currently locked-out user profiles -> medium alert (deduped)
        now = timezone.now()
        locked = UserProfile.objects.filter(
            locked_until__isnull=False, locked_until__gt=now,
        )
        for lp in locked:
            exists = SecurityAlert.objects.filter(
                alert_type='account_locked',
                user=lp.user,
                status__in=['new', 'investigating'],
            ).exists()
            if exists:
                continue
            SecurityAlert.objects.create(
                alert_type='account_locked',
                severity='medium',
                status='new',
                title=f'Account locked: {lp.user.email}',
                description=(
                    f'Account locked until {lp.locked_until.isoformat()} '
                    f'after {lp.failed_login_attempts} failed attempts.'
                ),
                detection_time=now,
                user=lp.user,
                ai_confidence=1.0,
                threat_indicators=['repeated_failed_login'],
                recommended_actions=['Verify user identity', 'Reset password'],
            )
            created += 1

        return created
    except Exception as exc:
        logger.exception('collect_security_alerts failed: %s', exc)
        return 0


def collect_feature_usage(force: bool = False) -> int:
    """Roll up today's AuditLog resource_type usage into FeatureUsageAnalytics."""
    today = timezone.now().date()
    if not force:
        latest = (
            FeatureUsageAnalytics.objects.filter(date=today)
            .order_by('-updated_at')
            .values_list('updated_at', flat=True)
            .first()
        )
        if latest and (timezone.now() - latest).total_seconds() < FEATURE_USAGE_TTL_SEC:
            return 0
    try:
        start = datetime.combine(today, time.min)
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        rows = (
            AuditLog.objects.filter(timestamp__gte=start)
            .exclude(resource_type='')
            .values('resource_type')
            .annotate(
                total=Count('id'),
                active=Count('user_id', distinct=True),
                failed=Count('id', filter=Q(success=False)),
            )
        )
        written = 0
        for r in rows:
            total = r['total']
            failed = r['failed']
            success_rate = ((total - failed) / total * 100.0) if total else 100.0
            FeatureUsageAnalytics.objects.update_or_create(
                feature_name=r['resource_type'],
                date=today,
                defaults={
                    'total_users': r['active'],
                    'active_users': r['active'],
                    'total_usage_count': total,
                    'avg_usage_per_user': round((total / r['active']) if r['active'] else 0, 2),
                    'success_rate': round(success_rate, 2),
                    'health_score': round(success_rate, 2),
                    'trend': 'growing' if total > 10 else 'stable',
                },
            )
            written += 1
        return written
    except Exception as exc:
        logger.exception('collect_feature_usage failed: %s', exc)
        return 0


def collect_predictive_insights(force: bool = False) -> int:
    """Generate rule-based insights from the freshest metric/health rows."""
    if not force:
        latest = (
            PredictiveInsight.objects.order_by('-created_at')
            .values_list('created_at', flat=True)
            .first()
        )
        if latest and (timezone.now() - latest).total_seconds() < INSIGHT_TTL_SEC:
            return 0
    try:
        # Retire stale insights so the dashboard doesn't stack repeats
        cutoff = timezone.now() - timedelta(days=INSIGHT_RETENTION_DAYS)
        PredictiveInsight.objects.filter(
            created_at__lt=cutoff, is_acknowledged=False,
        ).update(is_active=False)

        metrics = SystemMetrics.objects.first()
        health = SystemHealthCheck.objects.first()
        if not metrics and not health:
            return 0

        created = 0

        def _emit(insight_type, title, description, impact, area, recs, confidence=0.8):
            nonlocal created
            # Dedup: skip if same type emitted in the last 6 hours
            dedup_start = timezone.now() - timedelta(hours=6)
            if PredictiveInsight.objects.filter(
                insight_type=insight_type,
                title=title,
                created_at__gte=dedup_start,
                is_active=True,
            ).exists():
                return
            PredictiveInsight.objects.create(
                insight_type=insight_type,
                title=title,
                description=description,
                prediction_date=timezone.now().date(),
                confidence_score=confidence,
                predicted_values={},
                impact_level=impact,
                affected_area=area,
                recommendations=recs,
                action_items=recs,
                ml_model_used='rule_engine_v1',
                training_data_period='live',
                is_active=True,
            )
            created += 1

        if metrics:
            if metrics.success_rate_percentage < (100 - HIGH_ERROR_RATE_PCT):
                _emit(
                    'performance_optimization',
                    'Elevated API failure rate',
                    f'Success rate dropped to {metrics.success_rate_percentage:.1f}%. '
                    f'Review error logs and recent deploys.',
                    'high', 'API',
                    ['Inspect error logs', 'Roll back recent deploy if applicable'],
                    0.9,
                )
            if metrics.cpu_usage_percentage >= 90:
                _emit(
                    'capacity_planning',
                    'CPU saturation detected',
                    f'CPU at {metrics.cpu_usage_percentage:.1f}% — scale workers.',
                    'high', 'Infrastructure',
                    ['Scale horizontally', 'Profile hot endpoints'],
                    0.95,
                )

        if health and health.health_score < HEALTH_DEGRADED_SCORE:
            _emit(
                'performance_optimization',
                f'System health degraded ({health.health_score:.0f}%)',
                f'Components flagged: {", ".join(health.issues_found) or "see health check"}.',
                'high' if health.health_score < HEALTH_CRITICAL_SCORE else 'medium',
                'System',
                ['Investigate failing components', 'Page on-call if critical'],
                0.85,
            )

        return created
    except Exception as exc:
        logger.exception('collect_predictive_insights failed: %s', exc)
        return 0


# ---------------------------------------------------------------------------
# Orchestrator — call this from the dashboard overview endpoint.
# ---------------------------------------------------------------------------
def ensure_fresh(force: bool = False) -> dict:
    """Run every collector subject to its soft-coded TTL gate.

    Safe to call on every admin dashboard request: each collector is a no-op
    when its latest snapshot is still within its freshness window.
    """
    result = {}
    try:
        result['system_metrics'] = bool(collect_system_metrics(force=force))
        result['health_check'] = bool(collect_health_check(force=force))
        result['user_activity'] = collect_user_activity_today(force=force)
        result['security_alerts'] = collect_security_alerts(force=force)
        result['feature_usage'] = collect_feature_usage(force=force)
        result['predictive_insights'] = collect_predictive_insights(force=force)
    except Exception as exc:  # final safety net
        logger.exception('ensure_fresh orchestrator failed: %s', exc)
    return result
