"""
AI-Assisted Header Mapper  (Step 1).

Goal
────
Replace the brittle hard-coded alias table with a learning matcher that maps
*any* customer header to our canonical schema — even when the customer uses
abbreviations, typos, foreign-language synonyms or industry shorthand.

Strategy (all soft-coded, deterministic-first):
  1. **Exact / aliased match**  — uses the existing schema aliases from
     `services._TOOL_SCHEMAS`. Zero-cost, highest precision.
  2. **Token-set Jaccard similarity** — handles word reorder & extra words.
  3. **Character n-gram Dice similarity** — handles typos & abbreviations.
  4. **Domain keyword boosting** — soft-coded synonym packs (multi-lingual,
     industry shorthand) bump weak matches.
  5. **Optional LLM fallback** — only invoked when the top score is below
     `_LOW_CONFIDENCE`. Disabled by default; activates if OPENAI_API_KEY is
     set in env. Failures are silent — we fall back to deterministic match.

The output is a mapping `{customer_header: canonical_key}` plus per-column
confidence scores in `[0,1]`. The mapper never raises; if everything fails
it leaves the column unmapped (downstream rules will flag missing fields).
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Iterable

from . import services as _svc
from . import ai_features as _flags

logger = logging.getLogger(__name__)

# ─── Soft-coded LLM safety knobs (shared semantics with ai_explainer.py) ─────
_LLM_TIMEOUT_SEC   = float(os.environ.get('INSTRUMENT_TOOLS_LLM_TIMEOUT_SEC', '6'))
_LLM_COOLDOWN_SEC  = float(os.environ.get('INSTRUMENT_TOOLS_LLM_COOLDOWN_SEC', '60'))
# Process-wide circuit-breaker timestamp; populated on first LLM failure.
_llm_disabled_until: float = 0.0

# ─── Soft-coded thresholds (tune without touching logic) ─────────────────────
_HIGH_CONFIDENCE = 0.90    # auto-accept
_LOW_CONFIDENCE  = 0.60    # below this → try LLM fallback (if available)
_MIN_ACCEPT      = 0.50    # below this → leave column unmapped
_NGRAM_SIZE      = 3       # char n-gram for Dice similarity
_TOKEN_SPLIT     = re.compile(r'[^a-z0-9]+')

# ─── Soft-coded domain synonym packs (one per canonical field) ───────────────
# Used to boost token-set matches with industry shorthand & multilingual hints.
_DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    'tag':           ('tagno', 'tagnumber', 'itemno', 'inststag', 'kks', 'plantid'),
    'description':   ('service', 'svc', 'function', 'duty', 'remarks'),
    'signal_type':   ('iotype', 'io', 'sigtype', 'siglevel', 'channel', 'aiao', 'didout'),
    'pid':           ('pandid', 'piddwg', 'pidno', 'drawing', 'dwgno'),
    'location':      ('area', 'unit', 'plant', 'zone', 'module'),
    'panel':         ('panel', 'mp', 'marshalling', 'cabinet', 'rack', 'cab'),
    'range':         ('measrange', 'opdrange', 'spanrange', 'workingrange'),
    'units':         ('eu', 'engunits', 'uom', 'unit'),
    'manufacturer':  ('make', 'maker', 'oem', 'brand', 'vendor', 'supplier'),
    'model':         ('modelnumber', 'partno', 'typedesignation', 'typecode'),
    'cable_tag':     ('cableid', 'cableno', 'kabelno', 'cableref'),
    'from_tag':      ('source', 'origin', 'startpoint', 'fromend'),
    'to_tag':        ('destination', 'target', 'endpoint', 'toend'),
    'cable_type':    ('cablespec', 'cablecategory', 'cabletype', 'specification'),
    'size':          ('csa', 'crosssection', 'gauge', 'awg', 'mmsq'),
    'cores':         ('numcores', 'nocores', 'conductors', 'wires'),
    'length_m':      ('lengthm', 'cablelen', 'len', 'meters'),
    'voltage':       ('vrating', 'kv', 'volts', 'ratedvolt'),
    'from_panel':    ('originpanel', 'sourcerack', 'frompanelid'),
    'to_panel':      ('destpanel', 'targetrack', 'topanelid'),
    'gland_from':    ('fromgland', 'glandsource', 'glandorigin'),
    'gland_to':      ('togland', 'glanddest', 'glandtarget'),
    'tray':          ('cabletray', 'route', 'duct', 'conduit'),
    'system':        ('subsystem', 'package', 'plantsection'),
    'function':      ('purpose', 'duty', 'role'),
    'qty':           ('quantity', 'count', 'no'),
    'source':        ('fromend', 'origin', 'startpoint'),
    'destination':   ('toend', 'target', 'endpoint'),
}


def _slug(s) -> str:
    return re.sub(r'[^a-z0-9]', '', str(s or '').strip().lower())


def _tokens(s) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(str(s or '').lower()) if t}


def _ngrams(s: str, n: int = _NGRAM_SIZE) -> set[str]:
    s = _slug(s)
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _dice(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return (2 * inter) / (len(a) + len(b))


def _candidate_blob(canonical: str, aliases: Iterable[str]) -> str:
    extra = _DOMAIN_SYNONYMS.get(canonical, ())
    return ' '.join((canonical, *aliases, *extra))


def _score(header: str, canonical: str, aliases: Iterable[str]) -> float:
    """Hybrid similarity score in [0,1]."""
    blob = _candidate_blob(canonical, aliases)
    # 1) Slug exact match against any alias → 1.0 (handled by caller too).
    if _slug(header) == _slug(canonical):
        return 1.0
    if _slug(header) in {_slug(a) for a in aliases}:
        return 0.98
    # 2) Token-set Jaccard
    j = _jaccard(_tokens(header), _tokens(blob))
    # 3) Char n-gram Dice
    d = _dice(_ngrams(header), _ngrams(blob))
    # 4) Domain synonym hit boost
    boost = 0.0
    if _slug(header) in {_slug(s) for s in _DOMAIN_SYNONYMS.get(canonical, ())}:
        boost = 0.15
    return min(1.0, 0.55 * d + 0.30 * j + boost)


def _llm_resolve(unmatched: dict[str, list[tuple[str, float]]],
                 schema: dict) -> dict[str, str]:
    """Optional LLM fallback. Returns {header: canonical_or_empty}.

    Hard-bounded by `_LLM_TIMEOUT_SEC` and a process-wide cooldown so a slow
    OpenAI endpoint cannot block a Django request beyond the client timeout.
    Silent failure — any exception just returns an empty dict so the caller
    can fall back to the deterministic top candidate.
    """
    global _llm_disabled_until
    if not unmatched:
        return {}
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return {}
    if time.time() < _llm_disabled_until:
        return {}
    try:
        # Local import keeps openai an optional dependency.
        from openai import OpenAI                                       # noqa: WPS433
    except Exception:                                                   # noqa: BLE001
        return {}
    try:
        client = OpenAI(api_key=api_key, timeout=_LLM_TIMEOUT_SEC)
        canonical_keys = list(schema.keys())
        prompt = (
            'Map each customer column name to the closest canonical key, or '
            'return an empty string if none fit. Reply ONLY in JSON of the '
            'form {"<customer>": "<canonical_or_empty>"}.\n'
            f'Canonical keys: {canonical_keys}\n'
            f'Customer columns: {list(unmatched.keys())}'
        )
        rsp = client.chat.completions.create(
            model=os.environ.get('INSTRUMENT_TOOLS_LLM_MODEL', 'gpt-4o-mini'),
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0,
            response_format={'type': 'json_object'},
            timeout=_LLM_TIMEOUT_SEC,
        )
        import json
        text = rsp.choices[0].message.content or '{}'
        parsed = json.loads(text)
        out: dict[str, str] = {}
        for h, can in parsed.items():
            if isinstance(can, str) and can in schema:
                out[h] = can
        return out
    except Exception as exc:                                            # noqa: BLE001
        _llm_disabled_until = time.time() + _LLM_COOLDOWN_SEC
        logger.warning('LLM header mapping disabled for %ss (reason=%s)', _LLM_COOLDOWN_SEC, exc)
        return {}


def map_headers(tool: str, headers: Iterable[str]) -> dict:
    """Map an arbitrary set of customer headers to canonical keys.

    Returns:
        {
          'mapping':    {customer_header: canonical_key},
          'confidence': {customer_header: float},
          'unmapped':   [customer_header, ...],
          'method':     {customer_header: 'alias'|'fuzzy'|'llm'},
        }
    """
    schema = _svc._TOOL_SCHEMAS.get(tool, {})
    if not schema:
        return {'mapping': {}, 'confidence': {}, 'unmapped': list(headers), 'method': {}}

    mapping:    dict[str, str]   = {}
    confidence: dict[str, float] = {}
    method:     dict[str, str]   = {}

    if not _flags.is_enabled('smart_header_mapping'):
        # Defer entirely to the existing alias index (cheap, deterministic).
        idx = _svc._TOOL_ALIAS_INDEX[tool]
        for h in headers:
            can = idx.get(_slug(h))
            if can:
                mapping[h] = can
                confidence[h] = 1.0
                method[h] = 'alias'
        return {
            'mapping': mapping, 'confidence': confidence,
            'unmapped': [h for h in headers if h not in mapping],
            'method': method,
        }

    idx = _svc._TOOL_ALIAS_INDEX[tool]
    low_conf: dict[str, list[tuple[str, float]]] = {}

    for header in headers:
        # Step 1 — exact / aliased match.
        canonical = idx.get(_slug(header))
        if canonical:
            mapping[header]    = canonical
            confidence[header] = 1.0
            method[header]     = 'alias'
            continue
        # Step 2 — hybrid similarity over every canonical column.
        scored: list[tuple[str, float]] = []
        for can, aliases in schema.items():
            scored.append((can, _score(header, can, aliases)))
        scored.sort(key=lambda x: x[1], reverse=True)
        top_can, top_score = scored[0]
        if top_score >= _HIGH_CONFIDENCE:
            mapping[header]    = top_can
            confidence[header] = round(top_score, 3)
            method[header]     = 'fuzzy'
        elif top_score >= _MIN_ACCEPT:
            mapping[header]    = top_can
            confidence[header] = round(top_score, 3)
            method[header]     = 'fuzzy'
            if top_score < _LOW_CONFIDENCE:
                low_conf[header] = scored[:3]
        else:
            low_conf[header] = scored[:3]

    # Step 3 — optional LLM fallback for low-confidence + unmapped headers.
    if low_conf:
        llm_map = _llm_resolve(low_conf, schema)
        for header, canonical in llm_map.items():
            mapping[header]    = canonical
            confidence[header] = max(confidence.get(header, 0.0), _LOW_CONFIDENCE)
            method[header]     = 'llm'

    unmapped = [h for h in headers if h not in mapping]
    return {
        'mapping': mapping, 'confidence': confidence,
        'unmapped': unmapped, 'method': method,
    }


def remap_rows(tool: str, rows: list[dict]) -> dict:
    """Apply the AI mapper, then rewrite rows to canonical keys.

    Returns:
        {
          'rows':        list[dict],      # canonical-keyed rows
          'header_map':  map_headers(...) # auditable mapping report
        }
    """
    if not rows:
        return {'rows': [], 'header_map': map_headers(tool, [])}
    headers: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                headers.append(k)
    report = map_headers(tool, headers)
    mapping = report['mapping']
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        norm: dict = {}
        for k, v in r.items():
            can = mapping.get(k)
            if can and (can not in norm or _is_blank(norm[can])):
                norm[can] = v
        out.append(norm)
    return {'rows': out, 'header_map': report}


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())
