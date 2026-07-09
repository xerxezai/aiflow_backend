"""
Legend Sheet Extractor — AI-Powered
=====================================
Extracts structured data from P&ID legend sheets using a two-stage pipeline:

  Stage 1: PDF/image → text via PyMuPDF (fast, free, deterministic).
  Stage 2: If text is sparse OR caller requests AI, use OpenAI GPT-4-Vision
           to visually read the legend sheet and return structured JSON.

Six standard legend categories are extracted and stored per sheet:
  1. line_representation       — line types, dash patterns, colours
  2. line_numbering_piping     — piping designation format breakdown
  3. line_numbering_pipeline   — pipeline designation format breakdown
  4. abbreviations_process     — process abbreviation lookups
  5. inline_equipment          — in-line equipment / instrument symbols
  6. service_codes             — fluid / service code table
  (plus: piping_specs, insulation_codes, instrument_prefixes, valve_prefixes)

All configuration (thresholds, prompts, section headings) is soft-coded via
module-level constants so the render logic never needs touching.
"""
import base64
import io
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Soft-coded: extraction config ─────────────────────────────────────────────
# Minimum character count for text extraction to be considered "good enough"
# to skip OpenAI Vision.  Scanned/rasterised PDFs return very little text.
MIN_TEXT_CHARS_FOR_TEXT_ONLY = 200

# OpenAI model to use for vision extraction
AI_MODEL = 'gpt-4o'

# Gemini model for primary vision extraction
GEMINI_MODEL = os.getenv('GEMINI_VISION_MODEL', 'gemini-2.0-flash')

# Maximum image dimension (px) to send to AI — resized if larger.
AI_IMAGE_MAX_PX = 4096   # increased from 2048 for better OCR on scanned PDFs

# DPI scale factor for rendering PDF pages to images for AI.
# 3.0 × PyMuPDF base ≈ 216–300 DPI — good balance of quality and render speed.
# 4.0 was tried but caused 4-minute render times for 16-page PDFs without
# meaningful quality improvement (OpenAI tiles with detail='high' anyway).
AI_IMAGE_DPI_SCALE = 3.0

# Max tokens per AI batch call
AI_MAX_TOKENS = 16384    # increased from 4096

# Pages per AI call — 1 page per call gives maximum extraction thoroughness.
GEMINI_BATCH_SIZE = 1    # pages per Gemini call
OPENAI_BATCH_SIZE = 1    # pages per OpenAI call

# Set True to skip the Gemini pass entirely.
# Gemini always returns empty when the API key is invalid / quota exceeded.
# Skipping it removes 16+ wasted API round-trips (~25 seconds) per run.
SKIP_GEMINI_PASS = True

# Maximum concurrent OpenAI requests during parallel extraction.
# 4 workers reduces 16 sequential page calls from ~90 s to ~25 s.
MAX_PARALLEL_OPENAI_CALLS = 4

# ── Soft-coded: section heading patterns ──────────────────────────────────────
# Each key is the canonical section name; values are regex patterns that match
# the heading row in the legend text.  Case-insensitive.
SECTION_HEADING_RE = {
    'line_representation':       r'LINE\s+REPRESENT',
    'line_numbering_piping':     r'LINE\s+NUMB(?:ERING)?\s+FOR\s+PIP',
    'line_numbering_pipeline':   r'LINE\s+NUMB(?:ERING)?\s+FOR\s+PIPELINE',
    'abbreviations_process':     r'ABBREVIAT',
    'inline_equipment':          r'IN[\s\-]?LINE\s+EQUIP',
    'service_codes':             r'SERVICE\s+CODE|FLUID\s+CODE',
    'insulation_codes':          r'INSUL',
    'piping_specs':              r'PIPING\s+SPEC|SPEC\s+CODE',
    'instrument_prefixes':       r'INSTRUMENT\s+TYPE|INSTRUMENT\s+CODE',
    'valve_prefixes':            r'VALVE\s+TYPE|VALVE\s+CODE',
}

# ── Soft-coded: AI system prompt ──────────────────────────────────────────────
_AI_SYSTEM_PROMPT = """
You are an expert engineering drawing reader for oil & gas projects, covering P&ID legend
sheets, piping specifications, AND electrical typical diagrams (motor feeder circuits,
MCC/VSD schematics, DCS interface drawings). Extract ALL content from every page type.
Always respond with valid JSON only — no markdown, no explanations.
""".strip()

