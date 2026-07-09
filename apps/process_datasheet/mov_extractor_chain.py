"""
MOV P&ID extractor chain — soft-coded fallback orchestration.
=============================================================
The MOV datasheet pipeline historically used ONLY `GeminiPIDExtractor`. If that
class returned 0 valves (Gemini quota exhausted, no OCR text, scanned image
without recognisable circles), the user got the dreaded
    "No valve tags could be extracted from your P&ID."
even though several other extractors in this codebase could likely have
recovered the tags.

This module runs an ordered, soft-coded chain of extraction strategies. The
first strategy that returns ≥ `MIN_VALVES_REQUIRED` real (non-demo) MOV tags
wins. All thresholds, ordering, and per-strategy enablement live in the
`EXTRACTOR_CHAIN_CONFIG` block at the top of the file — tune without touching
flow logic.

Each strategy is a tiny adapter around an existing extractor in the codebase;
nothing here re-implements OCR or Vision logic. We just compose what's
already there:

    1. GeminiVisionStrategy        → apps.process_datasheet.gemini_pid_extractor
                                     (Gemini → OpenAI Vision → OCR-regex internally)
    2. HybridOCRVisionStrategy     → apps.process_datasheet.real_pid_extractor
                                     (multi-engine OCR + OpenAI Vision, used by SDV)
    3. NativeTextRegexStrategy     → PyMuPDF text-layer + tag_validator regex
                                     (deterministic, zero API cost)

Environment overrides (no code change needed):
    MOV_EXTRACTOR_CHAIN          — comma-separated list of strategy names to enable.
                                   Default: "gemini,hybrid_ocr_vision,native_text".
    MOV_EXTRACTOR_MIN_VALVES     — minimum valid valves required to short-circuit
                                   the chain. Default: 1.
    MOV_EXTRACTOR_KEEP_TRYING    — "1" to always run every enabled strategy and
                                   merge results (slower but maximises recall).
                                   Default: "0" (stop on first success).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable, Dict, List, Optional

from apps.process_datasheet.tag_validator import (
    VALID_TAG_PREFIXES,
    is_demo_tag,
    get_tag,
)

logger = logging.getLogger(__name__)


# =============================================================================
# SOFT-CODED CONFIG — tune here, no logic changes required.
# =============================================================================

def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [s.strip() for s in raw.split(',') if s.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


EXTRACTOR_CHAIN_CONFIG = {
    # Ordered list of strategy keys. Edit the env var or this default to
    # reorder, disable, or extend the chain without touching call sites.
    'STRATEGY_ORDER': _env_list(
        'MOV_EXTRACTOR_CHAIN',
        ['gemini', 'hybrid_ocr_vision', 'native_text'],
    ),

    # Minimum number of valid (non-demo) valves needed to consider a strategy
    # successful. If the result is below this, we move on to the next strategy.
    'MIN_VALVES_REQUIRED': _env_int('MOV_EXTRACTOR_MIN_VALVES', 1),

    # When True, every enabled strategy is run and results are merged (dedup by
    # tag). When False (default), we stop as soon as a strategy succeeds.
    'KEEP_TRYING_FOR_MERGE': _env_bool('MOV_EXTRACTOR_KEEP_TRYING', False),
}


# =============================================================================
# Tag normalisation helpers (shared across strategies)
# =============================================================================

# Pre-compiled regex matching any known valid prefix anchored at a word boundary,
# followed by a separator (-, _, /, space, or none) and an alphanumeric suffix.
# Built once from tag_validator.VALID_TAG_PREFIXES so future prefix additions
# automatically apply here.
_TAG_REGEX = re.compile(
    r'\b(' + '|'.join(sorted(VALID_TAG_PREFIXES, key=len, reverse=True)) +
    r')[\s\-_/]*([0-9]{1,5}[A-Z]?)\b',
    re.IGNORECASE,
)


def _normalise_valves(valves: List[Dict], valve_type: str = 'MOV') -> List[Dict]:
    """
    Filter to valid (non-demo) valves. When valve_type is provided, prefer
    matching tags but keep others if they match common valve prefixes — the
    downstream tag_validator does the final authoritative filter.
    """
    out: List[Dict] = []
    seen = set()
    for v in valves or []:
        tag = get_tag(v)
        if not tag:
            continue
        if is_demo_tag(tag):
            continue
        # Dedup by uppercased tag
        key = tag.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _filter_target_valve_type(valves: List[Dict], valve_type: str) -> List[Dict]:
    """
    Soft filter: keep entries whose type or tag matches the target valve_type.
    Falls back to returning the input untouched if filtering would drop everything
    (some extractors don't populate `type` reliably).
    """
    if not valve_type:
        return valves
    vt = valve_type.upper()
    filtered = [
        v for v in valves
        if vt in (v.get('type', '') or '').upper()
        or vt in (get_tag(v) or '').upper()
    ]
    return filtered if filtered else valves


# =============================================================================
# Strategy implementations
# Each strategy returns a dict-shaped {'valves': [...], ...} on success or
# None on failure. They MUST NOT raise — log + return None instead.
# =============================================================================

def _strategy_gemini(pid_file_path: str, pid_filename: Optional[str], valve_type: str) -> Optional[Dict]:
    """Primary: Gemini Vision (with internal Gemini → OpenAI → OCR fallback chain)."""
    try:
        from apps.process_datasheet.gemini_pid_extractor import GeminiPIDExtractor
        extractor = GeminiPIDExtractor()
        result = extractor.extract_valves_from_pdf(
            pid_file_path, original_filename=pid_filename, valve_type=valve_type
        )
        valves = _normalise_valves(result.get('valves', []), valve_type)
        if not valves:
            logger.info('[MOV-Chain:gemini] returned 0 valid valves')
            return None
        result['valves'] = valves
        return result
    except Exception as e:
        logger.warning(f'[MOV-Chain:gemini] failed: {e}')
        return None


def _strategy_hybrid_ocr_vision(pid_file_path: str, pid_filename: Optional[str], valve_type: str) -> Optional[Dict]:
    """Hybrid OCR (Tesseract / EasyOCR / PaddleOCR) + OpenAI Vision. Used by SDV pipeline."""
    try:
        from apps.process_datasheet.real_pid_extractor import RealPIDExtractor
        extractor = RealPIDExtractor()
        result = extractor.extract_valves_from_pdf(
            pid_file_path, original_filename=pid_filename, valve_type=valve_type
        )
        valves = _filter_target_valve_type(result.get('valves', []), valve_type)
        valves = _normalise_valves(valves, valve_type)
        if not valves:
            logger.info('[MOV-Chain:hybrid_ocr_vision] returned 0 valid valves')
            return None
        result['valves'] = valves
        return result
    except Exception as e:
        logger.warning(f'[MOV-Chain:hybrid_ocr_vision] failed: {e}')
        return None


def _strategy_native_text(pid_file_path: str, pid_filename: Optional[str], valve_type: str) -> Optional[Dict]:
    """
    Deterministic last-resort: scan the PDF text layer (PyMuPDF) + OCR pass via
    pytesseract if available, then regex out every known valid tag. Zero LLM cost.
    Works on vector PDFs even when all upstream Vision providers are down.
    """
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        logger.warning(f'[MOV-Chain:native_text] PyMuPDF unavailable: {e}')
        return None

    found: Dict[str, Dict] = {}
    drawing_no: Optional[str] = None

    try:
        doc = fitz.open(pid_file_path)
        try:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                # 1) Embedded text layer
                text = page.get_text() or ''
                # 2) Rawdict pass (catches CAD char-by-char glyphs)
                if len(text.strip()) < 30:
                    try:
                        raw = page.get_text('rawdict')
                        chunks = []
                        for block in raw.get('blocks', []) or []:
                            if block.get('type') != 0:
                                continue
                            for line in block.get('lines', []) or []:
                                for span in line.get('spans', []) or []:
                                    t = (span.get('text') or '').strip()
                                    if t:
                                        chunks.append(t)
                        text = (text + '\n' + ' '.join(chunks)).strip()
                    except Exception:
                        pass
                # 3) Optional OCR fallback (only if still empty and pytesseract works)
                if len(text.strip()) < 30:
                    try:
                        import io
                        from PIL import Image
                        import pytesseract
                        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
                        img = Image.open(io.BytesIO(pix.tobytes('png')))
                        ocr_text = pytesseract.image_to_string(img, config='--oem 3 --psm 11') or ''
                        text = (text + '\n' + ocr_text).strip()
                    except Exception:
                        pass

                if not text:
                    continue

                for m in _TAG_REGEX.finditer(text):
                    prefix = m.group(1).upper()
                    suffix = m.group(2).upper()
                    tag = f'{prefix}-{suffix}'
                    if is_demo_tag(tag):
                        continue
                    if tag in found:
                        continue
                    found[tag] = {
                        'tag_no': tag,
                        'tag': tag,
                        'type': prefix,
                        'line_no': '',
                        'service': '',
                        'location': f'Page {page_idx + 1}',
                        'piping_class': '',
                        'notes': 'Extracted via deterministic text-regex pass',
                    }

                # Cheap drawing-number heuristic — first long compound code on page 1
                if drawing_no is None and page_idx == 0:
                    dn_match = re.search(
                        r'\b([A-Z0-9]{2,8}-[A-Z0-9]{2,8}-[A-Z0-9]{2,8}-[A-Z0-9]{2,8}-\d{3,5})\b',
                        text,
                    )
                    if dn_match:
                        drawing_no = dn_match.group(1)
        finally:
            doc.close()
    except Exception as e:
        logger.warning(f'[MOV-Chain:native_text] PDF scan failed: {e}')
        return None

    valves = list(found.values())
    valves = _filter_target_valve_type(valves, valve_type)
    valves = _normalise_valves(valves, valve_type)
    if not valves:
        logger.info('[MOV-Chain:native_text] returned 0 valid valves')
        return None

    return {
        'valves': valves,
        'drawing_no': drawing_no or '',
        'pid_no': drawing_no or '',
        'extraction_method': 'native_text_regex',
    }


# Registry: name → callable. Add new strategies here, then list them in
# `MOV_EXTRACTOR_CHAIN` env var or the default `STRATEGY_ORDER` above.
STRATEGY_REGISTRY: Dict[str, Callable[[str, Optional[str], str], Optional[Dict]]] = {
    'gemini':              _strategy_gemini,
    'hybrid_ocr_vision':   _strategy_hybrid_ocr_vision,
    'native_text':         _strategy_native_text,
}


# =============================================================================
# Public entry point
# =============================================================================

def extract_pid_with_chain(
    pid_file_path: str,
    pid_filename: Optional[str],
    valve_type: str = 'MOV',
    on_attempt: Optional[Callable[[str, str], None]] = None,
) -> Dict:
    """
    Run the soft-coded extractor chain.

    Args:
        pid_file_path:  Local path to the P&ID PDF.
        pid_filename:   Original upload filename (used for fallback drawing-no).
        valve_type:     Target valve family (e.g. 'MOV', 'SDV'). Used for
                        per-strategy prompt hints and post-filtering.
        on_attempt:     Optional callback(strategy_name, status_msg) — used by
                        the threading processor to surface progress to the user.

    Returns:
        Dict with at least:
            - 'valves':     list[dict] (may be empty if every strategy failed)
            - 'extraction_method': str, name of the strategy that produced the
                                    result (or 'merged' / 'none').
            - any other keys the underlying extractor included (pid_no, drawing_no…)
    """
    order = EXTRACTOR_CHAIN_CONFIG['STRATEGY_ORDER']
    min_valves = EXTRACTOR_CHAIN_CONFIG['MIN_VALVES_REQUIRED']
    keep_trying = EXTRACTOR_CHAIN_CONFIG['KEEP_TRYING_FOR_MERGE']

    logger.info(
        f'[MOV-Chain] Starting chain order={order} min_valves={min_valves} '
        f'merge={keep_trying} target={valve_type}'
    )

    merged_valves: Dict[str, Dict] = {}
    successful_strategy: Optional[str] = None
    last_result: Optional[Dict] = None

    for name in order:
        strategy = STRATEGY_REGISTRY.get(name)
        if strategy is None:
            logger.warning(f'[MOV-Chain] Unknown strategy "{name}" — skipping')
            continue

        if on_attempt:
            try:
                on_attempt(name, f'Trying extractor: {name}')
            except Exception:
                pass

        logger.info(f'[MOV-Chain] ▶ strategy={name}')
        result = strategy(pid_file_path, pid_filename, valve_type)
        if not result:
            continue

        valves = result.get('valves') or []
        if len(valves) < min_valves:
            logger.info(
                f'[MOV-Chain] strategy={name} below threshold '
                f'({len(valves)} < {min_valves})'
            )
            continue

        logger.info(
            f'[MOV-Chain] ✅ strategy={name} produced {len(valves)} valve(s)'
        )
        if on_attempt:
            try:
                on_attempt(name, f'{name} produced {len(valves)} valve(s)')
            except Exception:
                pass

        if not keep_trying:
            # First success wins — return immediately.
            result.setdefault('extraction_method', name)
            return result

        # Merge mode: accumulate unique tags across strategies.
        last_result = result
        successful_strategy = name
        for v in valves:
            key = (get_tag(v) or '').upper()
            if key and key not in merged_valves:
                merged_valves[key] = v

    if merged_valves:
        out = dict(last_result or {})
        out['valves'] = list(merged_valves.values())
        out['extraction_method'] = (
            successful_strategy if not keep_trying else 'merged'
        )
        return out

    # Every strategy failed — return an empty-but-well-formed dict so callers
    # don't have to special-case None.
    return {'valves': [], 'extraction_method': 'none'}
