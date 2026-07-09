"""
Piping Valve MTO — PDF Extractor
=================================

Vision-assisted extractor that reads a P&ID / valve-data PDF and returns the
canonical Valve MTO row schema consumed by the frontend
(`frontend/src/pages/Engineering/Piping/ValveMTO.jsx`).

Design notes
------------
* Soft-coded: every threshold, regex, prompt template and model name lives
  at module level so they can be tuned without code changes.
* Fast & cheap: text-first via PyMuPDF; Vision (GPT-4o) only runs when text
  yields fewer than `TEXT_SUFFICIENT_CHARS` characters AND `OPENAI_API_KEY`
  is configured. If OpenAI is unavailable the extractor still returns any
  rows that text-regex could find (graceful degradation).
* Returns the frontend's row keys directly — no mapping layer needed.

Public entry point
------------------
    extract_valve_mto(pdf_path: str) -> dict

Returned shape::

    {
      "status": "ok" | "error",
      "engine": "text" | "vision" | "text+vision",
      "page_count": int,
      "rows":         [ { "sl_no": 1, "area": "...", ... }, ... ],
      "project_meta": { "doc_no": "...", "doc_title": "...", ... },
      "warnings":     [ "..." ]
    }
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Soft-coded constants (env-overridable) ─────────────────────────────────────
TEXT_SUFFICIENT_CHARS  = int(os.getenv('VALVE_MTO_TEXT_THRESHOLD', '1500'))
# Hard cap on pages we'll process — protects against runaway docs.
VISION_MAX_PAGES       = int(os.getenv('VALVE_MTO_VISION_MAX_PAGES', '50'))
# How many pages to bundle into a single OpenAI call (smaller = more accurate, larger = cheaper).
# Default lowered to 1 so each Vision call focuses on a single page — dramatically
# improves line-number recall on dense valve MTO drawings. Override via env if
# token-cost is more important than completeness.
VISION_BATCH_SIZE      = int(os.getenv('VALVE_MTO_VISION_BATCH_SIZE', '1'))
# How many batches to run in parallel.
VISION_PARALLEL_BATCHES = int(os.getenv('VALVE_MTO_VISION_PARALLEL', '4'))
VISION_IMAGE_DPI       = int(os.getenv('VALVE_MTO_VISION_DPI', '120'))
VISION_MAX_EDGE_PX     = int(os.getenv('VALVE_MTO_VISION_MAX_EDGE', '1600'))
VISION_MODEL           = os.getenv('VALVE_MTO_VISION_MODEL', 'gpt-4o-mini')
VISION_TEMPERATURE     = 0.0
VISION_TIMEOUT_SECS    = float(os.getenv('VALVE_MTO_VISION_TIMEOUT', '90'))
JPEG_QUALITY           = 80
MAX_ROWS               = int(os.getenv('VALVE_MTO_MAX_ROWS', '2000'))

# Canonical row schema — must match frontend `valveMTO.config.js` VALVE_COLUMNS.
# NOTE: order does not affect extraction; new keys are additive. Frontend
# default-row init iterates this list and treats unknown keys as text.
ROW_KEYS = [
    'sl_no', 'area', 'type', 'pms_class', 'piping_class', 'rating', 'size_1', 'size_2',
    'bore', 'line_number', 'line_list', 'valve_tag', 'description', 'qty_island', 'qty_field', 'unit',
    'remarks',
]

# Soft-coded list (kept small — vision model picks the closest match).
VALID_AREAS       = ['ISLAND', 'Field', 'COMBINED']
NUMERIC_KEYS      = {'sl_no', 'qty_island', 'qty_field', 'unit'}

# ─── Soft-coded line-number harvester ───────────────────────────────────
# Multi-format regex patterns mirrored from the Line List engine
# (apps/designiq/pid_ocr_extractor_v2.py → COVERAGE_AUDIT_CONFIG). Scans the
# PyMuPDF-extracted text and surfaces EVERY plausible line designation. The
# harvested list is then injected into the Vision prompt as a hint so the
# model does not skip line numbers that are clearly present in the embedded
# PDF text layer. Pure additive helper — never overrides Vision output.
#
# Supported shapes (any of these match):
#   • Onshore         2"-D-6152-033842-X-N
#   • Industrial      2"-2600-FL-352-32070R-E
#   • Offshore        604-LFG-3-AC2GA0-2012
#   • ADNOC           6"-CD-AC3N-8256
#   • General         4"-41-SWR-64313-A2AU16-V
#   • Borouge/Linde   1"-63-UA-149472-A1AU01-V
LINE_NUMBER_HARVEST_PATTERNS: List[str] = [
    # SIZE"-FLUID-SEQ-PIPESPEC-DEPT-INSUL (Onshore)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z]{1,4}[-\s]+\d{3,6}[-\s]+\d{4,8}(?:[-\s]+[A-Z0-9]{1,4}){0,3}\b',
    # SIZE"-UNIT-SERVICE-SEQ-PIPECLASS(-END) (Industrial / Samsung / Foster Wheeler)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+\d{3,4}[-\s]+[A-Z]{1,6}[-\s]+\d{3,5}[-\s]+\d{5}[A-Z]{1,2}(?:[-\s]+[A-Z])?\b',
    # AREA-FLUID-SIZE-PIPECLASS-SEQ (Offshore)
    r'\b\d{2,4}[-\s]+[A-Z]{1,4}[-\s]+\d{1,2}["]?[-\s]+[A-Z0-9]{4,10}[-\s]+\d{3,6}\b',
    # SIZE"-FLUID-CLASS-SEQ (ADNOC compact)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z]{1,4}[-\s]+[A-Z0-9]{3,6}[-\s]+\d{3,6}\b',
    # SIZE"-AREA-SERVICE-SEQ-PIPECLASS-END (Borouge / Linde area-first)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+\d{2,3}[-\s]+[A-Z]{1,3}[-\s]+\d{4,6}[-\s]+[A-Z0-9]{5,7}[-\s]+[A-Z]{1,2}\b',
    # Generic hyphenated tag — broad fallback (3-6 segments, alphanumerics)
    r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z0-9]{1,6}[-\s]+[A-Z0-9]{1,8}[-\s]+[A-Z0-9]{2,10}(?:[-\s]+[A-Z0-9]{1,10}){0,3}\b',
]
_LINE_NUMBER_HARVEST_RE = [re.compile(p, re.IGNORECASE) for p in LINE_NUMBER_HARVEST_PATTERNS]

# Cap how many candidates we forward to the Vision prompt (avoid token blow-up).
LINE_NUMBER_HARVEST_MAX_CANDIDATES = int(os.getenv('VALVE_MTO_LINE_HARVEST_MAX', '200'))


def _harvest_line_numbers(text: str) -> List[str]:
    """Return a deduplicated, normalised list of plausible line numbers.

    Pure additive helper used to inject high-quality candidates into the
    Vision prompt. Never raises — returns ``[]`` on any failure. Order is
    preserved (first occurrence wins) so the prompt list reads top-to-bottom
    of the source document.
    """
    if not text:
        return []
    seen: set = set()
    out: List[str] = []
    try:
        for regex in _LINE_NUMBER_HARVEST_RE:
            for match in regex.findall(text):
                # ``findall`` returns either str or tuple depending on groups.
                tag = match if isinstance(match, str) else (match[0] if match else '')
                tag = re.sub(r'\s+', '', tag).strip().upper()
                # Drop obvious noise — must contain at least one digit AND one letter.
                if not tag or not re.search(r'\d', tag) or not re.search(r'[A-Z]', tag):
                    continue
                if tag in seen:
                    continue
                seen.add(tag)
                out.append(tag)
                if len(out) >= LINE_NUMBER_HARVEST_MAX_CANDIDATES:
                    return out
    except Exception as exc:                                            # pragma: no cover
        logger.warning('[ValveMTO] line-number harvest failed: %s', exc)
    return out

# ─── Soft-coded valve-suffix → Remark dictionary ─────────────────────────
# Standard P&ID condition / operator codes that are usually appended to a
# valve tag (e.g. ``BV-1234-LO``, ``GV-08-FBLC``). When a valve_tag contains
# any of these tokens we surface the matching code in the Remarks column so
# the engineer sees "LO, LC, TSO, …" without having to decode the tag.
#
# Order matters — longer codes are checked first so ``FBLC`` is not chopped
# down to ``LC`` mid-match. Edit this list to add new project conventions
# (e.g. car-sealed, fail-safe, normally-closed) without touching the core
# extraction logic.
VALVE_TAG_REMARK_CODES: List[Tuple[str, str]] = [
    ('FBLO',  'FBLO'),  # Full-Bore Locked Open
    ('FBLC',  'FBLC'),  # Full-Bore Locked Closed
    ('CSO',   'CSO'),   # Car-Sealed Open
    ('CSC',   'CSC'),   # Car-Sealed Closed
    ('TSO',   'TSO'),   # Tight Shut-Off
    ('NRV',   'NRV'),   # Non-Return Valve
    ('LO',    'LO'),    # Locked Open
    ('LC',    'LC'),    # Locked Closed
    ('NO',    'NO'),    # Normally Open
    ('NC',    'NC'),    # Normally Closed
    ('FO',    'FO'),    # Fail Open
    ('FC',    'FC'),    # Fail Closed
    ('FL',    'FL'),    # Fail Last
    ('FI',    'FI'),    # Fail Indeterminate
]
# Pre-compile a single regex with ordered alternation; word-boundary on both
# sides of the token avoids matching letters embedded inside a longer word.
# Boundary excludes letters on both sides (so FLOW does not match FL, FCV does
# not match FC) but ALLOWS digits/hyphens/spaces — that way patterns like
# ``BV-LO-1234``, ``V101LO``, ``LO/LC`` or ``LO 6"`` all match.
_VALVE_TAG_REMARK_RE = re.compile(
    r'(?<![A-Z])(' + '|'.join(re.escape(c) for c, _ in VALVE_TAG_REMARK_CODES) + r')(?![A-Z])',
    re.IGNORECASE,
)

# ─── Soft-coded spelled-out phrase → code dictionary ────────────────────
# Vision often emits the English phrase in `description`/`remarks` instead of
# the short token (e.g. ``Locked Open`` rather than ``LO``). Map these to
# the canonical code so the Remarks column stays consistent. Order matters:
# longer/more-specific phrases first so ``FAIL OPEN`` is not stolen by ``OPEN``.
VALVE_PHRASE_REMARK_CODES: List[Tuple[str, str]] = [
    ('FULL-BORE LOCKED OPEN',   'FBLO'),
    ('FULL BORE LOCKED OPEN',   'FBLO'),
    ('FULL-BORE LOCKED CLOSED', 'FBLC'),
    ('FULL BORE LOCKED CLOSED', 'FBLC'),
    ('CAR-SEALED OPEN',         'CSO'),
    ('CAR SEALED OPEN',         'CSO'),
    ('CAR-SEALED CLOSED',       'CSC'),
    ('CAR SEALED CLOSED',       'CSC'),
    ('TIGHT SHUT-OFF',          'TSO'),
    ('TIGHT SHUT OFF',          'TSO'),
    ('TIGHT SHUTOFF',           'TSO'),
    ('NON-RETURN VALVE',        'NRV'),
    ('NON RETURN VALVE',        'NRV'),
    ('LOCKED OPEN',             'LO'),
    ('LOCKED CLOSED',           'LC'),
    ('NORMALLY OPEN',           'NO'),
    ('NORMALLY CLOSED',         'NC'),
    ('FAIL OPEN',               'FO'),
    ('FAIL CLOSED',             'FC'),
    ('FAIL CLOSE',              'FC'),
    ('FAIL LAST',               'FL'),
    ('FAIL IN PLACE',           'FL'),
    ('FAIL INDETERMINATE',      'FI'),
]
_VALVE_PHRASE_REMARK_RE = re.compile(
    r'(?<![A-Z])(' + '|'.join(re.escape(p) for p, _ in VALVE_PHRASE_REMARK_CODES) + r')(?![A-Z])',
    re.IGNORECASE,
)

# Fields scanned when deriving Remarks from a row. Vision can misplace the
# operational status code into ANY string column (tag, description, type,
# line_number, even pms_class), so scan every text field. Order controls
# which field's match wins when duplicates appear (purely cosmetic since
# duplicates are deduped against canonical codes).
REMARK_SOURCE_FIELDS: Tuple[str, ...] = (
    'valve_tag', 'remarks', 'description',
    'type', 'line_number', 'pms_class', 'rating', 'bore',
)

# Substrings of the original `remarks` text that should be DROPPED when
# merging derived codes back — these are placeholder/empty-ish values the
# Vision model sometimes emits. Anything else free-text is preserved.
_REMARK_DROP_PATTERNS: Tuple[str, ...] = (
    'n/a', 'na', 'none', 'null', '-', '—', '–', '.',
)


def _derive_remarks_from_row(row: Dict[str, Any]) -> str:
    """Return a comma-separated list of suffix codes found anywhere in the row.

    Scans REMARK_SOURCE_FIELDS for BOTH short-token codes (LO/LC/…) and
    spelled-out phrase variants ("Locked Open" → LO). Empty string when no
    codes match. Order of returned codes follows VALVE_TAG_REMARK_CODES so
    longer/more-specific codes win and duplicates are dropped.
    """
    found: List[str] = []
    label_by_code = {c.upper(): label for c, label in VALVE_TAG_REMARK_CODES}

    def _add(label: str) -> None:
        if label and label not in found:
            found.append(label)

    for field in REMARK_SOURCE_FIELDS:
        text = row.get(field, '') or ''
        if not text:
            continue
        s = str(text)
        # 1) Spelled-out phrases first (longer matches win).
        for raw in _VALVE_PHRASE_REMARK_RE.findall(s):
            phrase = raw.upper().replace('-', ' ')
            for ph, code in VALVE_PHRASE_REMARK_CODES:
                if ph.upper().replace('-', ' ') == phrase:
                    _add(label_by_code.get(code.upper(), code))
                    break
        # 2) Short-token codes (LO, LC, TSO, FBLC, FBLO, …).
        for raw in _VALVE_TAG_REMARK_RE.findall(s):
            _add(label_by_code.get(raw.upper(), ''))

    # Preserve the canonical code-list order from VALVE_TAG_REMARK_CODES so
    # output is deterministic regardless of which field surfaced the code.
    order = {label: i for i, (_, label) in enumerate(VALVE_TAG_REMARK_CODES)}
    found.sort(key=lambda l: order.get(l, 999))
    return ', '.join(found)


def _merge_remarks(original: str, derived: str) -> str:
    """Combine derived codes with any pre-existing free-text remarks.

    Keeps the original free-text (if meaningful) and APPENDS any derived
    codes that aren't already present — so legitimate engineering notes are
    never destroyed by the code-derivation pass.
    """
    orig = (original or '').strip()
    der  = (derived or '').strip()
    if not der:
        return orig
    if not orig or orig.lower() in _REMARK_DROP_PATTERNS:
        return der
    # Skip merge when the original is just the same code list (case-insensitive,
    # whitespace-insensitive comparison).
    norm = lambda s: re.sub(r'\s+', '', s).upper()
    if norm(orig) == norm(der):
        return der
    # If every derived code already appears as a sub-string of the original,
    # leave the original untouched.
    orig_up = orig.upper()
    extra = [c.strip() for c in der.split(',') if c.strip() and c.strip().upper() not in orig_up]
    if not extra:
        return orig
    return f"{orig} ({', '.join(extra)})"


# Backwards-compatible thin wrapper (kept in case other modules import it).
def _derive_remarks_from_tag(valve_tag: str) -> str:
    return _derive_remarks_from_row({'valve_tag': valve_tag})

# Soft-coded fatal-error classifier. If a batch exception's stringified form
# contains any of these substrings (case-insensitive), the whole job is
# considered unrecoverable and the snapshot status is flipped to 'error' so
# the frontend can show a meaningful message instead of a silent zero-row
# result. Map: substring -> human-friendly message.
OPENAI_FATAL_ERROR_PATTERNS: List[Tuple[str, str]] = [
    ('insufficient_quota',
     'OpenAI account has no remaining quota. Top up the API plan or rotate '
     'OPENAI_API_KEY, then retry the extraction.'),
    ('invalid_api_key',
     'OPENAI_API_KEY is invalid or revoked. Update the backend env var and '
     'restart the container.'),
    ('error code: 401',
     'OpenAI rejected the API key (401 Unauthorized). Check OPENAI_API_KEY.'),
    ('error code: 403',
     'OpenAI denied access to the Vision model (403). Verify the project '
     'has access to the configured VALVE_MTO_VISION_MODEL.'),
    ('billing_hard_limit_reached',
     'OpenAI hard billing limit reached. Raise the limit or top up credit.'),
]


def _classify_openai_error(exc: Exception) -> Optional[str]:
    """Return a friendly message if the exception matches a fatal pattern."""
    msg = str(exc).lower()
    for needle, friendly in OPENAI_FATAL_ERROR_PATTERNS:
        if needle.lower() in msg:
            return friendly
    return None

# Prompt is intentionally explicit — every column is described including
# accepted values so the model emits clean JSON.
VISION_PROMPT_TEMPLATE = """\
You are a senior piping engineer extracting a VALVE MATERIAL TAKE-OFF (Valve MTO)
from the attached drawing/datasheet pages (this batch covers pages {page_range} of a
larger document). Return ONLY a valid JSON object — no prose.

