"""
Mirror-mode query services — produce the same JSON shapes as `services.py`
but reading from the Postgres `TimesheetEvent` table (populated by the
office-side sync agent) instead of the on-prem SQL Server.

Used when `TIMESHEET_DATA_SOURCE=mirror` (Railway/production). Same
employee-enrichment helper from `services.py` is reused so the frontend
never sees a different payload shape.
"""
from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict
from typing import Optional

from django.db.models import Max, Min, Q
from django.utils import timezone

from . import config as ts_config
from .models import TimesheetEvent, BiometricUserMaster, DailyAttendanceSummary
from .identity import norm_code as _norm_emp_code, norm_email as _norm_emp_email, norm_name as _norm_emp_name
from .services import (
    _enrich_with_rad_users, _backfill_email_from_matrix_name,
    _hours_between, _is_late, _empty_summary,
    _working_days, _parse_date,
    # Soft-coded helpers reused from the SQL Server backend so both data
    # sources behave identically when matching a RAD AI user → biometric row.
    _norm_key, _name_tokens,
    _USER_NAME_RESOLVE, _USER_NAME_RESOLVE_MIN_TOKS,
    _USER_NAME_RESOLVE_MAX_HITS, _USER_NAME_RESOLVE_TTL_SEC,
)


_VARIANT = 'mirror'


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


def _to_naive(t):
    """Strip tz to keep frontend formatting consistent with sqlserver path."""
    if t is None:
        return None
    if hasattr(t, 'tzinfo') and t.tzinfo is not None:
        return t.astimezone(timezone.get_current_timezone()).replace(tzinfo=None)
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded BiometricUserMaster enrichment (mirror backend)
# ─────────────────────────────────────────────────────────────────────────────
# Map model field → row payload key. Same alias scheme as the SQL Server
# `_enrich_with_user_details` so the frontend reads the same keys in either
# data source. Add a row here to expose a new column on production.
_USER_MASTER_ROW_FIELDS = (
    ('card1',          'card1'),
    ('card2',          'card2'),
    ('office_email',   'office_email'),
    ('personal_email', 'personal_email'),
    ('full_name',      'matrix_full_name'),
    ('designation',    'designation'),
    ('department',     'department_master'),
)

# Soft-coded list of BiometricUserMaster email columns checked when resolving
# a RAD AI user's email → biometric employee_code. Order matters — the first
# match wins. Add a column here (and to BiometricUserMaster) to bridge a new
# email source without changing resolver code.
_USER_MASTER_EMAIL_COLUMNS = ('office_email', 'personal_email')

# Soft-coded list of BiometricUserMaster *name* columns checked when the email
# bridge has no hit (e.g. the user is in the master under a record that lacks
# OfficeEmail/PersEmail but has a FullName). Each entry is searched with
# `__icontains` ANDed across tokens. Add a column here (and the field on
# BiometricUserMaster) to bridge a new name source — order matters; the first
# column with hits wins. Empty tuple disables the name bridge entirely.
_USER_MASTER_NAME_COLUMNS = ('full_name',)


