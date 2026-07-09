"""
Query + aggregation services for the Time Sheet Analytics feature.

Every SQL identifier is read from apps.timesheet.config (soft-coded), so adding
support for a new timesheet system is a config change, not a code change.

Two supported schema variants (auto-detected from configured columns):

    1. event_stream  — one row per punch: (emp_code, punch_time, punch_type IN/OUT)
    2. two_column    — one row per day:   (emp_code, date, login_time, logout_time)

Public functions return plain dicts ready for JSON serialisation.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
from collections import defaultdict
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from decouple import config as _env

from . import config as ts_config
from .sqlserver import connect, rows_to_dicts

# Import intelligent caching layer
try:
    from . import cache_service
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False

logger = logging.getLogger(__name__)
User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded per-user match strategy
# ─────────────────────────────────────────────────────────────────────────────
# When True, the per-user SQL filter is case-insensitive and trim-tolerant —
# mirrors the normalised email/code matching used by `_enrich_with_rad_users`
# in the live aggregate. Required because SQL Server collations are often
# case-sensitive and biometric tables routinely contain trailing whitespace
# in [EmpEmail] / [EmpCode]. Override to 'false' only if a downstream
# collation explicitly requires strict comparison.
_USER_MATCH_NORMALISE = _env('TIMESHEET_USER_MATCH_NORMALISE', default='true').lower() in ('1', 'true', 'yes', 'on')

# When True, a per-user query that returns zero rows triggers a second pass:
# fetch all punches in the (already date-bounded) window and filter in Python
# using the same normalised keys plus alternate identifiers resolved from the
# RAD AI UserProfile. Handles edge cases the SQL `LOWER(LTRIM(RTRIM(...)))`
# can't (non-ASCII whitespace, NFC/NFD Unicode, alternate email aliases).
_USER_MATCH_PY_FALLBACK = _env('TIMESHEET_USER_MATCH_PY_FALLBACK', default='true').lower() in ('1', 'true', 'yes', 'on')

# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded NAME-BASED resolver
# ─────────────────────────────────────────────────────────────────────────────
# Many biometric systems expose only [UserID] and [UserName] — the UserID is
# a system-generated badge number (e.g. '22972') that bears no relation to
# the HR employee code (e.g. 'EMP001'). To make per-user lookups work without
# requiring an admin to manually maintain a mapping table, we resolve the
# biometric UserID from the user's name and email-stem via a `LIKE`-match
# against [UserName] the first time we see them, then cache it.
_USER_NAME_RESOLVE          = _env('TIMESHEET_USER_NAME_RESOLVE',          default='true').lower() in ('1', 'true', 'yes', 'on')
_USER_NAME_RESOLVE_MIN_TOKS = int(_env('TIMESHEET_USER_NAME_RESOLVE_MIN_TOKENS', default='2'))
_USER_NAME_RESOLVE_MAX_HITS = int(_env('TIMESHEET_USER_NAME_RESOLVE_MAX_MATCHES', default='5'))
_USER_NAME_RESOLVE_TTL_SEC  = int(_env('TIMESHEET_USER_NAME_RESOLVE_CACHE_SEC',  default='3600'))
# Tokens shorter than this are dropped (avoids matching everyone with "al"
# or "el" in their name).
_USER_NAME_TOKEN_MIN_LEN    = int(_env('TIMESHEET_USER_NAME_TOKEN_MIN_LEN',      default='3'))
# Common email-prefix words that should not be used as name tokens.
_USER_NAME_TOKEN_STOPWORDS  = set(
    s.strip().lower() for s in
    _env('TIMESHEET_USER_NAME_TOKEN_STOPWORDS', default='admin,info,test,demo,user,mail,no-reply,noreply,support')
    .split(',')
    if s.strip()
)


def _norm_key(v) -> str:
    """Normalise an identifier for case/whitespace-insensitive comparison."""
    return (str(v) if v is not None else '').strip().lower()


def _name_tokens(*sources: str) -> list[str]:
    """Extract clean name tokens from arbitrary sources (first/last name,
    email local-part, employee code stem). Used to drive a fuzzy
    `[UserName] LIKE` resolver when no shared id exists between RAD AI and
    the biometric DB."""
    import re
    toks: list[str] = []
    seen: set[str] = set()
    for src in sources:
        if not src:
            continue
        # Strip email domain if present
        s = str(src).split('@', 1)[0]
        # Split on every non-letter so we keep only word-like tokens
        for raw in re.split(r'[^A-Za-z\u00C0-\u024F]+', s):
            t = raw.strip().lower()
            if (
                len(t) >= _USER_NAME_TOKEN_MIN_LEN
                and t not in _USER_NAME_TOKEN_STOPWORDS
                and t not in seen
            ):
                seen.add(t)
                toks.append(t)
    return toks


def _resolve_biometric_user_ids(*, profile=None, email: Optional[str] = None,
                                 employee_code: Optional[str] = None) -> set[str]:
    """Resolve a RAD AI user → set of biometric UserIDs via fuzzy
    `[UserName] LIKE` matching. Cached per `(email|code)` key for
    `_USER_NAME_RESOLVE_TTL_SEC` seconds.

    Tries multiple token strategies in order of confidence and returns the
    first one that yields between 1 and `_USER_NAME_RESOLVE_MAX_HITS`
    matches. Common cases:
      • Real corporate email (`firstname.lastname@…`) → email-stem tokens
        alone uniquely identify the user.
      • Email is generic (`info@…`) → fall back to profile first/last name.
      • Profile name is a placeholder (`Smoke Test`) → email-stem still wins.
    """
    if not _USER_NAME_RESOLVE:
        return set()
    name_col = ts_config.SCHEMA['columns'].get('employee_name', '')
    code_col = ts_config.SCHEMA['columns'].get('employee_code', '')
    if not (name_col and code_col):
        return set()

    first = (profile.user.first_name if profile and profile.user else '') or ''
    last  = (profile.user.last_name  if profile and profile.user else '') or ''
    # Ordered strategies (most-canonical first). Each entry is a tuple of
    # token-source strings; tokens from all sources in the tuple are ANDed.
    strategies: list[tuple[str, tuple]] = [
        ('email_stem',          (email,)),
        ('email_and_code_stem', (email, employee_code)),
        ('profile_full_name',   (first, last)),
        ('profile_and_email',   (first, last, email)),
    ]

    # Versioned cache key so deploys invalidate any stale empty entries.
    _CACHE_VER = 'v3'
    cache_key = f'ts:bio_uid:{_CACHE_VER}:{(_norm_key(email) or _norm_key(employee_code))[:128]}'
    cache = None
    try:
        from django.core.cache import cache as _c
        cache = _c
        cached = cache.get(cache_key)
        if cached is not None:
            return set(cached)
    except Exception:
        cache = None

    def _run_match(tokens: list[str]) -> list[dict]:
        if len(tokens) < _USER_NAME_RESOLVE_MIN_TOKS:
            return []
        where = ' AND '.join([f"[{_safe(name_col)}] LIKE %s" for _ in tokens])
        sql = (
            f"SELECT DISTINCT TOP {max(1, _USER_NAME_RESOLVE_MAX_HITS + 1)} "
            f"[{_safe(code_col)}] AS code, [{_safe(name_col)}] AS name "
            f"FROM {_table()} WHERE {where}"
        )
        try:
            with connect() as cur:
                cur.execute(sql, tuple(f'%{t}%' for t in tokens))
                return rows_to_dicts(cur, cur.fetchall())
        except Exception as exc:
            logger.warning('Timesheet name-resolver SQL failed (tokens=%s): %s', tokens, exc)
            return []

    chosen_hits: list[dict] = []
    chosen_strategy = None
    chosen_tokens: list[str] = []

    # Strategy 0 — Mx_VEW_UserDetails email bridge. The user-details view
    # holds the authoritative email ↔ UserID mapping; using it as the first
    # step lets per-user lookups succeed even when the attendance view has
    # no email column at all. All identifiers (table, columns) come from
    # ``ts_config.USER_DETAILS`` so swapping the source view requires no
    # code change.
    if email:
        try:
            ud_cfg  = ts_config.USER_DETAILS
            ud_tbl  = (ud_cfg.get('table') or '').strip()
            ud_join = (ud_cfg.get('join_col') or '').strip()
            # Detect which email columns the view exposes — soft-coded via
            # the same TIMESHEET_USER_DETAILS_COLUMNS list (entries like
            # "OfficeEmail:office_email" map source → alias). We only need
            # the source side here.
            ud_emails: list[str] = []
            for raw in (ud_cfg.get('columns') or []):
                src = (raw.split(':', 1)[0] if isinstance(raw, str) else '').strip()
                low = src.lower()
                if 'email' in low and src not in ud_emails:
                    ud_emails.append(src)
            if ud_cfg.get('enabled') and ud_tbl and ud_join and ud_emails:
                conds = ' OR '.join(f'[{_safe(c)}] = %s' for c in ud_emails)
                sql = (
                    f'SELECT DISTINCT TOP {max(1, _USER_NAME_RESOLVE_MAX_HITS + 1)} '
                    f'[{_safe(ud_join)}] AS code FROM {ud_tbl} WHERE {conds}'
                )
                params = tuple(str(email).strip() for _ in ud_emails)
                with connect() as cur:
                    cur.execute(sql, params)
                    ud_hits = rows_to_dicts(cur, cur.fetchall())
                ud_hits = [h for h in ud_hits if h.get('code')]
                if 0 < len(ud_hits) <= _USER_NAME_RESOLVE_MAX_HITS:
                    chosen_hits = [{'code': h['code'], 'name': ''} for h in ud_hits]
                    chosen_strategy = 'user_details_email'
                    chosen_tokens = [str(email).strip()]
        except Exception as exc:
            logger.warning('Timesheet user-details email bridge failed: %s', exc)

    for label, sources in strategies:
        if chosen_hits:
            break
        tokens = _name_tokens(*sources)
        if not tokens:
            continue
        hits = _run_match(tokens)
        # Refuse to guess when the strategy is too ambiguous, but keep trying
        # narrower strategies — earlier strategies are broader by design.
        if 0 < len(hits) <= _USER_NAME_RESOLVE_MAX_HITS:
            chosen_hits = hits
            chosen_strategy = label
            chosen_tokens = tokens
            break

    if not chosen_hits:
        if cache is not None:
            try: cache.set(cache_key, [], _USER_NAME_RESOLVE_TTL_SEC)
            except Exception: pass
        return set()

    resolved = {_norm_key(h.get('code')) for h in chosen_hits if h.get('code')}
    resolved.discard('')
    logger.info(
        'Timesheet name-resolver[%s]: tokens=%s → biometric UserIDs=%s (%s)',
        chosen_strategy, chosen_tokens, sorted(resolved),
        [(h.get('code'), h.get('name')) for h in chosen_hits],
    )
    if cache is not None:
        try: cache.set(cache_key, list(resolved), _USER_NAME_RESOLVE_TTL_SEC)
        except Exception: pass
    return resolved


def _resolve_user_aliases(employee_code: Optional[str], email: Optional[str]) -> tuple[set[str], set[str]]:
    """Return ({normalised emails}, {normalised employee codes}) including the
    primary identifiers AND any alternates discovered on the RAD AI
    UserProfile (so a user who logs into RAD AI with one email but is mapped
    to a different email in the biometric DB still resolves). Also folds in
    biometric UserIDs resolved by fuzzy [UserName] match when enabled."""
    emails: set[str] = set()
    codes: set[str] = set()
    if email:
        emails.add(_norm_key(email))
    if employee_code:
        codes.add(_norm_key(employee_code))
    resolved_profile = None
    try:
        from apps.rbac.models import UserProfile
        qs = UserProfile.objects.select_related('user').filter(is_deleted=False)
        cond = None
        from django.db.models import Q
        if email:
            cond = Q(user__email__iexact=str(email).strip())
        if employee_code:
            c2 = Q(employee_id__iexact=str(employee_code).strip())
            cond = c2 if cond is None else (cond | c2)
        if cond is not None:
            for p in qs.filter(cond):
                resolved_profile = resolved_profile or p
                if p.user and p.user.email:
                    emails.add(_norm_key(p.user.email))
                if p.employee_id:
                    codes.add(_norm_key(p.employee_id))
    except Exception:
        # Never let RAD AI lookup failure break biometric reporting
        pass

    # Augment with biometric UserIDs discovered via fuzzy name match.
    try:
        codes |= _resolve_biometric_user_ids(
            profile=resolved_profile, email=email, employee_code=employee_code,
        )
    except Exception as exc:
        logger.warning('Timesheet biometric UserID resolver failed: %s', exc)

    emails.discard('')
    codes.discard('')
    return emails, codes

# ─────────────────────────────────────────────────────────────────────────────
# Schema helpers
# ─────────────────────────────────────────────────────────────────────────────
def _table() -> str:
    """Return the bracket-quoted [schema].[table] identifier."""
    raw = (ts_config.SCHEMA['table'] or '').strip()
    if not raw:
        raise RuntimeError('TIMESHEET_TABLE is not configured.')
    if '.' in raw:
        schema, tbl = raw.split('.', 1)
    else:
        schema, tbl = 'dbo', raw
    return f'[{_safe(schema)}].[{_safe(tbl)}]'


def _col(key: str) -> str:
    name = ts_config.SCHEMA['columns'].get(key, '')
    return f'[{_safe(name)}]' if name else ''


def _opt_select(key: str, alias: str, *, prefix: str = '', agg: str | None = None) -> str:
    """Soft-coded SELECT-fragment for an optional column.

    Returns ``'<prefix><col> AS alias,'`` (or ``'<agg>(<prefix><col>) AS alias,'``)
    when the column is configured, else an empty string. Lets any timesheet
    schema work without code changes — drop the env var and the field is gone.
    """
    col = _col(key)
    if not col:
        return ''
    expr = f'{prefix}{col}' if not agg else f'{agg}({prefix}{col})'
    return f'{expr} AS {alias}, '


def _safe(ident: str) -> str:
    return ''.join(c for c in (ident or '') if c.isalnum() or c == '_')


def _variant() -> str:
    return ts_config._detect_schema_variant()


# ─────────────────────────────────────────────────────────────────────────────
# Date helpers
# ─────────────────────────────────────────────────────────────────────────────
def _parse_date(s: Optional[str], default: Optional[dt.date] = None) -> dt.date:
    if not s:
        return default or dt.date.today()
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return default or dt.date.today()


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


# ─────────────────────────────────────────────────────────────────────────────
# RAD AI user enrichment (email-first, employee_id fallback)
# ─────────────────────────────────────────────────────────────────────────────
def _parse_user_details_columns() -> list[tuple[str, str]]:
    """Resolve TIMESHEET_USER_DETAILS_COLUMNS env into [(source, alias), ...].
    Accepts plain names or 'Source:alias' entries. Lower-snake-cased keys
    keep the JSON payload predictable for the frontend."""
    out: list[tuple[str, str]] = []
    for entry in ts_config.USER_DETAILS.get('columns', []) or []:
        s = str(entry).strip()
        if not s:
            continue
        if ':' in s:
            src, alias = s.split(':', 1)
            src, alias = src.strip(), alias.strip()
        else:
            src = s
            # 'Card1' → 'card1', 'OfficeEmail' → 'office_email'
            alias = ''.join(
                ('_' + c.lower()) if c.isupper() and i > 0 else c.lower()
                for i, c in enumerate(s)
            )
        if src and alias:
            out.append((src, alias))
    return out


def _safe_ident(name: str) -> str:
    """Allow only ASCII letters/digits/underscore — defence-in-depth so an
    env-var typo can't introduce SQL injection in column/table names."""
    return ''.join(ch for ch in str(name or '') if ch.isalnum() or ch == '_')


