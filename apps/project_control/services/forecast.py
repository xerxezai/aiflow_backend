"""Phase 3 — Cashflow / EAC forecasting (stub)."""
from __future__ import annotations

from ..config import is_phase_enabled


def forecast_cashflow(project) -> dict:
    if not is_phase_enabled('phase_3_cashflow_curve'):
        raise NotImplementedError('Phase 3 — Cashflow Curve is not enabled.')
    raise NotImplementedError('Cashflow forecasting pending.')
