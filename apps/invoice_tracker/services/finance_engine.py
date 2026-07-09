"""
Finance Engine — Excel-derived business rules in soft-coded form.

Encodes the exact formulas the finance team uses in "Customer Inv masterfile":

  Excel cell  | Formula                                              | Field
  ────────────┼──────────────────────────────────────────────────────┼─────────────────────
  D = E&" "&F |                                                      | company_project   (display)
  H           | =K / 1.05 / (0.95 if ICV=YES else 1)                 | ppc_value         (excl-VAT, excl-ICV)
  I           | =H * 5%  (only when ICV=YES)                         | retention         (ICV holdback)
  L           | =K * FX_RATE (or copy K when already AED)            | invoice_amount_aed
  M           | =C + N   (when payment-terms is a number of days)    | due_date
  Q           | derived from due-date + payment_date                 | payment_status / days_overdue
  T           | =K - actual_payment_received                         | balance_to_be_received

All thresholds / rates / labels live in the FINANCE_RULES dict at the top so
they can be retuned without touching code.

Pure functions only — no Django imports here. Tested standalone.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


# ─── Soft-coded rule book ───────────────────────────────────────────────────

FINANCE_RULES: dict = {
    # VAT rate applied on customer invoices (UAE standard: 5%)
    'vat_rate': Decimal('0.05'),

    # ICV (In-Country Value) retention rate applied when ICV=Yes (5% holdback)
    'icv_retention_rate': Decimal('0.05'),

    # Currency → AED FX rate. Edit here when treasury updates the rates.
    # 3.6725 is the actual USD/AED peg; the spreadsheet rounds to 3.625.
    'fx_to_aed': {
        'AED': Decimal('1.0000'),
        'USD': Decimal('3.6725'),
        'EUR': Decimal('4.0000'),
        'GBP': Decimal('4.6800'),
        'SGD': Decimal('2.7300'),
    },

    # Default payment terms when none supplied (in days)
    'default_payment_terms_days': 30,

    # When status is left blank, derive from balance + due-date using this map
    'status_auto_derive': True,

    # Tolerance under which a balance is considered "paid" (AED)
    'paid_tolerance_aed': Decimal('1.00'),

    # Status labels — must match the model's PaymentStatus enum values
    'status': {
        'paid':       'paid',
        'pending':    'pending',
        'overdue':    'overdue',
        'partial':    'partial',
        'cancelled':  'cancelled',
        'credit_note': 'credit_note',
    },
}


# ─── Helpers ────────────────────────────────────────────────────────────────

def _as_decimal(value: Any) -> Decimal | None:
    if value in (None, ''):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _is_icv_yes(icv_applicable: Any) -> bool:
    if isinstance(icv_applicable, bool):
        return icv_applicable
    if icv_applicable is None:
        return False
    return str(icv_applicable).strip().upper() in {'YES', 'Y', 'TRUE', '1'}


def _payment_terms_days(payment_terms: Any) -> int:
    """Extract a day count from free-text payment terms.

    Accepts: 30, '30', '30 days', 'Net 45', 'within 60 days', etc.
    Falls back to the default in FINANCE_RULES.
    """
    if payment_terms in (None, ''):
        return FINANCE_RULES['default_payment_terms_days']
    if isinstance(payment_terms, (int, float)):
        return int(payment_terms)
    s = str(payment_terms)
    import re
    m = re.search(r'\d+', s)
    if m:
        return int(m.group(0))
    return FINANCE_RULES['default_payment_terms_days']


# ─── Compute primitives — directly mirror the Excel formulas ────────────────

def compute_company_project(company: str, rad_project_no: str) -> str:
    """D = IF(F="", E, E&" "&F)."""
    company = (company or '').strip()
    rad = (rad_project_no or '').strip()
    if not rad:
        return company
    return f'{company} {rad}'.strip()


def compute_ppc_value(invoice_amount: Any, icv_applicable: Any) -> Decimal | None:
    """H = IF(K="","", IF(ICV="YES", K/1.05/0.95, K/1.05)).

    PPC = Project Procurement Charge = invoice amount stripped of VAT
    (and stripped of the 5% ICV holdback when ICV applies).
    """
    k = _as_decimal(invoice_amount)
    if k is None:
        return None
    vat = FINANCE_RULES['vat_rate']
    icv = FINANCE_RULES['icv_retention_rate']
    denom_vat = Decimal('1') + vat
    if _is_icv_yes(icv_applicable):
        denom_icv = Decimal('1') - icv
        return (k / denom_vat / denom_icv).quantize(Decimal('0.01'))
    return (k / denom_vat).quantize(Decimal('0.01'))


def compute_retention(ppc_value: Any, icv_applicable: Any) -> Decimal | None:
    """I = IF(OR(H="", ICV<>"YES"), "", H * 5%)."""
    if not _is_icv_yes(icv_applicable):
        return None
    h = _as_decimal(ppc_value)
    if h is None:
        return None
    return (h * FINANCE_RULES['icv_retention_rate']).quantize(Decimal('0.01'))


def compute_amount_excl_vat(invoice_amount: Any) -> Decimal | None:
    """Invoice amount with the 5% VAT removed (used by `Inv. Amount (Excl. VAT)`)."""
    k = _as_decimal(invoice_amount)
    if k is None:
        return None
    return (k / (Decimal('1') + FINANCE_RULES['vat_rate'])).quantize(Decimal('0.01'))


def compute_paid_amount_excl_vat(actual_payment_received: Any) -> Decimal | None:
    p = _as_decimal(actual_payment_received)
    if p is None:
        return None
    return (p / (Decimal('1') + FINANCE_RULES['vat_rate'])).quantize(Decimal('0.01'))


def compute_invoice_amount_aed(invoice_amount: Any, currency: str | None) -> Decimal | None:
    """L = K (AED) or K * FX_RATE for other currencies."""
    k = _as_decimal(invoice_amount)
    if k is None:
        return None
    ccy = (currency or 'AED').upper()
    rate = FINANCE_RULES['fx_to_aed'].get(ccy)
    if rate is None:
        return None
    return (k * rate).quantize(Decimal('0.01'))


def compute_due_date(invoice_date: date | None, payment_terms: Any) -> date | None:
    """M = IF(C+N) when terms is days."""
    if not invoice_date:
        return None
    days = _payment_terms_days(payment_terms)
    return invoice_date + timedelta(days=days)


def compute_days_overdue(due_date: date | None, payment_status: str | None,
                         today: date | None = None) -> int | None:
    """How many days past due. None when paid / cancelled / no due date."""
    if not due_date:
        return None
    status = (payment_status or '').lower()
    if status in {FINANCE_RULES['status']['paid'],
                  FINANCE_RULES['status']['cancelled'],
                  FINANCE_RULES['status']['credit_note']}:
        return None
    today = today or date.today()
    delta = (today - due_date).days
    return delta if delta > 0 else None


def compute_balance(invoice_amount: Any, actual_payment_received: Any) -> Decimal | None:
    """T = invoice_amount - actual_payment_received (clamped at 0)."""
    k = _as_decimal(invoice_amount)
    if k is None:
        return None
    p = _as_decimal(actual_payment_received) or Decimal('0')
    bal = k - p
    if bal < 0:
        bal = Decimal('0')
    return bal.quantize(Decimal('0.01'))


def derive_payment_status(invoice_amount: Any,
                          actual_payment_received: Any,
                          due_date: date | None,
                          current_status: str | None = None,
                          today: date | None = None) -> str:
    """Auto-derive payment status when not explicitly set.

    Precedence:
      1. Respect `cancelled` / `credit_note` if already set.
      2. Balance ≤ tolerance → paid
      3. Partial payment received → partial
      4. Past due date → overdue
      5. Otherwise → pending
    """
    S = FINANCE_RULES['status']
    cur = (current_status or '').lower()
    if cur in {S['cancelled'], S['credit_note']}:
        return cur

    invoice = _as_decimal(invoice_amount) or Decimal('0')
    paid    = _as_decimal(actual_payment_received) or Decimal('0')
    bal     = invoice - paid

    if invoice > 0 and bal <= FINANCE_RULES['paid_tolerance_aed']:
        return S['paid']
    if paid > 0 and bal > FINANCE_RULES['paid_tolerance_aed']:
        return S['partial']
    if due_date and (today or date.today()) > due_date:
        return S['overdue']
    return S['pending']


# ─── High-level: recompute all derived fields on an invoice instance ────────

DERIVED_FIELDS = (
    'ppc_value', 'retention', 'amount_excl_vat', 'paid_amount_excl_vat',
    'invoice_amount_aed', 'due_date', 'balance_to_be_received',
    'days_overdue', 'payment_status',
)


def recompute(invoice, *, today: date | None = None) -> set[str]:
    """Apply all formulas to a CustomerInvoice instance in-place.

    Returns the set of field names that were changed (for update_fields).
    """
    changed: set[str] = set()

    def setf(name: str, value):
        old = getattr(invoice, name, None)
        if old != value and value is not None:
            setattr(invoice, name, value)
            changed.add(name)

    # PPC + Retention (driven by invoice_amount + ICV)
    setf('ppc_value', compute_ppc_value(invoice.invoice_amount, invoice.icv_applicable))
    setf('retention', compute_retention(invoice.ppc_value, invoice.icv_applicable))

    # VAT-stripped amounts
    setf('amount_excl_vat',      compute_amount_excl_vat(invoice.invoice_amount))
    setf('paid_amount_excl_vat', compute_paid_amount_excl_vat(invoice.actual_payment_received))

    # AED equivalent
    setf('invoice_amount_aed', compute_invoice_amount_aed(invoice.invoice_amount, invoice.currency))

    # Due date (only when blank — never overwrite an explicit value)
    if not invoice.due_date:
        setf('due_date', compute_due_date(invoice.invoice_date, invoice.payment_terms))

    # Balance + days overdue
    setf('balance_to_be_received', compute_balance(invoice.invoice_amount, invoice.actual_payment_received))

    # Status auto-derive (only when blank OR rules say so)
    if FINANCE_RULES['status_auto_derive']:
        derived = derive_payment_status(
            invoice.invoice_amount,
            invoice.actual_payment_received,
            invoice.due_date,
            invoice.payment_status,
            today=today,
        )
        # Only override 'pending' (the default) — respect any explicit edit
        S = FINANCE_RULES['status']
        if invoice.payment_status in (None, '', S['pending']) or derived in {S['paid'], S['overdue'], S['partial']}:
            setf('payment_status', derived)

    days = compute_days_overdue(invoice.due_date, invoice.payment_status, today=today)
    if days != invoice.days_overdue:
        invoice.days_overdue = days
        changed.add('days_overdue')

    return changed
