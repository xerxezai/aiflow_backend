"""
Leave Encashment Service
========================
Converts each employee's unused monthly leave balance into a monetary
encashment payment, triggered manually by an HR Manager.

Formula (soft-coded constants below):
  days_to_encash  = max(0, earned − taken)  for the target (year, month)
  daily_rate      = monthly_salary ÷ ENCASHMENT_WORKING_DAYS
  encashment_pay  = days_to_encash × daily_rate

Salary source:
  Most recent MasterPayrollRow for the employee_code where the parent
  MasterPayrollImport.workflow_stage is one of the approved stages.
  If no salary is found, encashment_pay is set to 0 and the employee_code
  is recorded in the run's missing_salaries list.

Usage:
    from apps.payroll.services.leave_encashment import run_leave_encashment
    result = run_leave_encashment(year=2026, month=7, triggered_by_user=request.user)
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)

User = get_user_model()

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED POLICY CONSTANTS
# Mirror LEAVE_ENCASHMENT_WORKING_DAYS in hrLeave.config.js on the frontend.
# ─────────────────────────────────────────────────────────────────────────────

# Number of working days used to derive the daily salary rate for encashment.
# UAE standard: 22 working days per month.
ENCASHMENT_WORKING_DAYS: int = 22

# Monetary precision — 2 decimal places (AED cents)
_MONEY_PREC = Decimal('0.01')

# Day precision — 4 decimal places (matches EmployeeLeaveMonthly)
_DAY_PREC = Decimal('0.0001')


# ─────────────────────────────────────────────────────────────────────────────
# APPROVED WORKFLOW STAGES — salary from payroll at these stages is reliable
# ─────────────────────────────────────────────────────────────────────────────
_RELIABLE_STAGES = {'frozen', 'hr_approved', 'finance_review', 'finance_approved', 'released'}


def _build_salary_lookup() -> dict[str, Decimal]:
    """
    Return { employee_code → employee_salary } from the most recent
    MasterPayrollRow per employee_code across all reliable payroll sessions.
    """
    from apps.payroll.models import MasterPayrollRow

    salary_map: dict[str, Decimal] = {}
    # Order by year desc, month desc so the latest entry wins
    qs = (
        MasterPayrollRow.objects
        .filter(import_session__workflow_stage__in=_RELIABLE_STAGES)
        .select_related('import_session')
        .order_by(
            'employee_code',
            '-import_session__year',
            '-import_session__month',
        )
    )
    for row in qs:
        # Only record the first (most recent) entry per employee_code
        if row.employee_code not in salary_map:
            salary_map[row.employee_code] = row.employee_salary
    return salary_map


class EncashmentAlreadyRunError(Exception):
    """Raised when an encashment run already exists for the requested period."""


def run_leave_encashment(
    year: int,
    month: int,
    triggered_by_user: Optional[User] = None,
    dry_run: bool = False,
) -> dict:
    """
    Execute the monthly leave encashment for (year, month).

    Parameters
    ----------
    year, month         : Target period.
    triggered_by_user   : Django User who triggered the run (HR Manager).
    dry_run             : If True, compute everything but do NOT write to DB.

    Returns
    -------
    dict with keys:
      records_processed   : int
      total_days_encashed : Decimal
      total_pay           : Decimal
      missing_salaries    : list[str]  — employee_codes with no salary
      preview             : list[dict] — per-employee detail (always returned)
      dry_run             : bool

    Raises
    ------
    EncashmentAlreadyRunError  : if a successful run already exists for (year, month)
    """
    from apps.payroll.models import (
        EmployeeLeaveMonthly,
        EmployeeLeaveRecord,
        LeaveEncashmentRun,
    )

    # ── Guard: prevent double-run ─────────────────────────────────────────────
    if not dry_run:
        existing = LeaveEncashmentRun.objects.filter(year=year, month=month, status='success').first()
        if existing:
            raise EncashmentAlreadyRunError(
                f'Encashment for {year}-{month:02d} already completed on {existing.executed_at.date()}.'
            )

    # ── Build salary lookup ───────────────────────────────────────────────────
    salary_map = _build_salary_lookup()
    working_days = Decimal(str(ENCASHMENT_WORKING_DAYS))

    # ── Fetch monthly rows for target period ──────────────────────────────────
    monthly_qs = (
        EmployeeLeaveMonthly.objects
        .filter(record__year=year, month=month)
        .select_related('record')
    )

    records_processed  = 0
    total_days         = Decimal('0')
    total_pay          = Decimal('0')
    missing_salaries   = []
    preview            = []

    # Collect DB updates (applied atomically if not dry_run)
    monthly_updates  = []   # (instance, days, pay)
    record_increments= {}   # employee_code → days increment for EmployeeLeaveRecord

    for monthly in monthly_qs:
        records_processed += 1
        emp_code = monthly.record.employee_code or ''

        days_to_encash = max(
            Decimal('0'),
            (monthly.earned - monthly.taken).quantize(_DAY_PREC, rounding=ROUND_HALF_UP),
        )

        salary = salary_map.get(emp_code)
        if salary is None or salary <= Decimal('0'):
            if emp_code and emp_code not in missing_salaries:
                missing_salaries.append(emp_code)
            daily_rate     = Decimal('0')
            enc_pay        = Decimal('0')
        else:
            daily_rate = (salary / working_days).quantize(_MONEY_PREC, rounding=ROUND_HALF_UP)
            enc_pay    = (days_to_encash * daily_rate).quantize(_MONEY_PREC, rounding=ROUND_HALF_UP)

        total_days += days_to_encash
        total_pay  += enc_pay

        monthly_updates.append((monthly, days_to_encash, enc_pay))
        record_increments[emp_code] = record_increments.get(emp_code, Decimal('0')) + days_to_encash

        preview.append({
            'employee_code': emp_code,
            'employee_name': monthly.record.employee_name,
            'earned':        float(monthly.earned),
            'taken':         float(monthly.taken),
            'days_encashed': float(days_to_encash),
            'monthly_salary': float(salary) if salary else None,
            'daily_rate':    float(daily_rate),
            'encashment_pay': float(enc_pay),
        })

    # ── Persist (unless dry run) ───────────────────────────────────────────────
    if not dry_run:
        with transaction.atomic():
            # Update EmployeeLeaveMonthly rows
            for monthly, days, pay in monthly_updates:
                monthly.encashed       = days
                monthly.encashment_pay = pay
                monthly.save(update_fields=['encashed', 'encashment_pay'])

            # Increment EmployeeLeaveRecord.total_encashed per employee
            for emp_code, delta in record_increments.items():
                if delta > Decimal('0'):
                    from django.db.models import F
                    (EmployeeLeaveRecord.objects
                     .filter(employee_code=emp_code, year=year)
                     .update(total_encashed=F('total_encashed') + delta))

            # Create audit log
            status = 'partial' if missing_salaries else 'success'
            LeaveEncashmentRun.objects.create(
                year=year,
                month=month,
                triggered_by=triggered_by_user,
                status=status,
                records_processed=records_processed,
                total_days_encashed=total_days.quantize(_DAY_PREC),
                total_pay=total_pay.quantize(_MONEY_PREC),
                missing_salaries=missing_salaries,
            )

    return {
        'records_processed':   records_processed,
        'total_days_encashed': float(total_days),
        'total_pay':           float(total_pay),
        'missing_salaries':    missing_salaries,
        'preview':             preview,
        'dry_run':             dry_run,
    }


def get_encashment_status(year: int, month: int) -> Optional[dict]:
    """
    Return the most recent LeaveEncashmentRun for (year, month), or None.
    """
    from apps.payroll.models import LeaveEncashmentRun
    run = LeaveEncashmentRun.objects.filter(year=year, month=month).first()
    if run is None:
        return None
    return {
        'id':                  str(run.id),
        'year':                run.year,
        'month':               run.month,
        'status':              run.status,
        'executed_at':         run.executed_at.isoformat(),
        'triggered_by':        run.triggered_by.get_full_name() if run.triggered_by else 'System',
        'records_processed':   run.records_processed,
        'total_days_encashed': float(run.total_days_encashed),
        'total_pay':           float(run.total_pay),
        'missing_salaries':    run.missing_salaries,
    }
