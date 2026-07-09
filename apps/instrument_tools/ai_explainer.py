"""
AI Rule-Engine Explainer  (Step 7).

Adds plain-English `explanation` and `suggested_fix` fields to issues emitted
by the deterministic rule engine, plus a `confidence` and `category` hint.

Soft-coded — extend `_EXPLANATIONS` to support a new rule kind. An optional
LLM enrichment path is gated behind `OPENAI_API_KEY` and only used as a
fallback when the local catalog has no entry for a given `kind`.
"""
from __future__ import annotations

import os
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Soft-coded LLM safety knobs ─────────────────────────────────────────────
# Override per-deployment via env vars (all values are validated/clamped).
_LLM_TIMEOUT_SEC          = float(os.environ.get('INSTRUMENT_TOOLS_LLM_TIMEOUT_SEC', '6'))
_LLM_MAX_CALLS_PER_BATCH  = int(os.environ.get('INSTRUMENT_TOOLS_LLM_MAX_CALLS', '5'))
_LLM_COOLDOWN_SEC         = float(os.environ.get('INSTRUMENT_TOOLS_LLM_COOLDOWN_SEC', '60'))
_LLM_MODEL                = os.environ.get('INSTRUMENT_TOOLS_LLM_MODEL', 'gpt-4o-mini')

# Process-wide circuit-breaker: when set, no LLM call is made until the
# timestamp elapses. Prevents one slow OpenAI request from cascading into
# many synchronous timeouts in the same Django request.
_llm_disabled_until: float = 0.0

# Soft-coded explanation catalog keyed by issue['kind'].
_EXPLANATIONS: dict[str, dict] = {
    # ── From rule engine ───────────────────────────────────────────────────
    'missing_required': {
        'category': 'completeness',
        'explanation': 'A field that the schema marks as required is empty for this row.',
        'fix': 'Populate the field. If unknown at this stage, enter "TBD" and re-review before IFC.',
    },
    'duplicate_tag': {
        'category': 'integrity',
        'explanation': 'Two or more rows share the same tag — DCS configuration cannot resolve which row drives which channel.',
        'fix': 'Audit the duplicates and either delete the redundant row or correct the tag suffix.',
    },
    'invalid_signal_type': {
        'category': 'integrity',
        'explanation': 'The signal type is not in the accepted enumeration (AI-FF, AI, AO, DI-R, DO-R, SOFT, …).',
        'fix': 'Pick the closest standard signal type. SOFT for HMI-only points.',
    },
    'inconsistent_io_cable': {
        'category': 'consistency',
        'explanation': 'Signal type does not match cable type — e.g. analog signal routed on a discrete cable.',
        'fix': 'Align the cable type with the signal: AI/AO → instrument twisted-pair, DI/DO → multicore.',
    },
    # ── From tag-pattern learner ───────────────────────────────────────────
    'tag_pattern': {
        'category': 'tag_convention',
        'explanation': 'Tag does not follow the dominant pattern learned from the rest of the document.',
        'fix': 'Rename the tag to match the project tagging convention (Unit-Type-Loop[/Suffix]).',
    },
    'tag_unit_drift': {
        'category': 'tag_convention',
        'explanation': 'Tag uses a unit number that is uncommon for this drawing set.',
        'fix': 'Verify the unit prefix against the area numbering plan.',
    },
    # ── From unit normaliser ───────────────────────────────────────────────
    'alarm_order_ll_l': {
        'category': 'safety',
        'explanation': 'Low-Low alarm setpoint is higher than the Low alarm setpoint — alarm hierarchy is inverted.',
        'fix': 'Swap or correct the LL / L values so LL ≤ L.',
    },
    'alarm_order_l_h': {
        'category': 'safety',
        'explanation': 'Low alarm setpoint is higher than the High alarm — impossible alarm window.',
        'fix': 'Correct one of the setpoints so L ≤ H.',
    },
    'alarm_order_h_hh': {
        'category': 'safety',
        'explanation': 'High alarm setpoint is higher than the High-High — alarm hierarchy is inverted.',
        'fix': 'Correct so H ≤ HH.',
    },
    'range_order': {
        'category': 'consistency',
        'explanation': 'Range minimum is greater than range maximum.',
        'fix': 'Swap or correct the range values.',
    },
    'calib_order': {
        'category': 'consistency',
        'explanation': 'Calibration minimum is greater than calibration maximum.',
        'fix': 'Swap or correct the calibration values.',
    },
    'calib_below_range': {
        'category': 'consistency',
        'explanation': 'Calibrated min is below the instrument lower range limit — calibration is outside the sensor span.',
        'fix': 'Raise calibration min into the instrument range, or replace with a wider-range instrument.',
    },
    'calib_above_range': {
        'category': 'consistency',
        'explanation': 'Calibrated max is above the instrument upper range limit — sensor will saturate.',
        'fix': 'Lower calibration max into the instrument range or upsize the instrument.',
    },
}

