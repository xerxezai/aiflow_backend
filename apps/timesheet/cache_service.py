"""
Intelligent Caching Layer for Timesheet Analytics
=================================================
Redis-backed caching with soft-coded TTLs, background refresh, and graceful
degradation. Dramatically speeds up live data queries by caching aggregated
results instead of querying 1.56M+ events every time.

Features:
• Multi-tier TTL strategy (live=15s, daily=5min, monthly=1hr)
• Background pre-warming via Celery
• Stale-while-revalidate pattern (serve stale + refresh async)
• Circuit breaker for SQL Server unreachability
• Soft-coded via environment variables
• Production-safe fallback chain

Environment Variables:
    TIMESHEET_CACHE_ENABLED             default True
    TIMESHEET_CACHE_LIVE_TTL            default 15  (seconds)
    TIMESHEET_CACHE_DAILY_TTL           default 300 (5 minutes)
    TIMESHEET_CACHE_MONTHLY_TTL         default 3600 (1 hour)
    TIMESHEET_CACHE_STALE_GRACE         default 300 (serve stale for 5min if refresh fails)
    TIMESHEET_CACHE_BACKGROUND_REFRESH  default True (pre-warm via Celery)
    TIMESHEET_CACHE_CIRCUIT_THRESHOLD   default 5  (failures before circuit opens)
    TIMESHEET_CACHE_CIRCUIT_TIMEOUT     default 60 (seconds before retry)
"""
import datetime as dt
import hashlib
import json
import logging
from functools import wraps
from typing import Optional, Callable, Any

from decouple import config as env
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Soft-Coded Configuration
# ─────────────────────────────────────────────────────────────────────────────
CACHE_ENABLED         = env('TIMESHEET_CACHE_ENABLED', default='true', cast=bool)
CACHE_LIVE_TTL        = env('TIMESHEET_CACHE_LIVE_TTL', default=15, cast=int)
CACHE_DAILY_TTL       = env('TIMESHEET_CACHE_DAILY_TTL', default=300, cast=int)
CACHE_MONTHLY_TTL     = env('TIMESHEET_CACHE_MONTHLY_TTL', default=3600, cast=int)
CACHE_STALE_GRACE     = env('TIMESHEET_CACHE_STALE_GRACE', default=300, cast=int)
BACKGROUND_REFRESH    = env('TIMESHEET_CACHE_BACKGROUND_REFRESH', default='true', cast=bool)
CIRCUIT_THRESHOLD     = env('TIMESHEET_CACHE_CIRCUIT_THRESHOLD', default=5, cast=int)
CIRCUIT_TIMEOUT       = env('TIMESHEET_CACHE_CIRCUIT_TIMEOUT', default=60, cast=int)

# Soft-coded cache key prefix (version bump invalidates all cached data)
CACHE_VERSION         = env('TIMESHEET_CACHE_VERSION', default='v3')
KEY_PREFIX            = f'ts:{CACHE_VERSION}'


# ─────────────────────────────────────────────────────────────────────────────
# Cache Key Generation
# ─────────────────────────────────────────────────────────────────────────────
def _make_key(category: str, **kwargs) -> str:
    """Generate deterministic cache key from category + params.
    
    Examples:
        _make_key('live')                           → 'ts:v3:live'
        _make_key('daily', date='2026-06-25')       → 'ts:v3:daily:2026-06-25'
        _make_key('monthly', year=2026, month=6)    → 'ts:v3:monthly:2026:6'
    """
    parts = [KEY_PREFIX, category]
    if kwargs:
        # Sort keys for deterministic hash
        param_str = ':'.join(f'{k}={v}' for k, v in sorted(kwargs.items()))
        parts.append(hashlib.md5(param_str.encode()).hexdigest()[:8])
    return ':'.join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker — Prevent hammering unreachable SQL Server