def _enrich_with_user_details(rows: list[dict]) -> list[dict]:
    """Pull the soft-coded columns (default Card1) from Mx_VEW_UserDetails
    and merge them into each attendance row by employee_code (UserID).

    Safe-by-default:
      - no-op when the feature is disabled or no columns configured
      - never raises on SQL errors — attendance reporting must keep working
        even if the user-details view is missing or unreachable
      - cached per (sorted-userids) for USER_DETAILS.cache_ttl seconds to
        keep repeated daily-report calls cheap
    """
    cfg = ts_config.USER_DETAILS
    if not cfg.get('enabled') or not rows:
        return rows
    cols = _parse_user_details_columns()
    if not cols:
        return rows

    codes = sorted({str(r.get('employee_code') or '').strip()
                    for r in rows if r.get('employee_code')})
    if not codes:
        return rows

    table = _safe_ident(cfg['table'].split('.', 1)[-1])
    schema = _safe_ident(cfg['table'].split('.', 1)[0]) if '.' in cfg['table'] else 'dbo'
    join_col = _safe_ident(cfg['join_col'])
    safe_cols = [(_safe_ident(src), alias) for src, alias in cols]
    safe_cols = [(s, a) for s, a in safe_cols if s]  # drop typos
    if not (table and join_col and safe_cols):
        return rows

    cache_key = (
        f"ts:user_details:v1:{table}:{join_col}:"
        f"{','.join(a for _, a in safe_cols)}:{hash(tuple(codes)) & 0xFFFFFFFF:x}"
    )
    cached = None
    try:
        from django.core.cache import cache
        cached = cache.get(cache_key)
    except Exception:
        cache = None  # type: ignore

    if cached is None:
        select_list = ', '.join([f'[{join_col}]'] + [f'[{s}]' for s, _ in safe_cols])
        placeholders = ', '.join(['%s'] * len(codes))
        sql = (
            f'SELECT DISTINCT {select_list} '
            f'FROM [{schema}].[{table}] '
            f'WHERE [{join_col}] IN ({placeholders})'
        )
        cached = {}
        try:
            from .sqlserver import connect
            with connect() as cur:
                cur.execute(sql, tuple(codes))
                for row in cur.fetchall():
                    key = str(row.get(join_col) or '').strip()
                    if not key:
                        continue
                    cached[key] = {alias: row.get(src) for src, alias in safe_cols}
        except Exception as exc:
            logger.info('[timesheet] user-details enrichment skipped: %s', exc)
            cached = {}
        try:
            if cache is not None:
                cache.set(cache_key, cached, cfg.get('cache_ttl', 600))
        except Exception:
            pass

    for r in rows:
        details = cached.get(str(r.get('employee_code') or '').strip()) or {}
        for _, alias in safe_cols:
            # Don't overwrite a value already populated upstream
            if alias not in r or r.get(alias) in (None, ''):
                r[alias] = details.get(alias) or ''
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# RAD AI user enrichment (email-first, employee_id fallback)
# ─────────────────────────────────────────────────────────────────────────────
def _backfill_email_from_matrix_name(rows: list[dict]) -> list[dict]:
    """Last-resort email resolver. For rows that still have no `radai_email`
    after the primary email/employee_id match, tokenise the best available
    name source and search RAD AI `UserProfile`s for a name-token overlap.

    Name source priority (first non-empty wins):
      matrix_full_name → from Mx_VEW_UserDetails enrichment (SQL backend)
      employee_name    → on every TimesheetEvent / attendance row (both)
      name             → legacy alias used by older code paths

    Soft-coded via `ts_config.NAME_BACKFILL`:
      enabled        — master toggle
      min_token_hits — minimum number of name tokens that must overlap
      max_candidates — cap on `__icontains` query results per token to
                       prevent runaway DB scans
    """
    cfg = getattr(ts_config, 'NAME_BACKFILL', None) or {}
    if not cfg.get('enabled', False) or not rows:
        return rows

    needs: list[tuple[dict, list[str]]] = []
    for r in rows:
        if r.get('radai_email'):
            continue
        full = (
            r.get('matrix_full_name')
            or r.get('employee_name')
            or r.get('name')
            or ''
        )
        toks = _name_tokens(full)
        if toks:
            needs.append((r, toks))
    if not needs:
        return rows

    try:
        from apps.rbac.models import UserProfile
        from django.db.models import Q
    except Exception:
        return rows

    min_hits = max(1, int(cfg.get('min_token_hits', 2)))
    cap = max(1, int(cfg.get('max_candidates', 8)))

    # Aggregate every distinct token across all needy rows so we issue
    # ONE candidate query per token instead of N per-row queries.
    token_to_rows: dict[str, list[tuple[dict, set[str]]]] = {}
    for r, toks in needs:
        tset = set(toks)
        for t in tset:
            token_to_rows.setdefault(t, []).append((r, tset))

    candidate_cache: dict[str, list] = {}
    for token in token_to_rows.keys():
        if len(token) < 3:
            continue
        try:
            qs = UserProfile.objects.select_related('user').filter(
                is_deleted=False
            ).filter(
                Q(user__first_name__icontains=token)
                | Q(user__last_name__icontains=token)
                | Q(user__email__icontains=token)
                | Q(user__username__icontains=token)
            )[:cap]
            candidate_cache[token] = list(qs)
        except Exception as exc:
            logger.info('[timesheet] name-backfill token=%r skipped: %s', token, exc)
            candidate_cache[token] = []

    for r, toks in needs:
        tset = set(toks)
        best_hits, best_profile = 0, None
        seen_ids: set = set()
        for token in tset:
            for profile in candidate_cache.get(token, []):
                if profile.user_id in seen_ids:
                    continue
                seen_ids.add(profile.user_id)
                u = profile.user
                profile_tokens = set(_name_tokens(
                    u.first_name, u.last_name, u.email, u.username
                ))
                hits = len(tset & profile_tokens)
                if hits > best_hits:
                    best_hits, best_profile = hits, profile
        if best_profile and best_hits >= min_hits:
            u = best_profile.user
            r['radai_user_id'] = str(u.id)
            r['radai_email'] = u.email
            r['radai_full_name'] = f'{u.first_name or ""} {u.last_name or ""}'.strip() or r.get('matrix_full_name')
            r['radai_department'] = best_profile.department or r.get('department') or ''
            r['radai_job_title'] = best_profile.job_title or r.get('radai_job_title') or ''
            r['matched_by'] = 'matrix_name'
    return rows