def _enrich_from_user_master_mirror(rows: list[dict]) -> list[dict]:
    """Merge `BiometricUserMaster` columns (Card1, OfficeEmail, FullName …)
    into each attendance row by `employee_code`. Safe-by-default: if the
    table is empty (agent hasn't synced yet) the rows are returned unchanged
    with the new keys absent — the frontend already renders that as `—`."""
    if not rows:
        return rows
    codes = [str(r.get('employee_code') or '').strip()
             for r in rows if r.get('employee_code')]
    if not codes:
        return rows
    try:
        master = {
            m.employee_code: m
            for m in BiometricUserMaster.objects.filter(employee_code__in=codes)
        }
    except Exception:
        return rows
    if not master:
        return rows
    for r in rows:
        m = master.get(str(r.get('employee_code') or '').strip())
        if not m:
            continue
        for src, alias in _USER_MASTER_ROW_FIELDS:
            val = getattr(m, src, '') or ''
            if alias not in r or r.get(alias) in (None, ''):
                r[alias] = val
        # Backfill `employee_name` (used by `_backfill_email_from_matrix_name`)
        # if the attendance event itself shipped without a name.
        if m.full_name and not (r.get('employee_name') or r.get('name')):
            r['employee_name'] = m.full_name
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded biometric employee_code resolver (mirror backend)
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_biometric_codes_mirror(*, profile=None,
                                    email: Optional[str] = None,
                                    employee_code: Optional[str] = None) -> set[str]:
    """Resolve a RAD AI user → set of `TimesheetEvent.employee_code` values
    via fuzzy ``employee_name__icontains`` matching. Same multi-strategy
    approach as ``services._resolve_biometric_user_ids`` but operating on the
    Postgres mirror table — so Railway/production users whose biometric
    `employee_code` (system-generated badge number) bears no relation to
    their RAD AI ``UserProfile.employee_id`` still resolve.
    """
    if not _USER_NAME_RESOLVE:
        return set()

    first = (profile.user.first_name if profile and profile.user else '') or ''
    last  = (profile.user.last_name  if profile and profile.user else '') or ''
    # Try strategies in order of confidence — first one yielding 1..N hits wins.
    # NOTE: 'email_exact' and 'last_name_only' are mirror-only strategies that
    # leverage columns the SQL Server biometric view does not expose. Both are
    # soft-bounded by MAX_HITS so an over-broad match never resolves.
    strategies: list[tuple[str, tuple]] = [
        ('email_stem',          (email,)),
        ('email_and_code_stem', (email, employee_code)),
        ('profile_full_name',   (first, last)),
        ('profile_and_email',   (first, last, email)),
    ]

    # Versioned cache key — bumping _CACHE_VER below invalidates ALL stale
    # entries from older deploys (e.g. a previous deploy that cached an
    # empty result before the resolver was active).
    _CACHE_VER = 'v4'
    cache_key = f'ts:bio_uid_mirror:{_CACHE_VER}:{(_norm_key(email) or _norm_key(employee_code))[:128]}'
    cache = None
    try:
        from django.core.cache import cache as _c
        cache = _c
        cached = cache.get(cache_key)
        if cached is not None:
            return set(cached)
    except Exception:
        cache = None

    chosen_hits: list[dict] = []
    chosen_strategy = None
    chosen_tokens: list[str] = []

    # Strategy 0 — direct email match against the mirror's `employee_email`
    # column. Uses norm_email so 'JOHN@x.com' and 'john@x.com' resolve the same.
    if email:
        email_norm = _norm_emp_email(email)
        if email_norm:
            email_hits = list(
                TimesheetEvent.objects
                    .filter(employee_email__iexact=email_norm)
                    .values('employee_code', 'employee_name')
                    .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
            )
            if 0 < len(email_hits) <= _USER_NAME_RESOLVE_MAX_HITS:
                chosen_hits = email_hits
                chosen_strategy = 'email_exact'
                chosen_tokens = [email_norm]

    # Strategy 0b — BiometricUserMaster bridge. The mirror table holds the
    # Matrix `Mx_VEW_UserDetails` snapshot the office-side agent pushes (or
    # that auto-seeds from JOINed event payloads). It is the authoritative
    # source for email ↔ employee_code mapping when individual events ship
    # without `employee_email`. Soft-coded via _USER_MASTER_EMAIL_COLUMNS so
    # operators can extend / reorder the columns checked without changing
    # code.
    if not chosen_hits and email:
        email_norm = _norm_emp_email(email)   # canonical form for consistent lookup
        if email_norm:
            master_cond = Q()
            for col in _USER_MASTER_EMAIL_COLUMNS:
                master_cond |= Q(**{f'{col}__iexact': email_norm})
            master_codes = list(
                BiometricUserMaster.objects
                    .filter(master_cond)
                    .values_list('employee_code', flat=True)
                    .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
            )
            master_codes = [c for c in master_codes if c]
            if 0 < len(master_codes) <= _USER_NAME_RESOLVE_MAX_HITS:
                chosen_hits = [{'employee_code': c, 'employee_name': ''} for c in master_codes]
                chosen_strategy = 'user_master_email'
                chosen_tokens = [email_norm]

    for label, sources in strategies:
        if chosen_hits:
            break
        tokens = _name_tokens(*sources)
        if len(tokens) < _USER_NAME_RESOLVE_MIN_TOKS:
            continue
        qs = TimesheetEvent.objects.all()
        for t in tokens:
            qs = qs.filter(employee_name__icontains=t)
        hits = list(
            qs.values('employee_code', 'employee_name')
              .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
        )
        if 0 < len(hits) <= _USER_NAME_RESOLVE_MAX_HITS:
            chosen_hits = hits
            chosen_strategy = label
            chosen_tokens = tokens
            break

    # Last-resort — most-distinctive single token (usually the surname).
    # Only runs when every multi-token strategy returned 0 hits. Bounded by
    # MAX_HITS so a too-common token (e.g. 'mohammed' across 50 rows) is
    # rejected rather than guessed.
    if not chosen_hits:
        all_tokens = _name_tokens(email, employee_code, first, last)
        if all_tokens:
            # Longest token first — most discriminating.
            for t in sorted(set(all_tokens), key=len, reverse=True):
                hits = list(
                    TimesheetEvent.objects
                        .filter(employee_name__icontains=t)
                        .values('employee_code', 'employee_name')
                        .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
                )
                if 0 < len(hits) <= _USER_NAME_RESOLVE_MAX_HITS:
                    chosen_hits = hits
                    chosen_strategy = f'single_token({t})'
                    chosen_tokens = [t]
                    break

    if not chosen_hits:
        if cache is not None:
            try: cache.set(cache_key, [], _USER_NAME_RESOLVE_TTL_SEC)
            except Exception: pass
        return set()

    resolved = {str(h.get('employee_code') or '').strip() for h in chosen_hits if h.get('employee_code')}
    resolved.discard('')
    import logging
    logging.getLogger(__name__).info(
        'Timesheet mirror name-resolver[%s]: tokens=%s → codes=%s (%s)',
        chosen_strategy, chosen_tokens, sorted(resolved),
        [(h.get('employee_code'), h.get('employee_name')) for h in chosen_hits],
    )
    if cache is not None:
        try: cache.set(cache_key, list(resolved), _USER_NAME_RESOLVE_TTL_SEC)
        except Exception: pass
    return resolved