Schema:
{{
  "project_meta": {{
    "doc_no": "<COMPANY Document No., e.g. PJ6-EXD-GEN-TX0T-0004>",
    "doc_title": "<title, e.g. PIPING VALVES MTO>",
    "doc_desc": "<doc description>",
    "revision": "<numeric or alphanumeric revision>",
    "doc_date": "<YYYY-MM-DD if visible>",
    "project_name": "<project name if visible>"
  }},
  "rows": [
    {{
      "sl_no":       <integer>,
      "area":        "ISLAND" | "Field" | "COMBINED",
      "type":        "<BALL VALVE | GATE VALVE | GLOBE VALVE | CHECK VALVE | PLUG VALVE | BUTTERFLY VALVE | NEEDLE VALVE>",
      "pms_class":   "<piping material class — long descriptive name, e.g. 'CS A106 Gr B'>",
      "piping_class": "<piping spec class — short code only, e.g. 'A1A', 'B1B', 'CS1A'>",
      "rating":      "<e.g. CLASS 150 RF, CLASS 600 RTJ>",
      "size_1":      "<nominal bore in inches with double quotes, e.g. 2\\"",
      "size_2":      "<reduced size if any, else empty string>",
      "bore":        "FB" | "RB" | "",
      "line_number": "<piping line number / line tag / P&ID number the valve sits on, e.g. 6\"-P-12345-A1A-N>",
      "line_list":   "<line-list document ref or LL row id if visible (e.g. 'LL-001', 'PJ6-LL-003'), else empty string>",
      "valve_tag":   "<valve tag id>",
      "description": "<short service description>",
      "qty_island":  <integer total in ISLAND, 0 if none>,
      "qty_field":   <integer total in FIELD, 0 if none>,
      "unit":        <integer total quantity (units) for this valve row, 0 if none>,
      "remarks":     "<operational status codes if visible: LO, LC, CSO, CSC, TSO, FBLO, FBLC, NRV, NO, NC, FO, FC, FL, FI — comma-separated; otherwise free text>"
    }}
  ]
}}

