"""Phase 2 — AI Take-Off (stub).

Lights up when PROJECT_CONTROL_PHASE_2_AI_TAKEOFF=true. Until then every
public entry point raises NotImplementedError so callers can render the
"Coming in Phase 2" stub card.
"""
from __future__ import annotations

from ..config import is_phase_enabled


def run_takeoff(document) -> dict:
    if not is_phase_enabled('phase_2_ai_takeoff'):
        raise NotImplementedError('Phase 2 — AI Take-Off is not enabled.')
    # Reserved for future implementation: call OpenAI vision on drawing PDF,
    # return BOQ-shaped JSON to feed Estimate creation.
    raise NotImplementedError('AI Take-Off implementation pending.')
