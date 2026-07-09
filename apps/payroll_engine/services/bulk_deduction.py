"""Bulk percentage deduction service.

Applies one percentage-based PayslipLineItem (kind=deduction) per employee
inside a Draft PayrollRun. The deduction base is the sum of the selected
earning fields on each Payslip; ``basic`` is permanently protected (see
``catalog.BULK_DEDUCTION_PROTECTED_FIELDS``).

Re-running the action with the same component code is idempotent: the
previous bulk line item is deleted before the new one is created, so HR
can adjust the percentage as many times as needed while the run is Draft.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import List, Optional

from django.db import transaction

from .. import catalog
from ..catalog import (
    BULK_DEDUCTION_ALLOWED_FIELDS,
    BULK_DEDUCTION_COMPONENT_CODE,
    BULK_DEDUCTION_DEFAULT_FIELDS,
    BULK_DEDUCTION_DEFAULT_LABEL,
    BULK_DEDUCTION_MAX_PCT,
    BULK_DEDUCTION_MIN_PCT,
    BULK_DEDUCTION_PROTECTED_FIELDS,
    LineItemKind,
    LineItemSource,
)
from ..models import PayslipLineItem
from .calculator import (
    quantize,
    recompute_payslip_totals,
    recompute_run_totals,
    to_decimal,
)


class BulkDeductionError(Exception):
    """Raised when input validation or run-state checks fail."""


@dataclass
class BulkDeductionSummary:
    employees_affected: int
    employees_skipped: int
    total_deducted: Decimal
    percentage: Decimal
    fields_used: List[str]
    label: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d['total_deducted'] = str(self.total_deducted)
        d['percentage']     = str(self.percentage)
        return d


@transaction.atomic
def apply_bulk_percentage_deduction(
    run,
    *,
    percentage,
    fields: Optional[List[str]] = None,
    label: Optional[str] = None,
    description: str = '',
    replace_existing: bool = True,
) -> BulkDeductionSummary:
    """Add/replace a percentage-based deduction line on every payslip in *run*.

    Args:
        run:               A Draft PayrollRun.
        percentage:        Decimal-compatible value in [MIN_PCT, MAX_PCT].
        fields:            Earning fields whose sum forms the deduction base.
                           Defaults to BULK_DEDUCTION_DEFAULT_FIELDS.
        label:             Visible label on the generated line item.
        description:       Optional reviewer note.
        replace_existing:  When True (default), any previous bulk-deduction
                           line on each payslip is removed first.
    """
    if not run.is_editable:
        raise BulkDeductionError(
            f'Run {run.cycle_code} is "{run.status}"; only Draft runs can be modified.'
        )

    pct = to_decimal(percentage)
    min_pct = Decimal(BULK_DEDUCTION_MIN_PCT)
    max_pct = Decimal(BULK_DEDUCTION_MAX_PCT)
    if pct < min_pct or pct > max_pct:
        raise BulkDeductionError(
            f'Percentage {pct} is out of range [{min_pct}–{max_pct}].'
        )

    chosen = list(fields) if fields else list(BULK_DEDUCTION_DEFAULT_FIELDS)
    if not chosen:
        raise BulkDeductionError('At least one earning field must be selected.')

    bad = [f for f in chosen if f not in BULK_DEDUCTION_ALLOWED_FIELDS]
    if bad:
        raise BulkDeductionError(
            f'Invalid fields {bad}. Allowed: {BULK_DEDUCTION_ALLOWED_FIELDS}.'
        )
    protected = [f for f in chosen if f in BULK_DEDUCTION_PROTECTED_FIELDS]
    if protected:
        raise BulkDeductionError(
            f'Protected fields cannot be deducted: {protected}.'
        )

    # Only strip trailing zeros that follow a decimal point — keep integers
    # like Decimal('10') intact (otherwise '10'.rstrip('0') == '1').
    pct_str = format(pct, 'f')
    if '.' in pct_str:
        pct_str = pct_str.rstrip('0').rstrip('.')
    pretty_pct = pct_str or '0'
    final_label = label or f'{BULK_DEDUCTION_DEFAULT_LABEL} ({pretty_pct}%)'
    factor = pct / Decimal('100')

    total = Decimal('0')
    affected = 0
    skipped = 0

    for slip in run.payslips.select_for_update():
        base = sum(
            (to_decimal(getattr(slip, f, 0)) for f in chosen),
            Decimal('0'),
        )
        amount = quantize(base * factor)

        # Always clear an earlier bulk line so re-applies don't stack.
        if replace_existing:
            PayslipLineItem.objects.filter(
                payslip=slip,
                component_code=BULK_DEDUCTION_COMPONENT_CODE,
            ).delete()

        if amount <= 0:
            skipped += 1
            recompute_payslip_totals(slip)
            slip.save()
            continue

        PayslipLineItem.objects.create(
            payslip=slip,
            kind=LineItemKind.DEDUCTION,
            component_code=BULK_DEDUCTION_COMPONENT_CODE,
            label=final_label,
            description=description or '',
            amount=amount,
            source=LineItemSource.ADJUSTMENT,
        )
        recompute_payslip_totals(slip)
        slip.save()

        total += amount
        affected += 1

    recompute_run_totals(run)
    run.save()

    return BulkDeductionSummary(
        employees_affected=affected,
        employees_skipped=skipped,
        total_deducted=quantize(total),
        percentage=pct,
        fields_used=chosen,
        label=final_label,
    )


def reverse_bulk_percentage_deduction(run) -> int:
    """Delete every bulk-deduction line on *run*. Returns rows removed."""
    if not run.is_editable:
        raise BulkDeductionError(
            f'Run {run.cycle_code} is "{run.status}"; only Draft runs can be modified.'
        )
    deleted, _ = PayslipLineItem.objects.filter(
        payslip__run=run,
        component_code=BULK_DEDUCTION_COMPONENT_CODE,
    ).delete()
    for slip in run.payslips.all():
        recompute_payslip_totals(slip)
        slip.save()
    recompute_run_totals(run)
    run.save()
    return deleted
