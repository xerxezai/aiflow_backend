"""Phase 1 — Estimate vs Estimate variance.

Compares two estimates (typically baseline vs latest revised) at the WBS-code
or discipline level and returns variance percentages bucketed by the
soft-coded VARIANCE_THRESHOLDS.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional

from ..config import VARIANCE_THRESHOLDS
from ..models import Estimate, EstimateLineItem


def _bucket(delta_pct: float) -> str:
    if delta_pct <= VARIANCE_THRESHOLDS['green_max']:
        return 'green'
    if delta_pct <= VARIANCE_THRESHOLDS['amber_max']:
        return 'amber'
    return 'red'


def _aggregate(estimate: Estimate, group_by: str) -> Dict[str, Decimal]:
    qs = EstimateLineItem.objects.filter(estimate=estimate, is_deleted=False)
    out: Dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    for li in qs.only('wbs_code', 'discipline', 'line_total'):
        key = (li.wbs_code if group_by == 'wbs' else li.discipline) or '(unassigned)'
        out[key] += Decimal(li.line_total or 0)
    return dict(out)


def compute_variance(
    *,
    project,
    base_estimate: Optional[Estimate] = None,
    compare_estimate: Optional[Estimate] = None,
    group_by: str = 'wbs',
) -> Dict:
    """Return a variance report dict comparing two estimates.

    If `base_estimate` or `compare_estimate` is None, defaults are picked:
        base    → latest 'baseline' (else oldest estimate of any kind)
        compare → latest 'revised' (else newest estimate of any kind)
    """
    qs = Estimate.objects.filter(project=project, is_deleted=False)

    if base_estimate is None:
        base_estimate = (
            qs.filter(kind='baseline').order_by('-version').first()
            or qs.order_by('created_at').first()
        )
    if compare_estimate is None:
        compare_estimate = (
            qs.filter(kind='revised').order_by('-version').first()
            or qs.order_by('-created_at').first()
        )

    if not base_estimate or not compare_estimate:
        return {
            'project_id': project.id,
            'project_code': project.code,
            'group_by': group_by,
            'base': None,
            'compare': None,
            'rows': [],
            'totals': {'base': '0', 'compare': '0', 'delta': '0', 'delta_pct': 0, 'bucket': 'green'},
            'thresholds': VARIANCE_THRESHOLDS,
            'message': 'At least two estimates are required to compute variance.',
        }

    base_map = _aggregate(base_estimate, group_by)
    cmp_map  = _aggregate(compare_estimate, group_by)

    all_keys = sorted(set(base_map) | set(cmp_map))
    rows: List[Dict] = []
    total_base = Decimal('0')
    total_cmp = Decimal('0')
    for key in all_keys:
        b = base_map.get(key, Decimal('0'))
        c = cmp_map.get(key, Decimal('0'))
        delta = c - b
        delta_pct = float((delta / b) * 100) if b > 0 else (100.0 if c > 0 else 0.0)
        rows.append({
            'key': key,
            'base_amount': str(b),
            'compare_amount': str(c),
            'delta_amount': str(delta),
            'delta_pct': round(delta_pct, 2),
            'bucket': _bucket(delta_pct),
        })
        total_base += b
        total_cmp += c

    total_delta = total_cmp - total_base
    total_pct = float((total_delta / total_base) * 100) if total_base > 0 else 0.0

    return {
        'project_id': project.id,
        'project_code': project.code,
        'group_by': group_by,
        'base': {
            'id': base_estimate.id, 'kind': base_estimate.kind,
            'version': base_estimate.version, 'total': str(base_estimate.total_amount),
        },
        'compare': {
            'id': compare_estimate.id, 'kind': compare_estimate.kind,
            'version': compare_estimate.version, 'total': str(compare_estimate.total_amount),
        },
        'rows': rows,
        'totals': {
            'base': str(total_base),
            'compare': str(total_cmp),
            'delta': str(total_delta),
            'delta_pct': round(total_pct, 2),
            'bucket': _bucket(total_pct),
        },
        'thresholds': VARIANCE_THRESHOLDS,
    }