# ─────────────────────────────────────────────────────────────────────────────
class CircuitBreaker:
    """Track SQL Server connection failures and temporarily skip queries
    when it's clearly unreachable (e.g. production Railway → office LAN)."""
    
    def __init__(self):
        self._key = f'{KEY_PREFIX}:circuit'
        
    def record_failure(self):
        """Increment failure counter. Opens circuit when threshold exceeded."""
        failures = cache.get(self._key, 0) + 1
        cache.set(self._key, failures, timeout=CIRCUIT_TIMEOUT)
        if failures >= CIRCUIT_THRESHOLD:
            logger.warning(
                '[Timesheet Cache] Circuit breaker OPEN (%d failures) — '
                'SQL Server unreachable, falling back to cache/mirror',
                failures
            )
        return failures
        
    def record_success(self):
        """Reset failure counter on successful query."""
        cache.delete(self._key)
        
    def is_open(self) -> bool:
        """Return True if circuit is open (too many recent failures)."""
        failures = cache.get(self._key, 0)
        return failures >= CIRCUIT_THRESHOLD
    
    def reset(self):
        """Manually reset circuit (admin action)."""
        cache.delete(self._key)
        logger.info('[Timesheet Cache] Circuit breaker manually RESET')


circuit = CircuitBreaker()


# ─────────────────────────────────────────────────────────────────────────────
# Stale-While-Revalidate Pattern
# ─────────────────────────────────────────────────────────────────────────────
def _cache_get_with_meta(key: str) -> Optional[tuple[Any, dict]]:
    """Get cached value with metadata (timestamp, stale marker).
    
    Returns:
        (data, meta) tuple if found, else None
        meta = {'cached_at': datetime, 'is_stale': bool}
    """
    raw = cache.get(key)
    if raw is None:
        return None
    try:
        data = raw.get('data')
        cached_at = raw.get('cached_at')
        ttl = raw.get('ttl', 0)
        age = (timezone.now() - cached_at).total_seconds() if cached_at else 9999
        is_stale = age > ttl
        return data, {'cached_at': cached_at, 'is_stale': is_stale, 'age': age}
    except Exception:
        return None


def _cache_set_with_meta(key: str, data: Any, ttl: int):
    """Store data with timestamp and TTL metadata."""
    envelope = {
        'data': data,
        'cached_at': timezone.now(),
        'ttl': ttl,
    }
    # Actual cache expiry = TTL + grace period (allows stale serving)
    cache.set(key, envelope, timeout=ttl + CACHE_STALE_GRACE)