# ── Soft-coded: AI user prompt template ───────────────────────────────────────
# P&ID legend-focused prompt — used for the primary extraction pass.
# Keeps the schema narrow so GPT-4o focuses solely on P&ID/piping legend content.
_AI_PID_USER_PROMPT = """
You are reading ONE page from a P&ID legend / symbol library sheet for an oil & gas project.
Your job is to VISUALLY ENUMERATE every drawn symbol on this page.
Return ONLY the JSON object — no markdown, no explanations.

STEP 1 — VISUAL SCAN (do this mentally before producing JSON):
  Look at the page and count exactly how many individual symbol drawings you can see.
  Each distinct graphical icon/drawing on the page is ONE separate entry.
  A page with a grid of 8 columns × 12 rows = 96 entries even if many share the same base code.

STEP 2 — SCHEMA:
{
  "line_representation": [
    { "key": "string", "description": "string", "line_style": "solid|dashed|dotted|chain|other" }
  ],
  "line_numbering_piping": {
    "format": "NPS-FLUID-LINENO-SPEC-INSUL",
    "example": "4\\"-BD-4860-038842-N",
    "fields": [
      { "position": 1, "name": "NPS", "example": "4\\"", "description": "Nominal Pipe Size" }
    ]
  },
  "line_numbering_pipeline": {
    "format": "DIA-FLUID-LINENO-CLASS",
    "example": "",
    "fields": []
  },
  "abbreviations_process": [
    { "abbr": "BD", "full_name": "Blowdown", "category": "fluid|equipment|electrical|general|status" }
  ],
  "service_codes": { "BD": "Blowdown", "PG": "Process Gas" },
  "insulation_codes": { "N": "No Insulation", "H": "Hot Insulation" },
  "piping_specs": { "038842": "Carbon Steel ANSI 150#" },
  "instrument_prefixes": ["FI", "FIC", "PI", "TI"],
  "valve_prefixes": ["HV", "FV", "XV", "PV"],
  "equipment_class_codes": { "V": "Vessel/Drum", "P": "Pump", "E": "Heat Exchanger", "K": "Compressor", "C": "Column", "T": "Tank", "F": "Filter", "H": "Fired Heater" },
  "general_notes": [
    "All flanges shall be raised face unless noted otherwise.",
    "Symbol per drawing reference XYZ-123."
  ],
  "drawing_references": [
    { "ref": "PJ6-EXD-GEN-BQDA-0001", "description": "Legend & Symbols Sheet 1 of 6" },
    { "ref": "ASME B31.3",          "description": "Process Piping code reference" }
  ],
  "tie_in_symbols": [
    { "symbol": "T1", "description": "Tie-in to existing flow line", "type": "tie_in|battery_limit|off_page|continuation|cloud|hold|future" }
  ],
  "pid_symbols": [
    {
      "code": "XV-FC",
      "description": "On/Off Valve — Ball Type, Pneumatic Actuator, Fail Closed",
      "category": "valve|instrument|equipment|piping|actuator|signal|fitting|other",
      "body_type": "ball|gate|globe|butterfly|check|needle|diaphragm|plug|other|",
      "actuator": "pneumatic|electric|hydraulic|solenoid|manual|none|",
      "fail_action": "FC|FO|FL|FI|none|",
      "normal_position": "NC|NO|LC|LO|CSO|CSC|none|"
    }
  ],
  "raw_sections": {
    "LINE REPRESENTATION": ["solid line - process piping"]
  }
}

CRITICAL EXTRACTION RULES:
1. ONLY extract what you can VISUALLY SEE drawn on this specific page.
   DO NOT fill in symbols from your training knowledge — only from what is drawn here.

2. Every INDIVIDUAL DRAWN SYMBOL on the page = ONE separate entry in "pid_symbols".
   If the same valve TYPE appears with 6 different actuator/fail-action combinations,
   that is 6 separate "pid_symbols" entries — each with a unique "description".

3. For "code": use the EXACT alphanumeric tag label printed next to or inside the drawing.
   If no code is shown, construct one: e.g. "GV-NO" for a gate valve normally-open.

4. For "description": make it fully self-describing with all visible attributes:
   Body type + Actuator type + Fail action + Normal position (from the symbol markings).
   Example: "Gate Valve, Manual, Locked Open" or "Globe Valve, Pneumatic, Fail Open".

5. Capture EVERY category of symbol visible on this page:
   VALVES — gate, ball, globe, butterfly, check, needle, diaphragm, plug, relief, safety,
             control (each with ALL actuator and fail-action variants shown)
   INSTRUMENTS — every ISA bubble variant (FI, FIC, FT, FC, FF, FR, FE, FY, FAH, FAL,
                 FAHL, FALL, FAHH, FALL, FQI, FdI, etc.) including discrete/shared/DCS/SIS
                 shown as different hatching or boundary types
   PIPING — reducers (concentric/eccentric), flanges (weld neck/slip-on/blind/spectacle),
             blind flange, spectacle blind, branch connections, unions, expansion joints,
             rupture discs, flame arrestors, silencers, strainers (Y, T, basket type)
   EQUIPMENT — vessels (horizontal/vertical), drums, tanks, pumps (centrifugal/reciprocating/
                gear/screw), compressors, heat exchangers (shell&tube/plate/hairpin),
                fired heaters, air coolers, columns, reactors, mixers, ejectors, eductors,
                demisters, mist eliminators, internals, packing, trays, distributors
   ACTUATORS — standalone actuator symbols if shown separately
   SIGNALS — pneumatic signal line, electrical signal, hydraulic signal, capillary tube,
              bus signal, wireless signal, guided-wave — each as a separate entry
   FITTINGS — if shown in a dedicated section

6. ALSO capture these structured tables if present on this page:
   EQUIPMENT-CLASS CODES → "equipment_class_codes" dict (e.g. V→Vessel, P→Pump, E→Heat Exchanger,
                            K→Compressor, C→Column, T→Tank, F→Filter, H→Fired Heater).
                            Read the equipment item-code legend table directly.
   GENERAL NOTES         → "general_notes" array.  Capture EVERY numbered or bulleted note
                            verbatim (e.g. "1. All flanges shall be raised face …").
   DRAWING / CODE REFS   → "drawing_references" array.  Any cross-sheet drawing number,
                            specification reference, code reference (ASME, API, ISA),
                            or "see drawing #" callout shown in title block / notes.
   TIE-IN / OFF-PAGE     → "tie_in_symbols" array.  Tie-in bubbles, battery-limit symbols,
                            off-page connectors, continuation arrows, hold-clouds, future
                            scope clouds — each as one entry with its visible label.

7. For line representation tables: list EVERY line type shown (each row = one entry).

8. For abbreviation/service code tables: list EVERY row — do not truncate.

9. Do NOT merge similar symbols into one entry to save space.
   500+ entry pages are normal and expected for comprehensive legend sheets.

10. Populate only fields that have visible content on this page.
    Return empty [] or {} for sections with no content.
""".strip()

