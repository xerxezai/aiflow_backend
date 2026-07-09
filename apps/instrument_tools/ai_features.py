"""
Instrument Tools — AI feature flags.

Single source of truth for enabling / disabling the AI layer on top of the
deterministic rule engine in `services.py`. Each flag is independently
toggleable per-deployment via Django settings or environment variables, so
the rollout can be staged safely.

Order of precedence (highest first):
  1. Django settings    : `INSTRUMENT_TOOLS_AI_FEATURES = {...}`
  2. Environment vars   : `INSTRUMENT_TOOLS_AI_<FEATURE>=true|false`
  3. Module-level defaults below.
"""
from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

# ─── Soft-coded defaults (feature OFF if the dependency is missing) ──────────
_AI_DEFAULTS: dict[str, bool] = {
    'smart_header_mapping':     True,   # Step 1 — fuzzy + LLM column matching
    'tag_pattern_learning':     True,   # Step 2 — derive project tag regex
    'service_classification':   True,   # Step 3 — service text → IO type
    'unit_normalisation':       True,   # Step 4 — unit conversion / sanity
    'loop_template_expansion':  True,   # Step 5 — one valve → full loop
    'cabinet_auto_allocation':  True,   # Step 6 — Node/Slot/Channel pooling
    'rule_engine_explanations': True,   # Step 7 — plain English why
    'anomaly_detection':        True,   # Step 8 — cross-row outliers
}

# Soft-coded env-var prefix.
_ENV_PREFIX = 'INSTRUMENT_TOOLS_AI_'

# Cached resolved flags (lazy).
_RESOLVED: dict[str, bool] | None = None


def _coerce_bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ('1', 'true', 'yes', 'on', 'enabled'):
        return True
    if s in ('0', 'false', 'no', 'off', 'disabled'):
        return False
    return None


def _resolve() -> dict[str, bool]:
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED
    flags = dict(_AI_DEFAULTS)
    # Django settings override
    try:
        from django.conf import settings  # noqa: WPS433
        for k, v in getattr(settings, 'INSTRUMENT_TOOLS_AI_FEATURES', {}).items():
            b = _coerce_bool(v)
            if k in flags and b is not None:
                flags[k] = b
    except Exception:                                                  # noqa: BLE001
        pass
    # Env-var override
    for k in flags:
        env_val = os.environ.get(f'{_ENV_PREFIX}{k.upper()}')
        b = _coerce_bool(env_val)
        if b is not None:
            flags[k] = b
    _RESOLVED = flags
    return flags


def is_enabled(feature: str) -> bool:
    return bool(_resolve().get(feature, False))


def all_flags() -> dict[str, bool]:
    return dict(_resolve())


def reset_cache() -> None:
    """Test hook — recompute flags on next call."""
    global _RESOLVED
    _RESOLVED = None