def _enrich_with_rad_users(rows: list[dict]) -> list[dict]:
    """Add `radai_user_id`, `radai_email`, `radai_full_name` to each row by
    matching on email (primary) then employee_id (fallback)."""
    if not rows:
        return rows

    emails = {(r.get('email') or '').strip().lower() for r in rows if r.get('email')}
    emp_codes = {str(r.get('employee_code') or '').strip() for r in rows if r.get('employee_code')}

    # Bulk lookup — never N+1
    from apps.rbac.models import UserProfile

    profiles_by_email = {}
    if emails:
        for p in UserProfile.objects.select_related('user').filter(
            user__email__in=list(emails), is_deleted=False
        ):
            profiles_by_email[(p.user.email or '').lower()] = p

    profiles_by_emp_id = {}
    if emp_codes:
        for p in UserProfile.objects.select_related('user').filter(
            employee_id__in=list(emp_codes), is_deleted=False
        ):
            profiles_by_emp_id[str(p.employee_id)] = p

    for r in rows:
        email_key = (r.get('email') or '').strip().lower()
        emp_key = str(r.get('employee_code') or '').strip()
        profile = profiles_by_email.get(email_key) or profiles_by_emp_id.get(emp_key)
        if profile:
            r['radai_user_id'] = str(profile.user.id)
            r['radai_email'] = profile.user.email
            r['radai_full_name'] = f'{profile.user.first_name or ""} {profile.user.last_name or ""}'.strip()
            r['radai_department'] = profile.department or r.get('department') or ''
            r['radai_job_title'] = profile.job_title or ''
            r['matched_by'] = 'email' if email_key and email_key in profiles_by_email else 'employee_id'
        else:
            r['radai_user_id'] = None
            r['radai_email'] = None
            r['radai_full_name'] = None
            r['radai_department'] = r.get('department') or ''
            r['radai_job_title'] = ''
            r['matched_by'] = None
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Live status — who's currently in the office
# ─────────────────────────────────────────────────────────────────────────────
@cache_service.cached_timesheet_query('live', cache_service.CACHE_LIVE_TTL) if _CACHE_AVAILABLE else lambda f: f
def live_status() -> dict:
    """Return last-known punch per user for today + a roll-up of who's IN/OUT.
    
    CACHED: 15 seconds (default) via Redis + background refresh.
    Stale data served if SQL Server unreachable (circuit breaker).
    
    Uses soft-coded rolling time window (TIMESHEET_LIVE_LOOKBACK_HOURS) instead
    of strict today-only filter to handle timezone differences gracefully.
    """
    # Soft-coded rolling window (default 20h covers UAE office day + UTC offset)
    lookback_hours = int(ts_config.RULES.get('live_lookback_hours', 20))
    
    variant = _variant()
    if variant == 'unknown':
        return {'rows': [], 'summary': _empty_summary(), 'variant': variant}

    if variant == 'event_stream':
        sql = (
            f"SELECT a.{_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email', prefix='a.')}"
            f"       a.{_col('employee_name')} AS name, "
            f"       {('a.' + _col('department') + ' AS department,') if _col('department') else ''}"
            f"       a.{_col('punch_time')} AS punch_time, "
            f"       a.{_col('punch_type')} AS punch_type "
            f"FROM {_table()} a "
            f"INNER JOIN ("
            f"    SELECT {_col('employee_code')} AS ec, MAX({_col('punch_time')}) AS mx "
            f"    FROM {_table()} "
            f"    WHERE {_col('punch_time')} >= DATEADD(HOUR, %s, GETDATE()) "
            f"    GROUP BY {_col('employee_code')}"
            f") last ON a.{_col('employee_code')} = last.ec "
            f"      AND a.{_col('punch_time')} = last.mx "
            f"ORDER BY a.{_col('punch_time')} DESC"
        )
        params = (-lookback_hours,)
    else:  # two_column
        today = dt.date.today()
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email')}"
            f"       {_col('employee_name')} AS name, "
            f"       {(_col('department') + ' AS department,') if _col('department') else ''}"
            f"       {_col('login_time')} AS login_time, "
            f"       {_col('logout_time')} AS logout_time, "
            f"       {_col('date')} AS work_date "
            f"FROM {_table()} "
            f"WHERE CAST({_col('date')} AS DATE) = %s "
            f"ORDER BY {_col('login_time')} DESC"
        )
        params = (today,)

    with connect() as cur:
        cur.execute(sql, params)
        rows = rows_to_dicts(cur, cur.fetchall())

    rows = _enrich_with_rad_users(rows)
    rows = _enrich_with_user_details(rows)
    rows = _backfill_email_from_matrix_name(rows)

    # Compute IN/OUT/late counters
    in_value = (ts_config.SCHEMA['columns']['in_value'] or 'IN').upper()
    summary = _empty_summary()
    for r in rows:
        if variant == 'event_stream':
            is_in = str(r.get('punch_type', '')).upper() == in_value
        else:
            is_in = bool(r.get('login_time')) and not r.get('logout_time')
        r['is_in'] = is_in
        r['is_late'] = _is_late(r)
        if is_in:
            summary['currently_in'] += 1
        else:
            summary['currently_out'] += 1
        if r['is_late']:
            summary['late_today'] += 1
    summary['total_seen_today'] = len(rows)
    summary['matched_to_radai'] = sum(1 for r in rows if r.get('radai_user_id'))
    
    # Calculate window cutoff for frontend diagnostic display
    cutoff = dt.datetime.now() - dt.timedelta(hours=lookback_hours)
    
    return {
        'rows': rows,
        'summary': summary,
        'variant': variant,
        'as_of': dt.datetime.now().isoformat(),
        # Soft-coded: expose the window so the frontend can show a helpful
        # diagnostic like "Showing punches from the last N h" instead of
        # a confusing empty table when no data is in the rolling window.
        'lookback_hours': lookback_hours,
        'window_from': cutoff.isoformat(),
    }


