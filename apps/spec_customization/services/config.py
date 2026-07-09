"""
Spec Customization — Soft-Coded Configuration
==============================================

EVERY knob lives here. Adjust values in this module to retune chunking,
AI engine priorities, regex patterns, cost guard rails, etc — no other file
should ever hold a literal magic number for this feature.
"""
from __future__ import annotations

import os


def _env_int(name: str, default: int, *, lo: int = 1, hi: int | None = None) -> int:
    """Read an integer env-var with safe bounds; falls back to `default` on
    missing / un-parseable values. Allows ops to retune chunking via
    environment without code changes (e.g. `SPEC_CHUNK_SIZE_PAGES=5`)."""
    raw = os.environ.get(name)
    if raw in (None, ''):
        return default
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v < lo:
        return lo
    if hi is not None and v > hi:
        return hi
    return v


# ─────────────────────────────────────────────────────────────────────────────
# Master config dict — exported as `SPEC_EXTRACTION_CONFIG`
# ─────────────────────────────────────────────────────────────────────────────
SPEC_EXTRACTION_CONFIG = {
    # ── Chunking ────────────────────────────────────────────────────────
    # Smaller chunks = more accurate per-page AI extraction & finer progress
    # granularity (better visibility for the user).  Larger chunks = fewer
    # Celery round-trips.  Default 5 pages/chunk; override via env var
    # `SPEC_CHUNK_SIZE_PAGES` (1..50) without redeploying.
    "chunk_size_pages":      _env_int("SPEC_CHUNK_SIZE_PAGES", 5, lo=1, hi=50),
    "max_chunks_parallel":   _env_int("SPEC_MAX_CHUNKS_PARALLEL", 4, lo=1, hi=32),
    "page_overlap":          _env_int("SPEC_PAGE_OVERLAP", 0, lo=0, hi=10),

    # ── AI engine waterfall (first non-failed engine wins per chunk) ────
    # Supported: 'pymupdf_text', 'gemini_vision', 'openai_vision', 'tesseract'
    "ai_engines": [
        "pymupdf_text",        # free, instant; if rich text → may skip AI entirely
        "gemini_vision",       # primary AI
        "openai_vision",       # fallback AI
        "tesseract",           # last resort for fully scanned pages
    ],

    # ── Model identifiers ───────────────────────────────────────────────
    "gemini_model":            "gemini-2.0-flash",
    "openai_model":            "gpt-4o",
    "openai_max_tokens":        8000,
    "openai_temperature":       0.1,
    "gemini_temperature":       0.1,

    # ── Cost guard rails ────────────────────────────────────────────────
    # If a page already has ≥ this many chars from PyMuPDF text-layer,
    # do NOT send it to Vision AI — saves $$ on bulk PDFs.
    # IMPORTANT: only skip AI if the text-layer regex already found classes.
    # A page may be text-rich yet use a non-standard header — AI must still run.
    # Raised from 800 to 3000 so only clearly data-dense text pages skip AI.
    "skip_ai_if_text_chars_gte": 3000,

    # Hard ceiling on how many pages may be sent to a Vision AI per job.
    # Once exceeded, remaining pages are processed by PyMuPDF/Tesseract only.
    "max_ai_pages_per_job":      500,

    # ── PDF rendering ───────────────────────────────────────────────────
    "render_dpi":            150,
    "max_image_size":       3072,
    "jpeg_quality":           85,

    # ── Retry + timeout ─────────────────────────────────────────────────
    "retry_max":             3,
    "retry_backoff_base":    2,        # exponential: 2s, 4s, 8s…
    "chunk_timeout_s":       300,      # per chunk Celery task soft-limit
    "job_total_timeout_s":   60 * 60,  # 60 min absolute job cap

    # ── Extraction quality knobs ────────────────────────────────────────
    "confidence_threshold":  0.35,   # below this, class flagged low-confidence
    "min_components_to_keep":  0,    # keep header-only detections (0 = no filter)

    # ── Regex patterns (soft-coded) ─────────────────────────────────────
    # Broad regex covers common Oil & Gas PMS header formats:
    #   PIPING SPEC: A  |  PIPING SPECIFICATION: A  |  CLASS 150-A
    #   PIPING MATERIAL SPECIFICATION A  |  P.M.S. A  |  PMS: A
    #   PIPING CLASS: A1  |  SPEC CODE: A1A  |  MATERIAL CLASS A
    #   LINE CLASS: A  |  PIPE CLASS A
    "piping_class_header_regex": (
        r'(?:'
        r'PIPING\s+(?:MATERIAL\s+)?SPEC(?:IFICATION)?'  # PIPING [MATERIAL] SPEC[IFICATION]
        r'|P\.?M\.?S\.?'                                 # PMS or P.M.S.
        r'|PIPE\s+CLASS'                                  # PIPE CLASS
        r'|PIPING\s+CLASS'                                # PIPING CLASS
        r'|LINE\s+CLASS'                                  # LINE CLASS
        r'|MATERIAL\s+CLASS'                              # MATERIAL CLASS
        r'|SPEC(?:IFICATION)?\s+CODE'                     # SPEC CODE
        r')'
        r'\s*[:\-]?\s*'
        r'(?P<code>[A-Z][A-Z0-9]{0,10})\b'
    ),
    # Recognises P/T rating tables (column-1 = pressure, col-2 = temperature)
    "pt_table_header_regex": (
        r'(?:PRESSURE.{0,5}TEMPERATURE|P\s*/\s*T\s+RATING|SERVICE\s+LIMITS)'
    ),
    # Service-block keywords
    "service_keywords": [
        "general process", "sweet fuel gas", "sour gas", "lp steam", "mp steam",
        "hp steam", "utility air", "instrument air", "nitrogen", "propane",
        "butane", "light distillate", "condensate", "produced water", "cooling water",
        "boiler feed water", "diesel", "lube oil",
    ],

    # ── Dedupe ──────────────────────────────────────────────────────────
    "dedupe_by_sha256":      True,

    # ── Progress band reserved for chunk loop (0-100) ───────────────────
    # 0-10  → upload + page count
    # 10-90 → chunk processing
    # 90-100 → merge + persist
    "chunk_progress_start":  10,
    "chunk_progress_end":    90,
}


# ─────────────────────────────────────────────────────────────────────────────
# Cache key templates (Redis) — keep aligned with Celery tasks
# ─────────────────────────────────────────────────────────────────────────────
PROGRESS_CACHE_KEY_TPL  = "paper_spec_progress_{job_id}"
PARTIAL_CACHE_KEY_TPL   = "paper_spec_partial_{job_id}"
PROGRESS_CACHE_TIMEOUT  = 3600
