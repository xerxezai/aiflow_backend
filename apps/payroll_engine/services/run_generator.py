"""Monthly run generator.

Auto-generates a fresh Draft PayrollRun for a given (year, month) using
the PayrollEmployee master roster. Materialises pending PayrollAdjustment
rows into PayslipLineItem entries. Optionally carries forward last
month's free-form line items.

After generation, HR uploads an optional "Adjustments Only" XLSX (handled
by services.excel_import.import_adjustments) which is also materialised
on the next call to refresh_run_from_adjustments.
"""
from __future__ import annotations
from typing import Optional

from django.db import transaction
from django.utils import timezone

from ..catalog import (
    AdjustmentStatus, LineItemSource, Status,
)
from ..config import CARRY_FORWARD_LINE_ITEMS, HOURS_FROM_TIMESHEET
from ..models import (
    PayrollAdjustment, PayrollEmployee, PayrollRun, Payslip, PayslipLineItem,
)
from .attendance import compute_monthly_hours
from .calculator import recompute_payslip_totals, recompute_run_totals


class GenerationError(Exception):
    """Raised when a run can't be generated (e.g. already exists)."""


def _cycle_code(year: int, month: int) -> str:
    return f'{year:04d}-{month:02d}'


def _previous_period(year: int, month: int):
    if month == 1:
        return year - 1, 12
    return year, month - 1