Rules:
- For "remarks": this column is CRITICAL. Inspect EVERY valve symbol and its adjacent legend annotation on the drawing very carefully — valves are often labelled with a 2-4 letter operational-status code in small text right next to the valve body (sometimes above, below, or attached by a leader line).
  Always emit these codes when visible (comma-separated, in this exact spelling): LO (Locked Open), LC (Locked Closed), CSO (Car-Sealed Open), CSC (Car-Sealed Closed), TSO (Tight Shut-Off), FBLO (Full-Bore Locked Open), FBLC (Full-Bore Locked Closed), NRV (Non-Return Valve), NO (Normally Open), NC (Normally Closed), FO (Fail Open), FC (Fail Closed), FL (Fail Last), FI (Fail Indeterminate).
  These codes may ALSO appear inside the valve tag itself (e.g. ``BV-LO-1234``, ``GV-08-FBLC``) — include them either way. If the legend uses the spelled-out phrase (e.g. "Locked Open"), emit the matching short code instead. Only fall back to free text when NONE of these codes apply.
- For "line_number" (a.k.a. P&ID NUMBER): this column is ALSO CRITICAL. Extract the COMPLETE line designation for every valve row. Line numbers follow one of these standard formats — recognise ALL of them, do not skip lines simply because they look unusual:
    * Onshore         2"-D-6152-033842-X-N            (SIZE-FLUID-SEQ-PIPESPEC-DEPT-INSUL)
    * Industrial      2"-2600-FL-352-32070R-E         (SIZE-UNIT-SERVICE-SEQ-PIPECLASS-END)
    * Offshore        604-LFG-3-AC2GA0-2012           (AREA-FLUID-SIZE-PIPECLASS-SEQ)
    * ADNOC compact   6"-CD-AC3N-8256                 (SIZE-FLUID-CLASS-SEQ)
    * General         4"-41-SWR-64313-A2AU16-V        (auto-detect any hyphenated tag with size + alphanumerics)
    * Borouge/Linde   1"-63-UA-149472-A1AU01-V        (SIZE-AREA-SERVICE-SEQ-PIPECLASS-END)
  When the same valve sits on more than one line, emit a separate row per line. Never leave line_number empty if the line designation is visible anywhere on the drawing or in the text excerpt below.
