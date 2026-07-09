"""
AI Service-Text Classifier  (Step 3).

Given a free-form description / service / instrument-type string, infer:
  • Instrument family (transmitter, valve, switch, analyzer, RTD, …)
  • Likely signal type (AI / AO / DI / DO / RTD / TC / HART / SOFT)
  • Whether the signal is HMI-only ("SOFT") or has a physical channel

Soft-coded keyword packs — one per family. The classifier is deterministic and
returns a confidence score; the AI layer can be turned off via feature flag.
"""
from __future__ import annotations

import re
from typing import Iterable

# Soft-coded family → keyword pack mapping.
# Each pack: ordered list of (regex, signal_type, family, score_boost).
_PACKS: tuple[tuple[str, str, str, float], ...] = (
    # ─── Analyzers ──────────────────────────────────────────────────────────
    (r'\b(ph|conductivity|oxygen|h2s|gas)\s*(analy[sz]er|sensor|detector)\b', 'AI-FF',  'analyzer',        1.0),
    (r'\banaly[sz]er\b',                                                    'AI-FF',  'analyzer',        0.7),
    (r'\banalyzer\s*(alarm|fault)\b',                                       'SOFT',   'analyzer_alarm',  0.9),

    # ─── Transmitters ──────────────────────────────────────────────────────
    (r'\b(pressure|press)\s*(indicating\s*)?transmitter\b',                 'AI-FF',  'pressure_tx',     1.0),
    (r'\btemperature\s*(indicating\s*)?transmitter\b',                      'AI-FF',  'temperature_tx',  1.0),
    (r'\bflow\s*(transmitter|element)\b',                                   'AI-FF',  'flow_tx',         1.0),
    (r'\blevel\s*(transmitter|gauge)\b',                                    'AI-FF',  'level_tx',        1.0),
    (r'\bd[/-]?p\s*type\b',                                                 'AI-FF',  'flow_tx',         0.9),
    (r'\btransmitter\b',                                                    'AI-FF',  'generic_tx',      0.6),

    # ─── Indicators / soft tags ────────────────────────────────────────────
    (r'\bindicator\s*-\s*dcs\b',                                            'SOFT',   'indicator',       1.0),
    (r'\b(indicator|alarm)\b',                                              'SOFT',   'indicator',       0.5),
    (r'\bcontroller\s*-\s*dcs\b',                                           'SOFT',   'controller',      1.0),
    (r'\bdiscrepancy\s*alarm\b',                                            'SOFT',   'discrepancy',     1.0),

    # ─── Valves & actuators ────────────────────────────────────────────────
    (r'\bcontrol\s*valve\s*positioner\b',                                   'AO-FF',  'cv_positioner',   1.0),
    (r'\bcontrol\s*valve\b',                                                'AO-FF',  'control_valve',   0.9),
    (r'\bon[-\s]*off\s*valve\b|\bxv\b|\bshutdown\s*valve\b',                'DO-R',   'on_off_valve',    1.0),
    (r'\bsolenoid\s*valve\b|\bsov\b|\bxy\b',                                'DO-R',   'solenoid_valve',  1.0),

    # ─── Switches & limits ─────────────────────────────────────────────────
    (r'\bposition\s*\(?limit\)?\s*switch\s*(open|closed)\b',                'DI-R',   'limit_switch',    1.0),
    (r'\bhand\s*switch\s*\(?auto|manual|local\)?\b',                        'SOFT',   'hand_switch',     0.9),
    (r'\bhand\s*switch\b|\bhs\b',                                           'SOFT',   'hand_switch',     0.7),
    (r'\blocal\s*hand\s*switch\b',                                          'DI-R',   'local_hs',        1.0),

    # ─── Temperature elements ──────────────────────────────────────────────
    (r'\b(rtd|pt100|pt1000)\b',                                             'RTD',    'rtd_element',     1.0),
    (r'\bthermocouple\b|\btc\s*element\b',                                  'TC',     'tc_element',      1.0),
)


def classify(text: str) -> dict:
    """Return {signal_type, family, confidence} for a single service text."""
    if not text:
        return {'signal_type': '', 'family': '', 'confidence': 0.0}
    s = re.sub(r'\s+', ' ', str(text).lower())
    best = ('', '', 0.0)
    for rx, sig, fam, score in _PACKS:
        if re.search(rx, s, flags=re.IGNORECASE):
            if score > best[2]:
                best = (sig, fam, score)
    return {
        'signal_type': best[0],
        'family':      best[1],
        'confidence':  round(best[2], 3),
    }


def classify_rows(rows: Iterable[dict],
                  desc_keys: tuple[str, ...] = ('description', 'service', 'tag_service',
                                                'instrument_type'),
                  out_key: str = 'signal_type',
                  family_key: str = 'family',
                  fill_only_when_blank: bool = True) -> list[dict]:
    """Apply the classifier to a list of rows in-place (returns the list).

    For each row, look at the soft-coded `desc_keys` for a non-empty string,
    classify it, and write the result into `signal_type` and `family` —
    only when those fields are blank (so explicit values are preserved).
    """
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = ''
        for k in desc_keys:
            v = row.get(k)
            if isinstance(v, str) and v.strip():
                text = v
                break
        if text:
            pred = classify(text)
            if pred['signal_type'] and (not fill_only_when_blank or _is_blank(row.get(out_key))):
                row.setdefault(out_key, pred['signal_type'])
                if _is_blank(row.get(out_key)):
                    row[out_key] = pred['signal_type']
            if pred['family'] and (not fill_only_when_blank or _is_blank(row.get(family_key))):
                row[family_key] = pred['family']
            row['_ai_classifier_confidence'] = pred['confidence']
        out.append(row)
    return out


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())