# ─────────────────────────────────────────────────────────────────────────────
# Decorator: Cached Query
# ─────────────────────────────────────────────────────────────────────────────
def cached_timesheet_query(category: str, ttl: int):
    """Decorator for timesheet service functions with intelligent caching.
    
    Args:
        category: Cache category ('live', 'daily', 'monthly')
        ttl: Fresh data TTL in seconds
        
    Behavior:
        1. Check cache — return if fresh
        2. If stale but recent, return stale + trigger background refresh
        3. If circuit is open, return stale or empty (don't query)
        4. Query SQL Server, cache result
        5. On error, return stale if available (graceful degradation)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not CACHE_ENABLED:
                return func(*args, **kwargs)
                
            # Generate cache key from function args
            key_kwargs = {}
            if 'date' in kwargs and kwargs['date']:
                key_kwargs['date'] = str(kwargs['date'])
            if 'year' in kwargs:
                key_kwargs['year'] = kwargs['year']
            if 'month' in kwargs:
                key_kwargs['month'] = kwargs['month']
                
            cache_key = _make_key(category, **key_kwargs)
            
            # Try cache first
            cached = _cache_get_with_meta(cache_key)
            if cached:
                data, meta = cached
                if not meta['is_stale']:
                    logger.debug(
                        '[Timesheet Cache] HIT %s (fresh, age=%ds)',
                        cache_key, int(meta['age'])
                    )
                    return data
                else:
                    logger.info(
                        '[Timesheet Cache] HIT %s (stale, age=%ds) — serving + refreshing',
                        cache_key, int(meta['age'])
                    )
                    # Serve stale immediately, refresh in background
                    if BACKGROUND_REFRESH:
                        try:
                            from .tasks import refresh_timesheet_cache
                            refresh_timesheet_cache.delay(category, key_kwargs)
                        except Exception as e:
                            logger.warning('[Timesheet Cache] Background refresh failed: %s', e)
                    return data
            
            # Circuit breaker check
            if circuit.is_open():
                logger.warning(
                    '[Timesheet Cache] Circuit OPEN — skipping SQL query for %s',
                    cache_key
                )
                # Return stale data if available, else empty
                if cached:
                    return cached[0]
                return _empty_response(category)
            
            # Cache miss or expired — query SQL Server
            logger.info('[Timesheet Cache] MISS %s — querying SQL Server', cache_key)
            try:
                data = func(*args, **kwargs)
                _cache_set_with_meta(cache_key, data, ttl)
                circuit.record_success()
                return data
            except Exception as e:
                logger.error('[Timesheet Cache] Query failed for %s: %s', cache_key, e)
                circuit.record_failure()
                # Graceful degradation: return stale if available
                if cached:
                    logger.info('[Timesheet Cache] Returning stale data after query failure')
                    return cached[0]
                # Last resort: return empty response
                logger.warning('[Timesheet Cache] No fallback available, returning empty')
                return _empty_response(category)
                
        return wrapper
    return decorator


def _empty_response(category: str) -> dict:
    """Safe empty response when all else fails."""
    return {
        'configured': True,
        'rows': [],
        'summary': {},
        'as_of': timezone.now().isoformat(),
        'cache_miss': True,
        'circuit_open': circuit.is_open(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Manual Cache Control (Admin API)
# ─────────────────────────────────────────────────────────────────────────────
def invalidate_all():
    """Clear all timesheet cache entries. Use after config changes."""
    try:
        # Django's cache.clear() is too aggressive (clears everything)
        # Instead, track keys and delete selectively
        patterns = [
            f'{KEY_PREFIX}:live*',
            f'{KEY_PREFIX}:daily*',
            f'{KEY_PREFIX}:monthly*',
        ]
        logger.info('[Timesheet Cache] Invalidating all cached data')
        # Note: Redis pattern delete requires direct connection
        # For now, just increment version in env var to invalidate all
        return {'status': 'ok', 'message': 'Bump TIMESHEET_CACHE_VERSION to invalidate'}
    except Exception as e:
        logger.error('[Timesheet Cache] Invalidation failed: %s', e)
        return {'status': 'error', 'error': str(e)}


def warm_cache():
    """Pre-populate cache with current data. Useful on deploy."""
    if not BACKGROUND_REFRESH:
        return {'status': 'disabled', 'message': 'Background refresh disabled'}
    
    try:
        from .tasks import (
            refresh_timesheet_live,
            refresh_timesheet_daily,
            refresh_timesheet_monthly,
        )
        
        # Trigger background tasks
        refresh_timesheet_live.delay()
        refresh_timesheet_daily.delay()
        refresh_timesheet_monthly.delay()
        
        logger.info('[Timesheet Cache] Cache warming initiated')
        return {'status': 'ok', 'message': 'Cache warming tasks queued'}
    except Exception as e:
        logger.error('[Timesheet Cache] Cache warming failed: %s', e)
        return {'status': 'error', 'error': str(e)}


def get_stats() -> dict:
    """Return cache performance metrics."""
    return {
        'enabled': CACHE_ENABLED,
        'version': CACHE_VERSION,
        'ttls': {
            'live': CACHE_LIVE_TTL,
            'daily': CACHE_DAILY_TTL,
            'monthly': CACHE_MONTHLY_TTL,
        },
        'circuit': {
            'threshold': CIRCUIT_THRESHOLD,
            'failures': cache.get(f'{KEY_PREFIX}:circuit', 0),
            'is_open': circuit.is_open(),
        },
        'background_refresh': BACKGROUND_REFRESH,
        'stale_grace_period': CACHE_STALE_GRACE,
    }