def _empty_summary() -> dict:
    return {
        'total_seen_today': 0,
        'currently_in': 0,
        'currently_out': 0,
        'late_today': 0,
        'matched_to_radai': 0,
    }


def _is_late(row: dict) -> bool:
    """Soft-coded late-arrival detection."""
    expected_h = ts_config.RULES['expected_login_hour']
    threshold = ts_config.RULES['late_threshold_min']
    first_in = row.get('punch_time') or row.get('login_time')
    if not first_in:
        return False
    if isinstance(first_in, str):
        try:
            first_in = dt.datetime.fromisoformat(first_in)
        except ValueError:
            return False
    expected = first_in.replace(hour=expected_h, minute=0, second=0, microsecond=0)
    return first_in > expected + dt.timedelta(minutes=threshold)


# ─────────────────────────────────────────────────────────────────────────────
# Daily report — per user hours for a single date
# ─────────────────────────────────────────────────────────────────────────────
@cache_service.cached_timesheet_query('daily', cache_service.CACHE_DAILY_TTL) if _CACHE_AVAILABLE else lambda f: f
def daily_report(date: Optional[str] = None) -> dict:
    """Return per-user hours for a single date.
    
    CACHED: 5 minutes (default) via Redis + background refresh.
    Historical dates cached longer as they don't change.
    """
    day = _parse_date(date)
    variant = _variant()
    if variant == 'unknown':
        return {'date': day.isoformat(), 'rows': [], 'variant': variant}

    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email', agg='MAX')}"
            f"       MAX({_col('employee_name')}) AS name, "
            f"       {('MAX(' + _col('department') + ') AS department,') if _col('department') else ''}"
            f"       MIN({_col('punch_time')}) AS first_in, "
            f"       MAX({_col('punch_time')}) AS last_out, "
            f"       COUNT(*) AS punch_count "
            f"FROM {_table()} "
            f"WHERE CAST({_col('punch_time')} AS DATE) = %s "
            f"GROUP BY {_col('employee_code')} "
            f"ORDER BY first_in"
        )
    else:
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email')}"
            f"       {_col('employee_name')} AS name, "
            f"       {(_col('department') + ' AS department,') if _col('department') else ''}"
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out "
            f"FROM {_table()} "
            f"WHERE CAST({_col('date')} AS DATE) = %s "
            f"ORDER BY {_col('login_time')}"
        )

    with connect() as cur:
        cur.execute(sql, (day,))
        rows = rows_to_dicts(cur, cur.fetchall())

    for r in rows:
        r['hours_worked'] = _hours_between(r.get('first_in'), r.get('last_out'))
        r['is_late'] = _is_late({'punch_time': r.get('first_in')})
        r['is_full_day'] = (r['hours_worked'] or 0) >= ts_config.RULES['full_day_hours']

    rows = _enrich_with_rad_users(rows)
    rows = _enrich_with_user_details(rows)
    rows = _backfill_email_from_matrix_name(rows)
    return {'date': day.isoformat(), 'rows': rows, 'variant': variant}


