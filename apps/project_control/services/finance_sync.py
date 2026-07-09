"""Phase 1 — Finance sync.

Conservative soft-join from Finance Invoices → Project.spent. Skips silently
if `apps.finance` is missing or its Invoice model doesn't have the expected
fields, so this never blocks core flow.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict

from ..config import FINANCE_INVOICE_PROJECT_KEY

logger = logging.getLogger(__name__)


def _safe_import_invoice():
    try:
        from apps.finance.models import Invoice  # noqa: WPS433
        return Invoice
    except Exception as exc:  # noqa: BLE001
        logger.info('finance_sync: apps.finance.Invoice unavailable (%s); skipping', exc)
        return None


def _row_matches_project(invoice, code: str) -> bool:
    """Best-effort matcher: line_items JSON key, then extracted_text substring."""
    items = getattr(invoice, 'line_items', None) or []
    if isinstance(items, list):
        for li in items:
            if isinstance(li, dict) and str(li.get(FINANCE_INVOICE_PROJECT_KEY, '')).strip().upper() == code.upper():
                return True
    text = getattr(invoice, 'extracted_text', '') or ''
    return bool(text) and code.upper() in text.upper()


def sync_project_spend(project) -> Dict:
    """Recompute project.spent from approved/posted invoices linked to it."""
    Invoice = _safe_import_invoice()
    if Invoice is None:
        return {'project_code': project.code, 'matched_invoices': 0, 'total_spent': '0', 'skipped': True}

    candidates = Invoice.objects.all()
    matched_total = Decimal('0')
    matched_count = 0
    for inv in candidates.iterator(chunk_size=200):
        try:
            if _row_matches_project(inv, project.code):
                amt = inv.total_amount or inv.amount or 0
                matched_total += Decimal(str(amt))
                matched_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug('finance_sync: invoice %s skipped: %s', getattr(inv, 'id', '?'), exc)

    project.spent = matched_total
    project.save(update_fields=['spent', 'updated_at'])

    return {
        'project_code': project.code,
        'matched_invoices': matched_count,
        'total_spent': str(matched_total),
        'skipped': False,
    }


def sync_all_projects() -> Dict:
    """Iterate every active project and recompute spend. Returns aggregate stats."""
    from apps.core.project_models import Project  # local import; keeps boot cheap
    Invoice = _safe_import_invoice()
    if Invoice is None:
        return {'projects': 0, 'skipped': True}

    projects = Project.objects.filter(is_deleted=False)
    total = 0
    total_spent = Decimal('0')
    for proj in projects.iterator(chunk_size=50):
        res = sync_project_spend(proj)
        if not res.get('skipped'):
            total += 1
            total_spent += Decimal(res['total_spent'])

    return {'projects': total, 'total_spent': str(total_spent), 'skipped': False}
