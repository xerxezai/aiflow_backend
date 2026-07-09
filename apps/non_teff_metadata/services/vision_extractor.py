"""
Non-TEFF Vision/OCR Enrichment service.

Activated when regex-based extraction returns a sparse row — typical for:
  • Scanned PDFs (no embedded text layer)
  • Vendor drawings with title-block text only (no body text)
  • Old AutoCAD-printed layouts where PDF text is glyph-mapped garbage
  • Mixed-language / rotated documents

Strategy (in priority order, soft-coded in ``VISION_CONFIG``):

  1. **OCR fallback** — when pdfplumber returns < N clean chars, render the
     first few pages with PyMuPDF and run Tesseract (pytesseract) to recover
     text. The recovered text is fed back through the existing regex
     extractors transparently.

  2. **Vision AI fallback** — for fields still empty after OCR, render the
     **title-block region** of the first page (bottom-right by default) and
     ask a vision model to read just those fields. The vision call is
     skipped entirely when no API key is configured, keeping the service
     fully usable in offline mode.

     Provider chain (soft-coded, cost-first):
       1. **Google Gemini** (gemini-2.0-flash) — PRIMARY. Generous free tier,
          excellent at title-block reading. Used whenever GEMINI_API_KEY is
          set. Effectively zero-cost for our volume.
       2. **OpenAI gpt-4o-mini** — secondary. Used only when Gemini is
          unavailable or fails. Roughly 30× cheaper than gpt-4o.
       3. **OpenAI gpt-4o** — disabled by default; can be re-enabled per
          environment via VISION_CONFIG['providers'] for high-stakes batches.

     Cost optimisations applied automatically:
       • In-memory result cache keyed by file SHA-256 + missing-fields set
         — re-runs on the same batch never re-call the model.
       • Title-block crop sent first (≈20% of full page ⇒ ≈20% of tokens).
       • Reduced DPI (110) and JPEG re-encode shrink the image payload.
       • "low" detail hint to OpenAI when crop is small enough.
       • Trigger gated on number of empty columns AND file-size sanity.

All thresholds, model names, prompt templates, and which columns are
"vision-eligible" live in ``VISION_CONFIG`` below — change behaviour by
editing constants only, no code changes required.

This module is **fully additive** — it never overwrites a regex-extracted
value, and it never raises into the caller.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED configuration
# ---------------------------------------------------------------------------

VISION_CONFIG: Dict[str, Any] = {
    # Master switches
    'enable_ocr_fallback': True,
    'enable_vision_ai':    True,

    # OCR fallback ------------------------------------------------------------
    # If pdfplumber-extracted text has at least this many "clean" chars
    # (post-_normalize_text), skip OCR entirely.
    'ocr_skip_threshold_chars': 250,
    # DPI used to render pages for OCR. 200 is a good speed/quality balance
    # for engineering drawings; raise to 300 if accuracy is poor.
    'ocr_dpi': 200,
    # Maximum pages to OCR per document — title block lives on page 1, but
    # we sometimes scan a second page for revision history.
    'ocr_max_pages': 2,
    # pytesseract config — PSM 6 = "Assume a single uniform block of text",
    # which works well for title blocks. PSM 11 = sparse text (better for
    # full drawing pages). We try PSM 6 first, fall back to PSM 11.
    'ocr_psm_modes': [6, 11],
    'ocr_lang': 'eng',
    # Skip Tesseract if it isn't installed — flag is auto-detected at startup.
    'ocr_required_clean_chars_after': 80,  # below this, OCR is also discarded

    # Vision AI ---------------------------------------------------------------
    # Provider chain — tried in order until one returns usable JSON.
    # Each entry: {provider: 'gemini'|'openai', model: <name>, enabled: bool}.
    # Gemini's free tier (1M tokens/day on flash models) covers virtually
    # any document-control workload — we keep it first.
    'providers': [
        {'provider': 'gemini', 'model': 'gemini-2.0-flash',     'enabled': True},
        {'provider': 'gemini', 'model': 'gemini-1.5-flash',     'enabled': True},   # legacy fallback
        {'provider': 'openai', 'model': 'gpt-4o-mini',          'enabled': True},   # paid but cheap
        {'provider': 'openai', 'model': 'gpt-4o',               'enabled': False},  # opt-in only
    ],

    # Render parameters — lower DPI + JPEG cuts request size dramatically.
    'vision_dpi': 110,
    'vision_image_format': 'JPEG',     # JPEG is 5-10× smaller than PNG for drawings
    'vision_image_quality': 80,        # JPEG quality 0-100
    'vision_max_pages': 1,             # title block is on page 1; raise only if needed
    'vision_temperature': 0.0,
    'vision_max_tokens': 800,          # title-block JSON rarely needs more
    'vision_timeout_s': 45,
    'vision_openai_detail': 'low',     # 'low' = ~85 tokens/image (vs ~765 for high)

    # Trigger vision only when the regex pipeline left at least this many
    # ai_extract / batch_or_extract columns empty / NA. Reduces cost.
    'vision_min_empty_columns': 3,
    # Skip vision entirely on suspiciously large PDFs (likely a scan bundle
    # — not a single document; let the user split first).
    'vision_max_file_mb': 60,

    # In-memory result cache. Capped to keep memory predictable.
    'cache_enabled': True,
    'cache_max_entries': 256,

    # Vision crops the **title-block region** before sending to the model.
    # Coordinates are page-fractions ([x0, y0, x1, y1]); engineering drawings
    # almost universally place the title block in the bottom-right corner.
    # Set to None to send the whole page.
    'vision_title_block_crop': [0.55, 0.55, 1.00, 1.00],
    # Fall back to whole-page if the cropped region produces no useful answer.
    'vision_fallback_full_page': True,

    # Which logical fields can be enriched by vision. The model is asked only
    # for these keys, so the prompt stays short and parsable. Maps to columns
    # in master_index_template.json.
    'vision_eligible_fields': [
        'document_number', 'document_title', 'revision', 'issue_date',
        'revision_status', 'document_type', 'tag', 'vendor_name',
        'po_no', 'contractor_ref', 'vendor_ref', 'originator',
        'project_title', 'unit', 'area',
        # Title-block / document-control fields commonly printed in the
        # bottom-right block of vendor & ADNOC drawings — soft-coded so the
        # regex pipeline gets a vision safety-net for these.
        'author', 'agreement_no', 'agreement_desc',
        'class_review', 'transmittal_no',
    ],

    # Human-readable hints sent to the vision model so it knows exactly
    # what aliases to look for in the title block.  Soft-coded — adding new
    # entries here lets the model recognise more phrasings without code.
    'vision_field_hints': {
        'author':        'Author / Prepared By / Drawn By / Designer name',
        'agreement_no':  'Agreement No. / Contract No. / Frame Agreement / FA No.',
        'agreement_desc':'Agreement Description / Contract Title / Scope of Work',
        'class_review':  'Class / Review Code / CRS Code / Review Class (e.g. 1, 2, 3, A, B, C, D)',
        'transmittal_no':'Transmittal No. / Document Transmittal Number / DTN / TRN / TR-####',
        'issue_date':    'Issue Date / Date / Revision Date (numeric format DD/MM/YY or DD-MM-YYYY)',
        'revision':      'Revision: hand-printed or typed "Rev" / "rev" / "REV" — usually a single letter A-Z, two-digit 00-99, or short codes IFR / IFA / IFC / IFP. Old / handwritten drawings may show it without a colon, with extra whitespace, or with a hyphen ("Rev-0", "rev. A"). Read it from the title-block revision-history column, not from a body paragraph.',
        'document_number':'Drawing/Document Number printed in the title block (alphanumeric with hyphens)',
        'unit':          'Unit / Plant Unit / Unit No / Unit Code / Activate-Unit / Activated Unit / Active Unit. Value is usually a 2-5 digit number ("47", "7600"), but ADNOC legacy drawings may print a compound code like "7600 L 02" or "7300L-04" — return the value exactly as printed (digits + optional letter + digits).',
        'vendor_name':   'Vendor / Vendor Name / Manufacturer / Supplier / Made By / Supplied By / Make / MFG / M/S — the company that produced the equipment. Old or handwritten drawings often print the label "VENDOR" on one line and the company name on the NEXT line WITHOUT a colon (e.g. "VENDOR\\nNUOVO PIGNONE S.P.A."). Return the full company name including legal suffix (SPA, S.R.L., LLC, LTD, GMBH, INC, BV, NV, CO).',
    },

    # ─── Anti-hallucination: per-field regex validators ──────────────────
    # Any value the vision model returns that does NOT match the validator
    # for its field is DROPPED (not written to the row).  This is the safety
    # net for handwritten / illegible content where the model would otherwise
    # invent random letters (e.g. "Ex AOL LGC" instead of "T.T - 11/07/95").
    # All patterns are soft-coded — tune without code changes.
    #   * `pattern`   — must fullmatch (after .strip()).  Case-insensitive
    #                   when the regex itself sets the (?i) inline flag.
    #   * `min_alnum` — minimum alphanumeric chars to accept.
    #   * `max_len`   — caps the value length so the model can't ramble.
    'vision_field_validators': {
        # Dates: dd/mm/yy, dd-mm-yyyy, "12 JUL 95", "JUL-95", "1995-07-11", etc.
        'issue_date': {
            'pattern': (
                r'(?ix)^\s*(?:'
                r'\d{1,2}[\s./\-]+(?:\d{1,2}|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s./\-]+\d{2,4}'
                r'|\d{4}[\s./\-]+\d{1,2}[\s./\-]+\d{1,2}'
                r'|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s./\-]+\d{2,4}'
                r')\s*$'
            ),
            'min_alnum': 4, 'max_len': 24,
        },
        # Revision codes: A, 0, 01, IFA, IFC, IFR, P1, R0, IFR1, …
        # Soft-coded — accepts 1-2 leading letters + up to 3 trailing
        # alphanumerics with optional hyphen so handwritten "Rev-A1"
        # / "P1" / "IFR1" pass through cleanly.
        'revision': {
            'pattern': r'(?i)^[A-Z0-9][A-Z0-9\-]{0,5}$',
            'min_alnum': 1, 'max_len': 6,
        },
        # Document/drawing numbers: must contain at least one digit + a hyphen
        # or letter — rejects pure-letter hallucinations like "Ex AOL LGC".
        'document_number': {
            'pattern': r'(?i)^(?=[A-Z0-9\-/.\s]{4,60}$)(?=.*\d).+$',
            'min_alnum': 4, 'max_len': 60,
        },
        # Tag numbers — e.g. "P-101", "PT-1234A", "12-V-100"
        'tag': {
            'pattern': r'(?i)^[A-Z0-9][A-Z0-9\-/. ,]{1,80}$',
            'min_alnum': 2, 'max_len': 80,
        },
        # PO / Contractor / Vendor refs (alphanumeric, must contain a digit).
        'po_no':          {'pattern': r'(?i)^(?=.*\d)[A-Z0-9][A-Z0-9\-/]{2,30}$', 'min_alnum': 3, 'max_len': 30},
        'contractor_ref': {'pattern': r'(?i)^(?=.*\d)[A-Z0-9][A-Z0-9\-/.]{2,40}$', 'min_alnum': 3, 'max_len': 40},
        'vendor_ref':     {'pattern': r'(?i)^(?=.*\d)[A-Z0-9][A-Z0-9\-/.]{2,40}$', 'min_alnum': 3, 'max_len': 40},
        # Title-block document-control fields.
        'agreement_no':   {'pattern': r'(?i)^[A-Z0-9][A-Z0-9\-/]{2,30}$', 'min_alnum': 3, 'max_len': 30},
        'transmittal_no': {'pattern': r'(?i)^[A-Z0-9][A-Z0-9\-/]{3,40}$', 'min_alnum': 4, 'max_len': 40},
        'class_review':   {'pattern': r'(?i)^[A-Z0-9][A-Z0-9\-/]{0,15}$', 'min_alnum': 1, 'max_len': 15},
        # People / titles — letters, dots, spaces, commas, hyphens.
        'author':         {'pattern': r"^[A-Za-z][A-Za-z0-9 .,&'\-/()]{1,60}$", 'min_alnum': 2, 'max_len': 60},
        'originator':     {'pattern': r"^[A-Za-z][A-Za-z0-9 .,&'\-/()]{1,80}$", 'min_alnum': 2, 'max_len': 80},
        'vendor_name':    {'pattern': r"^[A-Za-z][A-Za-z0-9 .,&'\-/()]{1,80}$", 'min_alnum': 2, 'max_len': 80},
        # Unit codes: 2-5 digits ("47", "7600"), comma-joined ("68,94,95"),
        # OR ADNOC alphanumeric ("7600 L 02", "7300L-04"). Soft-coded —
        # widen the prefix/suffix digit range to accept more legacy forms.
        'unit':           {'pattern': r"(?i)^(?:\d{2,5}(?:\s*,\s*\d{2,5})*|\d{4}\s*[A-Z][\s\-]*\d{1,2})$", 'min_alnum': 2, 'max_len': 24},
    },

    # Generic noise/hallucination filter applied to EVERY vision result.
    # Catches the "Ex AOL LGC" style — short ALL-CAPS tokens that look like
    # the model is guessing letter-by-letter at handwritten text.  Anything
    # matching ANY of these patterns is dropped.  Soft-coded.
    'vision_hallucination_signals': [
        # Three or more 2-3 letter tokens separated by spaces:
        #   "Ex AOL LGC", "AB CDE FG HI", "Aa BB CCc"
        r'(?i)^(?:[A-Z]{1,3}\s+){2,}[A-Z]{1,3}$',
        # Repeated character runs ("AAAA", "----", "....")
        r'^(.)\1{3,}$',
        # Pure punctuation / underscores
        r'^[\W_]+$',
    ],

    # ─── OCR corroboration (permanent anti-hallucination layer) ──────────
    # If the regex/OCR pipeline managed to extract text from the document,
    # any vision-AI value MUST also appear (after normalisation) somewhere
    # in that OCR text, otherwise it is treated as a hallucination and
    # dropped.  This is the strongest single defence against the model
    # inventing strings like "Ex AOL LGC" for handwritten "T.T - 11/07/95":
    # if OCR cannot read it either, vision can't either — drop the value.
    #
    # All knobs are soft-coded.
    'vision_require_ocr_corroboration': True,
    # Minimum OCR text length (chars) before we trust corroboration. If OCR
    # returned almost nothing, we fall back to validators only (since the
    # document is likely a pure scan and OCR can't corroborate anything).
    'vision_ocr_min_text_chars': 80,
    # When the value (normalised) is shorter than this, we skip OCR
    # corroboration (a single char like "A" or "2" is too ambiguous to
    # cross-check).  Validators still apply.
    'vision_ocr_min_value_chars': 3,
    # For multi-token values, fraction of tokens (≥3 chars) that must
    # appear in OCR text before the value is accepted.
    'vision_ocr_token_overlap_ratio': 0.5,
    # Fields that bypass corroboration (e.g., free-form descriptions where
    # vision rephrases — soft-coded so per-field carve-outs are easy).
    'vision_ocr_corroboration_skip_fields': [
        'agreement_desc',  # often paraphrased / multi-line, hard to corroborate verbatim
    ],

    # System prompt — instructs the model to behave like a careful
    # engineering-document reader, never guess, and never invent values.
    'vision_system_prompt': (
        "You are a senior document-control engineer reading the title block "
        "of an engineering drawing or vendor document.\n"
        "Extract ONLY values that are clearly machine-printed and unambiguous.\n"
        "CRITICAL RULES — read carefully:\n"
        "  1. NEVER guess. NEVER invent. NEVER hallucinate.\n"
        "  2. If a value is HANDWRITTEN, partially erased, faded, smudged, "
        "rotated past 45 degrees, or otherwise illegible, return an EMPTY "
        "STRING for that field. Do NOT attempt to read handwriting unless "
        "every single character is unambiguous.\n"
        "  3. If you can read only some characters of a field, return an "
        "EMPTY STRING — partial reads cause downstream errors.\n"
        "  4. Use the EXACT format shown in the image (preserve dots, "
        "hyphens, slashes, leading zeros). Do not normalise dates.\n"
        "  5. Return strictly a JSON object with the requested keys. Use "
        "empty strings for unreadable fields. No commentary, no markdown."
    ),
}


# ---------------------------------------------------------------------------
# OCR fallback
# ---------------------------------------------------------------------------

def _has_tesseract() -> bool:
    """Detect pytesseract + tesseract binary at runtime."""
    try:
        import pytesseract  # noqa: F401
        from pytesseract import get_tesseract_version
        get_tesseract_version()
        return True
    except Exception:
        return False


def _render_page_to_pil(file_path: str, page_no: int, dpi: int):
    """Render *page_no* (0-based) of the PDF to a PIL.Image. Returns None on failure."""
    try:
        import fitz
        from PIL import Image
    except ImportError:
        return None
    try:
        doc = fitz.open(file_path)
        if page_no >= doc.page_count:
            doc.close()
            return None
        page = doc.load_page(page_no)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
        doc.close()
        return img
    except Exception:
        logger.exception('render_page_to_pil failed for %s p%s', file_path, page_no)
        return None


def ocr_pdf_text(file_path: str) -> str:
    """
    Run pytesseract over the first *ocr_max_pages* pages of the PDF and
    return concatenated text. Returns '' when OCR is unavailable or fails.
    """
    if not VISION_CONFIG['enable_ocr_fallback']:
        return ''
    if not _has_tesseract():
        logger.info('OCR fallback skipped — tesseract not available')
        return ''
    try:
        import pytesseract
    except ImportError:
        return ''
    chunks: List[str] = []
    max_pages = int(VISION_CONFIG['ocr_max_pages'])
    dpi = int(VISION_CONFIG['ocr_dpi'])
    for page_no in range(max_pages):
        img = _render_page_to_pil(file_path, page_no, dpi)
        if img is None:
            break
        # Try each PSM mode; first non-empty wins.
        page_text = ''
        for psm in VISION_CONFIG['ocr_psm_modes']:
            try:
                cfg = f'--psm {psm}'
                page_text = pytesseract.image_to_string(
                    img, lang=VISION_CONFIG['ocr_lang'], config=cfg,
                )
                if page_text and page_text.strip():
                    break
            except Exception:
                logger.exception('Tesseract PSM %s failed', psm)
        if page_text:
            chunks.append(page_text)
    return '\n'.join(chunks)


# ---------------------------------------------------------------------------
# Vision AI enrichment — multi-provider, cost-optimised
# ---------------------------------------------------------------------------

# Module-level result cache: {sha256+missing_key: {field: value}}
# Bounded; oldest entry is dropped when full.
_RESULT_CACHE: Dict[str, Dict[str, str]] = {}


def _cache_get(key: str) -> Optional[Dict[str, str]]:
    if not VISION_CONFIG.get('cache_enabled'):
        return None
    return _RESULT_CACHE.get(key)


def _cache_put(key: str, value: Dict[str, str]) -> None:
    if not VISION_CONFIG.get('cache_enabled'):
        return
    cap = int(VISION_CONFIG.get('cache_max_entries', 256))
    if len(_RESULT_CACHE) >= cap:
        # Drop oldest (insertion order — Python 3.7+ dicts).
        try:
            _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)))
        except StopIteration:
            pass
    _RESULT_CACHE[key] = value


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


def _gemini_api_key() -> Optional[str]:
    return (
        os.getenv('GEMINI_API_KEY')
        or os.getenv('GOOGLE_GENERATIVEAI_API_KEY')
        or getattr(settings, 'GEMINI_API_KEY', None)
    )


def _openai_api_key() -> Optional[str]:
    return os.getenv('OPENAI_API_KEY') or getattr(settings, 'OPENAI_API_KEY', None)


def _crop_pil(img, frac: Optional[List[float]]):
    if not frac or len(frac) != 4:
        return img
    w, h = img.size
    x0, y0, x1, y1 = frac
    return img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))


def _pil_to_bytes(img) -> Tuple[bytes, str]:
    """Encode PIL image using the configured format/quality (returns bytes, mime)."""
    import io
    fmt = (VISION_CONFIG.get('vision_image_format') or 'JPEG').upper()
    buf = io.BytesIO()
    if fmt == 'JPEG':
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img.save(buf, format='JPEG',
                 quality=int(VISION_CONFIG.get('vision_image_quality', 80)),
                 optimize=True)
        return buf.getvalue(), 'image/jpeg'
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue(), 'image/png'


def _build_user_prompt(missing_fields: List[str], file_name: str) -> str:
    """Compact user prompt — only asks for fields the regex pipeline missed."""
    hints = VISION_CONFIG.get('vision_field_hints', {}) or {}
    # Each line: `  - <key>   (alias hint, if known)` — gives the model the
    # synonyms it needs to recognise a field even when the title block uses
    # an abbreviation (e.g. "FA No." for agreement_no).  Soft-coded.
    lines = []
    for f in missing_fields:
        hint = hints.get(f)
        if hint:
            lines.append(f'  - {f}   ({hint})')
        else:
            lines.append(f'  - {f}')
    field_list = '\n'.join(lines)
    return (
        f"Image: title block / metadata region of '{file_name}'.\n\n"
        f"Read these fields from the image and return them as a JSON object. "
        f"Use an empty string when a field is not visible.\n\n"
        f"Required keys (use these exact key names; the parenthesised text "
        f"is a hint, not the answer):\n{field_list}\n\n"
        f"Output ONLY the JSON object — no prose, no markdown."
    )


def _parse_json_response(content: str) -> Dict[str, str]:
    """Permissive JSON extraction — strips code fences, finds first {...} block."""
    if not content:
        return {}
    s = content.strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s)
        s = re.sub(r'\s*```$', '', s)
    m = re.search(r'\{.*\}', s, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: ('' if v is None else str(v).strip()) for k, v in data.items()}


# ─── Anti-hallucination filter ──────────────────────────────────────────
# Compile validator + global-noise patterns once; soft-coded in VISION_CONFIG.
_COMPILED_VALIDATORS: Dict[str, Any] = {}
_COMPILED_HALLUCINATION: List[Any] = []


def _compile_anti_hallucination_patterns() -> None:
    """Compile validator regexes once; tolerate bad entries gracefully."""
    global _COMPILED_VALIDATORS, _COMPILED_HALLUCINATION
    _COMPILED_VALIDATORS = {}
    for key, spec in (VISION_CONFIG.get('vision_field_validators') or {}).items():
        try:
            _COMPILED_VALIDATORS[key] = {
                'regex':     re.compile(spec['pattern']),
                'min_alnum': int(spec.get('min_alnum', 1)),
                'max_len':   int(spec.get('max_len', 200)),
            }
        except re.error:
            logger.exception('Invalid validator regex for %s — skipping', key)
    _COMPILED_HALLUCINATION = []
    for pat in (VISION_CONFIG.get('vision_hallucination_signals') or []):
        try:
            _COMPILED_HALLUCINATION.append(re.compile(pat))
        except re.error:
            logger.exception('Invalid hallucination regex %r — skipping', pat)


_compile_anti_hallucination_patterns()


def _is_acceptable_vision_value(field_key: str, value: str) -> bool:
    """
    Soft-coded gate that drops vision hallucinations.

    Returns False if any of:
      * value matches a global hallucination pattern (e.g. "Ex AOL LGC"),
      * value fails the per-field validator (regex, min_alnum, max_len).
    Fields without a configured validator are accepted as long as they pass
    the global noise filter.
    """
    if not value:
        return False
    v = value.strip()
    # Global noise filter — applies to every field.
    for rx in _COMPILED_HALLUCINATION:
        if rx.match(v):
            return False
    spec = _COMPILED_VALIDATORS.get(field_key)
    if not spec:
        return True
    if len(v) > spec['max_len']:
        return False
    if sum(1 for c in v if c.isalnum()) < spec['min_alnum']:
        return False
    return bool(spec['regex'].fullmatch(v))


# ─── OCR corroboration ──────────────────────────────────────────────────
# A vision value is "corroborated" only if the same characters (after
# punctuation/whitespace stripping) appear in the OCR-extracted text of the
# same document.  This is the permanent defence against handwriting
# hallucination: if OCR couldn't read it, vision can't have read it either.

_NORMALISE_FOR_MATCH_RX = re.compile(r'[\W_]+', re.UNICODE)


def _normalise_for_match(s: str) -> str:
    """Uppercase + strip every non-alphanumeric char.  '11/07/95' → '110795'."""
    if not s:
        return ''
    return _NORMALISE_FOR_MATCH_RX.sub('', s).upper()


def _is_corroborated_by_ocr(field_key: str, value: str, ocr_text: str) -> bool:
    """
    Returns True when ``value`` appears (normalised, fuzzy) in ``ocr_text``.

    Behaviour:
      * Master switch off → always True (skip).
      * Field is in the skip list → always True.
      * OCR text is too short to be useful → always True (validators only).
      * Normalised value is too short to be meaningful → always True.
      * Substring match (normalised) → True.
      * Else: ≥``token_overlap_ratio`` of the value's significant tokens
        (length ≥ 3) must appear in OCR text.
    """
    if not VISION_CONFIG.get('vision_require_ocr_corroboration', False):
        return True
    if field_key in (VISION_CONFIG.get('vision_ocr_corroboration_skip_fields') or []):
        return True
    if not ocr_text or len(ocr_text.strip()) < int(VISION_CONFIG.get('vision_ocr_min_text_chars', 80)):
        return True

    nv = _normalise_for_match(value)
    if len(nv) < int(VISION_CONFIG.get('vision_ocr_min_value_chars', 3)):
        return True

    nt = _normalise_for_match(ocr_text)
    if nv and nv in nt:
        return True

    # Token-level fallback for multi-word values.
    tokens = [t for t in re.split(r'[\W_]+', value) if len(t) >= 3]
    if not tokens:
        return False  # short single-token value & not a substring → fail.
    haystack = ocr_text.upper()
    found = sum(1 for t in tokens if t.upper() in haystack)
    ratio = float(VISION_CONFIG.get('vision_ocr_token_overlap_ratio', 0.5))
    return (found / len(tokens)) >= ratio


# ----- Provider: Gemini ----------------------------------------------------

def _call_gemini_vision(model: str, img_bytes: bytes, mime: str,
                        missing_fields: List[str], file_name: str) -> Dict[str, str]:
    """Call Google Gemini vision. Returns {} on any failure."""
    api_key = _gemini_api_key()
    if not api_key:
        return {}
    try:
        from google import genai
        from google.genai import types as _gtypes
    except ImportError:
        logger.info('google-genai not installed — Gemini vision skipped')
        return {}
    try:
        client = genai.Client(api_key=api_key)
        parts = [
            _build_user_prompt(missing_fields, file_name),
            _gtypes.Part.from_bytes(data=img_bytes, mime_type=mime),
        ]
        cfg = _gtypes.GenerateContentConfig(
            system_instruction=VISION_CONFIG['vision_system_prompt'],
            max_output_tokens=int(VISION_CONFIG['vision_max_tokens']),
            temperature=float(VISION_CONFIG['vision_temperature']),
            response_mime_type='application/json',
            seed=42,
        )
        resp = client.models.generate_content(model=model, contents=parts, config=cfg)
        text = (getattr(resp, 'text', '') or '').strip()
        return _parse_json_response(text)
    except Exception as exc:
        logger.warning('Gemini vision call failed (%s): %s', model, exc)
        return {}


# ----- Provider: OpenAI ----------------------------------------------------

def _call_openai_vision(model: str, img_bytes: bytes, mime: str,
                        missing_fields: List[str], file_name: str) -> Dict[str, str]:
    """Call OpenAI vision. Returns {} on any failure."""
    api_key = _openai_api_key()
    if not api_key:
        return {}
    try:
        from openai import OpenAI
    except ImportError:
        return {}
    try:
        client = OpenAI(
            api_key=api_key,
            timeout=float(VISION_CONFIG['vision_timeout_s']),
            max_retries=1,
        )
        b64 = base64.b64encode(img_bytes).decode('ascii')
        resp = client.chat.completions.create(
            model=model,
            temperature=float(VISION_CONFIG['vision_temperature']),
            max_tokens=int(VISION_CONFIG['vision_max_tokens']),
            messages=[
                {'role': 'system', 'content': VISION_CONFIG['vision_system_prompt']},
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': _build_user_prompt(missing_fields, file_name)},
                        {
                            'type': 'image_url',
                            'image_url': {
                                'url': f'data:{mime};base64,{b64}',
                                'detail': VISION_CONFIG.get('vision_openai_detail', 'low'),
                            },
                        },
                    ],
                },
            ],
            response_format={'type': 'json_object'},
        )
        content = resp.choices[0].message.content or ''
        return _parse_json_response(content)
    except Exception as exc:
        logger.warning('OpenAI vision call failed (%s): %s', model, exc)
        return {}


def _dispatch_vision(img_bytes: bytes, mime: str,
                     missing_fields: List[str], file_name: str) -> Tuple[str, Dict[str, str]]:
    """
    Walk through ``providers`` in order, returning (provider_label, result) on
    first non-empty extraction. Returns ('', {}) if all providers fail or are
    unavailable.
    """
    for entry in VISION_CONFIG.get('providers', []):
        if not entry.get('enabled'):
            continue
        provider = entry.get('provider')
        model = entry.get('model')
        if not provider or not model:
            continue
        if provider == 'gemini':
            result = _call_gemini_vision(model, img_bytes, mime, missing_fields, file_name)
        elif provider == 'openai':
            result = _call_openai_vision(model, img_bytes, mime, missing_fields, file_name)
        else:
            continue
        if any((result.get(k) or '').strip() for k in missing_fields):
            return f'{provider}:{model}', result
    return '', {}


def enrich_via_vision(*, file_path: str, file_name: str,
                      current_row: Dict[str, Any],
                      na_value: str = 'NA',
                      ocr_text: str = '') -> Dict[str, str]:
    """
    Top-level entry: returns a dict of {column_key: extracted_value} for any
    *eligible* field that is currently empty / NA in ``current_row``. Empty
    dict means "no enrichment available" (no API key, no missing fields,
    PDF unrenderable, model returned nothing). Caller merges only into
    blank cells — never overwrites.

    ``ocr_text`` (optional) is used as a corroboration corpus: vision
    values that don't appear (normalised) in the OCR text are treated as
    hallucinations and dropped.  Pass the full text already extracted by
    pdfplumber+OCR to enable this defence.
    """
    if not VISION_CONFIG['enable_vision_ai']:
        return {}
    if not file_path or not file_path.lower().endswith('.pdf'):
        return {}

    # File-size sanity check — skip absurdly large bundles.
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb > float(VISION_CONFIG.get('vision_max_file_mb', 60)):
            logger.info('Vision skipped — file %s is %.1f MB (limit %.0f)',
                        file_name, size_mb, VISION_CONFIG['vision_max_file_mb'])
            return {}
    except OSError:
        pass

    eligible = VISION_CONFIG['vision_eligible_fields']
    missing = [
        k for k in eligible
        if not current_row.get(k) or str(current_row.get(k)).strip().upper() in ('', na_value.upper())
    ]
    if len(missing) < int(VISION_CONFIG['vision_min_empty_columns']):
        return {}

    # Cost saver: bail early if no provider has credentials.
    if not _gemini_api_key() and not _openai_api_key():
        logger.info('Vision skipped — no GEMINI_API_KEY or OPENAI_API_KEY configured')
        return {}

    # Result cache lookup keyed by file content + missing set.
    cache_key = ''
    digest = _file_sha256(file_path)
    if digest:
        cache_key = f'{digest}:{",".join(sorted(missing))}'
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info('Vision cache hit for %s (%d field(s))', file_name, len(cached))
            return cached

    img = _render_page_to_pil(file_path, 0, int(VISION_CONFIG['vision_dpi']))
    if img is None:
        return {}

    # First attempt: title-block crop (cheaper, focused).
    crop = _crop_pil(img, VISION_CONFIG.get('vision_title_block_crop'))
    crop_bytes, mime = _pil_to_bytes(crop)
    provider_label, extracted = _dispatch_vision(crop_bytes, mime, missing, file_name)

    # Retry with full page if crop returned nothing useful.
    if not extracted and VISION_CONFIG.get('vision_fallback_full_page'):
        full_bytes, mime = _pil_to_bytes(img)
        provider_label, extracted = _dispatch_vision(full_bytes, mime, missing, file_name)

    if extracted:
        logger.info('Vision enrichment via %s for %s', provider_label, file_name)

    # Filter to eligible-only, non-blank, missing-only keys.
    out: Dict[str, str] = {}
    for k in missing:
        v = (extracted.get(k) or '').strip()
        if not v:
            continue
        if not _is_acceptable_vision_value(k, v):
            logger.info('Vision value rejected (%s = %r) — failed validator', k, v)
            continue
        if not _is_corroborated_by_ocr(k, v, ocr_text):
            logger.info('Vision value rejected (%s = %r) — not corroborated by OCR', k, v)
            continue
        out[k] = v

    if cache_key:
        _cache_put(cache_key, out)
    return out


# ---------------------------------------------------------------------------
# Convenience: single function the master_index_service hooks into.
# ---------------------------------------------------------------------------

def needs_ocr_fallback(text: str) -> bool:
    """Decide whether to fire the OCR fallback based on plain-text length."""
    if not VISION_CONFIG['enable_ocr_fallback']:
        return False
    threshold = int(VISION_CONFIG['ocr_skip_threshold_chars'])
    return (text or '').strip().__len__() < threshold