def _resolve_user_aliases_mirror(employee_code: Optional[str],
                                 email: Optional[str],
                                 *, with_trace: bool = False):
    """Return ({normalised emails}, {biometric employee_codes}) for a RAD AI
    user. Combines profile aliases with fuzzy-resolved biometric codes.

    When ``with_trace=True`` returns ``(emails, codes, trace)`` where ``trace``
    is a dict capturing every step considered (which sources were tried, how
    many rows each one returned). This makes per-user lookup failures
    diagnosable from the response payload without trawling server logs.
    """
    trace: dict = {
        'input_email':        _norm_key(email) if email else '',
        'input_code':         (str(employee_code).strip() if employee_code else ''),
        'user_master_cols':   list(_USER_MASTER_EMAIL_COLUMNS),
        'user_master_name_cols': list(_USER_MASTER_NAME_COLUMNS),
        'profile_matched':    False,
        'profile_emails':     [],
        'profile_codes':      [],
        'name_resolver_used': False,
        'name_resolver_codes': [],
        'master_email_hits':  [],   # BiometricUserMaster rows matched via email
        'master_code_hits':   [],   # BiometricUserMaster rows matched via code
        'master_name_hits':   [],   # BiometricUserMaster rows matched via name tokens
        'master_name_tokens': [],
        'master_name_strategy': None,
    }

    emails: set[str] = set()
    codes: set[str] = set()
    if email:
        emails.add(_norm_key(email))
    if employee_code:
        codes.add(str(employee_code).strip())

    resolved_profile = None
    try:
        from apps.rbac.models import UserProfile
        from django.db.models import Q
        cond = None
        if email:
            cond = Q(user__email__iexact=str(email).strip())
        if employee_code:
            c2 = Q(employee_id__iexact=str(employee_code).strip())
            cond = c2 if cond is None else (cond | c2)
        if cond is not None:
            for p in UserProfile.objects.select_related('user').filter(cond, is_deleted=False):
                resolved_profile = resolved_profile or p
                if p.user and p.user.email:
                    e = _norm_key(p.user.email)
                    emails.add(e)
                    if e not in trace['profile_emails']:
                        trace['profile_emails'].append(e)
                if p.employee_id:
                    c = str(p.employee_id).strip()
                    codes.add(c)
                    if c not in trace['profile_codes']:
                        trace['profile_codes'].append(c)
                trace['profile_matched'] = True
    except Exception as exc:
        # Never let RAD AI lookup failure break biometric reporting
        trace['profile_error'] = str(exc)[:200]

    try:
        bio_codes = _resolve_biometric_codes_mirror(
            profile=resolved_profile, email=email, employee_code=employee_code,
        )
        codes |= bio_codes
        if bio_codes:
            trace['name_resolver_used'] = True
            trace['name_resolver_codes'] = sorted(bio_codes)
    except Exception as exc:
        trace['name_resolver_error'] = str(exc)[:200]

    # Also bridge any BiometricUserMaster row whose office/personal email
    # matches the user's known emails, OR whose `employee_code` is already
    # in `codes`. Pulls the master's full set of email aliases into the
    # email filter so per-user history works even when individual events
    # ship without `employee_email`.
    try:
        master_cond = Q()
        has_master_cond = False
        for e in list(emails):
            if not e:
                continue
            for col in _USER_MASTER_EMAIL_COLUMNS:
                master_cond |= Q(**{f'{col}__iexact': e})
                has_master_cond = True
        if codes:
            master_cond |= Q(employee_code__in=list(codes))
            has_master_cond = True
        if has_master_cond:
            for m in BiometricUserMaster.objects.filter(master_cond):
                hit = {
                    'employee_code': str(m.employee_code or '').strip(),
                    'full_name':     m.full_name or '',
                    'office_email':  m.office_email or '',
                    'personal_email': m.personal_email or '',
                }
                # Decide whether this row was found via email or code so the
                # diagnostic message points to the right configuration knob.
                via_email = any(
                    _norm_key(getattr(m, col, '')) in emails
                    for col in _USER_MASTER_EMAIL_COLUMNS
                )
                if via_email:
                    trace['master_email_hits'].append(hit)
                else:
                    trace['master_code_hits'].append(hit)
                if m.employee_code:
                    codes.add(str(m.employee_code).strip())
                for col in _USER_MASTER_EMAIL_COLUMNS:
                    val = getattr(m, col, '') or ''
                    if val:
                        emails.add(_norm_key(val))
    except Exception as exc:
        trace['master_error'] = str(exc)[:200]

    # Bridge by NAME against BiometricUserMaster.full_name — last-resort
    # before giving up. Triggered only when neither the email nor code
    # bridges produced any new code, and the master table has rows worth
    # searching. Soft-coded via _USER_MASTER_NAME_COLUMNS; bounded by
    # _USER_NAME_RESOLVE_MAX_HITS so an over-broad token never wins.
    if (
        _USER_MASTER_NAME_COLUMNS
        and not trace['master_email_hits']
        and not trace['master_code_hits']
    ):
        try:
            first = (resolved_profile.user.first_name
                     if resolved_profile and resolved_profile.user else '') or ''
            last  = (resolved_profile.user.last_name
                     if resolved_profile and resolved_profile.user else '') or ''
            # Build a few token strategies — most specific first. First one
            # that yields 1..MAX_HITS rows wins. Soft-coded order so adding
            # a new strategy is a single-line change.
            name_strategies: list[tuple[str, list[str]]] = []
            if first and last:
                name_strategies.append(('profile_full_name', _name_tokens(first, last)))
            if email:
                # `_name_tokens` already strips the @domain and stop-words,
                # so 'michelle.dehoedt@rejlers.ae' → ['michelle', 'dehoedt'].
                name_strategies.append(('email_local_part', _name_tokens(email)))
            if last:
                name_strategies.append(('profile_last_name', _name_tokens(last)))

            chosen_rows = []
            chosen_label = None
            chosen_tokens: list[str] = []
            for label, toks in name_strategies:
                toks = [t for t in toks if t]
                if not toks:
                    continue
                name_cond = Q()
                for col in _USER_MASTER_NAME_COLUMNS:
                    sub = Q()
                    for t in toks:
                        sub &= Q(**{f'{col}__icontains': t})
                    name_cond |= sub
                rows = list(
                    BiometricUserMaster.objects
                        .filter(name_cond)
                        .distinct()[: _USER_NAME_RESOLVE_MAX_HITS + 1]
                )
                if 0 < len(rows) <= _USER_NAME_RESOLVE_MAX_HITS:
                    chosen_rows  = rows
                    chosen_label = label
                    chosen_tokens = toks
                    break

            if chosen_rows:
                trace['master_name_strategy'] = chosen_label
                trace['master_name_tokens']   = chosen_tokens
                for m in chosen_rows:
                    hit = {
                        'employee_code':  str(m.employee_code or '').strip(),
                        'full_name':      m.full_name or '',
                        'office_email':   m.office_email or '',
                        'personal_email': m.personal_email or '',
                    }
                    trace['master_name_hits'].append(hit)
                    if m.employee_code:
                        codes.add(str(m.employee_code).strip())
                    for col in _USER_MASTER_EMAIL_COLUMNS:
                        val = getattr(m, col, '') or ''
                        if val:
                            emails.add(_norm_key(val))
        except Exception as exc:
            trace['master_name_error'] = str(exc)[:200]

    emails.discard('')
    codes.discard('')

    if with_trace:
        trace['final_emails'] = sorted(emails)
        trace['final_codes']  = sorted(codes)
        return emails, codes, trace
    return emails, codes


