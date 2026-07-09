"""Phase 3 — Earned Value Management (stub)."""
from __future__ import annotations

from ..config import is_phase_enabled


def compute_evm(project) -> dict:
    if not is_phase_enabled('phase_3_evm_forecast'):
        raise NotImplementedError('Phase 3 — EVM Forecasting is not enabled.')
    raise NotImplementedError('EVM computation pending.')
