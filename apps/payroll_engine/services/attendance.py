"""Bridge between the Time Sheet module and the Payroll Engine.

Provides ``get_monthly_hours_map(year, month) -> {employee_code: Decimal}``
which mirrors the **Total** column shown on the HR ▸ Attendance ▸ Summary
tab (``Time Sheet Summary — All Branches``).

Computation parity with the Summary tab:

* Sum of biometric daily hours from
  :pyfunc:`apps.timesheet.services.monthly_report` (already capped at
  ``max_daily_hours``).
* HR overrides from :class:`apps.payroll.models.AttendanceOverride`
  **replace** the biometric value for the matching ``(employee_code,
  date)`` cell — only the most-recent active record is used.

Soft-coded knobs (all overrideable via env vars):

* ``PAYROLL_HOURS_FROM_TIMESHEET`` — master toggle (default ``True``)
* ``PAYROLL_HOURS_FALLBACK_TO_EMPLOYEE`` — when an employee_no has no
  biometric data, fall back to ``PayrollEmployee.hours`` instead of 0
  (default ``True``).
"""
from __future__ import annotations
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from ..config import (
    DEFAULT_EMPLOYEE_HOURS,
    HOURS_FALLBACK_TO_EMPLOYEE,
    HOURS_FROM_TIMESHEET,
)

logger = logging.getLogger(__name__)


ZERO = Decimal('0.00')


def _to_decimal(value) -> Decimal:
    if value is None:
        return ZERO
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def _safe_monthly_report(year: int, month: int) -> dict:
    """Wrap timesheet.monthly_report so payroll never blows up if the
    biometric DB is unreachable (Railway, network, etc.).
    """
    try:
        from apps.timesheet.services import monthly_report  # local import — soft dep
        return monthly_report(year, month) or {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            '[payroll_engine.attendance] timesheet.monthly_report(%s, %s) failed: %s',
            year, month, exc,
        )
        return {}


def _overrides_for_month(year: int, month: int) -> dict[str, dict[str, Decimal]]:
    """Return ``{employee_code: {YYYY-MM-DD: override_hours_decimal}}`` for
    the most-recent active override per (employee_code, date)."""
    try:
        from apps.payroll.models import AttendanceOverride  # local import
    except Exception as exc:  # pragma: no cover
        logger.warning('[payroll_engine.attendance] AttendanceOverride unavailable: %s', exc)
        return {}

    import calendar as _cal
    last_day = _cal.monthrange(int(year), int(month))[1]
    start = f'{year:04d}-{month:02d}-01'
    end = f'{year:04d}-{month:02d}-{last_day:02d}'

    qs = (
        AttendanceOverride.objects
        .filter(is_active=True, date__gte=start, date__lte=end)
        .order_by('employee_code', 'date', '-created_at')
        .values('employee_code', 'date', 'override_hours')
    )
    out: dict[str, dict[str, Decimal]] = defaultdict(dict)
    seen: set[tuple[str, str]] = set()
    for row in qs:
        code = str(row.get('employee_code') or '').strip()
        if not code:
            continue
        date_iso = row['date'].isoformat() if hasattr(row['date'], 'isoformat') else str(row['date'])
        key = (code, date_iso)
        if key in seen:
            continue  # earlier row already captured the most-recent active record
        seen.add(key)
        out[code][date_iso] = _to_decimal(row.get('override_hours'))
    return dict(out)


def compute_monthly_hours(year: int, month: int) -> dict[str, Decimal]:
    """Build ``{employee_code: Decimal(total_hours)}`` for the given month.

    Mirrors the **Total** column on the Attendance ▸ Summary view:
    biometric daily hours, with HR overrides replacing the cell when present.
    Empty when ``PAYROLL_HOURS_FROM_TIMESHEET=False``.
    """
    if not HOURS_FROM_TIMESHEET:
        return {}

    report = _safe_monthly_report(year, month)
    rows = report.get('rows') or []
    overrides_by_emp = _overrides_for_month(year, month)

    result: dict[str, Decimal] = {}
    for r in rows:
        code = str(r.get('employee_code') or '').strip()
        if not code:
            continue

        # Map biometric days for this employee
        biometric_days: dict[str, Decimal] = {}
        for d in (r.get('days_detail') or []):
            day_iso = d.get('date')
            if day_iso:
                biometric_days[str(day_iso)] = _to_decimal(d.get('hours'))

        # Overlay HR overrides — replace cell value (not add)
        emp_overrides = overrides_by_emp.get(code, {})
        for day_iso, ov_hours in emp_overrides.items():
            biometric_days[day_iso] = ov_hours

        total = sum(biometric_days.values(), ZERO)
        result[code] = total.quantize(Decimal('0.01'))

    # Include override-only employees (no biometric data this month)
    for code, days in overrides_by_emp.items():
        if code in result:
            continue
        total = sum(days.values(), ZERO)
        if total > 0:
            result[code] = total.quantize(Decimal('0.01'))

    return result


def get_hours_for_employee(
    employee_no: str,
    year: int,
    month: int,
    *,
    fallback: Optional[Decimal] = None,
) -> Decimal:
    """Convenience accessor for a single employee. Returns ``fallback``
    (or the soft-coded default) when no biometric/override data exists.
    """
    code = str(employee_no or '').strip()
    if not code:
        return _to_decimal(fallback if fallback is not None else DEFAULT_EMPLOYEE_HOURS)
    hours_map = compute_monthly_hours(year, month)
    if code in hours_map:
        return hours_map[code]
    if HOURS_FALLBACK_TO_EMPLOYEE and fallback is not None:
        return _to_decimal(fallback)
    return _to_decimal(DEFAULT_EMPLOYEE_HOURS)
