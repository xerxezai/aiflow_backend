"""Phase 4 — Change detection from documents (stub)."""
from __future__ import annotations

from ..config import is_phase_enabled


def detect_changes(document) -> dict:
    if not is_phase_enabled('phase_4_change_detection'):
        raise NotImplementedError('Phase 4 — Change Detection is not enabled.')
    raise NotImplementedError('Change detection pending.')