@transaction.atomic
def generate_monthly_run(
    year: int,
    month: int,
    *,
    user=None,
    overwrite: bool = False,
    note: str = '',
    working_days: Optional[int] = None,
    source_type: str = 'system',
) -> PayrollRun:
    """Create a Draft PayrollRun for (year, month) with one Payslip per
    active PayrollEmployee. Apply any pending PayrollAdjustments.

    Args:
        working_days: Total working days for this month (HR-supplied).
                      Defaults to catalog.DEFAULT_WORKING_DAYS_PER_MONTH (22).

    If a run for the period already exists:
      - ``overwrite=False`` (default) → raises GenerationError
      - ``overwrite=True`` AND the existing run is Draft → wipes & re-runs
      - ``overwrite=True`` AND the existing run is NOT Draft → raises
    """
    from ..catalog import DEFAULT_WORKING_DAYS_PER_MONTH
    effective_working_days = int(working_days) if working_days is not None else DEFAULT_WORKING_DAYS_PER_MONTH

    existing = PayrollRun.objects.filter(year=year, month=month).first()
    if existing:
        if not overwrite:
            raise GenerationError(
                f"PayrollRun for {_cycle_code(year, month)} already exists "
                f"(status={existing.status}). Pass overwrite=True to regenerate."
            )
        if existing.status != Status.DRAFT:
            raise GenerationError(
                f"Cannot overwrite a {existing.status} run. Revert to Draft first."
            )
        # Wipe slips so we can regenerate
        existing.payslips.all().delete()
        run = existing
        run.notes = note or run.notes
        run.working_days_in_month = effective_working_days
        run.source_type = source_type
    else:
        run = PayrollRun.objects.create(
            year=year,
            month=month,
            cycle_code=_cycle_code(year, month),
            status=Status.DRAFT,
            created_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
            notes=note,
            working_days_in_month=effective_working_days,
            source_type=source_type,
        )

    # Auto-compute public holidays for this month from the PH register.
    # Regions to count are soft-coded in catalog.DEFAULT_PH_REGIONS.
    try:
        from apps.payroll.models import PublicHoliday
        from ..catalog import DEFAULT_PH_REGIONS
        ph_count = PublicHoliday.objects.filter(
            date__year=year,
            date__month=month,
            is_active=True,
            region__in=DEFAULT_PH_REGIONS,
        ).count()
    except Exception:
        ph_count = 0
    run.public_holidays_in_month = ph_count
    run.save(update_fields=['working_days_in_month', 'public_holidays_in_month'])

    # Active employees only
    employees = PayrollEmployee.objects.filter(is_active=True).order_by('full_name')

    # Pull live "Total Hours" from the biometric Time Sheet Summary.
    # Keyed by employee_code which matches PayrollEmployee.employee_no.
    hours_map = compute_monthly_hours(year, month) if HOURS_FROM_TIMESHEET else {}

    # ── Leave days per employee for this month ────────────────────────────────
    # Sourced from approved LeaveRequests (apps.payroll) linked by employee_code.
    # Categories are soft-coded in catalog.LEAVE_CATEGORIES_FOR_PAYROLL.
    # Multi-month leaves are prorated by calendar-day overlap.
    leave_lookup: dict = {}
    try:
        import calendar as _cal
        from decimal import Decimal as _D
        from datetime import date as _date
        from apps.payroll.models import LeaveRequest as _LR, LeaveRequestStatus as _LRS
        from ..catalog import LEAVE_CATEGORIES_FOR_PAYROLL

        _month_first = _date(year, month, 1)
        _month_last  = _date(year, month, _cal.monthrange(year, month)[1])
        _approved = (
            _LR.objects
            .filter(
                status=_LRS.APPROVED,
                leave_type__category__in=list(LEAVE_CATEGORIES_FOR_PAYROLL.values()),
                start_date__lte=_month_last,
                end_date__gte=_month_first,
                employee_code__isnull=False,
            )
            .select_related('leave_type')
            .values('employee_code', 'leave_type__category', 'days_requested',
                    'start_date', 'end_date')
        )
        for req in _approved:
            emp_code = str(req['employee_code']).strip()
            category = req['leave_type__category']
            days     = _D(str(req['days_requested'] or 0))

            # Prorate if the leave spans across month boundaries
            req_start = req['start_date']
            req_end   = req['end_date']
            if req_start < _month_first or req_end > _month_last:
                overlap_start    = max(req_start, _month_first)
                overlap_end      = min(req_end,   _month_last)
                total_cal_days   = (_req_end   - req_start).days + 1  # noqa: not used
                total_cal_days   = (req_end    - req_start).days + 1
                overlap_cal_days = (overlap_end - overlap_start).days + 1
                if total_cal_days > 0:
                    days = (days * _D(overlap_cal_days) / _D(total_cal_days)).quantize(_D('0.01'))

            row = leave_lookup.setdefault(emp_code,
                      {f: _D('0.00') for f in LEAVE_CATEGORIES_FOR_PAYROLL})
            for field, cat in LEAVE_CATEGORIES_FOR_PAYROLL.items():
                if cat == category:
                    row[field] = row[field] + days
    except Exception:
        LEAVE_CATEGORIES_FOR_PAYROLL = {}  # graceful fallback if leave app unavailable

    # Carry-forward source = previous month's payslips keyed by employee_id
    carry_map = {}
    if CARRY_FORWARD_LINE_ITEMS:
        prev_y, prev_m = _previous_period(year, month)
        prev_run = PayrollRun.objects.filter(year=prev_y, month=prev_m).first()
        if prev_run:
            for slip in prev_run.payslips.prefetch_related('line_items'):
                carry_map[slip.employee_id] = list(slip.line_items.all())

    # Pending adjustments for this period, grouped by employee_id
    adj_map: dict[int, list[PayrollAdjustment]] = {}
    pending = PayrollAdjustment.objects.filter(
        target_year=year,
        target_month=month,
        status=AdjustmentStatus.PENDING,
    ).select_related('employee')
    for adj in pending:
        adj_map.setdefault(adj.employee_id, []).append(adj)

    created_slips: list[Payslip] = []
    for emp in employees:
        # Use live biometric total when available; fall back to the
        # employee's contracted hours (or the engine default) otherwise.
        live_hours = hours_map.get(str(emp.employee_no).strip())
        resolved_hours = live_hours if live_hours is not None else emp.hours

        # Leave days for this employee this month (from the lookup built above)
        from decimal import Decimal as _D
        _emp_leave = leave_lookup.get(str(emp.employee_no).strip(), {})

        slip = Payslip(
            run=run,
            employee=emp,
            hours=resolved_hours,
            basic=emp.basic,
            housing=emp.housing,
            transport=emp.transport,
            home_leave=emp.home_leave,
            payment_mode=emp.default_payment_mode,
            snapshot_full_name=emp.full_name,
            snapshot_department=emp.department,
            snapshot_designation=emp.designation,
            snapshot_iban=emp.iban,
            snapshot_joining_date=emp.joining_date,
        annual_leave_days=_emp_leave.get('annual_leave_days', _D('0.00')),
            unpaid_leave_days=_emp_leave.get('unpaid_leave_days', _D('0.00')),
            # Seed public holiday days from the run-level count; HR can override per payslip
            public_holiday_days=_D(str(run.public_holidays_in_month)),
            status=Status.DRAFT,
        )
        slip.save()

        # Carry-forward line items from prior period
        if emp.id in carry_map:
            for src in carry_map[emp.id]:
                PayslipLineItem.objects.create(
                    payslip=slip,
                    kind=src.kind,
                    component_code=src.component_code,
                    label=src.label,
                    description=src.description,
                    amount=src.amount,
                    source=LineItemSource.AUTO,
                )

        # Apply pending adjustments for this employee/period
        for adj in adj_map.get(emp.id, []):
            PayslipLineItem.objects.create(
                payslip=slip,
                kind=adj.kind,
                component_code=adj.component_code,
                label=adj.label,
                description=adj.description,
                amount=adj.amount,
                source=LineItemSource.ADJUSTMENT,
            )
            adj.status = AdjustmentStatus.APPLIED
            adj.applied_to = slip
            adj.applied_at = timezone.now()
            adj.save(update_fields=['status', 'applied_to', 'applied_at', 'updated_at'])

        recompute_payslip_totals(slip)
        slip.save()
        created_slips.append(slip)

    run.generated_at = timezone.now()
    recompute_run_totals(run)
    run.save()
    return run


