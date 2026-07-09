"""
GA4 Real-time client.

Lightweight wrapper around the GA4 Data API "runRealtimeReport" endpoint.
Uses google-auth (already a transitive dep of google-auth-oauthlib) to mint
a short-lived OAuth access token from a service account, then calls the
REST endpoint directly with `requests` — no `google-analytics-data` SDK
required, so we avoid adding heavy dependencies.

The module degrades gracefully:
  * Missing credentials → returns {configured: False, ...}
  * Missing google-auth → returns {configured: False, error: '...'}
  * Network/API error  → returns {configured: True, error: '...'}

That way the dashboard widget never breaks the page even when GA is
mis-configured or unreachable.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from . import config as ga_cfg

logger = logging.getLogger(__name__)

# Cache the (token, expiry) tuple per-process to avoid re-signing JWTs on
# every dashboard poll. The token itself is good for 1 hour.
_TOKEN_CACHE: dict[str, Any] = {'token': None, 'expires_at': 0.0}

# Optional response cache (TTL-keyed) shared by all callers.
_RESPONSE_CACHE: dict[str, Any] = {'payload': None, 'expires_at': 0.0}


def _load_service_account_info() -> dict | None:
    """Return the service-account JSON dict or None if not configured."""
    raw = ga_cfg.GA4_CREDENTIALS_JSON
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning('[GA4] GA4_CREDENTIALS_JSON not valid JSON: %s', exc)
            return None
    path = ga_cfg.GA4_CREDENTIALS_PATH
    if path:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except OSError as exc:
            logger.warning('[GA4] GA4_CREDENTIALS_PATH unreadable (%s): %s', path, exc)
            return None
    return None


def _get_access_token() -> str | None:
    """Return a cached OAuth access token, refreshing if expired."""
    now = time.time()
    if _TOKEN_CACHE['token'] and _TOKEN_CACHE['expires_at'] - 60 > now:
        return _TOKEN_CACHE['token']

    info = _load_service_account_info()
    if not info:
        return None

    try:
        from google.oauth2 import service_account            # type: ignore
        from google.auth.transport.requests import Request   # type: ignore
    except ImportError as exc:
        logger.warning('[GA4] google-auth not installed: %s', exc)
        return None

    creds = service_account.Credentials.from_service_account_info(
        info, scopes=ga_cfg.GA4_OAUTH_SCOPES,
    )
    creds.refresh(Request())
    expiry_ts = creds.expiry.timestamp() if creds.expiry else now + 3000
    _TOKEN_CACHE['token'] = creds.token
    _TOKEN_CACHE['expires_at'] = expiry_ts
    return creds.token


def _run_realtime_report(token: str, dimension: str, metric: str) -> dict:
    url = ga_cfg.GA4_REALTIME_URL_TEMPLATE.format(pid=ga_cfg.GA4_PROPERTY_ID)
    body = {
        'dimensions': [{'name': dimension}] if dimension else [],
        'metrics':    [{'name': metric}],
        'limit':      ga_cfg.GA4_REALTIME_TOP_LIMIT,
        'orderBys':   [{'metric': {'metricName': metric}, 'desc': True}],
    }
    resp = requests.post(
        url,
        headers={'Authorization': f'Bearer {token}'},
        json=body,
        timeout=ga_cfg.GA4_REALTIME_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _flatten_rows(report: dict) -> list[dict]:
    rows = report.get('rows') or []
    out: list[dict] = []
    for r in rows:
        dim_vals = [d.get('value') for d in (r.get('dimensionValues') or [])]
        met_vals = [m.get('value') for m in (r.get('metricValues') or [])]
        out.append({
            'label': dim_vals[0] if dim_vals else '(n/a)',
            'value': int(met_vals[0]) if met_vals and (met_vals[0] or '').isdigit() else 0,
        })
    return out


def fetch_realtime_snapshot() -> dict:
    """
    Return a JSON-serialisable snapshot of GA4 real-time metrics.

    Shape:
      {
        configured: bool,
        property_id: '...',
        console_url: '...',
        active_users: int,
        breakdowns: {top_pages: [...], top_countries: [...], ...},
        updated_at: epoch_seconds,
        error?: str
      }
    """
    now = time.time()
    if _RESPONSE_CACHE['payload'] and _RESPONSE_CACHE['expires_at'] > now:
        return _RESPONSE_CACHE['payload']

    base = {
        'configured':   False,
        'property_id':  ga_cfg.GA4_PROPERTY_ID,
        'console_url':  ga_cfg.GA4_CONSOLE_URL,
        'active_users': 0,
        'breakdowns':   {},
        'updated_at':   int(now),
    }

    token = _get_access_token()
    if not token:
        base['error'] = (
            'GA4 credentials not configured. Set GA4_CREDENTIALS_JSON '
            '(inline) or GA4_CREDENTIALS_PATH (file) plus GA4_PROPERTY_ID, '
            'then restart the backend.'
        )
        return base

    base['configured'] = True

    # Aggregate active users (no dimension) — primary headline KPI.
    try:
        head = _run_realtime_report(token, dimension='', metric='activeUsers')
        head_rows = head.get('rows') or []
        if head_rows:
            mv = head_rows[0].get('metricValues') or []
            if mv and (mv[0].get('value') or '').isdigit():
                base['active_users'] = int(mv[0]['value'])
    except requests.HTTPError as exc:
        try:
            body = exc.response.json()
            msg = body.get('error', {}).get('message') or str(exc)
        except Exception:
            msg = str(exc)
        base['error'] = f'GA4 API error: {msg}'
        return base
    except Exception as exc:
        base['error'] = f'GA4 fetch failed: {exc}'
        return base

    # Breakdowns are best-effort — if one fails we still return the others.
    breakdowns: dict[str, list[dict]] = {}
    for spec in ga_cfg.REALTIME_BREAKDOWNS:
        try:
            report = _run_realtime_report(token, spec['dimension'], spec['metric'])
            breakdowns[spec['key']] = _flatten_rows(report)
        except Exception as exc:
            logger.info('[GA4] breakdown %s failed: %s', spec['key'], exc)
            breakdowns[spec['key']] = []
    base['breakdowns'] = breakdowns

    _RESPONSE_CACHE['payload'] = base
    _RESPONSE_CACHE['expires_at'] = now + ga_cfg.GA4_REALTIME_CACHE_TTL
    return base