# Electrical-focused prompt — used as a fallback for pages that returned empty
# from the P&ID prompt (i.e. pages containing motor feeder / MCC / VSD schematics).
_AI_ELEC_USER_PROMPT = """
Analyse this electrical engineering drawing page (motor feeder typical, MCC schematic,
VSD circuit, DCS interface diagram, or control wiring drawing) and extract all visible
information into the JSON structure below. Return ONLY the JSON object, nothing else.

{
  "electrical_abbreviations": {
    "MCC": "Motor Control Centre",
    "VSD": "Variable Speed Drive",
    "BCU": "Booster Control Unit",
    "DCS": "Distributed Control System",
    "SCMS": "example — replace with actual abbreviation from drawing"
  },
  "typical_circuits": [
    {
      "typical_number": "04A",
      "title": "415V Motor Feeder",
      "voltage": "415V",
      "components": ["MCC", "DCS", "motor"],
      "dcs_signals": [
        { "tag_suffix": "RUN", "description": "Motor running status", "signal_type": "digital_in" },
        { "tag_suffix": "TRIP", "description": "Motor trip", "signal_type": "digital_in" }
      ]
    }
  ]
}

Rules:
- Extract EVERY typical circuit, abbreviation label, signal tag, and component visible.
- For each typical diagram number found, create one entry in typical_circuits.
- List all DCS I/O signals visible in the diagram (e.g. RUN, STOP, FAULT, TRIP, SPEED).
- List all abbreviations with their full meanings from any legend or title block.
- Return empty {} or [] for sections absent from this page.
""".strip()

# Keep a backward-compatible alias used by the Gemini helper below
_AI_USER_PROMPT = _AI_PID_USER_PROMPT


# ===========================================================================
# Public API
# ===========================================================================