@transaction.atomic
def refresh_run_totals(run: PayrollRun) -> PayrollRun:
    """Re-compute every payslip's totals and the run's aggregates.
    Use after manual edits or bulk adjustment uploads.
    """
    for slip in run.payslips.prefetch_related('line_items'):
        recompute_payslip_totals(slip)
        slip.save()
    recompute_run_totals(run)
    run.save()
    return run


@transaction.atomic
def refresh_run_hours_from_timesheet(run: PayrollRun, *, zero_missing: bool | None = None) -> dict:
    """Re-pull the live "Total" hours from the Time Sheet Summary and
    update every Payslip on this run.

    Args:
        run: The PayrollRun whose payslips should be refreshed.
        zero_missing: When True, employees absent from the attendance map
            (no biometric punches and no HR overrides) have their hours
            set to 0 so the run mirrors the Attendance ▸ Summary "Total"
            column exactly. When False the previous snapshot is kept.
            When ``None`` (default), the soft-coded
            ``REFRESH_ZERO_MISSING_HOURS`` config flag decides.

    Returns ``{'updated': n, 'unchanged': n, 'missing': [...], 'zeroed': n}``.
    Caller is responsible for gating to DRAFT runs.
    """
    from decimal import Decimal
    from ..config import hours_to_days, REFRESH_ZERO_MISSING_HOURS
    from .calculator import recompute_run_totals

    if zero_missing is None:
        zero_missing = REFRESH_ZERO_MISSING_HOURS

    hours_map = compute_monthly_hours(run.year, run.month)
    updated = 0
    unchanged = 0
    zeroed = 0
    missing: list[str] = []
    zero_dec = Decimal('0')
    for slip in run.payslips.select_related('employee'):
        emp_code = str(slip.employee.employee_no).strip()
        live = hours_map.get(emp_code)
        if live is None:
            missing.append(emp_code)
            if zero_missing and slip.hours != zero_dec:
                slip.hours = zero_dec
                slip.days = hours_to_days(zero_dec)
                slip.save(update_fields=['hours', 'days', 'updated_at'])
                zeroed += 1
            continue
        live_dec = live if isinstance(live, Decimal) else Decimal(str(live))
        if slip.hours == live_dec:
            unchanged += 1
            continue
        slip.hours = live_dec
        slip.days = hours_to_days(live_dec)
        slip.save(update_fields=['hours', 'days', 'updated_at'])
        updated += 1
    # Roll the new hours/days totals up to the run aggregates so the
    # Monthly Runs table reflects the refresh immediately.
    if updated or zeroed:
        recompute_run_totals(run)
        run.save(update_fields=[
            'total_gross', 'total_deductions', 'total_net',
            'total_hours', 'total_days', 'employee_count', 'updated_at',
        ])
    return {
        'updated': updated,
        'unchanged': unchanged,
        'missing': missing,
        'zeroed': zeroed,
    }