# ─────────────────────────────────────────────────────────────────────────────
# Paired-hours engine
# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded via TIMESHEET_HOURS_MODE and TIMESHEET_OPEN_SHIFT_MAX_HOURS.
#
# Algorithm (mode='paired'):
#   1. Sort all punches for an employee+day chronologically.
#   2. Walk through events: an IN punch opens a segment; the next OUT punch
#      closes it and banks the duration.  Back-to-back INs are collapsed
#      (only the first one opens the segment).  Orphan OUTs (no preceding IN)
#      are ignored.
#   3. A trailing unclosed IN is an "open shift".  It credits
#      min(elapsed_since_in, open_shift_max_hours) additional hours.
#   4. Return a result dict consumed by both the daily/monthly reports and
#      the DailyAttendanceSummary upsert.
#
# Mode='elapsed' falls back to first_in→last_out (legacy behaviour).
# ─────────────────────────────────────────────────────────────────────────────

_HOURS_MODE          = ts_config.RULES.get('hours_mode', 'paired')
_OPEN_SHIFT_MAX_H    = float(ts_config.RULES.get('open_shift_max_hours', 0.0))
_FULL_DAY_H          = float(ts_config.RULES.get('full_day_hours', 8.0))


def _compute_paired_hours(punches: list[dict], *, now: dt.datetime | None = None) -> dict:
    """Compute accurate worked hours from a list of punch dicts.

    Each dict must have:
        event_time  — naive or aware datetime
        event_type  — 'IN' | 'OUT'

    Returns:
        paired_hours        — only counted IN→OUT segments
        elapsed_hours       — first_in to last_out
        effective_hours     — paired or elapsed per TIMESHEET_HOURS_MODE
        first_in            — earliest punch time (any type)
        last_out            — latest punch time (any type)
        punch_count_in
        punch_count_out
        paired_segments     — number of completed IN→OUT pairs
        open_shift          — bool: unclosed IN remaining
        open_shift_since    — datetime of the unclosed IN
        open_shift_credited — hours credited for open shift (capped)
        segments            — list of {in: dt, out: dt, hours: float}
    """
    if not punches:
        return {
            'paired_hours': 0.0, 'elapsed_hours': 0.0, 'effective_hours': 0.0,
            'first_in': None, 'last_out': None,
            'punch_count_in': 0, 'punch_count_out': 0,
            'paired_segments': 0, 'open_shift': False,
            'open_shift_since': None, 'open_shift_credited': 0.0,
            'segments': [],
        }

    _now = now or dt.datetime.now()
    sorted_punches = sorted(punches, key=lambda p: p['event_time'])

    count_in  = sum(1 for p in sorted_punches if str(p.get('event_type', '')).upper() == 'IN')
    count_out = sum(1 for p in sorted_punches if str(p.get('event_type', '')).upper() == 'OUT')
    first_time = _to_naive(sorted_punches[0]['event_time'])
    last_time  = _to_naive(sorted_punches[-1]['event_time'])
    elapsed    = _hours_between(first_time, last_time)

    if _HOURS_MODE != 'paired':
        # Legacy elapsed mode: first punch → last punch
        return {
            'paired_hours': elapsed, 'elapsed_hours': elapsed, 'effective_hours': elapsed,
            'first_in': first_time, 'last_out': last_time,
            'punch_count_in': count_in, 'punch_count_out': count_out,
            'paired_segments': 0, 'open_shift': False,
            'open_shift_since': None, 'open_shift_credited': 0.0,
            'segments': [],
        }

    # ── Paired mode ──────────────────────────────────────────────────────────
    segments: list[dict] = []
    pending_in: dt.datetime | None = None
    paired_total = 0.0

    for p in sorted_punches:
        etype = str(p.get('event_type', '')).upper()
        etime = _to_naive(p['event_time'])
        if etype == 'IN':
            if pending_in is None:
                pending_in = etime  # open a new segment; ignore back-to-back INs
        elif etype == 'OUT':
            if pending_in is not None:
                seg_hours = _hours_between(pending_in, etime)
                segments.append({'in': pending_in, 'out': etime, 'hours': seg_hours})
                paired_total += seg_hours
                pending_in = None
            # OUT with no preceding IN → skip (stale/duplicate)

    # Handle open shift (trailing unclosed IN)
    open_shift    = pending_in is not None
    open_credited = 0.0
    if open_shift and _OPEN_SHIFT_MAX_H > 0:
        since_in  = _hours_between(pending_in, _now)
        open_credited = round(min(since_in, _OPEN_SHIFT_MAX_H), 2)

    paired_total  = round(paired_total + open_credited, 2)
    effective     = paired_total

    return {
        'paired_hours':        paired_total,
        'elapsed_hours':       round(elapsed, 2),
        'effective_hours':     effective,
        'first_in':            first_time,
        'last_out':            last_time,
        'punch_count_in':      count_in,
        'punch_count_out':     count_out,
        'paired_segments':     len(segments),
        'open_shift':          open_shift,
        'open_shift_since':    pending_in,
        'open_shift_credited': open_credited,
        'segments':            segments,
    }


