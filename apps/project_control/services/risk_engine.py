"""Phase 4 — Risk analytics (stub)."""
from __future__ import annotations

from ..config import is_phase_enabled


def score_project_risks(project) -> dict:
    if not is_phase_enabled('phase_4_risk_analytics'):
        raise NotImplementedError('Phase 4 — Risk Analytics is not enabled.')
    raise NotImplementedError('Risk scoring pending.')