# ─────────────────────────────────────────────────────────────────────────────
# Monthly report — per user rollup for a month
# ─────────────────────────────────────────────────────────────────────────────
@cache_service.cached_timesheet_query('monthly', cache_service.CACHE_MONTHLY_TTL) if _CACHE_AVAILABLE else lambda f: f
def monthly_report(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    """Return per-user monthly rollup with days/hours/late stats.
    
    CACHED: 1 hour (default) via Redis + background refresh.
    Current month refreshed more frequently, historical months cached longer.
    """
    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)
    start, end = _month_range(y, m)
    variant = _variant()
    if variant == 'unknown':
        return {'year': y, 'month': m, 'rows': [], 'variant': variant}

    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email', agg='MAX')}"
            f"       MAX({_col('employee_name')}) AS name, "
            f"       {('MAX(' + _col('department') + ') AS department,') if _col('department') else ''}"
            f"       CAST({_col('punch_time')} AS DATE) AS work_date, "
            f"       MIN({_col('punch_time')}) AS first_in, "
            f"       MAX({_col('punch_time')}) AS last_out "
            f"FROM {_table()} "
            f"WHERE {_col('punch_time')} >= %s AND {_col('punch_time')} < DATEADD(DAY, 1, %s) "
            f"GROUP BY {_col('employee_code')}, CAST({_col('punch_time')} AS DATE) "
            f"ORDER BY employee_code, work_date"
        )
    else:
        sql = (
            f"SELECT {_col('employee_code')} AS employee_code, "
            f"       {_opt_select('employee_email', 'email')}"
            f"       {_col('employee_name')} AS name, "
            f"       {(_col('department') + ' AS department,') if _col('department') else ''}"
            f"       CAST({_col('date')} AS DATE) AS work_date, "
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out "
            f"FROM {_table()} "
            f"WHERE {_col('date')} >= %s AND {_col('date')} <= %s "
            f"ORDER BY {_col('employee_code')}, {_col('date')}"
        )

    with connect() as cur:
        cur.execute(sql, (start, end))
        raw = rows_to_dicts(cur, cur.fetchall())

    # Roll up per employee
    # Soft-coded: strip whitespace from employee_code so the same employee
    # stored as '22393', '22393 ', ' 22393' all collapse to one row.
    by_emp: dict[str, dict] = {}
    for r in raw:
        key = str(r.get('employee_code') or '').strip()  # <- dedup key
        slot = by_emp.setdefault(key, {
            'employee_code': key,
            'email': r.get('email'),
            'name': r.get('name'),
            'department': r.get('department'),
            'days_present': 0,
            'full_days': 0,
            'half_days': 0,
            'late_arrivals': 0,
            'total_hours': 0.0,
            'days_detail': [],
        })
        # Apply max_daily_hours cap (soft-coded, default 9.0h)
        raw_hours = _hours_between(r.get('first_in'), r.get('last_out')) or 0
        max_daily_hrs = float(ts_config.RULES.get('max_daily_hours', 9.0))
        hours = min(raw_hours, max_daily_hrs)
        
        slot['days_present'] += 1
        slot['total_hours'] += hours
        if hours >= ts_config.RULES['full_day_hours']:
            slot['full_days'] += 1
        else:
            slot['half_days'] += 1
        if _is_late({'punch_time': r.get('first_in')}):
            slot['late_arrivals'] += 1
        slot['days_detail'].append({
            'date': str(r.get('work_date')),
            'first_in': str(r.get('first_in')) if r.get('first_in') else None,
            'last_out': str(r.get('last_out')) if r.get('last_out') else None,
            'hours': round(hours, 2),
        })

    rows = list(by_emp.values())
    for slot in rows:
        slot['total_hours'] = round(slot['total_hours'], 2)
        slot['avg_hours_per_day'] = (
            round(slot['total_hours'] / slot['days_present'], 2) if slot['days_present'] else 0
        )
    rows = _enrich_with_rad_users(rows)
    rows = _enrich_with_user_details(rows)
    rows = _backfill_email_from_matrix_name(rows)
    rows.sort(key=lambda x: (x.get('radai_full_name') or x.get('name') or ''))
    return {
        'year': y,
        'month': m,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'working_days_in_month': _working_days(start, end),
        'rows': rows,
        'variant': variant,
    }