- For "line_list": if the drawing references a line-list document number (e.g. 'PJ6-LL-003', 'LL-001'), emit it verbatim; otherwise leave empty — do not invent.
- Output only valid JSON; do not wrap in markdown fences.
- Extract EVERY valve row visible in the attached pages — do not summarise or skip.
- Use empty strings for unknown text fields and 0 for unknown numeric fields.
- Do not invent valve tags or sizes — leave empty if uncertain.
- Renumber sl_no starting from 1 within this batch (the server merges batches).
- Maximum {max_rows} rows per batch.

Candidate line numbers detected in the PDF text layer (validated against standard formats — every one of these IS a real line; cross-check the drawing and assign each to the correct valve row when applicable):
{candidate_line_numbers}

Embedded text excerpt (use as ground truth where it conflicts with the image):
---
{text_excerpt}
---
"""

# ─── Helpers ────────────────────────────────────────────────────────────
def _page_count(pdf_path: str) -> int:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        n = doc.page_count
        doc.close()
        return n
    except Exception:                                  # pragma: no cover
        return 0


def _extract_text(pdf_path: str, on_text_progress=None) -> str:
    """
    Best-effort text via PyMuPDF (fast). pdfplumber is only consulted as a
    fallback when PyMuPDF returns less than ``TEXT_SUFFICIENT_CHARS`` —
    pdfplumber is *much* slower (often 10-30× on large searchable PDFs)
    and Vision already handles image-only drawings, so the fallback rarely
    pays for itself.

    ``on_text_progress(current_page, total_pages)`` fires per page so the
    job snapshot keeps advancing during this otherwise-silent phase.
    """
    parts: List[str] = []
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total = doc.page_count
        for i, page in enumerate(doc):
            t = page.get_text() or ''
            if t.strip():
                parts.append(t)
            if on_text_progress:
                try:
                    on_text_progress(i + 1, total)
                except Exception:
                    pass
        doc.close()
    except Exception as exc:                           # pragma: no cover
        logger.warning('PyMuPDF failed: %s', exc)

    combined = '\n'.join(parts)
    if len(combined) >= TEXT_SUFFICIENT_CHARS or os.getenv('VALVE_MTO_DISABLE_PDFPLUMBER', '1') == '1':
        return combined

    # Slow fallback only when PyMuPDF clearly under-extracted.
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ''
                if t.strip():
                    parts.append(t)
    except Exception:                                  # pragma: no cover
        pass
    return '\n'.join(parts)


def _render_pages_b64(pdf_path: str, max_pages: int, dpi: int, on_render_progress=None) -> List[str]:
    """
    Render PDF pages to base64 JPEG strings.

    Soft-coded:
      * `max_pages` — hard cap (VISION_MAX_PAGES)
      * `dpi`       — render DPI (VISION_IMAGE_DPI)
      * `VISION_MAX_EDGE_PX` / `JPEG_QUALITY`

    `on_render_progress(current_page, total_pages)` fires after each page so
    the async job runner can keep its heartbeat alive even before any AI
    batch has completed (PDF rendering on a slim CPU can take minutes).
    """
    images: List[str] = []
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(pdf_path)
        total_to_render = min(doc.page_count, max_pages)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            # Cap the longest edge — P&IDs are huge, we don't need 4k pixels
            # to read tag text reliably.
            longest = max(img.size)
            if longest > VISION_MAX_EDGE_PX:
                ratio = VISION_MAX_EDGE_PX / float(longest)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            images.append(base64.b64encode(buf.getvalue()).decode('ascii'))
            if on_render_progress:
                try:
                    on_render_progress(i + 1, total_to_render)
                except Exception:
                    pass
        doc.close()
    except Exception as exc:                           # pragma: no cover
        logger.warning('Failed rendering PDF pages: %s', exc)
    return images


def _coerce_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Force a raw dict into the canonical row schema."""
    row: Dict[str, Any] = {}
    for k in ROW_KEYS:
        v = raw.get(k, '')
        if k in NUMERIC_KEYS:
            try:
                row[k] = int(float(str(v).replace(',', '').strip() or 0))
            except (TypeError, ValueError):
                row[k] = 0
        else:
            row[k] = '' if v is None else str(v).strip()

    # Area normalisation (case-insensitive against VALID_AREAS).
    if row['area']:
        for a in VALID_AREAS:
            if row['area'].lower() == a.lower():
                row['area'] = a
                break

    # Soft-coded Remark derivation. Vision often surfaces operational-status
    # codes (LO, LC, CSO, CSC, TSO, FBLC, FBLO, NRV, NO, NC, FO, FC, FL, FI)
    # or their spelled-out forms ("Locked Open", "Fail Closed", …) inside the
    # valve tag, description, type, line number, or remarks string itself.
    # We scan every text field and MERGE the canonical code list with any
    # pre-existing free-text remarks so legitimate notes aren't destroyed.
    derived = _derive_remarks_from_row(row)
    if derived:
        row['remarks'] = _merge_remarks(row.get('remarks', ''), derived)
    return row


