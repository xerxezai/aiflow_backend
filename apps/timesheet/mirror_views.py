"""
Mirror ingest API — receives biometric punch events from the office-side
sync agent (`scripts/timesheet_mirror_sync.py`).

POST  /api/v1/timesheet/mirror/ingest/
Header:  X-Timesheet-Mirror-Key: <shared secret>
Body:    {"events": [
    {
      "source_event_id": "deterministic-hash",
      "employee_code":   "1234",
      "employee_name":   "John Smith",
      "employee_email":  "" | "j@x.com",
      "department":      "" | "Engineering",
      "event_time":      "2026-06-10T08:31:42",
      "event_type":      "IN" | "OUT"
    }, ...
]}

Idempotent: re-uploading the same `source_event_id` updates instead of
duplicating. Designed for at-least-once delivery from the agent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import config as ts_config
from .models import TimesheetEvent, BiometricUserMaster
from .identity import norm_code, norm_email, norm_name

logger = logging.getLogger(__name__)


def _apply_ingest_tz(dt_naive: datetime) -> datetime:
    """Convert a naive ingest datetime to a UTC-aware datetime.

    Soft-coded via ``TIMESHEET_INGEST_TZ_OFFSET`` (default 0 = treat as UTC).
    Example: UAE office (UTC+4) → set env var to 4 so an incoming naive
    timestamp of ``08:31 local`` is stored as ``04:31 UTC`` instead of
    ``08:31 UTC`` (which was the old, incorrect behaviour).

    Changing the offset only affects NEWLY ingested events; re-run the sync
    agent with ``--full`` to back-fill existing records with the correct UTC.
    """
    offset_hours = ts_config.INGEST_TZ_OFFSET_HOURS
    utc_dt = dt_naive - timedelta(hours=offset_hours) if offset_hours else dt_naive
    return timezone.make_aware(utc_dt, dt_timezone.utc)


def _parse_event_time(raw: Any):
    if isinstance(raw, datetime):
        return raw if timezone.is_aware(raw) else _apply_ingest_tz(raw)
    if not raw:
        return None
    parsed = parse_datetime(str(raw))
    if not parsed:
        return None
    return parsed if timezone.is_aware(parsed) else _apply_ingest_tz(parsed)


# Soft-coded list of optional per-event keys that, when present, also seed
# `BiometricUserMaster`. Mirrors the ingest-users payload map so a single
# event row can carry full master data — letting the routine event sync
# bootstrap Card1 / OfficeEmail without a separate `--users` run.
_EVENT_TO_MASTER_FIELDS = {
    'card1':          'card1',
    'card2':          'card2',
    'office_email':   'office_email',
    'personal_email': 'personal_email',
    'full_name':      'full_name',
    'designation':    'designation',
    # `employee_name` / `employee_email` / `department` are the legacy event
    # keys — map them onto the master too so older agents still help seed it.
    'employee_name':  'full_name',
    'employee_email': 'office_email',
    'department':     'department',
}


def _maybe_upsert_user_master_from_event(ev: dict, emp_code: str) -> None:
    """Populate or refresh `BiometricUserMaster` from any user-master keys
    the event payload carries. No-op if none are present. Failures are
    swallowed so the event ingest itself can never fail because of master
    enrichment."""
    if not emp_code:
        return
    defaults = {}
    for src, model_field in _EVENT_TO_MASTER_FIELDS.items():
        val = ev.get(src)
        if val is None:
            continue
        s = str(val).strip()
        if not s:
            continue
        # Never overwrite a stronger key with a weaker legacy one (the dict
        # iteration order means legacy keys come last, so we only set the
        # field if it isn't already set in this defaults dict).
        defaults.setdefault(model_field, s[:255])
    if not defaults:
        return
    try:
        BiometricUserMaster.objects.update_or_create(
            employee_code=emp_code, defaults=defaults,
        )
    except Exception as exc:  # pragma: no cover
        logger.debug('[timesheet.ingest] user-master upsert skipped for %s: %s', emp_code, exc)


@api_view(['POST'])
@authentication_classes([])  # API-key auth only — no JWT required
@permission_classes([AllowAny])
def ingest_events(request):
    """Accept a batch of biometric events and upsert by `source_event_id`."""
    key_header = request.META.get('HTTP_X_TIMESHEET_MIRROR_KEY', '')
    expected = ts_config.MIRROR_API_KEY or ''
    if not expected:
        return Response({'error': 'mirror ingest disabled (no key configured)'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if key_header != expected:
        return Response({'error': 'invalid mirror key'}, status=status.HTTP_403_FORBIDDEN)

    payload = request.data if isinstance(request.data, dict) else {}
    events = payload.get('events') or []
    if not isinstance(events, list):
        return Response({'error': 'events must be a list'}, status=status.HTTP_400_BAD_REQUEST)

    max_batch = ts_config.MIRROR_INGEST_MAX_BATCH
    if len(events) > max_batch:
        return Response(
            {'error': f'batch too large ({len(events)} > {max_batch})'},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    inserted = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []

    for idx, ev in enumerate(events):
        try:
            sid = str(ev.get('source_event_id') or '').strip()
            emp_code   = norm_code(ev.get('employee_code'))
            event_time = _parse_event_time(ev.get('event_time'))
            event_type = str(ev.get('event_type') or '').strip().upper()
            if not (sid and emp_code and event_time and event_type in ('IN', 'OUT')):
                skipped += 1
                continue
            defaults = {
                'employee_code':   emp_code,
                'employee_name':   norm_name(ev.get('employee_name')),
                'employee_email':  norm_email(ev.get('employee_email')),
                'department':      norm_name(ev.get('department')),
                'event_time':      event_time,
                'event_type':      event_type,
            }
            _, created = TimesheetEvent.objects.update_or_create(
                source_event_id=sid, defaults=defaults,
            )
            if created:
                inserted += 1
            else:
                updated += 1

            # Opportunistically seed BiometricUserMaster from any extra
            # user-master fields the agent included on this event. This makes
            # the routine event sync also populate Card1 / OfficeEmail without
            # requiring a separate `--users` run. Keys ignored unless present.
            _maybe_upsert_user_master_from_event(ev, emp_code)
        except Exception as exc:  # pragma: no cover — never let one bad row kill the batch
            logger.warning('[timesheet.ingest] row %s failed: %s', idx, exc)
            errors.append({'index': idx, 'error': str(exc)})

    logger.info(
        '[timesheet.ingest] %s in, %s updated, %s skipped, %s errors',
        inserted, updated, skipped, len(errors),
    )

    # ── Recompute DailyAttendanceSummary for all touched employee+date combos ──
    # Best-effort: failures are logged but never bubble up to the agent.
    # The summary table is the source of truth for paired-hours; keeping it
    # in sync here means payroll and self-service always see correct hours
    # without a separate cron job.
    if inserted + updated > 0:
        try:
            from . import mirror_services as _ms
            saved_events = [
                {
                    'employee_code': str(ev.get('employee_code') or '').strip(),
                    'event_time':    _parse_event_time(ev.get('event_time')),
                }
                for ev in events
                if ev.get('employee_code') and ev.get('event_time')
            ]
            n_summaries = _ms.recompute_summaries_for_events(saved_events)
            logger.info('[timesheet.ingest] recomputed %s daily summaries', n_summaries)
        except Exception as exc:
            logger.warning('[timesheet.ingest] summary recompute failed: %s', exc)

    return Response({
        'received': len(events),
        'inserted': inserted,
        'updated':  updated,
        'skipped':  skipped,
        'errors':   errors,
    })


# ─────────────────────────────────────────────────────────────────────────────
# User-master mirror — Card1 / OfficeEmail / FullName from Mx_VEW_UserDetails
# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded payload key → model field map. The agent can omit any key; only
# `employee_code` is required. Unknown keys are stashed in `extra` so adding a
# new Matrix column doesn't require a code change on Railway.
_USER_MASTER_FIELDS = {
    'full_name':      'full_name',
    'card1':          'card1',
    'card2':          'card2',
    'office_email':   'office_email',
    'personal_email': 'personal_email',
    'designation':    'designation',
    'department':     'department',
}


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def ingest_users(request):
    """Accept a batch of biometric user-master rows and upsert by
    `employee_code`. Mirrors Matrix `Mx_VEW_UserDetails` to Postgres so the
    production frontend can show Card1 / OfficeEmail without LAN access."""
    key_header = request.META.get('HTTP_X_TIMESHEET_MIRROR_KEY', '')
    expected = ts_config.MIRROR_API_KEY or ''
    if not expected:
        return Response({'error': 'mirror ingest disabled (no key configured)'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if key_header != expected:
        return Response({'error': 'invalid mirror key'}, status=status.HTTP_403_FORBIDDEN)

    payload = request.data if isinstance(request.data, dict) else {}
    users = payload.get('users') or []
    if not isinstance(users, list):
        return Response({'error': 'users must be a list'}, status=status.HTTP_400_BAD_REQUEST)

    max_batch = ts_config.MIRROR_INGEST_MAX_BATCH
    if len(users) > max_batch:
        return Response(
            {'error': f'batch too large ({len(users)} > {max_batch})'},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    inserted = updated = skipped = 0
    errors: list[dict] = []

    for idx, u in enumerate(users):
        try:
            emp_code = norm_code(u.get('employee_code'))
            if not emp_code:
                skipped += 1
                continue
            defaults = {}
            for payload_key, model_field in _USER_MASTER_FIELDS.items():
                val = u.get(payload_key)
                if val is None:
                    continue
                # Apply identity normalisation per field type
                s = str(val).strip()[:255]
                if model_field in ('office_email', 'personal_email'):
                    s = norm_email(s)
                elif model_field == 'full_name':
                    s = norm_name(s)
                defaults[model_field] = s
            # Bucket anything the agent pushes that isn't in the known map.
            known = set(_USER_MASTER_FIELDS) | {'employee_code', 'extra'}
            extra = {k: v for k, v in u.items() if k not in known}
            if 'extra' in u and isinstance(u['extra'], dict):
                extra.update(u['extra'])
            if extra:
                defaults['extra'] = extra
            _, created = BiometricUserMaster.objects.update_or_create(
                employee_code=emp_code, defaults=defaults,
            )
            if created:
                inserted += 1
            else:
                updated += 1
        except Exception as exc:
            logger.warning('[timesheet.ingest-users] row %s failed: %s', idx, exc)
            errors.append({'index': idx, 'error': str(exc)})

    logger.info(
        '[timesheet.ingest-users] %s in, %s updated, %s skipped, %s errors',
        inserted, updated, skipped, len(errors),
    )
    return Response({
        'received': len(users),
        'inserted': inserted,
        'updated':  updated,
        'skipped':  skipped,
        'errors':   errors,
    })