def _working_days(start: dt.date, end: dt.date) -> int:
    workdays = {d[:3] for d in ts_config.RULES['working_days']}
    name_map = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    count = 0
    cur = start
    while cur <= end:
        if name_map[cur.weekday()] in workdays:
            count += 1
        cur += dt.timedelta(days=1)
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Per-user drill-down
# ─────────────────────────────────────────────────────────────────────────────
def _fallback_user_scan(variant: str,
                        alias_emails: set[str],
                        alias_codes: set[str],
                        start: dt.date,
                        end: dt.date) -> list[dict]:
    """Scan the biometric table for the given date window with no WHERE clause
    and filter in Python using the supplied normalised aliases. Used as the
    last-resort matcher when the strict (even normalised) SQL `=` returns
    nothing — mirrors the way `live_status` succeeds via `_enrich_with_rad_users`.
    """
    cols = ts_config.SCHEMA['columns']
    has_email = bool(cols.get('employee_email'))
    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('punch_time')} AS punch_time, "
            f"       {_col('punch_type')} AS punch_type, "
            f"       {_col('employee_code')} AS employee_code"
            f"{(', ' + _col('employee_email') + ' AS email') if has_email else ''} "
            f"FROM {_table()} "
            f"WHERE {_col('punch_time')} >= %s "
            f"  AND {_col('punch_time')} < DATEADD(DAY, 1, %s) "
            f"ORDER BY {_col('punch_time')}"
        )
    else:
        sql = (
            f"SELECT {_col('date')} AS work_date, "
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out, "
            f"       {_col('employee_code')} AS employee_code"
            f"{(', ' + _col('employee_email') + ' AS email') if has_email else ''} "
            f"FROM {_table()} "
            f"WHERE {_col('date')} >= %s AND {_col('date')} <= %s "
            f"ORDER BY {_col('date')}"
        )
    try:
        with connect() as cur:
            cur.execute(sql, (start, end))
            scanned = rows_to_dicts(cur, cur.fetchall())
    except Exception as exc:
        logger.warning('Timesheet user fallback scan failed: %s', exc)
        return []

    matched = [
        r for r in scanned
        if _norm_key(r.get('email')) in alias_emails
        or _norm_key(r.get('employee_code')) in alias_codes
    ]
    if matched:
        logger.info(
            'Timesheet user fallback matched %d/%d rows via aliases (emails=%s codes=%s)',
            len(matched), len(scanned), alias_emails, alias_codes,
        )
    return matched


def lookup_by_code(code: str) -> Optional[dict]:
    """Reverse lookup: biometric employee_code → {employee_code, employee_name,
    employee_email}. Returns None if not found. Used by the HR Employees page
    so a user can type a badge number and the page jumps to the matching RAD
    AI record. Email is only returned when the schema exposes an email column.
    """
    code = (code or '').strip()
    if not code:
        return None
    tbl = _table()
    code_col = _col('employee_code')
    name_col = _col('employee_name')
    email_col = _col('employee_email')  # '' when schema has no email column
    select_extra = f', {email_col} AS email' if email_col else ''
    sql = (
        f"SELECT TOP 1 {code_col} AS code, {name_col} AS name{select_extra} "
        f"FROM {tbl} WHERE {code_col} = %s"
    )
    from .sqlserver import connect
    with connect() as cur:
        cur.execute(sql, (code,))
        r = cur.fetchone()
    if not r:
        return None
    return {
        'employee_code': r.get('code') or '',
        'employee_name': r.get('name') or '',
        'employee_email': (r.get('email') or '') if email_col else '',
    }


