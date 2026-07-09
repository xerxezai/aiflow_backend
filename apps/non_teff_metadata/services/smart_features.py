"""
Non-TEFF Smart Features service.

This module is **fully additive** — it does not touch the core regex / vision
extractor, the storage layer, or the existing recommendation flow. It is a
post-extraction analytic layer that powers the new "AI Insights" side panel
in the Non-TEFF Metadata Generator UI.

Eight feature endpoints are exposed via ``smart_views.py``; each lives behind
a small pure function in this module. Where an LLM is useful the function
reuses the cost-first provider dispatch already defined in
``ai_recommendations.py`` so we don't duplicate provider plumbing.

All thresholds, regex hints, and column lists are SOFT-CODED in
``SMART_CONFIG`` below — change behaviour without touching logic.

Functions (all return JSON-serialisable dicts; none ever raise):

  1. compute_confidence_scores(items)        — per-cell + per-row confidence
  2. repair_row(row, text_excerpt='')        — AI proposes safe fixes
  3. detect_consistency_issues(items)        — cross-document conflicts
  4. translate_nl_query(query, items)        — NL → filter spec
  5. classify_documents(items)               — type/discipline per row
  6. auto_link_tags(items)                   — find cross-refs inside batch
  7. build_revision_timeline(items)          — group by doc_no + summarise
  8. suggest_bulk_edits(items, selected_idx) — pattern-based edit hints
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import ai_recommendations as _reco

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED configuration — single place to tune every smart feature.
# ---------------------------------------------------------------------------

SMART_CONFIG: Dict[str, Any] = {
    # ---- Confidence scoring ------------------------------------------------
    # Base score by source. The extractor flags filled cells; if no source is
    # given we fall back to "regex".
    'confidence_source_weights': {
        'regex':     78,
        'vision':    82,
        'ocr':       70,
        'ai':        85,
        'manual':    98,
        'default':   60,
        'empty':      0,
    },
    # Per-field validators — bump or trim score by validating shape.
    'confidence_validators': {
        # field: (regex, bonus_if_match, penalty_if_no_match)
        'document_no':       (r'^[A-Z0-9][A-Z0-9\-_/]{2,}$',                  +8, -25),
        'revision':          (r'^[A-Z0-9]{1,3}$',                              +6, -15),
        'date':              (r'^(\d{4}-\d{2}-\d{2}|\d{2}[./-][A-Z0-9]{2,3}[./-]\d{2,4})$', +6, -20),
        'instrument_tag_no': (r'^[A-Z]{1,4}[-_][0-9]{2,5}[A-Z]?',              +5, -10),
        'equipment_no':      (r'^[A-Z]{1,3}-\d{3,5}[A-Z]?',                    +5, -10),
        'line_number':       (r'^\d{1,4}"[-–]\w',                              +5, -10),
    },
    # Below this overall row score, recommend manual review.
    'review_threshold': 60,

    # ---- Repair Row --------------------------------------------------------
    'repair_max_fields':         8,
    'repair_max_value_chars':    120,

    # ---- Consistency detector ---------------------------------------------
    'consistency_groups_by':     'document_no',
    'consistency_max_issues':    50,

    # ---- NL Query ----------------------------------------------------------
    # Columns the LLM is allowed to reference in its filter spec. Anything
    # else gets dropped before we hand the spec back to the UI.
    'query_allowed_fields': [
        'document_no', 'document_title', 'revision', 'discipline',
        'instrument_tag_no', 'line_number', 'equipment_no',
        'mechanical_component', 'status', 'date', 'originator', 'remarks',
    ],
    'query_max_filters':         6,

    # ---- Classifier --------------------------------------------------------
    # Reuse the existing type_lexicon from ai_recommendations.RECO_CONFIG
    # so we keep ONE source of truth for document-type keywords.

    # ---- Auto-linker -------------------------------------------------------
    'autolink_tag_columns': [
        ('instrument_tag_no', 'instrument'),
        ('equipment_no',      'equipment'),
        ('line_number',       'line'),
    ],

    # ---- Bulk-edit suggestions --------------------------------------------
    'bulk_min_support_ratio':    0.6,    # >= 60% of selected rows agree
    'bulk_min_rows':             3,
    'bulk_max_suggestions':      8,

    # ---- Provider gating ---------------------------------------------------
    # If LLM is unavailable, all features still produce a heuristic answer.
    'enable_llm':                True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(v: Any) -> str:
    return '' if v is None else str(v).strip()


def _llm_available() -> bool:
    if not SMART_CONFIG.get('enable_llm', True):
        return False
    return bool(_reco._gemini_api_key() or _reco._openai_api_key())


def _llm_json(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """
    Thin wrapper over ai_recommendations._dispatch that uses a different
    system prompt. Returns {} on any failure — caller falls back to heuristics.
    """
    if not _llm_available():
        return {}
    try:
        # Temporarily swap system prompt inside the dispatch path. The
        # underlying _call_gemini/_call_openai pull the system prompt from
        # RECO_CONFIG, so we monkey-poke just for this call.
        saved = _reco.RECO_CONFIG.get('system_prompt')
        _reco.RECO_CONFIG['system_prompt'] = system_prompt
        try:
            _, data = _reco._dispatch(user_prompt)
        finally:
            _reco.RECO_CONFIG['system_prompt'] = saved
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning('smart_features LLM call failed: %s', exc)
        return {}


# ===========================================================================
# 1. CONFIDENCE SCORING
# ===========================================================================

def compute_confidence_scores(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Heuristic, deterministic, zero-cost scoring of extracted cells.

    Score model:
      • Empty cell                                → 0
      • Base from `_source` map on the row (if any), else 'default'
      • Validator bonus / penalty by field shape
      • Row score = arithmetic mean of cell scores (cells with value 0 ignored
        only when computing "row confidence among filled cells")

    Returns:
      {
        "scores": [
          {"row_idx": 0,
           "row_overall": 78,
           "row_filled_overall": 84,
           "needs_review": false,
           "cells": {"document_no": 92, "date": 40, ...}},
          ...
        ],
        "overall": 81,            # average row_overall across all rows
        "review_threshold": 60
      }
    """
    weights    = SMART_CONFIG['confidence_source_weights']
    validators = SMART_CONFIG['confidence_validators']
    threshold  = int(SMART_CONFIG['review_threshold'])

    out_rows: List[Dict[str, Any]] = []
    grand_total = 0
    grand_n = 0

    for idx, row in enumerate(items or []):
        sources = (row.get('_sources') or {}) if isinstance(row, dict) else {}
        cells: Dict[str, int] = {}
        total = 0
        n = 0
        filled_total = 0
        filled_n = 0

        for k, v in (row or {}).items():
            if k.startswith('_'):
                continue
            val = _safe_str(v)
            if not val:
                score = 0
            else:
                src = (sources.get(k) or 'default').lower()
                base = int(weights.get(src, weights['default']))
                rule = validators.get(k)
                if rule:
                    pat, bonus, penalty = rule
                    try:
                        score = base + (bonus if re.match(pat, val) else penalty)
                    except re.error:
                        score = base
                else:
                    score = base
                score = max(0, min(100, score))
                filled_total += score
                filled_n += 1
            cells[k] = score
            total += score
            n += 1

        row_overall = int(round(total / n)) if n else 0
        filled_overall = int(round(filled_total / filled_n)) if filled_n else 0
        out_rows.append({
            'row_idx':            idx,
            'row_overall':        row_overall,
            'row_filled_overall': filled_overall,
            'needs_review':       row_overall < threshold,
            'cells':              cells,
        })
        grand_total += row_overall
        grand_n += 1

    return {
        'scores':           out_rows,
        'overall':          int(round(grand_total / grand_n)) if grand_n else 0,
        'review_threshold': threshold,
    }