def extract_legend_sheet(file_path: str, use_ai: bool = True, pages_b64: list = None) -> dict:
    """
    Main entry point.  Returns structured extraction dict for all 6 categories.

    Args:
        file_path:  Absolute path to the legend PDF or image file.
        use_ai:     True = always attempt AI extraction after text pass.
                    False = try text-only; skip AI even if text is sparse.
        pages_b64:  Optional pre-rendered page images (base64 PNG strings).
                    When provided the render step is skipped — allows callers to
                    render the PDF once and pass the same images to multiple
                    extractors, cutting total render time by ~50 %.

    Returns:
        Structured dict with keys: line_representation, line_numbering_piping,
        line_numbering_pipeline, abbreviations_process, service_codes,
        insulation_codes, piping_specs, instrument_prefixes, valve_prefixes,
        pid_symbols, raw_sections, extraction_method.
    """
    fp = Path(file_path)
    suffix = fp.suffix.lower()

    # ── Stage 1: text extraction ───────────────────────────────────────────
    raw_text = ''
    if suffix == '.pdf':
        raw_text = _extract_pdf_text(file_path)
    elif suffix in {'.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp'}:
        raw_text = ''  # image file — no text extraction
    else:
        logger.warning('[LegendExtractor] Unknown file type %s — skipping text pass', suffix)

    # ── Stage 2: decide whether AI is needed ──────────────────────────────
    if use_ai and (len(raw_text.strip()) < MIN_TEXT_CHARS_FOR_TEXT_ONLY or suffix != '.pdf'):
        logger.info('[LegendExtractor] Sparse text (%d chars) — using AI Vision', len(raw_text))
        # Use pre-rendered pages if provided; otherwise render now
        rendered = pages_b64 if pages_b64 is not None else _render_pages_to_b64(file_path)
        if rendered:
            ai_result = _extract_via_ai(rendered)
            if ai_result:
                ai_result['extraction_method'] = 'ai_vision'
                ai_result['raw_text_chars'] = len(raw_text)
                ai_result['file_name'] = fp.name
                return ai_result
            logger.warning('[LegendExtractor] AI extraction returned nothing; falling back to text')
        else:
            logger.warning('[LegendExtractor] Could not render pages for AI')

    # ── Stage 3: text-based parsing (fallback / when text is sufficient) ──
    result = _parse_text(raw_text)
    result['extraction_method'] = 'text_parse'
    result['raw_text_chars'] = len(raw_text)
    result['file_name'] = fp.name
    return result


# ===========================================================================
# Stage 1 — PDF text extraction
# ===========================================================================

def _extract_pdf_text(file_path: str) -> str:
    """Extract plain text from all pages of a PDF via PyMuPDF."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        chunks = []
        for page in doc:
            chunks.append(page.get_text('text'))
        doc.close()
        return '\n'.join(chunks)
    except Exception as exc:
        logger.warning('[LegendExtractor] PDF text extraction failed: %s', exc)
        return ''


# ===========================================================================
# Stage 2 — AI Vision extraction
# ===========================================================================

def _render_pages_to_b64(file_path: str) -> list[str]:
    """
    Render each page of the file to a base64 PNG string for OpenAI Vision.
    Returns a list of base64 strings (one per page, capped at 4 pages).
    """
    pages = []
    fp = Path(file_path)
    suffix = fp.suffix.lower()

    try:
        if suffix == '.pdf':
            import fitz
            doc = fitz.open(file_path)
            # Process ALL pages — legend sheets for scanned P&IDs can be 16+ pages
            for i in range(len(doc)):
                page = doc[i]
                # Render using soft-coded DPI scale for clear small-text extraction
                mat = fitz.Matrix(AI_IMAGE_DPI_SCALE, AI_IMAGE_DPI_SCALE)
                pix = page.get_pixmap(matrix=mat)
                png_bytes = pix.tobytes('png')
                # Resize if too large for OpenAI (max AI_IMAGE_MAX_PX)
                png_bytes = _resize_if_needed(png_bytes)
                pages.append(base64.b64encode(png_bytes).decode('utf-8'))
            doc.close()
        elif suffix in {'.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp'}:
            with open(file_path, 'rb') as fh:
                raw = fh.read()
            raw = _resize_if_needed(raw)
            pages.append(base64.b64encode(raw).decode('utf-8'))
    except Exception as exc:
        logger.error('[LegendExtractor] Page render failed: %s', exc)

    return pages


def _resize_if_needed(image_bytes: bytes) -> bytes:
    """Down-scale image to AI_IMAGE_MAX_PX on the longest side if larger."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        max_dim = max(w, h)
        if max_dim > AI_IMAGE_MAX_PX:
            scale = AI_IMAGE_MAX_PX / max_dim
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
    except Exception as exc:
        logger.debug('[LegendExtractor] Resize skipped: %s', exc)
    return image_bytes