def _coerce_meta(raw: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k in ('doc_no', 'doc_title', 'doc_desc', 'revision', 'doc_date', 'project_name'):
        v = raw.get(k, '')
        out[k] = '' if v is None else str(v).strip()
    return out


# ─── Extractors ─────────────────────────────────────────────────────────
def _extract_meta_from_text(text: str) -> Dict[str, str]:
    """Cheap regex scan for project header fields."""
    meta: Dict[str, str] = {}
    patterns = {
        'doc_no':   [r'(?:Company\s+)?Doc(?:ument)?\.?\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,})'],
        'revision': [r'\bRev(?:ision)?\.?\s*[:\-]?\s*([A-Z0-9]{1,3})\b'],
        'doc_date': [r'\bDate\s*[:\-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})',
                     r'\bDate\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'],
        'doc_title': [r'(PIPING\s+VALVES?\s+MTO)', r'(VALVE\s+M(?:ATERIAL\s+)?T(?:AKE[\s-]?OFF)?)'],
    }
    for field, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                meta[field] = m.group(1).strip()
                break
    return meta


def _extract_via_vision(pdf_path: str, text_excerpt: str) -> Dict[str, Any]:
    """
    Render every page (up to VISION_MAX_PAGES), split into batches of
    VISION_BATCH_SIZE pages each, then call OpenAI in parallel.
    All rows are merged across batches, deduplicated and renumbered.
    """
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — no OPENAI_API_KEY']}

    try:
        from openai import OpenAI
    except Exception:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — openai package unavailable']}

    images = _render_pages_b64(pdf_path, VISION_MAX_PAGES, VISION_IMAGE_DPI)
    if not images:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — no pages rendered']}

    # Harvest line-number candidates from PDF text — passed to Vision as a hint.
    candidate_lines = _harvest_line_numbers(text_excerpt or '')
    candidate_block = '\n'.join(f'  • {ln}' for ln in candidate_lines) if candidate_lines else '  (none detected in text layer — rely on drawing imagery)'

    # Split into batches.
    batches: List[Tuple[int, List[str]]] = []
    for i in range(0, len(images), VISION_BATCH_SIZE):
        batches.append((i, images[i:i + VISION_BATCH_SIZE]))

    client = OpenAI(api_key=api_key, timeout=VISION_TIMEOUT_SECS)
    logger.info(
        '[ValveMTO] Vision → model=%s pages=%d batches=%d (size=%d, parallel=%d) dpi=%d',
        VISION_MODEL, len(images), len(batches), VISION_BATCH_SIZE,
        VISION_PARALLEL_BATCHES, VISION_IMAGE_DPI,
    )

    def _call_one_batch(batch_idx: int, batch_imgs: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]:
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=(text_excerpt or '')[:6000],
            page_range=f'{batch_idx + 1}–{batch_idx + len(batch_imgs)}',
            candidate_line_numbers=candidate_block,
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
            })
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=VISION_TEMPERATURE,
                response_format={'type': 'json_object'},
                messages=[{'role': 'user', 'content': content}],
            )
            raw = resp.choices[0].message.content or '{}'
            data = json.loads(raw)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d failed: %s', batch_idx, exc)
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}']

        rows_raw = data.get('rows') or []
        meta_raw = data.get('project_meta') or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(rows_raw, list):
            for r in rows_raw:
                if not isinstance(r, dict):
                    continue
                row = _coerce_row(r)
                if row['valve_tag'] or row['description'] or row['type']:
                    rows.append(row)
        return rows, _coerce_meta(meta_raw), []

    all_rows: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = {}
    warnings: List[str] = []

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        # Collect results in submission order so row order roughly tracks page order.
        results_by_idx: Dict[int, Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception as exc:                                            # pragma: no cover
                results_by_idx[idx] = ([], {}, [f'batch {idx} crashed: {exc}'])

    for idx in sorted(results_by_idx):
        rows, meta, warns = results_by_idx[idx]
        all_rows.extend(rows)
        for k, v in meta.items():
            if v and not merged_meta.get(k):
                merged_meta[k] = v
        warnings.extend(warns)

    # Deduplicate across batches — same valve appearing on consecutive pages
    # must not be double-counted. Key on the discriminating columns.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for r in all_rows:
        key = (
            (r.get('area') or '').lower(),
            (r.get('valve_tag') or '').lower(),
            (r.get('pms_class') or '').lower(),
            (r.get('size_1') or '').lower(),
            (r.get('rating') or '').lower(),
            (r.get('description') or '').lower(),
            (r.get('type') or '').lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    # Cap and renumber.
    deduped = deduped[:MAX_ROWS]
    for i, r in enumerate(deduped):
        r['sl_no'] = i + 1

    return {'rows': deduped, 'project_meta': merged_meta, 'warnings': warnings}


# ─── Public API ─────────────────────────────────────────────────────────
def extract_valve_mto(pdf_path: str) -> Dict[str, Any]:
    pages = _page_count(pdf_path)
    text  = _extract_text(pdf_path)
    text_meta = _extract_meta_from_text(text)
    warnings: List[str] = []

    use_vision = len(text) < TEXT_SUFFICIENT_CHARS or True  # always on for now — drawings rarely have enough text
    vision_result: Dict[str, Any] = {'rows': [], 'project_meta': {}, 'warnings': []}
    if use_vision:
        vision_result = _extract_via_vision(pdf_path, text)
        warnings.extend(vision_result.get('warnings') or [])

    rows  = vision_result['rows']
    meta  = {**text_meta, **{k: v for k, v in vision_result['project_meta'].items() if v}}

    engine = 'vision' if rows and not text_meta else (
        'text+vision' if rows and text_meta else (
            'text' if text_meta else 'none'
        )
    )

    return {
        'status': 'ok' if rows or meta else 'empty',
        'engine': engine,
        'page_count': pages,
        'rows': rows,
        'project_meta': meta,
        'warnings': warnings,
    }


# ─── Streaming public API (used by the async job runner) ────────────────
def _dedupe_and_renumber(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        key = (
            (r.get('area') or '').lower(),
            (r.get('valve_tag') or '').lower(),
            (r.get('pms_class') or '').lower(),
            (r.get('size_1') or '').lower(),
            (r.get('rating') or '').lower(),
            (r.get('description') or '').lower(),
            (r.get('type') or '').lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out = out[:MAX_ROWS]
    for i, r in enumerate(out):
        r['sl_no'] = i + 1
    return out


def extract_valve_mto_streaming(
    pdf_path: str,
    on_progress=None,
    on_partial=None,
) -> Dict[str, Any]:
    """
    Same logic as `extract_valve_mto` but emits incremental progress/results
    through callbacks so a long-running async job can be polled.

    Callbacks
    ---------
    * `on_progress(current_batch:int, total_batches:int, rows_so_far:int)`
    * `on_partial(rows_so_far:list, project_meta_so_far:dict)`
    """
    pages = _page_count(pdf_path)
    # Emit an immediate progress signal so the UI shows movement right after
    # the worker thread starts, even before any page is processed.
    if on_progress:
        try:
            on_progress(0, max(pages, 1), 0)
        except Exception:
            pass

    # Per-page heartbeat during text extraction (PyMuPDF) — searchable PDFs
    # can be 100+ pages and the user must see progress.
    text  = _extract_text(
        pdf_path,
        on_text_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    text_meta = _extract_meta_from_text(text)

    api_key = os.getenv('OPENAI_API_KEY')
    warnings: List[str] = []

    if not api_key:
        warnings.append('vision skipped — no OPENAI_API_KEY')
        return {
            'status': 'ok' if text_meta else 'empty',
            'engine': 'text' if text_meta else 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    try:
        from openai import OpenAI
    except Exception:
        warnings.append('vision skipped — openai package unavailable')
        return {
            'status': 'ok' if text_meta else 'empty',
            'engine': 'text' if text_meta else 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    # Render with a per-page heartbeat so the frontend's stall timer never
    # trips during the slow PDF→JPEG phase on slim-CPU containers.
    images = _render_pages_b64(
        pdf_path,
        VISION_MAX_PAGES,
        VISION_IMAGE_DPI,
        on_render_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    if not images:
        warnings.append('vision skipped — no pages rendered')
        return {
            'status': 'empty',
            'engine': 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    batches: List[Tuple[int, List[str]]] = []
    for i in range(0, len(images), VISION_BATCH_SIZE):
        batches.append((i, images[i:i + VISION_BATCH_SIZE]))

    total_batches = len(batches)
    if on_progress:
        try:
            on_progress(0, total_batches, 0)
        except Exception:
            pass

    # Harvest line-number candidates from PDF text — passed to Vision as a hint
    # so the model does not miss line designations already present in the text layer.
    candidate_lines  = _harvest_line_numbers(text or '')
    candidate_block  = '\n'.join(f'  • {ln}' for ln in candidate_lines) if candidate_lines else '  (none detected in text layer — rely on drawing imagery)'
    if candidate_lines:
        logger.info('[ValveMTO] Harvested %d candidate line numbers from text layer', len(candidate_lines))

    client = OpenAI(api_key=api_key, timeout=VISION_TIMEOUT_SECS)
    logger.info(
        '[ValveMTO] Streaming vision → model=%s pages=%d batches=%d (size=%d, parallel=%d)',
        VISION_MODEL, len(images), total_batches, VISION_BATCH_SIZE,
        VISION_PARALLEL_BATCHES,
    )

    def _call_one_batch(batch_idx: int, batch_imgs: List[str]):
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=(text or '')[:6000],
            page_range=f'{batch_idx + 1}–{batch_idx + len(batch_imgs)}',
            candidate_line_numbers=candidate_block,
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
            })
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=VISION_TEMPERATURE,
                response_format={'type': 'json_object'},
                messages=[{'role': 'user', 'content': content}],
            )
            raw = resp.choices[0].message.content or '{}'
            data = json.loads(raw)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d failed: %s', batch_idx, exc)
            fatal = _classify_openai_error(exc)
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}'], fatal

        rows_raw = data.get('rows') or []
        meta_raw = data.get('project_meta') or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(rows_raw, list):
            for r in rows_raw:
                if not isinstance(r, dict):
                    continue
                row = _coerce_row(r)
                if row['valve_tag'] or row['description'] or row['type']:
                    rows.append(row)
        return rows, _coerce_meta(meta_raw), [], None

    all_rows: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = dict(text_meta)  # seed with regex-derived meta
    completed = 0
    fatal_msgs: List[str] = []

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                rows, meta, warns, fatal = fut.result()
            except Exception as exc:                                            # pragma: no cover
                rows, meta, warns, fatal = [], {}, [f'batch {idx} crashed: {exc}'], None
            all_rows.extend(rows)
            for k, v in meta.items():
                if v and not merged_meta.get(k):
                    merged_meta[k] = v
            warnings.extend(warns)
            if fatal and fatal not in fatal_msgs:
                fatal_msgs.append(fatal)
            completed += 1

            partial = _dedupe_and_renumber(list(all_rows))
            if on_progress:
                try:
                    on_progress(completed, total_batches, len(partial))
                except Exception:
                    pass
            if on_partial:
                try:
                    on_partial(partial, dict(merged_meta))
                except Exception:
                    pass

    final_rows = _dedupe_and_renumber(all_rows)

    # ── Soft-coded line-number recovery pass ───────────────────────────────
    # Any line number harvested from the PDF text layer that Vision DID NOT
    # surface anywhere in its output rows is appended as a "line-only" row
    # (sl_no, line_number, line_list populated; other fields empty). This
    # guarantees the user sees every detected line on the PMS table — the
    # same completeness guarantee that the dedicated Line List page offers.
    # Disabled when VALVE_MTO_APPEND_MISSING_LINES=0.
    if candidate_lines and os.getenv('VALVE_MTO_APPEND_MISSING_LINES', '1') == '1':
        _norm = lambda s: re.sub(r'\s+', '', str(s or '')).upper()
        seen_lines = {_norm(r.get('line_number')) for r in final_rows if r.get('line_number')}
        missing = [ln for ln in candidate_lines if _norm(ln) not in seen_lines]
        if missing:
            logger.info(
                '[ValveMTO] Appending %d line-only rows for line numbers Vision missed',
                len(missing),
            )
            next_sl = len(final_rows) + 1
            for ln in missing:
                synthetic = _coerce_row({'line_number': ln, 'line_list': ln})
                synthetic['sl_no'] = next_sl
                next_sl += 1
                final_rows.append(synthetic)

    # If every batch failed with a fatal error and we recovered no rows,
    # surface a clean top-level error so the frontend can display it.
    error_msg: Optional[str] = None
    if not final_rows and fatal_msgs:
        error_msg = fatal_msgs[0]
    return {
        'status': 'error' if error_msg else ('ok' if final_rows or merged_meta else 'empty'),
        'engine': 'vision' if final_rows else ('text' if text_meta else 'none'),
        'page_count': pages,
        'rows': final_rows,
        'project_meta': merged_meta,
        'warnings': warnings,
        'error': error_msg,
        'harvested_line_numbers': candidate_lines,
    }
