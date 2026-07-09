"""
Annual Leave Accrual Service
============================
Computes per-employee, per-month leave earned values based on the company
policy encoded in the HR leave Excel (Summary Leave Calculation-RAD).

Policy (soft-coded constants below — change ANNUAL_LEAVE_DAYS to adjust):
  • Full month accrual  : ANNUAL_LEAVE_DAYS / 12
  • Pro-rated month     : ANNUAL_LEAVE_DAYS / 365 × days_remaining_in_joining_month
                         (inclusive of the joining day)
  • Pre-joining months  : 0
  • Future months       : 0  (not yet accrued)

The "taken", "encashed", and "carryforward" values are treated as the
authoritative source from the imported Excel and are NEVER overwritten
by this service — only the computed "earned" and derived "balance" are
updated.

Usage (called by the compute_leave_accrual management command):

    from apps.payroll.services.leave_accrual import compute_accrual_for_record
    compute_accrual_for_record(record, target_year=2026, dry_run=False)
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED POLICY CONSTANTS
# Change these values here — nowhere else in the codebase uses magic numbers.
# ─────────────────────────────────────────────────────────────────────────────

# Total annual leave entitlement in working days.
# UAE Labour Law art.75 mandates 22 days; update if policy changes.
ANNUAL_LEAVE_DAYS: int = 22

# Monthly leave accrual — derived from annual entitlement.
# Standard accrual: ANNUAL_LEAVE_DAYS / 12 = 1.8333... days per month.
# This is the exact value earned each full month of service.
MONTHLY_LEAVE_ACCRUAL: float = ANNUAL_LEAVE_DAYS / 12  # 1.8333... ≈ 1.83 days

# Decimal precision for leave day calculations (4 dp matches the DB field).
ACCRUAL_PRECISION: str = '0.0001'

# Accrual basis — number of days in a year used for daily rate calculation.
# 365 (not 365.25) aligns with the HR Excel formula as verified from data.
ACCRUAL_YEAR_DAYS: int = 365

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _dec(value: float | str | Decimal) -> Decimal:
    """Round value to ACCRUAL_PRECISION using ROUND_HALF_UP."""
    return Decimal(str(value)).quantize(Decimal(ACCRUAL_PRECISION), rounding=ROUND_HALF_UP)


def compute_monthly_earned(
    joining_date: Optional[date],
    year: int,
    month: int,
    annual_entitlement: int | Decimal = ANNUAL_LEAVE_DAYS,
    reference_date: Optional[date] = None,
) -> Decimal:
    """
    Return the number of annual-leave days earned in (year, month).

    Parameters
    ----------
    joining_date     : Employee's start date (None → employee joined before
                       any tracked year, treat every month as full).
    year / month     : The period to compute.
    annual_entitlement : Override the global constant (e.g. from DB record).
    reference_date   : Treat months after this date as 0 (not yet accrued).
                       Defaults to today.

    Returns
    -------
    Decimal — earned days, always ≥ 0.
    """
    if reference_date is None:
        reference_date = date.today()

    entitlement = int(annual_entitlement)

    # Future month — nothing accrued yet
    first_day_of_period = date(year, month, 1)
    if first_day_of_period > reference_date:
        return _dec(0)

    # No joining date (very old employee) → full accrual every month
    if joining_date is None:
        return _dec(entitlement / 12)

    # Month is entirely before joining → 0
    days_in_month = calendar.monthrange(year, month)[1]
    last_day_of_period = date(year, month, days_in_month)
    if last_day_of_period < joining_date:
        return _dec(0)

    # Joining is within this month → pro-rate
    if joining_date.year == year and joining_date.month == month:
        # Days from (including) joining day to end of month
        days_remaining = days_in_month - joining_date.day + 1
        # Daily rate: entitlement / ACCRUAL_YEAR_DAYS
        earned = entitlement / ACCRUAL_YEAR_DAYS * days_remaining
        return _dec(earned)

    # Full month after joining → standard monthly accrual
    return _dec(entitlement / 12)


def compute_running_balance(
    carryforward: Decimal,
    monthly_rows: list[dict],
    up_to_month: Optional[int] = None,
) -> dict[int, Decimal]:
    """
    Compute running cumulative balance for each month 1-12.

    Parameters
    ----------
    carryforward  : Balance brought forward from the previous year.
    monthly_rows  : List of dicts with keys:
                    month (int 1-12), earned, taken, encashed (all Decimal).
    up_to_month   : If supplied, months after this are set to 0 (future).

    Returns
    -------
    dict  { month_number: running_balance }  for months 1-12.
    """
    rows_by_month: dict[int, dict] = {r['month']: r for r in monthly_rows}
    running = carryforward
    result  = {}
    for m in range(1, 13):
        if up_to_month is not None and m > up_to_month:
            result[m] = running  # carry the last balance forward
            continue
        row    = rows_by_month.get(m, {})
        earned   = _dec(row.get('earned',   0))
        taken    = _dec(row.get('taken',    0))
        encashed = _dec(row.get('encashed', 0))
        running  = _dec(running + earned - taken - encashed)
        result[m] = running
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def compute_accrual_for_record(record, target_year: int, dry_run: bool = False) -> dict:
    """
    Recompute earned + balance for all months of *target_year* for the given
    EmployeeLeaveRecord instance.

    Behaviour
    ---------
    • Reads existing EmployeeLeaveMonthly rows for taken/encashed (these are
      the authoritative HR source; we never overwrite them).
    • Recomputes "earned" using compute_monthly_earned().
    • Recomputes per-month balance as a running total.
    • If dry_run=False, bulk-upserts EmployeeLeaveMonthly rows and updates
      the parent EmployeeLeaveRecord totals.

    Returns a summary dict for logging.
    """
    from apps.payroll.models import EmployeeLeaveMonthly  # lazy import

    joining  = record.joining_date          # date | None
    cf       = _dec(record.carryforward)    # Decimal carryforward
    ent      = int(record.annual_entitlement or ANNUAL_LEAVE_DAYS)
    today    = date.today()

    # Load existing monthly rows (may be empty if import hasn't run yet)
    existing: dict[int, object] = {
        m.month: m
        for m in record.monthly_breakdown.all()
    }

    monthly_data = []
    total_earned   = _dec(0)
    total_taken    = _dec(0)
    total_encashed = _dec(0)

    for month in range(1, 13):
        ex  = existing.get(month)
        earned   = compute_monthly_earned(joining, target_year, month, ent, today)
        taken    = _dec(ex.taken    if ex else 0)
        encashed = _dec(ex.encashed if ex else 0)

        total_earned   += earned
        total_taken    += taken
        total_encashed += encashed
        monthly_data.append({'month': month, 'earned': earned, 'taken': taken, 'encashed': encashed})

    # Running balance
    balances = compute_running_balance(cf, monthly_data)
    leave_balance = balances[12]  # year-end balance

    if not dry_run:
        from django.db import transaction
        with transaction.atomic():
            for row in monthly_data:
                m        = row['month']
                bal      = balances[m]
                defaults = dict(
                    earned   = row['earned'],
                    taken    = row['taken'],
                    encashed = row['encashed'],
                    balance  = bal,
                )
                EmployeeLeaveMonthly.objects.update_or_create(
                    record=record,
                    month=m,
                    defaults=defaults,
                )

            record.total_earned    = total_earned
            record.total_taken     = total_taken
            record.total_encashed  = total_encashed
            record.leave_balance   = leave_balance
            record.save(update_fields=[
                'total_earned', 'total_taken', 'total_encashed', 'leave_balance'
            ])

    return {
        'employee_name':  record.employee_name,
        'employee_code':  record.employee_code,
        'year':           target_year,
        'joining_date':   joining,
        'carryforward':   float(cf),
        'total_earned':   float(total_earned),
        'total_taken':    float(total_taken),
        'total_encashed': float(total_encashed),
        'leave_balance':  float(leave_balance),
    }
