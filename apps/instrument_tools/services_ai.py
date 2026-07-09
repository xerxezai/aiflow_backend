"""
AI Orchestrator for Instrument Tools.

Wraps the deterministic `services.run_generator` / `services.run_qc` core
with the optional AI capabilities (each gated by a feature flag in
`ai_features.py`).  Failing AI steps are *silent* — they degrade gracefully
to the deterministic-only result.

Pipelines:
  Generator: input_rows
             → header remap        (smart_header_mapping)
             → service classify    (service_classification)
             → loop expansion      (loop_template_expansion)
             → services.run_generator
             → cabinet allocation  (cabinet_auto_allocation)
             → tag-pattern issues  (tag_pattern_learning)
             → unit/range issues   (unit_normalisation)
             → cross-row anomalies (anomaly_detection)
             → explainer           (rule_engine_explanations)

  QC:        input_rows
             → header remap
             → services.run_qc
             → tag-pattern issues + unit/range + anomalies + explainer
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from . import (
    ai_anomaly,
    ai_cabinet_allocator,
    ai_explainer,
    ai_features,
    ai_header_mapper,
    ai_loop_templates,
    ai_service_classifier,
    ai_tag_patterns,
    ai_units,
    services as svc,
)

logger = logging.getLogger(__name__)

# ─── Soft-coded performance budgets (env-overridable) ───────────────────────
# If a single AI step exceeds this many seconds, it is skipped and the
# pipeline falls back to the deterministic-only result.
_STEP_BUDGET_S    = float(os.getenv('INSTRUMENT_TOOLS_AI_STEP_BUDGET_S', '8'))
# Overall AI pipeline budget — anything beyond returns the partial result.
_TOTAL_BUDGET_S   = float(os.getenv('INSTRUMENT_TOOLS_AI_BUDGET_S', '45'))
# Loop expansion is the most expensive step; skip it when row count exceeds
# this threshold to keep generator latency predictable.
_LOOP_EXPAND_MAX_ROWS = int(os.getenv('INSTRUMENT_TOOLS_LOOP_EXPAND_MAX_ROWS', '300'))


def _safe(name: str, fn, *a, **kw):
    """Call an AI step with a wall-time budget; degrade gracefully on failure."""
    start = time.monotonic()
    try:
        result = fn(*a, **kw)
        elapsed = time.monotonic() - start
        if elapsed > _STEP_BUDGET_S:
            logger.warning('AI step %s took %.1fs (>%.1fs budget).',
                           name, elapsed, _STEP_BUDGET_S)
        return result
    except Exception:
        logger.exception('AI step %s failed; falling back', name)
        return a[-1] if a else None


def _apply_header_remap(tool: str, rows: list[dict]) -> tuple[list[dict], Optional[dict]]:
    if not rows or not ai_features.is_enabled('smart_header_mapping'):
        return rows, None
    headers = list(rows[0].keys()) if isinstance(rows[0], dict) else []
    if not headers:
        return rows, None
    info = ai_header_mapper.map_headers(tool, headers)
    if not info or not info.get('mapping'):
        return rows, info
    mapping = info['mapping']
    remapped = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        new = {}
        for k, v in r.items():
            new[mapping.get(k, k)] = v
        remapped.append(new)
    return remapped, info


def _apply_service_classifier(rows: list[dict]) -> list[dict]:
    if not ai_features.is_enabled('service_classification'):
        return rows
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        # Only fill blanks.
        if r.get('signal_type') and r.get('family'):
            continue
        text = ' '.join(str(r.get(k) or '') for k in ('description', 'service', 'tag'))
        info = ai_service_classifier.classify(text)
        if info.get('signal_type') and not r.get('signal_type'):
            r['signal_type'] = info['signal_type']
        if info.get('family') and not r.get('family'):
            r['family'] = info['family']
        if info.get('confidence') is not None:
            r['_ai_classifier_confidence'] = info['confidence']
    return rows


def _apply_loop_expansion(rows: list[dict]) -> tuple[list[dict], dict]:
    if not ai_features.is_enabled('loop_template_expansion'):
        return rows, {}
    # Soft-coded guard: skip expansion when the input is already large, to
    # avoid an O(n × template-size) explosion that blows the request budget.
    if len(rows or []) > _LOOP_EXPAND_MAX_ROWS:
        logger.info('Skipping loop expansion: %d rows exceeds threshold %d.',
                    len(rows or []), _LOOP_EXPAND_MAX_ROWS)
        return rows, {'skipped': 'row_count_threshold'}
    out = ai_loop_templates.expand_many(rows or [])
    return out.get('rows', rows), {
        'families': out.get('families', {}),
        'skipped':  out.get('skipped',  []),
    }


def _apply_cabinet_allocation(rows: list[dict]) -> list[dict]:
    if not ai_features.is_enabled('cabinet_auto_allocation'):
        return []
    out = ai_cabinet_allocator.allocate_rows(rows or [])
    return out.get('report', [])


def _apply_tag_pattern(rows: list[dict]) -> tuple[list[dict], Optional[dict]]:
    if not ai_features.is_enabled('tag_pattern_learning'):
        return [], None
    tags = [str(r.get('tag') or '') for r in rows if isinstance(r, dict)]
    learned = ai_tag_patterns.learn(tags)
    if not learned.get('pattern'):
        return [], learned
    issues = ai_tag_patterns.validate(tags, learned) or []
    return issues, learned


def _apply_unit_checks(rows: list[dict]) -> list[dict]:
    if not ai_features.is_enabled('unit_normalisation'):
        return []
    issues: list[dict] = []
    for idx, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        for it in ai_units.sanity_check_range(r):
            it.setdefault('row', idx)
            it.setdefault('tag', r.get('tag'))
            issues.append(it)
    return issues


def _apply_anomalies(rows: list[dict]) -> list[dict]:
    if not ai_features.is_enabled('anomaly_detection'):
        return []
    return ai_anomaly.detect(rows or []) or []


def _apply_explainer(issues: list[dict]) -> list[dict]:
    if not ai_features.is_enabled('rule_engine_explanations'):
        return issues
    return ai_explainer.enrich_issues(issues)


def _augment_result(result: dict, *, tag_info=None, header_info=None,
                    cab_report=None, loop_info=None) -> dict:
    ai_meta = {
        'flags': ai_features.all_flags(),
    }
    if header_info:
        ai_meta['header_map']   = header_info.get('mapping')
        ai_meta['header_confidence'] = header_info.get('confidence')
        ai_meta['unmapped_headers']  = header_info.get('unmapped')
        ai_meta['header_method'] = header_info.get('method')
    if tag_info:
        ai_meta['tag_pattern'] = {
            'pattern':  tag_info.get('pattern'),
            'label':    tag_info.get('label'),
            'coverage': tag_info.get('coverage'),
        }
    if cab_report:
        ai_meta['cabinet_utilisation'] = cab_report
    if loop_info:
        ai_meta['loop_expansion'] = loop_info
    result['ai'] = ai_meta
    return result


# ─── Public entry points ─────────────────────────────────────────────────────
def run(tool: str, mode: str, rows: list[dict]) -> dict:
    """Single entry — dispatches to generator or QC pipeline."""
    if mode == svc.MODE_QC:
        return run_qc(tool, rows)
    return run_generator(tool, rows)


def run_generator(tool: str, rows: list[dict]) -> dict:
    remapped,  header_info = _safe('header_remap', _apply_header_remap, tool, rows or [])
    if not isinstance(remapped, list):
        remapped, header_info = rows or [], None

    _safe('service_classify', _apply_service_classifier, remapped)
    expanded, loop_info = _safe('loop_expansion', _apply_loop_expansion, remapped) or (remapped, {})

    # Deterministic core — never altered.
    result = svc.run_generator(tool, expanded)

    cab_report = _safe('cabinet_alloc', _apply_cabinet_allocation, result.get('rows', [])) or []

    # Augment issues additively.
    extra_issues: list[dict] = []
    tag_issues, tag_info = _safe('tag_pattern', _apply_tag_pattern, result.get('rows', [])) or ([], None)
    extra_issues.extend(tag_issues or [])
    extra_issues.extend(_safe('unit_checks',  _apply_unit_checks,  result.get('rows', [])) or [])
    extra_issues.extend(_safe('anomalies',    _apply_anomalies,    result.get('rows', [])) or [])

    combined = list(result.get('issues') or []) + extra_issues
    combined = _safe('explainer', _apply_explainer, combined) or combined
    result['issues'] = combined
    # Refresh summary now that we added issues.
    try:
        result['summary'] = svc._summarise(combined, len(result.get('rows') or []))
    except Exception:
        pass

    return _augment_result(
        result,
        tag_info=tag_info,
        header_info=header_info,
        cab_report=cab_report,
        loop_info=loop_info,
    )


def run_qc(tool: str, rows: list[dict]) -> dict:
    remapped, header_info = _safe('header_remap', _apply_header_remap, tool, rows or [])
    if not isinstance(remapped, list):
        remapped, header_info = rows or [], None

    result = svc.run_qc(tool, remapped)
    normalised = result.get('normalised') or []

    extra_issues: list[dict] = []
    tag_issues, tag_info = _safe('tag_pattern', _apply_tag_pattern, normalised) or ([], None)
    extra_issues.extend(tag_issues or [])
    extra_issues.extend(_safe('unit_checks', _apply_unit_checks, normalised) or [])
    extra_issues.extend(_safe('anomalies',   _apply_anomalies,   normalised) or [])

    combined = list(result.get('issues') or []) + extra_issues
    combined = _safe('explainer', _apply_explainer, combined) or combined
    result['issues'] = combined
    try:
        result['summary'] = svc._summarise(combined, len(normalised))
    except Exception:
        pass

    return _augment_result(result, tag_info=tag_info, header_info=header_info)