def user_history(employee_code: Optional[str] = None,
                 email: Optional[str] = None,
                 from_date: Optional[str] = None,
                 to_date: Optional[str] = None,
                 include_punches: bool = False,
                 with_trace: bool = False) -> dict:
    today = dt.date.today()
    start = _parse_date(from_date, today - dt.timedelta(days=30))
    end = _parse_date(to_date, today)
    variant = _variant()
    if variant == 'unknown':
        return {'rows': [], 'variant': variant}
    if not (employee_code or email):
        return {'rows': [], 'error': 'employee_code or email required'}

    cols = ts_config.SCHEMA['columns']
    where = []
    params: list = []
    # ── Normalised (case-insensitive + trim) matching ─────────────────────
    # Aligns the per-user filter with `_enrich_with_rad_users`, which keys
    # off `(email or '').strip().lower()` and `str(emp_code).strip()`. Without
    # this, SQL Server's case-sensitive default collation (and routine
    # trailing spaces in [EmpEmail]) make the strict `=` match fail even
    # though the same record shows up in /live/.
    if _USER_MATCH_NORMALISE:
        if employee_code:
            where.append(f"LTRIM(RTRIM({_col('employee_code')})) = LTRIM(RTRIM(%s))")
            params.append(str(employee_code).strip())
        if email and cols['employee_email']:
            where.append(f"LOWER(LTRIM(RTRIM({_col('employee_email')}))) = LOWER(LTRIM(RTRIM(%s)))")
            params.append(str(email).strip().lower())
    else:
        if employee_code:
            where.append(f"{_col('employee_code')} = %s")
            params.append(employee_code)
        if email and cols['employee_email']:
            where.append(f"{_col('employee_email')} = %s")
            params.append(email)
    # OR-match (either identifier resolves the record). Mirrors the email-first /
    # employee_id-fallback strategy used by `_enrich_with_rad_users`.
    where_sql = ' OR '.join(where) if where else '1=1'

    if variant == 'event_stream':
        sql = (
            f"SELECT {_col('punch_time')} AS punch_time, "
            f"       {_col('punch_type')} AS punch_type, "
            f"       {_col('employee_code')} AS employee_code "
            f"FROM {_table()} "
            f"WHERE ({where_sql}) "
            f"  AND {_col('punch_time')} >= %s "
            f"  AND {_col('punch_time')} < DATEADD(DAY, 1, %s) "
            f"ORDER BY {_col('punch_time')}"
        )
        params.extend([start, end])
    else:
        sql = (
            f"SELECT {_col('date')} AS work_date, "
            f"       {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out, "
            f"       {_col('employee_code')} AS employee_code "
            f"FROM {_table()} "
            f"WHERE ({where_sql}) "
            f"  AND {_col('date')} >= %s AND {_col('date')} <= %s "
            f"ORDER BY {_col('date')}"
        )
        params.extend([start, end])

    with connect() as cur:
        cur.execute(sql, tuple(params))
        rows = rows_to_dicts(cur, cur.fetchall())

    # ── Fallback: rescan the date window without WHERE and filter in Python.
    # Triggered only when the normalised SQL filter still returned nothing —
    # covers edge cases like non-ASCII whitespace in [EmpEmail], Unicode
    # NFC/NFD differences, or users whose RAD AI email differs from the
    # biometric DB email (alternate aliases pulled from UserProfile).
    if not rows and _USER_MATCH_PY_FALLBACK and (employee_code or email):
        alias_emails, alias_codes = _resolve_user_aliases(employee_code, email)
        if alias_emails or alias_codes:
            rows = _fallback_user_scan(variant, alias_emails, alias_codes, start, end)

    if variant == 'event_stream':
        # collapse to per-day in/out pairs
        per_day = defaultdict(lambda: {'first_in': None, 'last_out': None, 'punches': 0})
        for r in rows:
            t = r.get('punch_time')
            if isinstance(t, str):
                try:
                    t = dt.datetime.fromisoformat(t)
                except ValueError:
                    continue
            d = t.date().isoformat()
            slot = per_day[d]
            if slot['first_in'] is None or t < slot['first_in']:
                slot['first_in'] = t
            if slot['last_out'] is None or t > slot['last_out']:
                slot['last_out'] = t
            slot['punches'] += 1
        daily_rows = [
            {
                'date': d,
                'first_in': v['first_in'].isoformat() if v['first_in'] else None,
                'last_out': v['last_out'].isoformat() if v['last_out'] else None,
                # Cap hours at configured maximum (default: 9 hours)
                'hours': min(_hours_between(v['first_in'], v['last_out']) or 0, 
                            float(ts_config.RULES.get('max_daily_hours', 9.0))),
                'punches': v['punches'],
            }
            for d, v in sorted(per_day.items())
        ]
    else:
        daily_rows = [
            {
                'date': str(r.get('work_date')),
                'first_in': str(r.get('first_in')) if r.get('first_in') else None,
                'last_out': str(r.get('last_out')) if r.get('last_out') else None,
                # Cap hours at configured maximum (default: 9 hours)
                'hours': min(_hours_between(r.get('first_in'), r.get('last_out')) or 0,
                            float(ts_config.RULES.get('max_daily_hours', 9.0))),
                'punches': None,
            }
            for r in rows
        ]
    # ── Consolidated summary + monthly breakdown + optional raw punches
    full_day_hours = float(ts_config.RULES.get('full_day_hours', 8.0))
    total_hours    = sum((r['hours'] or 0) for r in daily_rows)
    total_punches  = sum((r['punches'] or 0) for r in daily_rows)
    days_present   = len(daily_rows)
    days_full      = sum(1 for r in daily_rows if (r['hours'] or 0) >= full_day_hours)
    days_partial   = days_present - days_full

    def _avg_hhmm(timestamps):
        valid = [t for t in timestamps if t]
        if not valid:
            return None
        secs = [(t.hour * 3600 + t.minute * 60 + t.second) for t in valid]
        avg = sum(secs) // len(secs)
        return f'{avg // 3600:02d}:{(avg % 3600) // 60:02d}'

    first_ins, last_outs = [], []
    if variant == 'event_stream':
        for v in per_day.values():
            if v['first_in']:
                first_ins.append(v['first_in'])
            if v['last_out'] and v['last_out'] != v['first_in']:
                last_outs.append(v['last_out'])

    summary = {
        'total_hours':       round(total_hours, 2),
        'total_punches':     total_punches,
        'days_present':      days_present,
        'days_full':         days_full,
        'days_partial':      days_partial,
        'avg_hours_per_day': round(total_hours / days_present, 2) if days_present else 0,
        'avg_first_in':      _avg_hhmm(first_ins),
        'avg_last_out':      _avg_hhmm(last_outs),
        'range_days':        (end - start).days + 1,
    }

    monthly_buckets: dict[str, dict] = defaultdict(lambda: {'hours': 0.0, 'days': 0, 'punches': 0})
    for r in daily_rows:
        ym = (r['date'] or '')[:7]
        if not ym:
            continue
        b = monthly_buckets[ym]
        b['hours']   += r['hours'] or 0
        b['days']    += 1
        b['punches'] += r['punches'] or 0
    monthly_breakdown = [
        {
            'month':        ym,
            'hours':        round(b['hours'], 2),
            'days_present': b['days'],
            'punches':      b['punches'],
            'avg_per_day':  round(b['hours'] / b['days'], 2) if b['days'] else 0,
        }
        for ym, b in sorted(monthly_buckets.items())
    ]

    raw_punches = None
    if include_punches and variant == 'event_stream':
        raw_punches = []
        for r in rows:
            t = r.get('punch_time')
            if isinstance(t, str):
                try:
                    t = dt.datetime.fromisoformat(t)
                except ValueError:
                    continue
            raw_punches.append({
                'event_time': t.isoformat() if t else None,
                'event_type': r.get('punch_type'),
                'date':       t.date().isoformat() if t else None,
            })

    return {
        'employee_code':     employee_code,
        'email':             email,
        'from':              start.isoformat(),
        'to':                end.isoformat(),
        'rows':              daily_rows,
        'summary':           summary,
        'monthly_breakdown': monthly_breakdown,
        'punches':           raw_punches,
        'variant':           variant,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────────────────────────────────────
def _hours_between(start, end) -> float:
    if not start or not end:
        return 0.0
    if isinstance(start, str):
        try:
            start = dt.datetime.fromisoformat(start)
        except ValueError:
            return 0.0
    if isinstance(end, str):
        try:
            end = dt.datetime.fromisoformat(end)
        except ValueError:
            return 0.0
    delta = end - start
    return max(0.0, round(delta.total_seconds() / 3600, 2))


def _calculate_live_metrics(employee_code: str) -> dict:
    """Calculate detailed live metrics for a single employee.
    
    Returns first IN punch, last punch, hours today, punch counts (IN/OUT).
    Used by my_live_attendance self-service endpoint.
    
    Args:
        employee_code: Employee ID to query
        
    Returns:
        {
            'first_in': datetime,      # First IN punch time
            'last_punch': datetime,    # Absolute last punch time
            'hours_today': float,      # Total hours worked
            'punch_in_count': int,     # Number of IN punches
            'punch_out_count': int,    # Number of OUT punches
            'is_in': bool,             # Whether currently checked IN
            'is_late': bool,           # Late arrival detection
        }
    """
    variant = _variant()
    if variant != 'event_stream':
        # two_column variant: simpler logic
        today = dt.date.today()
        sql = (
            f"SELECT {_col('login_time')} AS first_in, "
            f"       {_col('logout_time')} AS last_out "
            f"FROM {_table()} "
            f"WHERE CAST({_col('date')} AS DATE) = %s "
            f"  AND {_col('employee_code')} = %s"
        )
        with connect() as cur:
            cur.execute(sql, (today, employee_code))
            row = rows_to_dicts(cur, cur.fetchone() or [])
        
        if not row:
            return {
                'first_in': None,
                'last_punch': None,
                'hours_today': 0.0,
                'punch_in_count': 0,
                'punch_out_count': 0,
                'is_in': False,
                'is_late': False,
            }
        
        first_in = row.get('first_in')
        last_out = row.get('last_out')
        hours = _hours_between(first_in, last_out) if first_in and last_out else 0.0
        
        return {
            'first_in': first_in,
            'last_punch': last_out or first_in,
            'hours_today': hours,
            'punch_in_count': 1 if first_in else 0,
            'punch_out_count': 1 if last_out else 0,
            'is_in': bool(first_in and not last_out),
            'is_late': _is_late({'punch_time': first_in}),
        }
    
    # event_stream variant: query all punches within rolling window
    lookback_hours = int(ts_config.RULES.get('live_lookback_hours', 20))
    in_value = (ts_config.SCHEMA['columns']['in_value'] or 'IN').upper()
    out_value = (ts_config.SCHEMA['columns']['out_value'] or 'OUT').upper()
    
    sql = (
        f"SELECT {_col('punch_time')} AS punch_time, "
        f"       {_col('punch_type')} AS punch_type "
        f"FROM {_table()} "
        f"WHERE {_col('employee_code')} = %s "
        f"  AND {_col('punch_time')} >= DATEADD(HOUR, %s, GETDATE()) "
        f"ORDER BY {_col('punch_time')} ASC"
    )
    
    with connect() as cur:
        cur.execute(sql, (employee_code, -lookback_hours))
        punches = rows_to_dicts(cur, cur.fetchall())
    
    if not punches:
        return {
            'first_in': None,
            'last_punch': None,
            'hours_today': 0.0,
            'punch_in_count': 0,
            'punch_out_count': 0,
            'is_in': False,
            'is_late': False,
        }
    
    # Find first IN punch
    first_in = None
    for p in punches:
        if str(p.get('punch_type', '')).upper() == in_value:
            first_in = p.get('punch_time')
            break
    
    # Count IN/OUT punches
    punch_in_count = sum(1 for p in punches if str(p.get('punch_type', '')).upper() == in_value)
    punch_out_count = sum(1 for p in punches if str(p.get('punch_type', '')).upper() == out_value)
    
    # Last punch (absolute)
    last_punch = punches[-1].get('punch_time')
    last_type = str(punches[-1].get('punch_type', '')).upper()
    is_in = (last_type == in_value)
    
    # Calculate hours worked (pair IN/OUT punches)
    # Simple algorithm: sum all (OUT[i] - IN[i]) durations
    hours_today = 0.0
    i = 0
    while i < len(punches):
        p = punches[i]
        if str(p.get('punch_type', '')).upper() == in_value:
            # Find next OUT
            next_out = None
            for j in range(i + 1, len(punches)):
                if str(punches[j].get('punch_type', '')).upper() == out_value:
                    next_out = punches[j].get('punch_time')
                    i = j  # Skip to the OUT punch
                    break
            
            if next_out:
                hours_today += _hours_between(p.get('punch_time'), next_out)
            else:
                # Still IN, calculate up to now
                hours_today += _hours_between(p.get('punch_time'), dt.datetime.now())
        i += 1
    
    # Apply max_daily_hours cap
    max_daily_hrs = float(ts_config.RULES.get('max_daily_hours', 9.0))
    hours_today = min(hours_today, max_daily_hrs)
    
    return {
        'first_in': first_in,
        'last_punch': last_punch,
        'hours_today': round(hours_today, 2),
        'punch_in_count': punch_in_count,
        'punch_out_count': punch_out_count,
        'is_in': is_in,
        'is_late': _is_late({'punch_time': first_in}) if first_in else False,
    }