# ── Soft-coded: list dedup keys per category ─────────────────────────────────
# Maps list-of-dict fields to the key used for deduplication.
#
# pid_symbols is deduped by 'description' (NOT 'code') so that valve variants
# with the same base code but different actuator/fail-action/body combinations
# are kept as separate entries.  For example:
#   { code: 'XV', description: 'On/Off Valve, Ball, Pneumatic, FC' }
#   { code: 'XV', description: 'On/Off Valve, Ball, Pneumatic, FO' }
#   { code: 'XV', description: 'On/Off Valve, Ball, Electric, FC' }
# → 3 distinct entries preserved (all share code 'XV' but differ in description).
_LIST_DEDUP_KEY = {
    'line_representation':   'key',
    'abbreviations_process': 'abbr',
    # pid_symbols: each visual variant is a separate entry; dedup by description
    'pid_symbols':           'description',
    # New soft-coded list fields (added without touching merge core logic):
    'drawing_references':    'ref',          # cross-sheet drawing / code refs
    'tie_in_symbols':        'symbol',       # tie-in bubbles, off-page connectors
}
# String-list fields deduplicated by value:
_STRING_LIST_FIELDS = {'instrument_prefixes', 'valve_prefixes', 'general_notes'}
# Dict-merge fields (later batches override earlier for missing keys):
_DICT_MERGE_FIELDS = {'service_codes', 'insulation_codes', 'piping_specs', 'equipment_class_codes'}
# Nested-object fields (keep the most complete one):
_NESTED_OBJ_FIELDS = {'line_numbering_piping', 'line_numbering_pipeline'}


def _has_legend_content(result: dict) -> bool:
    """Return True if the result has at least one non-empty data field."""
    if not result:
        return False
    check_keys = list(_LIST_DEDUP_KEY) + list(_STRING_LIST_FIELDS) + list(_DICT_MERGE_FIELDS) + list(_NESTED_OBJ_FIELDS)
    # Also consider electrical and pid_symbols fields
    for k in check_keys + ['electrical_abbreviations', 'typical_circuits', 'pid_symbols']:
        v = result.get(k)
        if isinstance(v, list) and v:
            return True
        if isinstance(v, dict) and v:
            return True
    return False


def _merge_legend_results(results: list) -> Optional[dict]:
    """Merge paginated extraction results into a single structured dict."""
    merged = {}
    raw_sections_merged: dict = {}

    for res in results:
        if not res:
            continue
        # List-of-dict fields — deduplicate by identifying key
        for field, id_key in _LIST_DEDUP_KEY.items():
            existing = {item[id_key]: item for item in merged.get(field, []) if isinstance(item, dict) and id_key in item}
            for item in res.get(field, []):
                if isinstance(item, dict) and id_key in item and item[id_key] not in existing:
                    existing[item[id_key]] = item
            if existing:
                merged[field] = list(existing.values())
        # String-list fields — deduplicate by value
        for field in _STRING_LIST_FIELDS:
            seen = set(merged.get(field, []))
            for val in res.get(field, []):
                seen.add(val)
            if seen:
                merged[field] = sorted(seen)
        # Dict-merge fields
        for field in _DICT_MERGE_FIELDS:
            base = dict(merged.get(field, {}))
            base.update(res.get(field, {}))
            if base:
                merged[field] = base
        # Electrical abbreviations — dict merge (code → meaning)
        elec_abbr = dict(merged.get('electrical_abbreviations', {}))
        elec_abbr.update(res.get('electrical_abbreviations', {}))
        if elec_abbr:
            merged['electrical_abbreviations'] = elec_abbr
        # Typical circuits — deduplicate by typical_number, keep most complete entry
        existing_typicals = {t.get('typical_number', ''): t for t in merged.get('typical_circuits', []) if isinstance(t, dict)}
        for t in res.get('typical_circuits', []):
            if not isinstance(t, dict):
                continue
            num = t.get('typical_number', '')
            if num not in existing_typicals or len(t.get('components', [])) > len(existing_typicals.get(num, {}).get('components', [])):
                existing_typicals[num] = t
        if existing_typicals:
            merged['typical_circuits'] = list(existing_typicals.values())
        # Nested-object fields — keep the one with the most 'fields' entries
        for field in _NESTED_OBJ_FIELDS:
            cur = merged.get(field, {})
            new = res.get(field, {})
            if len(new.get('fields', [])) > len(cur.get('fields', [])):
                merged[field] = new
            elif field not in merged and new:
                merged[field] = new
        # raw_sections — merge dicts
        raw_sections_merged.update(res.get('raw_sections', {}))

    if raw_sections_merged:
        merged['raw_sections'] = raw_sections_merged
    return merged if merged else None


def _safe_legend_json(raw: str) -> Optional[dict]:
    """Strip markdown fences and parse JSON. Returns None on failure."""
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _extract_batch_gemini_legend(images: list[str]) -> Optional[dict]:
    """Send a batch of base64-encoded pages to Gemini and return parsed dict."""
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL)

        import PIL.Image
        parts = [_AI_USER_PROMPT]
        for b64 in images:
            img_bytes = base64.b64decode(b64)
            parts.append(PIL.Image.open(io.BytesIO(img_bytes)))

        response = model.generate_content(
            parts,
            generation_config={'max_output_tokens': AI_MAX_TOKENS, 'temperature': 0},
        )
        raw = getattr(response, 'text', '') or ''
        logger.info('[LegendExtractor][Gemini] batch reply %d chars', len(raw))
        return _safe_legend_json(raw)
    except Exception as exc:
        logger.warning('[LegendExtractor][Gemini] batch failed: %s', exc)
        return None