def _compute_and_save_day(employee_code: str, day: dt.date,
                          *, name: str = '', email: str = '') -> 'DailyAttendanceSummary | None':
    """Compute paired hours for `employee_code` on `day` from `TimesheetEvent`
    and upsert a `DailyAttendanceSummary` row.

    Always normalises `employee_code` (via identity.norm_code) before the
    DB upsert so the unique_together(code, date) constraint is always evaluated
    against the canonical form, preventing split-record duplicates.
    """
    employee_code = _norm_emp_code(employee_code)   # canonical form at write time
    punches = list(
        TimesheetEvent.objects
        .filter(employee_code=employee_code, event_time__date=day)
        .values('event_time', 'event_type')
        .order_by('event_time')
    )
    if not punches:
        return None

    result = _compute_paired_hours(punches)

    first_in   = result['first_in']
    is_late    = _is_late({'punch_time': first_in}) if first_in else False
    is_full    = result['effective_hours'] >= _FULL_DAY_H

    summary, _ = DailyAttendanceSummary.objects.update_or_create(
        employee_code=employee_code,
        date=day,
        defaults={
            'paired_hours':        result['paired_hours'],
            'elapsed_hours':       result['elapsed_hours'],
            'effective_hours':     result['effective_hours'],
            'first_in':            first_in,
            'last_out':            result['last_out'],
            'punch_count_in':      result['punch_count_in'],
            'punch_count_out':     result['punch_count_out'],
            'paired_segments':     result['paired_segments'],
            'open_shift':          result['open_shift'],
            'open_shift_since':    result['open_shift_since'],
            'open_shift_credited': result['open_shift_credited'],
            'is_late':             is_late,
            'is_full_day':         is_full,
        },
    )
    return summary


