"""
AI Champion — Activity Capture Middleware
=========================================

Captures every authenticated DRF API request as an `ActivityEvent` row so the
AI Champion leaderboard, cost dashboard, and gamified scoring engine receive
LIVE data without requiring every feature to call `analyticsService.trackActivity`
manually.

All thresholds, URL→application/feature mappings and excluded prefixes are
SOFT-CODED at module level so SuperAdmin can rebalance behaviour without
touching business logic. To override at deploy time, set Django settings:

    AI_CHAMPION_TELEMETRY_EXCLUDE_PREFIXES = [...]
    AI_CHAMPION_TELEMETRY_APPLICATION_MAP  = {...}

Failure semantics: middleware NEVER raises. Any tracking error is swallowed
and logged at WARNING — request flow is never disrupted.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED CONFIG — module-level constants, overridable via Django settings
# ---------------------------------------------------------------------------

# URL prefixes (after API_PREFIX strip) that should NOT be tracked.
# Tracking endpoints themselves must be excluded to prevent recursion.
DEFAULT_EXCLUDE_PREFIXES = (
    'rbac/ai-champion/',           # the tracker itself
    'rbac/analytics/',             # legacy analytics endpoints
    'auth/',                       # token refresh / login
    'health/',                     # health probes
    'cors/',                       # CORS pings
    'rbac/users/me/',              # noisy on every page load
    'notifications/poll',          # noisy poller
)

# Stripped before matching — soft-coded so we work in any environment
API_PREFIX = '/api/v1/'

# Soft-coded HTTP method → action_type mapping
METHOD_TO_ACTION = {
    'GET':    'view',
    'POST':   'click',
    'PUT':    'edit',
    'PATCH':  'edit',
    'DELETE': 'delete',
}

# URL segment → application code mapping. Order matters: first match wins.
# Each entry: (url_substring, application_code, default_module)
DEFAULT_APPLICATION_MAP = (
    ('pid-verification',   'pid-verification',     'process'),
    ('pid_verification',   'pid-verification',     'process'),
    ('pfd_quality',        'pfd-quality',          'process'),
    ('pfd_converter',      'pfd-converter',        'process'),
    ('pfd',                'pfd',                  'process'),
    ('process_datasheet',  'process-datasheet',    'process'),
    ('equipment',          'equipment-list',       'process'),
    ('line',               'line-list',            'piping'),
    ('electrical',         'electrical',           'electrical'),
    ('instrument',         'instrument',           'instrument'),
    ('mechanical',         'mechanical',           'mechanical'),
    ('piping',             'piping',               'piping'),
    ('qhse',               'qhse',                 'qhse'),
    ('crs',                'crs',                  'documents'),
    ('designiq',           'designiq',             'designiq'),
    ('finance',            'finance',              'finance'),
    ('procurement',        'procurement',          'procurement'),
    ('sales',              'sales',                'sales'),
    ('wrench',             'wrench-integration',   'integrations'),
    ('rbac/users',         'user-management',      'admin'),
    ('rbac/roles',         'rbac-roles',           'admin'),
    ('rbac/audit',         'audit',                'admin'),
    ('rbac',               'rbac',                 'admin'),
    ('activity',           'activity-tracking',    'admin'),
    ('usage_tracking',     'usage-tracking',       'admin'),
    ('notifications',      'notifications',        'platform'),
)


def _get_setting(name: str, default):
    return getattr(settings, name, default)


def _excluded(short_path: str, exclude_prefixes) -> bool:
    return any(short_path.startswith(p) for p in exclude_prefixes)


def _resolve_app_feature(short_path: str, app_map) -> tuple[str, str, str]:
    """
    Returns (application, module, feature) tuple from a stripped URL path.

    feature   — second URL segment after the application root, e.g.
                'rbac/ai-champion/leaderboard/' → feature='leaderboard'
    """
    segments = [s for s in short_path.split('/') if s]
    feature = segments[1] if len(segments) >= 2 else ''
    for needle, app_code, module in app_map:
        if needle in short_path:
            return app_code, module, feature
    # Fallback: first URL segment becomes the application code
    if segments:
        return segments[0], 'platform', feature
    return 'platform', 'platform', feature


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
class AIChampionTelemetryMiddleware:
    """
    Captures authenticated API requests as `ActivityEvent` rows.

    Mounting: register AFTER auth middleware so `request.user` is populated.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._exclude_prefixes = tuple(_get_setting(
            'AI_CHAMPION_TELEMETRY_EXCLUDE_PREFIXES',
            DEFAULT_EXCLUDE_PREFIXES,
        ))
        self._app_map = tuple(_get_setting(
            'AI_CHAMPION_TELEMETRY_APPLICATION_MAP',
            DEFAULT_APPLICATION_MAP,
        ))
        self._enabled = bool(_get_setting('AI_CHAMPION_TELEMETRY_ENABLED', True))
        self._api_prefix = _get_setting('AI_CHAMPION_API_PREFIX', API_PREFIX)

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        if self._enabled:
            try:
                self._record(request, response, start)
            except Exception as exc:
                # Telemetry must NEVER break the request
                logger.warning("AI Champion telemetry failed: %s", exc)
        return response

    # -----------------------------------------------------------------
    def _record(self, request, response, start_monotonic: float) -> None:
        path = getattr(request, 'path', '') or ''
        if not path.startswith(self._api_prefix):
            return

        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return

        short = path[len(self._api_prefix):]
        if _excluded(short, self._exclude_prefixes):
            return

        # Lazy import to avoid AppRegistryNotReady at startup
        from .ai_champion_models import ActivityEvent

        application, module, feature = _resolve_app_feature(short, self._app_map)
        action_type = METHOD_TO_ACTION.get(request.method, 'other')
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        success = 200 <= int(getattr(response, 'status_code', 200)) < 400

        # Session id derived from auth header / session — kept stable per browser tab
        session_id = (
            request.META.get('HTTP_X_SESSION_ID')
            or getattr(request, 'session', None)
            and request.session.session_key
            or ''
        )[:64]

        ActivityEvent.objects.create(
            user=user,
            application=application[:64],
            module=module[:64],
            feature=feature[:64],
            action_type=action_type,
            session_id=session_id or '',
            duration_ms=max(0, duration_ms),
            success=success,
            metadata={
                'method': request.method,
                'path': path,
                'status': int(getattr(response, 'status_code', 0)),
            },
        )
