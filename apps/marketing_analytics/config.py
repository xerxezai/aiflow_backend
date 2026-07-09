"""
Soft-coded configuration for the Marketing Analytics (GA4) integration.

All values are env-driven so the same code works in local Docker, Railway
production, and CI without touching source. Defaults are tuned for the
RAD AI property (a394429261 / 537260287) shown in the GA4 web console URL.

ENV VARS (override any of these without code changes):
  GA4_PROPERTY_ID            GA4 numeric property id (no 'properties/' prefix)
  GA4_CREDENTIALS_PATH       absolute path to service account JSON file
  GA4_CREDENTIALS_JSON       inline service account JSON (for Railway etc.)
  GA4_REALTIME_TOP_LIMIT     row cap per dimension (default 10)
  GA4_REALTIME_TIMEOUT       HTTP timeout in seconds (default 8)
  GA4_REALTIME_CACHE_TTL     server-side cache seconds (default 20)
  GA4_REQUIRE_ADMIN          'true' to limit endpoint to admin users
"""

from __future__ import annotations

import os


def _env(name: str, default: str = '') -> str:
    return (os.environ.get(name) or default).strip()


# ─── GA4 property under measurement ────────────────────────────────────────
# Falls back to the property id visible in the GA4 console URL
# https://analytics.google.com/analytics/web/#/a394429261p537260287/...
GA4_PROPERTY_ID = _env('GA4_PROPERTY_ID', '537260287')

# Public GA4 reports URL — used by the frontend "Open in Google Analytics"
# link. Soft-coded so we can swap accounts without redeploying frontend.
GA4_CONSOLE_ACCOUNT = _env('GA4_CONSOLE_ACCOUNT', 'a394429261')
GA4_CONSOLE_URL = (
    f'https://analytics.google.com/analytics/web/#/'
    f'{GA4_CONSOLE_ACCOUNT}p{GA4_PROPERTY_ID}/reports/intelligenthome'
)

# ─── Credentials ───────────────────────────────────────────────────────────
GA4_CREDENTIALS_PATH = _env('GA4_CREDENTIALS_PATH')
GA4_CREDENTIALS_JSON = _env('GA4_CREDENTIALS_JSON')

# ─── Query knobs ───────────────────────────────────────────────────────────
def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except (TypeError, ValueError):
        return default


GA4_REALTIME_TOP_LIMIT = _int_env('GA4_REALTIME_TOP_LIMIT', 10)
GA4_REALTIME_TIMEOUT   = _int_env('GA4_REALTIME_TIMEOUT', 8)
GA4_REALTIME_CACHE_TTL = _int_env('GA4_REALTIME_CACHE_TTL', 20)

# ─── Access control ────────────────────────────────────────────────────────
GA4_REQUIRE_ADMIN = _env('GA4_REQUIRE_ADMIN', 'false').lower() in ('1', 'true', 'yes')

# ─── Realtime report shape ─────────────────────────────────────────────────
# Each entry produces one slice in the API response. Soft-coded so adding a
# new breakdown (e.g. browser, language) is a single-line change.
REALTIME_BREAKDOWNS = [
    {'key': 'top_pages',     'dimension': 'unifiedScreenName', 'metric': 'activeUsers'},
    {'key': 'top_countries', 'dimension': 'country',           'metric': 'activeUsers'},
    {'key': 'top_devices',   'dimension': 'deviceCategory',    'metric': 'activeUsers'},
    {'key': 'top_sources',   'dimension': 'unifiedPageScreen', 'metric': 'screenPageViews'},
]

GA4_OAUTH_SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
GA4_REALTIME_URL_TEMPLATE = (
    'https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runRealtimeReport'
)