def recompute_summaries_for_events(events: list[dict]) -> int:
    """Bulk-recompute DailyAttendanceSummary for all (employee_code, date)
    combos touched by a batch of ingest events.

    `events` is a list of dicts with keys ``employee_code`` and
    ``event_time`` (datetime).  Returns the number of summaries written.
    """
    affected: set[tuple[str, dt.date]] = set()
    for ev in events:
        code = str(ev.get('employee_code') or '').strip()
        evt  = ev.get('event_time')
        if code and evt:
            try:
                d = evt.date() if hasattr(evt, 'date') else dt.date.fromisoformat(str(evt)[:10])
                affected.add((code, d))
            except (ValueError, AttributeError):
                pass
    count = 0
    for code, day in affected:
        try:
            if _compute_and_save_day(code, day):
                count += 1
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                '[timesheet] summary recompute failed for %s %s: %s', code, day, exc
            )
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Live status — latest punch per user today
# ─────────────────────────────────────────────────────────────────────────────
def live_status() -> dict:
    # ── Soft-coded rolling time window ───────────────────────────────────────
    # Replaces the old strict `event_time__date=today (UTC)` filter which
    # silently dropped punches whenever the office timezone (e.g. UAE UTC+4)
    # and the Railway/production server (UTC) were on different calendar dates
    # (i.e. between midnight and 04:00 UAE = previous UTC day), or whenever
    # the sync agent sent naive local times that were stored as "UTC" (4 h off).
    #
    # The rolling window approach is timezone-agnostic: it asks "what happened
    # in the last N hours?" which is correct regardless of how timestamps are
    # stored.  TIMESHEET_LIVE_LOOKBACK_HOURS (default 20 h) covers a full UAE
    # office day plus a 4-hour UTC offset buffer.  Increase for offices further
    # from UTC (e.g. UTC+8 → set to 24).
    import logging
    logger = logging.getLogger(__name__)
    
    lookback_hours = int(ts_config.RULES.get('live_lookback_hours', 20))
    cutoff = timezone.now() - dt.timedelta(hours=lookback_hours)
    
    # ── Production diagnostic logging + stale data detection ────────────────
    # Log query parameters AND check for stale data (sync agent not running)
    total_events = TimesheetEvent.objects.count()
    latest_event = TimesheetEvent.objects.order_by('-event_time').first() if total_events > 0 else None
    latest_event_time = latest_event.event_time if latest_event else None
    
    logger.info(
        '[mirror_services.live_status] Query params: lookback_hours=%d, '
        'cutoff=%s, now=%s, total_events_in_db=%d, latest_event=%s',
        lookback_hours,
        cutoff.isoformat(),
        timezone.now().isoformat(),
        total_events,
        latest_event_time.isoformat() if latest_event_time else 'NONE'
    )
    
    qs = TimesheetEvent.objects.filter(event_time__gte=cutoff)
    windowed_count = qs.count()
    
    # Soft-coded stale data detection: if DB has events but rolling window is
    # empty, calculate sync age to help diagnose sync agent failures
    sync_age_hours = None
    if total_events > 0 and windowed_count == 0 and latest_event_time:
        sync_age_hours = (timezone.now() - latest_event_time).total_seconds() / 3600
    
    logger.info(
        '[mirror_services.live_status] Events in time window: %d (total in DB: %d), '
        'sync_age_hours: %s',
        windowed_count,
        total_events,
        f'{sync_age_hours:.1f}' if sync_age_hours else 'N/A'
    )

    # Latest punch per employee_code
    latest_by_emp: dict[str, TimesheetEvent] = {}
    for ev in qs.order_by('employee_code', 'event_time'):
        latest_by_emp[ev.employee_code] = ev  # last write wins → latest

    rows = []
    for ev in latest_by_emp.values():
        rows.append({
            'employee_code': ev.employee_code,
            'email': ev.employee_email or None,
            'name': ev.employee_name,
            'employee_name': ev.employee_name,
            'department': ev.department,
            'punch_time': _to_naive(ev.event_time),
            'punch_type': ev.event_type,
        })

    rows = _enrich_from_user_master_mirror(rows)
    rows = _enrich_with_rad_users(rows)
    rows = _backfill_email_from_matrix_name(rows)

    summary = _empty_summary()
    for r in rows:
        is_in = str(r.get('punch_type', '')).upper() == TimesheetEvent.EVENT_IN
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

    rows.sort(key=lambda r: r.get('punch_time') or dt.datetime.min, reverse=True)
    
    # ── Soft-coded stale data metadata for frontend diagnostics ─────────────────
    # When rolling window is empty but DB has events, expose the sync age so
    # the frontend can show actionable error messages like "Last sync: 3 days
    # ago" instead of a generic "No events" message.
    result = {
        'rows': rows,
        'summary': summary,
        'variant': _VARIANT,
        'as_of': dt.datetime.now().isoformat(),
        # Soft-coded: expose the window so the frontend can show a helpful
        # diagnostic like "Showing punches from the last 20 h" instead of
        # a confusing empty table when the sync agent hasn't run yet today.
        'lookback_hours': lookback_hours,
        'window_from': cutoff.isoformat(),
    }
    
    # Add stale data diagnostics when needed
    if total_events > 0:
        result['mirror_total_events'] = total_events
        if latest_event_time:
            result['mirror_latest_event'] = latest_event_time.isoformat()
            if sync_age_hours and sync_age_hours > lookback_hours:
                result['sync_stale'] = True
                result['sync_age_hours'] = round(sync_age_hours, 1)
                result['sync_age_days'] = round(sync_age_hours / 24, 1)
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Calculate detailed live metrics for a single employee (ESS self-service)
# ─────────────────────────────────────────────────────────────────────────────
def _calculate_live_metrics(employee_code: str) -> dict:
    """Calculate detailed live metrics for a single employee (mirror mode).
    
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
    # Soft-coded rolling time window (same as live_status)
    lookback_hours = int(ts_config.RULES.get('live_lookback_hours', 20))
    cutoff = timezone.now() - dt.timedelta(hours=lookback_hours)
    
    # Query all punches for this employee within the rolling window
    punches = list(
        TimesheetEvent.objects
        .filter(employee_code=employee_code, event_time__gte=cutoff)
        .order_by('event_time')
        .values('event_time', 'event_type')
    )
    
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
        if p['event_type'] == TimesheetEvent.EVENT_IN:
            first_in = _to_naive(p['event_time'])
            break
    
    # Count IN/OUT punches
    punch_in_count = sum(1 for p in punches if p['event_type'] == TimesheetEvent.EVENT_IN)
    punch_out_count = sum(1 for p in punches if p['event_type'] == TimesheetEvent.EVENT_OUT)
    
    # Last punch (absolute)
    last_punch = _to_naive(punches[-1]['event_time'])
    last_type = punches[-1]['event_type']
    is_in = (last_type == TimesheetEvent.EVENT_IN)
    
    # Calculate hours worked (pair IN/OUT punches)
    # Simple algorithm: sum all (OUT[i] - IN[i]) durations
    hours_today = 0.0
    i = 0
    while i < len(punches):
        p = punches[i]
        if p['event_type'] == TimesheetEvent.EVENT_IN:
            # Find next OUT
            next_out = None
            for j in range(i + 1, len(punches)):
                if punches[j]['event_type'] == TimesheetEvent.EVENT_OUT:
                    next_out = _to_naive(punches[j]['event_time'])
                    i = j  # Skip to the OUT punch
                    break
            
            in_time = _to_naive(p['event_time'])
            if next_out:
                hours_today += _hours_between(in_time, next_out)
            else:
                # Still IN, calculate up to now
                hours_today += _hours_between(in_time, dt.datetime.now())
        i += 1
    
    # Apply max_daily_hours cap (soft-coded)
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


# ─────────────────────────────────────────────────────────────────────────────
# Daily report
# ─────────────────────────────────────────────────────────────────────────────
def daily_report(date: Optional[str] = None) -> dict:
    day = _parse_date(date)

    # Step 1: get all events for the day, grouped by employee
    events_qs = list(
        TimesheetEvent.objects
        .filter(event_time__date=day)
        .values('employee_code', 'employee_name', 'employee_email', 'department', 'event_time', 'event_type')
        .order_by('employee_code', 'event_time')
    )
    if not events_qs:
        return {'date': day.isoformat(), 'rows': [], 'variant': _VARIANT}

    # Group by employee
    from collections import defaultdict
    emp_punches: dict[str, list] = defaultdict(list)
    emp_meta: dict[str, dict] = {}
    for ev in events_qs:
        code = ev['employee_code']
        emp_punches[code].append({'event_time': _to_naive(ev['event_time']), 'event_type': ev['event_type']})
        # Last write wins for meta (name/email/dept can vary; take latest)
        emp_meta[code] = {
            'employee_name':  ev.get('employee_name', ''),
            'employee_email': ev.get('employee_email') or '',
            'department':     ev.get('department', ''),
        }

    rows = []
    for code, punches in emp_punches.items():
        m = emp_meta[code]
        result = _compute_paired_hours(punches)

        # Upsert DailyAttendanceSummary asynchronously (best-effort)
        try:
            _compute_and_save_day(code, day)
        except Exception:
            pass

        rows.append({
            'employee_code':       code,
            'email':               m['employee_email'] or None,
            'name':                m['employee_name'],
            'employee_name':       m['employee_name'],
            'department':          m['department'],
            'first_in':            result['first_in'],
            'last_out':            result['last_out'],
            'hours_worked':        result['effective_hours'],
            'paired_hours':        result['paired_hours'],
            'elapsed_hours':       result['elapsed_hours'],
            'paired_segments':     result['paired_segments'],
            'open_shift':          result['open_shift'],
            'open_shift_since':    result['open_shift_since'].isoformat() if result['open_shift_since'] else None,
            'open_shift_credited': result['open_shift_credited'],
            'punch_count_in':      result['punch_count_in'],
            'punch_count_out':     result['punch_count_out'],
            'is_late':             _is_late({'punch_time': result['first_in']}),
            'is_full_day':         result['effective_hours'] >= _FULL_DAY_H,
            'hours_mode':          _HOURS_MODE,
        })

    rows = _enrich_from_user_master_mirror(rows)
    rows = _enrich_with_rad_users(rows)
    rows = _backfill_email_from_matrix_name(rows)
    rows.sort(key=lambda r: r.get('first_in') or dt.datetime.min)
    return {
        'date': day.isoformat(),
        'rows': rows,
        'variant': _VARIANT,
        'hours_mode': _HOURS_MODE,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Monthly report
# ─────────────────────────────────────────────────────────────────────────────
def monthly_report(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)
    start, end = _month_range(y, m)
    end_exclusive = end + dt.timedelta(days=1)

    # ── Read from DailyAttendanceSummary for already-computed days ───────────
    # For today (and any day without a summary), fall back to on-demand compute.
    existing_summaries: dict[tuple, DailyAttendanceSummary] = {
        (s.employee_code, s.date): s
        for s in DailyAttendanceSummary.objects.filter(date__gte=start, date__lt=end_exclusive)
    }

    # Determine which employee+day combos have events but no summary yet
    events_agg = list(
        TimesheetEvent.objects
        .filter(event_time__gte=start, event_time__lt=end_exclusive)
        .values('employee_code', 'event_time__date')
        .distinct()
    )
    needs_compute = [
        (r['employee_code'], r['event_time__date'])
        for r in events_agg
        if (r['employee_code'], r['event_time__date']) not in existing_summaries
    ]
    for code, day in needs_compute:
        try:
            s = _compute_and_save_day(code, day)
            if s:
                existing_summaries[(code, day)] = s
        except Exception:
            pass

    # ── Meta (names / emails / departments) ─────────────────────────────────
    meta = {
        e['employee_code']: e
        for e in TimesheetEvent.objects
        .filter(event_time__gte=start, event_time__lt=end_exclusive)
        .values('employee_code', 'employee_name', 'employee_email', 'department')
        .distinct()
    }

    # ── Build per-employee roll-up from DailyAttendanceSummary ──────────────
    # Soft-coded: normalise employee_code (strip whitespace) so that the same
    # employee stored under '22393', '22393 ', and ' 22393' all map to one row.
    by_emp: dict[str, dict] = {}
    for (code, day_date), s in existing_summaries.items():
        norm_code = str(code or '').strip()   # <- dedup key
        m_ = meta.get(code, {}) or meta.get(norm_code, {})
        slot = by_emp.setdefault(norm_code, {
            'employee_code': norm_code,   # store the normalised code
            'email':         m_.get('employee_email') or None,
            'name':          m_.get('employee_name', ''),
            'employee_name': m_.get('employee_name', ''),
            'department':    m_.get('department', ''),
            'days_present':  0,
            'full_days':     0,
            'half_days':     0,
            'late_arrivals': 0,
            'total_hours':   0.0,
            'open_shifts':   0,
            'days_detail':   [],
        })
        h = s.effective_hours or 0.0
        slot['days_present'] += 1
        slot['total_hours']  += h
        if s.is_full_day:
            slot['full_days'] += 1
        else:
            slot['half_days'] += 1
        if s.is_late:
            slot['late_arrivals'] += 1
        if s.open_shift:
            slot['open_shifts'] += 1
        slot['days_detail'].append({
            'date':            str(day_date),
            'first_in':        s.first_in.isoformat() if s.first_in else None,
            'last_out':        s.last_out.isoformat() if s.last_out else None,
            'hours':           round(h, 2),
            'paired_segments': s.paired_segments,
            'open_shift':      s.open_shift,
            'hours_mode':      _HOURS_MODE,
        })

    rows = list(by_emp.values())
    for slot in rows:
        slot['total_hours'] = round(slot['total_hours'], 2)
        slot['avg_hours_per_day'] = (
            round(slot['total_hours'] / slot['days_present'], 2) if slot['days_present'] else 0
        )
    rows = _enrich_from_user_master_mirror(rows)
    rows = _enrich_with_rad_users(rows)
    rows = _backfill_email_from_matrix_name(rows)
    rows.sort(key=lambda x: (x.get('radai_full_name') or x.get('name') or ''))
    return {
        'year': y,
        'month': m,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'working_days_in_month': _working_days(start, end),
        'rows': rows,
        'variant': _VARIANT,
        'hours_mode': _HOURS_MODE,
    }


def lookup_by_code(code: str) -> Optional[dict]:
    """Reverse lookup against the Postgres mirror — biometric ``employee_code``
    → ``{employee_code, employee_name, employee_email}``. Same contract as
    ``services.lookup_by_code`` so the dispatcher in views can stay backend-
    agnostic."""
    code = (code or '').strip()
    if not code:
        return None
    row = (
        TimesheetEvent.objects
            .filter(employee_code=code)
            .values('employee_code', 'employee_name', 'employee_email')
            .first()
    )
    return row or None


# ─────────────────────────────────────────────────────────────────────────────
# Per-user drill-down
# ─────────────────────────────────────────────────────────────────────────────
def user_history(employee_code: Optional[str] = None,
                 email: Optional[str] = None,
                 from_date: Optional[str] = None,
                 to_date: Optional[str] = None,
                 include_punches: bool = False,
                 with_trace: bool = False) -> dict:
    today = dt.date.today()
    start = _parse_date(from_date, today - dt.timedelta(days=30))
    end = _parse_date(to_date, today)
    end_exclusive = end + dt.timedelta(days=1)

    if not (employee_code or email):
        payload = {'rows': [], 'error': 'employee_code or email required'}
        if with_trace:
            payload['diagnostic'] = {'reason': 'no_identifier_supplied'}
        return payload

    from django.db.models import Q
    import logging
    log = logging.getLogger(__name__)
    qs = TimesheetEvent.objects.filter(event_time__gte=start, event_time__lt=end_exclusive)
    # OR-match: either identifier may resolve the record. Aliases include
    # alternate emails from the RAD AI UserProfile AND biometric employee_codes
    # discovered by fuzzy [employee_name] match — same multi-strategy logic
    # used by the SQL Server backend in services._resolve_user_aliases.
    alias_emails, alias_codes, trace = _resolve_user_aliases_mirror(
        employee_code, email, with_trace=True,
    )
    log.info(
        'timesheet.user_history.mirror inputs code=%r email=%r → aliases emails=%s codes=%s '
        'master_email_hits=%d master_code_hits=%d master_name_hits=%d (strategy=%s)',
        employee_code, email, sorted(alias_emails), sorted(alias_codes),
        len(trace.get('master_email_hits') or []), len(trace.get('master_code_hits') or []),
        len(trace.get('master_name_hits') or []), trace.get('master_name_strategy'),
    )
    cond = Q()
    has_filter = False
    if alias_codes:
        cond |= Q(employee_code__in=list(alias_codes))
        has_filter = True
    for e in alias_emails:
        cond |= Q(employee_email__iexact=e)
        has_filter = True
    if not has_filter:
        payload = {'rows': [], 'error': 'employee_code or email required'}
        if with_trace:
            trace['reason'] = 'no_aliases_resolved'
            payload['diagnostic'] = trace
        return payload
    qs = qs.filter(cond)
    matched = qs.count()
    log.info('timesheet.user_history.mirror matched %d events', matched)
    trace['matched_events_in_range'] = matched
    # Diagnostic: total events for any of these aliases regardless of date
    # range. Useful when the user picks a range that has no punches yet.
    try:
        total_for_aliases = TimesheetEvent.objects.filter(cond).count()
        trace['matched_events_all_time'] = total_for_aliases
    except Exception:
        pass

    per_day = defaultdict(lambda: {'first_in': None, 'last_out': None, 'punches': 0})
    raw_punches: list[dict] = []  # optional, per-event detail
    employee_meta = {'employee_code': '', 'employee_name': '', 'employee_email': '', 'department': ''}

    for ev in qs.order_by('event_time'):
        d = ev.event_time.date().isoformat()
        slot = per_day[d]
        ts = _to_naive(ev.event_time)
        if slot['first_in'] is None or ts < slot['first_in']:
            slot['first_in'] = ts
        if slot['last_out'] is None or ts > slot['last_out']:
            slot['last_out'] = ts
        slot['punches'] += 1
        # First seen wins for the employee header metadata
        if not employee_meta['employee_code']:
            employee_meta = {
                'employee_code':  ev.employee_code,
                'employee_name':  ev.employee_name,
                'employee_email': ev.employee_email,
                'department':     ev.department,
            }
        if include_punches:
            raw_punches.append({
                'event_time': ts.isoformat() if ts else None,
                'event_type': ev.event_type,
                'date':       d,
            })

    # Per-day rows (same shape as before)
    rows = []
    max_daily_hours = float(ts_config.RULES.get('max_daily_hours', 9.0))
    for d, slot in sorted(per_day.items()):
        raw_hours = _hours_between(slot['first_in'], slot['last_out']) or 0
        # Cap hours at configured maximum (default: 9 hours)
        hours = min(raw_hours, max_daily_hours)
        rows.append({
            'date':         d,
            'first_in':     str(slot['first_in']) if slot['first_in'] else None,
            'last_out':     str(slot['last_out']) if slot['last_out'] else None,
            'hours_worked': round(hours, 2),
            'punch_count':  slot['punches'],
        })

    # ── Consolidated summary across the whole range
    full_day_hours = float(ts_config.RULES.get('full_day_hours', 8.0))
    total_hours    = sum(r['hours_worked'] for r in rows)
    total_punches  = sum(r['punch_count'] or 0 for r in rows)
    days_present   = len(rows)
    days_full      = sum(1 for r in rows if (r['hours_worked'] or 0) >= full_day_hours)
    days_partial   = days_present - days_full

    # Average punch-in / punch-out time (HH:MM)
    def _avg_time(values):
        valid = [v for v in values if v]
        if not valid:
            return None
        secs = [v.hour * 3600 + v.minute * 60 + v.second for v in valid]
        avg = sum(secs) // len(secs)
        return f'{avg // 3600:02d}:{(avg % 3600) // 60:02d}'

    first_ins = []
    last_outs = []
    for d, slot in per_day.items():
        if slot['first_in']:
            first_ins.append(slot['first_in'])
        if slot['last_out'] and slot['last_out'] != slot['first_in']:
            last_outs.append(slot['last_out'])

    summary = {
        'total_hours':         round(total_hours, 2),
        'total_punches':       total_punches,
        'days_present':        days_present,
        'days_full':           days_full,
        'days_partial':        days_partial,
        'avg_hours_per_day':   round(total_hours / days_present, 2) if days_present else 0,
        'avg_first_in':        _avg_time(first_ins),
        'avg_last_out':        _avg_time(last_outs),
        'range_days':          (end - start).days + 1,
    }

    # ── Monthly breakdown (one entry per YYYY-MM in range)
    monthly_buckets: dict[str, dict] = defaultdict(lambda: {
        'hours': 0.0, 'days': 0, 'punches': 0,
    })
    for r in rows:
        ym = r['date'][:7]  # 'YYYY-MM'
        b = monthly_buckets[ym]
        b['hours']   += r['hours_worked'] or 0
        b['days']    += 1
        b['punches'] += r['punch_count'] or 0
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

    return {
        'employee':          employee_meta,
        'from':              start.isoformat(),
        'to':                end.isoformat(),
        'rows':              rows,
        'summary':           summary,
        'monthly_breakdown': monthly_breakdown,
        'punches':           raw_punches if include_punches else None,
        'variant':           _VARIANT,
        **({'diagnostic': trace} if with_trace else {}),
    }