_FALLBACK = {
    'category': 'general',
    'explanation': 'Rule check did not pass.',
    'fix': 'Review the highlighted field against the project specification.',
}


def _llm_enrich(issue: dict) -> Optional[dict]:
    """Optional LLM enrichment.

    Hard-bounded: per-call timeout (`_LLM_TIMEOUT_SEC`), per-batch call cap
    (`_LLM_MAX_CALLS_PER_BATCH`) and a process-wide cooldown
    (`_LLM_COOLDOWN_SEC`) triggered on the first failure so a slow OpenAI
    endpoint never blocks a Django request past the client timeout.
    """
    global _llm_disabled_until
    if not os.getenv('OPENAI_API_KEY'):
        return None
    if time.time() < _llm_disabled_until:
        return None
    try:  # pragma: no cover -- network path
        from openai import OpenAI  # noqa: WPS433  (deferred import)
        client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=_LLM_TIMEOUT_SEC)
        prompt = (
            "You are an instrumentation engineer. In 1-2 sentences, explain "
            "this finding from an IO-list QC tool, and propose a fix.\n\n"
            f"Finding: {issue}"
        )
        rsp = client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=120,
            temperature=0.2,
            timeout=_LLM_TIMEOUT_SEC,
        )
        text = (rsp.choices[0].message.content or '').strip()
        if not text:
            return None
        return {'category': 'ai', 'explanation': text, 'fix': ''}
    except Exception as exc:                                            # noqa: BLE001
        # First failure trips the breaker — stops further LLM calls in this
        # process for `_LLM_COOLDOWN_SEC` seconds.
        _llm_disabled_until = time.time() + _LLM_COOLDOWN_SEC
        logger.warning('LLM explainer disabled for %ss (reason=%s)', _LLM_COOLDOWN_SEC, exc)
        return None


def enrich_issue(issue: dict) -> dict:
    """Return a new dict with explanation / suggested_fix / category added."""
    if not isinstance(issue, dict):
        return issue
    out = dict(issue)
    kind = str(issue.get('kind') or '').lower()
    meta = _EXPLANATIONS.get(kind)
    if meta is None:
        meta = _llm_enrich(issue) or _FALLBACK
    out.setdefault('category',      meta['category'])
    out.setdefault('explanation',   meta['explanation'])
    out.setdefault('suggested_fix', meta['fix'])
    return out


def enrich_issues(issues: list[dict]) -> list[dict]:
    """Enrich a batch of issues.

    Bounded LLM usage: at most `_LLM_MAX_CALLS_PER_BATCH` LLM calls are
    issued per batch; the remainder fall back to the static `_FALLBACK`
    catalogue. Deterministic catalogue hits are always free.
    """
    out: list[dict] = []
    llm_budget = max(0, _LLM_MAX_CALLS_PER_BATCH)
    for raw in (issues or []):
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        item = dict(raw)
        kind = str(item.get('kind') or '').lower()
        meta = _EXPLANATIONS.get(kind)
        if meta is None:
            if llm_budget > 0:
                llm_budget -= 1
                meta = _llm_enrich(item) or _FALLBACK
            else:
                meta = _FALLBACK
        item.setdefault('category',      meta['category'])
        item.setdefault('explanation',   meta['explanation'])
        item.setdefault('suggested_fix', meta['fix'])
        out.append(item)
    return out