def _extract_batch_openai_legend(images: list[str], prompt: str = None) -> Optional[dict]:
    """Send a batch of base64-encoded pages to OpenAI and return parsed dict.

    Args:
        images: Base64-encoded page images.
        prompt: User prompt to use. Defaults to _AI_PID_USER_PROMPT.
    """
    if prompt is None:
        prompt = _AI_PID_USER_PROMPT
    try:
        from django.conf import settings as _settings
        import openai
        api_key = getattr(_settings, 'OPENAI_API_KEY', None) or os.environ.get('OPENAI_API_KEY')
        if not api_key:
            logger.warning('[LegendExtractor] OPENAI_API_KEY not set')
            return None

        client = openai.OpenAI(api_key=api_key)
        content = [{'type': 'text', 'text': prompt}]
        for b64 in images:
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/png;base64,{b64}', 'detail': 'high'},
            })

        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {'role': 'system', 'content': _AI_SYSTEM_PROMPT},
                {'role': 'user',   'content': content},
            ],
            max_tokens=AI_MAX_TOKENS,
            temperature=0,
        )
        raw = response.choices[0].message.content or ''
        parsed = _safe_legend_json(raw)
        item_count = 0
        if parsed:
            item_count = sum(
                len(parsed.get(k, [])) for k in ('line_representation', 'abbreviations_process', 'instrument_prefixes', 'valve_prefixes', 'pid_symbols')
            ) + sum(len(v) for k, v in parsed.items() if isinstance(v, dict))
        logger.info('[LegendExtractor][OpenAI] batch reply %d chars → %d items', len(raw), item_count)
        return parsed
    except Exception as exc:
        logger.warning('[LegendExtractor][OpenAI] batch failed: %s', exc)
        return None