# ===========================================================================
# 2. REPAIR ROW  (AI proposes fixes; never auto-applies)
# ===========================================================================

_REPAIR_SYSTEM = (
    "You are an engineering document-control assistant. The user will give "
    "you ONE extracted metadata row plus an optional text excerpt from the "
    "source document. Propose corrections ONLY for fields that are empty, "
    "clearly malformed, or inconsistent with the excerpt. NEVER invent "
    "information. If you are unsure, omit the field. Output STRICT JSON."
)

_REPAIR_SCHEMA = (
    '{\n'
    '  "fixes": {\n'
    '     "<field_key>": {\n'
    '         "value":      "<proposed new value, short>",\n'
    '         "reason":     "<one short sentence>",\n'
    '         "confidence": "<low|medium|high>"\n'
    '     }\n'
    '  }\n'
    '}'
)


def repair_row(row: Dict[str, Any], text_excerpt: str = '') -> Dict[str, Any]:
    """Returns {"fixes": {field: {value, reason, confidence}}, "provider": "..."}."""
    if not isinstance(row, dict):
        return {'fixes': {}, 'provider': 'noop'}

    cleaned = {k: _safe_str(v) for k, v in row.items() if not k.startswith('_')}
    excerpt = _safe_str(text_excerpt)[:int(_reco.RECO_CONFIG['text_excerpt_chars'])]

    prompt = (
        "Row metadata:\n```json\n"
        + json.dumps(cleaned, indent=2, ensure_ascii=False)
        + "\n```\n"
        + (f"\nDocument text excerpt:\n```text\n{excerpt}\n```\n" if excerpt else "")
        + "\nReturn corrections matching this schema:\n"
        + _REPAIR_SCHEMA
        + f"\nRules:\n"
          f"- Propose at most {SMART_CONFIG['repair_max_fields']} fixes.\n"
          f"- Each value <= {SMART_CONFIG['repair_max_value_chars']} chars.\n"
          "- Only include a field if you have direct evidence in the row or excerpt.\n"
          "- Output ONLY the JSON object."
    )

    data = _llm_json(_REPAIR_SYSTEM, prompt)
    fixes_raw = data.get('fixes') if isinstance(data, dict) else None
    fixes: Dict[str, Dict[str, str]] = {}

    if isinstance(fixes_raw, dict):
        cap_n = int(SMART_CONFIG['repair_max_fields'])
        cap_v = int(SMART_CONFIG['repair_max_value_chars'])
        for k, v in list(fixes_raw.items())[:cap_n]:
            if not isinstance(v, dict):
                continue
            val = _safe_str(v.get('value'))[:cap_v]
            if not val:
                continue
            conf = _safe_str(v.get('confidence')).lower()
            if conf not in {'low', 'medium', 'high'}:
                conf = 'medium'
            fixes[str(k)] = {
                'value':      val,
                'reason':     _safe_str(v.get('reason'))[:200],
                'confidence': conf,
            }

    # Heuristic fallback — add safe defaults for blank common fields.
    if not fixes:
        for k in ('discipline', 'status', 'revision'):
            if not cleaned.get(k):
                guess = _heuristic_field_guess(k, cleaned, excerpt)
                if guess:
                    fixes[k] = {
                        'value': guess,
                        'reason': 'Heuristic guess from filename / excerpt keywords.',
                        'confidence': 'low',
                    }

    return {'fixes': fixes, 'provider': 'llm' if _llm_available() else 'heuristic'}


def _heuristic_field_guess(field: str, row: Dict[str, str], excerpt: str) -> str:
    hay = (' '.join(row.values()) + ' ' + excerpt).lower()
    if field == 'discipline':
        for disc, pats in _reco.RECO_CONFIG.get('discipline_lexicon', {}).items():
            for pat in pats:
                try:
                    if re.search(pat, hay, re.IGNORECASE):
                        return disc.title()
                except re.error:
                    continue
    if field == 'status':
        for s in ('IFA', 'IFR', 'IFC', 'IFD', 'AFC', 'AFD', 'Draft'):
            if re.search(rf'\b{s}\b', hay, re.IGNORECASE):
                return s
    if field == 'revision':
        m = re.search(r'\brev(?:ision)?\.?\s*([A-Z0-9]{1,3})\b', hay, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return ''


# ===========================================================================
# 3. CONSISTENCY DETECTOR
# ===========================================================================

def detect_consistency_issues(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Surfaces cross-document inconsistencies. Pure-Python — no LLM call,
    runs instantly even on hundreds of rows.

    Issue types:
      • duplicate_doc_no       — same document_no, different title or rev
      • conflicting_title      — same doc_no across rows with diverging titles
      • orphan_tag             — instrument/equipment tag appears in remarks
                                 but no row claims it
      • revision_gap           — doc has rev C but no rev A or B in batch
      • date_outlier           — date > 5y away from batch median year
      • status_outlier         — a single doc_no with mixed statuses
    """
    items = items or []
    issues: List[Dict[str, Any]] = []
    cap = int(SMART_CONFIG['consistency_max_issues'])

    if not items:
        return {'issues': [], 'summary': {'total': 0}}

    # ---- Group by document_no ---------------------------------------------
    by_doc: Dict[str, List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for idx, r in enumerate(items):
        key = _safe_str(r.get('document_no'))
        if key:
            by_doc[key].append((idx, r))

    for doc_no, group in by_doc.items():
        titles = {_safe_str(r.get('document_title')).lower() for _, r in group if _safe_str(r.get('document_title'))}
        revs   = {_safe_str(r.get('revision')).upper()      for _, r in group if _safe_str(r.get('revision'))}
        stats  = {_safe_str(r.get('status')).upper()        for _, r in group if _safe_str(r.get('status'))}
        idxs   = [i for i, _ in group]

        if len(group) > 1 and len(titles) > 1:
            issues.append({
                'type':         'conflicting_title',
                'severity':     'high',
                'message':      f'Document "{doc_no}" appears {len(group)} times with {len(titles)} different titles.',
                'row_indexes':  idxs,
                'field':        'document_title',
            })
        if len(stats) > 1:
            issues.append({
                'type':         'status_outlier',
                'severity':     'medium',
                'message':      f'Document "{doc_no}" has mixed statuses: {", ".join(sorted(stats))}.',
                'row_indexes':  idxs,
                'field':        'status',
            })

        # Revision gap (only for alphabetic A/B/C sequences)
        alpha = sorted({r for r in revs if re.match(r'^[A-Z]$', r)})
        if alpha and alpha[0] != 'A':
            issues.append({
                'type':         'revision_gap',
                'severity':     'low',
                'message':      f'Document "{doc_no}" has revisions {alpha} — earlier revisions missing.',
                'row_indexes':  idxs,
                'field':        'revision',
            })

    # ---- Date outliers -----------------------------------------------------
    years: List[int] = []
    for r in items:
        d = _safe_str(r.get('date'))
        m = re.search(r'(19|20)\d{2}', d)
        if m:
            years.append(int(m.group(0)))
    if years:
        years_sorted = sorted(years)
        median = years_sorted[len(years_sorted) // 2]
        for idx, r in enumerate(items):
            d = _safe_str(r.get('date'))
            m = re.search(r'(19|20)\d{2}', d)
            if m and abs(int(m.group(0)) - median) > 5:
                issues.append({
                    'type':         'date_outlier',
                    'severity':     'low',
                    'message':      f'Date {d} is far from batch median year {median}.',
                    'row_indexes':  [idx],
                    'field':        'date',
                })

    # ---- Orphan tags in remarks -------------------------------------------
    declared_tags = set()
    for r in items:
        for col in ('instrument_tag_no', 'equipment_no'):
            for tok in re.split(r'[,\s;/]+', _safe_str(r.get(col))):
                tok = tok.strip().upper()
                if tok:
                    declared_tags.add(tok)
    tag_re = re.compile(r'\b([A-Z]{1,4}[-_][0-9]{2,5}[A-Z]?)\b')
    for idx, r in enumerate(items):
        remarks = _safe_str(r.get('remarks'))
        for m in tag_re.finditer(remarks.upper()):
            tag = m.group(1)
            if tag not in declared_tags:
                issues.append({
                    'type':         'orphan_tag',
                    'severity':     'low',
                    'message':      f'Tag "{tag}" referenced in remarks but not declared on any row.',
                    'row_indexes':  [idx],
                    'field':        'remarks',
                })
                break  # one per row is enough

    # Cap & de-dup
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for iss in issues:
        sig = (iss['type'], iss['message'])
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(iss)
        if len(deduped) >= cap:
            break

    sev_order = {'high': 0, 'medium': 1, 'low': 2}
    deduped.sort(key=lambda x: sev_order.get(x.get('severity', 'low'), 3))

    summary = {
        'total':  len(deduped),
        'high':   sum(1 for i in deduped if i['severity'] == 'high'),
        'medium': sum(1 for i in deduped if i['severity'] == 'medium'),
        'low':    sum(1 for i in deduped if i['severity'] == 'low'),
    }
    return {'issues': deduped, 'summary': summary}


# ===========================================================================
# 4. NATURAL LANGUAGE QUERY
# ===========================================================================

_QUERY_SYSTEM = (
    "You translate a single natural-language query about an engineering "
    "document table into a structured filter specification. NEVER make up "
    "filter fields. Output STRICT JSON matching the schema."
)


def translate_nl_query(query: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns:
      {
        "filters":    [{"field": "discipline", "op": "equals|contains|regex", "value": "..."}],
        "explanation": "Plain-English what we did",
        "matched_count": 12,
        "provider": "llm" | "heuristic"
      }
    Filters are evaluated client-side (the UI applies them on top of the
    existing search box).
    """
    q = _safe_str(query)
    items = items or []
    if not q:
        return {'filters': [], 'explanation': 'Empty query.', 'matched_count': len(items), 'provider': 'noop'}

    allowed = SMART_CONFIG['query_allowed_fields']
    cap = int(SMART_CONFIG['query_max_filters'])

    schema = (
        '{\n'
        '  "filters": [\n'
        '    {"field": "<one of: ' + ', '.join(allowed) + '>",\n'
        '     "op":    "equals|contains|regex|starts_with|ends_with",\n'
        '     "value": "<string>"}\n'
        '  ],\n'
        '  "explanation": "<one-sentence summary of what you understood>"\n'
        '}'
    )
    prompt = (
        f"User query: {q!r}\n\n"
        f"Available columns: {', '.join(allowed)}\n\n"
        f"Sample of the first row (for context):\n```json\n"
        + json.dumps({k: _safe_str(items[0].get(k)) for k in allowed} if items else {}, indent=2)
        + "\n```\n\n"
        + f"Return at most {cap} filters using this schema:\n{schema}\n"
        "Rules:\n"
        "- Choose the simplest 'op' that captures the user's intent (prefer 'contains').\n"
        "- Lowercase tokens when matching free-text columns.\n"
        "- If the user asks for 'all' or the query has no constraint, return an empty filters list.\n"
        "- Output ONLY the JSON object."
    )

    data = _llm_json(_QUERY_SYSTEM, prompt)
    filters: List[Dict[str, str]] = []
    explanation = ''

    if isinstance(data, dict):
        raw = data.get('filters') or []
        explanation = _safe_str(data.get('explanation'))[:240]
        if isinstance(raw, list):
            for entry in raw[:cap]:
                if not isinstance(entry, dict):
                    continue
                fld = _safe_str(entry.get('field')).lower()
                op  = _safe_str(entry.get('op')).lower()
                val = _safe_str(entry.get('value'))
                if fld not in allowed or not val:
                    continue
                if op not in {'equals', 'contains', 'regex', 'starts_with', 'ends_with'}:
                    op = 'contains'
                filters.append({'field': fld, 'op': op, 'value': val})

    # Heuristic fallback — single contains-anywhere filter on free text
    provider = 'llm'
    if not filters and not explanation:
        filters = []
        explanation = 'Falling back to free-text search across all fields.'
        provider = 'heuristic'

    matched = _count_matches(items, filters) if filters else len(items)

    return {
        'filters':       filters,
        'explanation':   explanation or 'Translated to structured filters.',
        'matched_count': matched,
        'provider':      provider,
    }


def _count_matches(items: List[Dict[str, Any]], filters: List[Dict[str, str]]) -> int:
    if not filters:
        return len(items)
    count = 0
    for r in items:
        ok = True
        for f in filters:
            cell = _safe_str(r.get(f['field'])).lower()
            val  = f['value'].lower()
            op   = f['op']
            if   op == 'equals'      and cell != val: ok = False
            elif op == 'contains'    and val not in cell: ok = False
            elif op == 'starts_with' and not cell.startswith(val): ok = False
            elif op == 'ends_with'   and not cell.endswith(val):   ok = False
            elif op == 'regex':
                try:
                    if not re.search(f['value'], cell, re.IGNORECASE):
                        ok = False
                except re.error:
                    ok = False
            if not ok:
                break
        if ok:
            count += 1
    return count


# ===========================================================================
# 5. CLASSIFY DOCUMENTS
# ===========================================================================

def classify_documents(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Per-row inferred document type + discipline + confidence.

    Pure heuristic — uses the type_lexicon defined in ai_recommendations.
    Cheap, runs offline, no LLM cost. Returns one record per input row.
    """
    out: List[Dict[str, Any]] = []
    type_counts: Counter = Counter()
    disc_counts: Counter = Counter()

    for idx, r in enumerate(items or []):
        title    = _safe_str(r.get('document_title'))
        doc_no   = _safe_str(r.get('document_no'))
        remarks  = _safe_str(r.get('remarks'))
        haystack = ' '.join([title, doc_no, remarks])
        inferred, discipline = _reco._classify_via_lexicon(haystack)
        if not discipline:
            discipline = _safe_str(r.get('discipline')).lower()
        conf = 'medium' if inferred else ('low' if not haystack.strip() else 'low')
        if inferred and discipline:
            conf = 'high'

        out.append({
            'row_idx':     idx,
            'document_no': doc_no,
            'doc_type':    inferred,
            'discipline':  discipline,
            'confidence':  conf,
        })
        if inferred:
            type_counts[inferred] += 1
        if discipline:
            disc_counts[discipline] += 1

    return {
        'classifications': out,
        'type_distribution':       dict(type_counts.most_common(10)),
        'discipline_distribution': dict(disc_counts.most_common(10)),
    }


# ===========================================================================
# 6. AUTO-LINK TAGS (within batch + remarks)
# ===========================================================================

def auto_link_tags(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Builds a tag → row-index index, then for each tag found in remarks/title
    of OTHER rows it produces a back-reference link. Surfaces the
    cross-document relationships that already exist inside the current
    batch — without touching other apps' data.

    Returns:
      {
        "links": [
          {"row_idx": 3, "field": "remarks", "tag": "PT-1011",
           "tag_kind": "instrument", "linked_rows": [0, 12]},
          ...
        ],
        "tag_index": {"PT-1011": {"kind": "instrument", "owners": [0]}}
      }
    """
    items = items or []
    tag_index: Dict[str, Dict[str, Any]] = {}

    # Build authoritative index from typed columns first
    for idx, r in enumerate(items):
        for col, kind in SMART_CONFIG['autolink_tag_columns']:
            for tok in re.split(r'[,\s;/]+', _safe_str(r.get(col))):
                tag = tok.strip().upper()
                if not tag or len(tag) < 3:
                    continue
                entry = tag_index.setdefault(tag, {'kind': kind, 'owners': []})
                if idx not in entry['owners']:
                    entry['owners'].append(idx)

    # Now scan free-text columns for back-references
    free_cols = ('remarks', 'document_title')
    tag_pat = re.compile(r'\b([A-Z]{1,4}[-_][0-9]{2,5}[A-Z]?)\b')
    links: List[Dict[str, Any]] = []
    for idx, r in enumerate(items):
        for col in free_cols:
            text = _safe_str(r.get(col)).upper()
            for m in tag_pat.finditer(text):
                tag = m.group(1)
                entry = tag_index.get(tag)
                if not entry:
                    continue
                # Only emit when the *current* row isn't itself an owner.
                if idx in entry['owners']:
                    continue
                links.append({
                    'row_idx':     idx,
                    'field':       col,
                    'tag':         tag,
                    'tag_kind':    entry['kind'],
                    'linked_rows': list(entry['owners'])[:5],
                })

    return {
        'links': links[:200],   # safety cap
        'tag_index': tag_index,
        'total_tags': len(tag_index),
        'total_links': len(links),
    }


# ===========================================================================
# 7. REVISION TIMELINE
# ===========================================================================

def build_revision_timeline(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Groups rows by document_no and orders revisions. For each pair of
    consecutive revisions it generates a short heuristic diff summary
    (added tags, status change, title change). LLM is NOT called per pair —
    the timeline must stay fast for batches of 200+ docs.

    Returns:
      {
        "groups": [
          {
            "document_no": "P16093-PR-PFD-001",
            "title": "Process Flow Diagram - Train A",
            "revisions": [
              {"row_idx": 4, "revision": "A", "date": "2024-03-12",
               "status": "IFR", "tag_count": 8, "diff_summary": ""},
              {"row_idx": 9, "revision": "B", "date": "2024-06-01",
               "status": "IFA", "tag_count": 11,
               "diff_summary": "3 new instrument tags; status IFR \u2192 IFA"}
            ]
          }
        ],
        "single_revision_count": 42,
        "multi_revision_count":   7
      }
    """
    items = items or []
    groups_map: Dict[str, List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for idx, r in enumerate(items):
        key = _safe_str(r.get('document_no'))
        if not key:
            continue
        groups_map[key].append((idx, r))

    def _rev_sort_key(t: Tuple[int, Dict[str, Any]]) -> Tuple[int, str]:
        rev = _safe_str(t[1].get('revision')).upper()
        # Alpha first (A < B < C), then numeric, then anything else
        if re.match(r'^[A-Z]$', rev):
            return (0, rev)
        if re.match(r'^\d+$', rev):
            return (1, rev.zfill(4))
        return (2, rev)

    groups: List[Dict[str, Any]] = []
    single = 0
    multi = 0
    for doc_no, rows in sorted(groups_map.items()):
        rows.sort(key=_rev_sort_key)
        revisions: List[Dict[str, Any]] = []
        prev: Optional[Dict[str, Any]] = None
        for idx, r in rows:
            tags = _split_tags(r.get('instrument_tag_no')) | _split_tags(r.get('equipment_no'))
            entry = {
                'row_idx':   idx,
                'revision':  _safe_str(r.get('revision')) or '—',
                'date':      _safe_str(r.get('date')),
                'status':    _safe_str(r.get('status')),
                'title':     _safe_str(r.get('document_title')),
                'tag_count': len(tags),
                'tags':      sorted(tags)[:8],
                'diff_summary': '',
            }
            if prev is not None:
                entry['diff_summary'] = _diff_summary(prev, entry, tags, _split_tags(prev.get('_raw_tags')))
            entry['_raw_tags'] = list(tags)   # internal — stripped before serialise
            revisions.append(entry)
            prev = entry

        for e in revisions:
            e.pop('_raw_tags', None)

        title = next((_safe_str(r.get('document_title')) for _, r in rows if r.get('document_title')), '')
        groups.append({
            'document_no': doc_no,
            'title':       title,
            'revisions':   revisions,
        })
        if len(rows) > 1:
            multi += 1
        else:
            single += 1

    groups.sort(key=lambda g: (-len(g['revisions']), g['document_no']))
    return {
        'groups':                 groups,
        'single_revision_count':  single,
        'multi_revision_count':   multi,
    }


def _split_tags(v: Any) -> set:
    s = _safe_str(v).upper()
    if not s:
        return set()
    return {t.strip() for t in re.split(r'[,\s;/]+', s) if t.strip()}


def _diff_summary(prev: Dict[str, Any], curr: Dict[str, Any],
                  curr_tags: set, prev_tags: set) -> str:
    parts: List[str] = []
    added = curr_tags - prev_tags
    removed = prev_tags - curr_tags
    if added:
        parts.append(f"+{len(added)} new tag{'s' if len(added) != 1 else ''}")
    if removed:
        parts.append(f"-{len(removed)} removed")
    if prev.get('status') and curr.get('status') and prev['status'] != curr['status']:
        parts.append(f"status {prev['status']} \u2192 {curr['status']}")
    if prev.get('title') and curr.get('title') and prev['title'] != curr['title']:
        parts.append('title updated')
    return '; '.join(parts)


# ===========================================================================
# 8. BULK-EDIT SUGGESTIONS
# ===========================================================================

def suggest_bulk_edits(items: List[Dict[str, Any]],
                       selected_indexes: Optional[List[int]] = None) -> Dict[str, Any]:
    """
    Analyses the selected rows (or the full set) and suggests bulk fills:
      • Most common non-empty value in a column — propose to copy to blanks
      • Inferred discipline from tag prefixes
      • Common originator / status

    Pure-Python. No LLM call.

    Returns:
      {
        "suggestions": [
          {"field": "discipline", "value": "Instrument", "applies_to_rows": [3,5,8],
           "support": 0.8, "rationale": "8 of 10 selected rows have instrument tags."}
        ],
        "selected_count": 10
      }
    """
    items = items or []
    n = len(items)
    if selected_indexes is None or not selected_indexes:
        selected = list(range(n))
    else:
        selected = [i for i in selected_indexes if 0 <= i < n]

    if len(selected) < int(SMART_CONFIG['bulk_min_rows']):
        return {'suggestions': [], 'selected_count': len(selected),
                'note': f"Need at least {SMART_CONFIG['bulk_min_rows']} rows to suggest bulk edits."}

    rows = [items[i] for i in selected]
    suggestions: List[Dict[str, Any]] = []
    min_support = float(SMART_CONFIG['bulk_min_support_ratio'])
    cap = int(SMART_CONFIG['bulk_max_suggestions'])

    # Per-field majority-fill suggestion
    for field in SMART_CONFIG['query_allowed_fields']:
        values = [_safe_str(r.get(field)) for r in rows]
        non_empty = [v for v in values if v]
        if not non_empty:
            continue
        # If column is already full, nothing to do
        empties = [selected[i] for i, v in enumerate(values) if not v]
        if not empties:
            continue
        common, count = Counter(non_empty).most_common(1)[0]
        support = count / len(non_empty)
        if support < min_support:
            continue
        suggestions.append({
            'field':           field,
            'value':           common,
            'applies_to_rows': empties[:50],
            'support':         round(support, 2),
            'rationale':       (
                f'{count} of {len(non_empty)} filled rows share "{common}"; '
                f'apply to {len(empties)} blank row{"s" if len(empties) != 1 else ""}.'
            ),
        })

    # Discipline inference from tag prefixes
    if any(_safe_str(r.get('instrument_tag_no')) for r in rows):
        with_no_disc = [selected[i] for i, r in enumerate(rows)
                        if _safe_str(r.get('instrument_tag_no')) and not _safe_str(r.get('discipline'))]
        if len(with_no_disc) >= int(SMART_CONFIG['bulk_min_rows']):
            suggestions.append({
                'field':           'discipline',
                'value':           'Instrument',
                'applies_to_rows': with_no_disc,
                'support':         1.0,
                'rationale':       f'{len(with_no_disc)} rows have an instrument tag but no discipline set.',
            })

    suggestions.sort(key=lambda s: (-s['support'], -len(s['applies_to_rows'])))
    return {
        'suggestions':    suggestions[:cap],
        'selected_count': len(selected),
    }