def _extract_via_ai(pages_b64: list[str]) -> Optional[dict]:
    """
    Parallel three-pass extraction.

    Pass 1: Gemini (skipped when SKIP_GEMINI_PASS=True — Gemini always returns empty
            when the API key is invalid/quota exceeded, wasting ~25 s per run).
    Pass 2: OpenAI P&ID prompt — all empty pages processed concurrently with
            MAX_PARALLEL_OPENAI_CALLS workers, reducing 16 sequential calls
            from ~90 s to ~25 s.
    Pass 3: OpenAI electrical prompt — pages still empty after P&ID pass, also parallel.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = len(pages_b64)
    logger.info('[LegendExtractor] Paginated extraction: %d pages (parallel=%d, skip_gemini=%s)',
                n, MAX_PARALLEL_OPENAI_CALLS, SKIP_GEMINI_PASS)

    # One result slot per page (with BATCH_SIZE=1, batch_idx == page_idx)
    page_results: list = [None] * n

    # ── Pass 1 — Gemini (optional) ──────────────────────────────────────────
    if not SKIP_GEMINI_PASS:
        for idx in range(n):
            result = _extract_batch_gemini_legend([pages_b64[idx]])
            if _has_legend_content(result):
                page_results[idx] = result
                logger.info('[LegendExtractor][Gemini] page %d/%d → content', idx + 1, n)
            else:
                logger.info('[LegendExtractor][Gemini] page %d/%d → empty', idx + 1, n)
    else:
        logger.info('[LegendExtractor] Gemini pass skipped (SKIP_GEMINI_PASS=True)')

    # ── Pass 2 — OpenAI P&ID prompt (parallel) ──────────────────────────────
    empty_pages = [i for i, r in enumerate(page_results) if r is None]
    if empty_pages:
        logger.info('[LegendExtractor][OpenAI] Processing %d pages in parallel', len(empty_pages))
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_OPENAI_CALLS) as pool:
            future_map = {
                pool.submit(_extract_batch_openai_legend, [pages_b64[i]]): i
                for i in empty_pages
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    result = future.result()
                    if _has_legend_content(result):
                        page_results[idx] = result
                        logger.info('[LegendExtractor][OpenAI] page %d/%d → content', idx + 1, n)
                    else:
                        logger.info('[LegendExtractor][OpenAI] page %d/%d → empty', idx + 1, n)
                except Exception as exc:
                    logger.warning('[LegendExtractor][OpenAI] page %d/%d failed: %s', idx + 1, n, exc)

    # ── Pass 3 — OpenAI electrical prompt (parallel) for still-empty pages ──
    still_empty = [i for i, r in enumerate(page_results) if r is None]
    if still_empty:
        logger.info('[LegendExtractor][OpenAI-Elec] Processing %d pages still empty', len(still_empty))
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_OPENAI_CALLS) as pool:
            future_map = {
                pool.submit(_extract_batch_openai_legend, [pages_b64[i]], _AI_ELEC_USER_PROMPT): i
                for i in still_empty
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    result = future.result()
                    if _has_legend_content(result):
                        page_results[idx] = result
                        logger.info('[LegendExtractor][OpenAI-Elec] page %d/%d → content', idx + 1, n)
                    else:
                        logger.info('[LegendExtractor][OpenAI-Elec] page %d/%d → empty', idx + 1, n)
                except Exception as exc:
                    logger.warning('[LegendExtractor][OpenAI-Elec] page %d/%d failed: %s', idx + 1, n, exc)

    all_results = [r for r in page_results if r]
    if not all_results:
        logger.warning('[LegendExtractor] All pages returned empty')
        return None

    merged = _merge_legend_results(all_results)
    total = sum(
        len(merged.get(k, [])) for k in list(_LIST_DEDUP_KEY) + list(_STRING_LIST_FIELDS)
    ) + sum(len(v) for k, v in merged.items() if isinstance(v, dict))
    logger.info('[LegendExtractor] Paginated extraction complete: %d total items across %d pages (pid_symbols=%d)',
                total, n, len(merged.get('pid_symbols', [])))
    return merged


# ===========================================================================
# Stage 3 — Text-based parsing (fallback)
# ===========================================================================

def _parse_text(raw_text: str) -> dict:
    """
    Parse legend text into the canonical 6-section structure.
    Mirrors the existing parse_legend_knowledge() but returns the full
    extended schema expected by PIDVLegendSheet.extracted_data.
    """
    # Import shared logic from the existing service to avoid duplication
    from .legend_knowledge import parse_legend_knowledge
    base = parse_legend_knowledge(raw_text)

    # Build raw_sections by splitting on heading patterns
    raw_sections: dict[str, list[str]] = {}
    current_key = 'other'
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    for line in lines:
        for section_key, pattern in SECTION_HEADING_RE.items():
            if re.search(pattern, line, re.IGNORECASE):
                current_key = section_key
                break
        else:
            raw_sections.setdefault(current_key, []).append(line)

    return {
        'line_representation':       _parse_line_representation(raw_sections.get('line_representation', [])),
        'line_numbering_piping':     _parse_line_numbering_format(raw_sections.get('line_numbering_piping', []), 'piping'),
        'line_numbering_pipeline':   _parse_line_numbering_format(raw_sections.get('line_numbering_pipeline', []), 'pipeline'),
        'abbreviations_process':     _parse_abbreviations(raw_sections.get('abbreviations_process', [])),
        'service_codes':             base.get('service_codes', {}),
        'insulation_codes':          base.get('insulation_codes', {}),
        'piping_specs':              base.get('piping_specs', {}),
        'instrument_prefixes':       base.get('instrument_prefixes', []),
        'valve_prefixes':            base.get('valve_prefixes', []),
        'raw_sections':              {k: v for k, v in raw_sections.items() if v},
    }


# ── Soft-coded: text parsers for each section ─────────────────────────────────
# Add new parsers here — no changes to extract_legend_sheet() needed.

def _parse_line_representation(rows: list[str]) -> list[dict]:
    """Parse 'LINE REPRESENTATION' section rows."""
    result = []
    # Soft-coded: patterns that signal the line style
    STYLE_HINTS = {
        'dashed':  r'dash|hidden|exist|future|underground',
        'dotted':  r'dot|instrument|utility',
        'chain':   r'chain|centre|centerline|boundary',
        'solid':   r'solid|process|main|continuous',
    }
    for row in rows:
        if len(row) < 4:
            continue
        line_style = 'other'
        for style, pattern in STYLE_HINTS.items():
            if re.search(pattern, row, re.IGNORECASE):
                line_style = style
                break
        # Try to split "symbol description" or "key - description"
        m = re.match(r'^([A-Z0-9\-/\s]{1,20})[-:–]\s*(.+)$', row)
        if m:
            result.append({'key': m.group(1).strip(), 'description': m.group(2).strip(), 'line_style': line_style})
        else:
            result.append({'key': '', 'description': row, 'line_style': line_style})
    return result


def _parse_line_numbering_format(rows: list[str], kind: str) -> dict:
    """Parse 'LINE NUMBERING FOR PIPING/PIPELINE' section."""
    # Soft-coded: regex for common format strings like 4"-BD-4860-038842-N
    FORMAT_RE = re.compile(
        r'(?P<nps>\d+(?:\.\d+)?"|DN\d+)\s*[-/]\s*(?P<fluid>[A-Z]{1,4})\s*[-/]\s*(?P<lineno>\d{3,6})',
        re.IGNORECASE,
    )
    # Soft-coded: field name hints ordered by typical position
    FIELD_HINTS = [
        ('NPS',          r'NPS|nominal|pipe\s*size|diameter|DN'),
        ('Fluid/Service',r'fluid|service|medium|content'),
        ('Line No',      r'line\s*no|sequential|number'),
        ('Piping Spec',  r'spec|material|class'),
        ('Insulation',   r'insul|trace|lagging'),
        ('Area/Plant',   r'area|plant|unit|section'),
    ]
    format_str = ''
    example    = ''
    fields     = []

    for row in rows:
        m = FORMAT_RE.search(row)
        if m and not format_str:
            example = m.group(0)

        # Try to detect field descriptions: "NPS - Nominal Pipe Size"
        parts = re.split(r'[-:–]', row, maxsplit=1)
        if len(parts) == 2:
            code, desc = parts[0].strip(), parts[1].strip()
            for pos, (name, pattern) in enumerate(FIELD_HINTS, start=1):
                if re.search(pattern, desc, re.IGNORECASE) or re.search(pattern, code, re.IGNORECASE):
                    fields.append({'position': pos, 'name': name, 'example': code, 'description': desc})
                    break

    # Derive format string from detected fields
    if fields:
        format_str = '-'.join(f['name'] for f in sorted(fields, key=lambda x: x['position']))
    elif kind == 'piping':
        format_str = 'NPS-FLUID-LINENO-SPEC-INSUL'
    else:
        format_str = 'DIA-FLUID-LINENO-CLASS'

    return {'format': format_str, 'example': example, 'fields': fields}


def _parse_abbreviations(rows: list[str]) -> list[dict]:
    """Parse abbreviation table rows: 'BD - Blowdown', 'FT: Flow Transmitter'."""
    result = []
    # Soft-coded: category classifier keywords
    ABBR_CATS = {
        'fluid':     r'drain|condensate|flare|gas|liquid|oil|water|steam|coolant|vent|blowdown|relief|slop|chemical',
        'equipment': r'vessel|pump|compress|exchanger|cooler|heater|filter|separator|tank',
        'status':    r'hold|tbc|tbd|tbe|open|closed|future|exist|spare|standby',
    }
    for row in rows:
        m = re.match(r'^([A-Z][A-Z0-9/\-]{0,7})\s*[-:–]\s*(.+)$', row)
        if not m:
            continue
        abbr     = m.group(1).strip()
        full_name= m.group(2).strip()
        category = 'general'
        for cat, pattern in ABBR_CATS.items():
            if re.search(pattern, full_name, re.IGNORECASE):
                category = cat
                break
        result.append({'abbr': abbr, 'full_name': full_name, 'category': category})
    return result


# ===========================================================================
# Merge helper — apply extracted data back into a project's legend_knowledge
# ===========================================================================

def merge_into_project_legend(project, extracted: dict) -> dict:
    """
    Merge `extracted` data from a new legend sheet into the project's
    existing `legend_knowledge_data`.  Returns the updated knowledge dict.

    Safe to call multiple times (idempotent for same extractions).
    """
    existing = project.legend_knowledge_data or {}

    def _merge_list_unique(key: str, id_field: str) -> list:
        """Merge two lists, deduplicating by id_field."""
        old = {item.get(id_field): item for item in existing.get(key, []) if item.get(id_field)}
        for item in extracted.get(key, []):
            k = item.get(id_field)
            if k:
                old[k] = item  # overwrite with newer
        return list(old.values())

    def _merge_dict(key: str) -> dict:
        merged = dict(existing.get(key, {}))
        merged.update(extracted.get(key, {}))
        return merged

    def _merge_str_set(key: str) -> list:
        combined = set(existing.get(key, []))
        combined.update(extracted.get(key, []))
        return sorted(combined)

    updated = {
        **existing,
        'line_representation':     _merge_list_unique('line_representation', 'key'),
        'line_numbering_piping':   extracted.get('line_numbering_piping')   or existing.get('line_numbering_piping',  {}),
        'line_numbering_pipeline': extracted.get('line_numbering_pipeline') or existing.get('line_numbering_pipeline', {}),
        'abbreviations_process':   _merge_list_unique('abbreviations_process', 'abbr'),
        'service_codes':           _merge_dict('service_codes'),
        'insulation_codes':        _merge_dict('insulation_codes'),
        'piping_specs':            _merge_dict('piping_specs'),
        'instrument_prefixes':     _merge_str_set('instrument_prefixes'),
        'valve_prefixes':          _merge_str_set('valve_prefixes'),
        'note_keywords':           _merge_str_set('note_keywords'),
        'hold_keywords':           _merge_str_set('hold_keywords'),
    }
    return updated
