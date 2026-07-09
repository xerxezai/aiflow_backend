"""
Master Index Service
--------------------

Column-class dispatcher for the NONTEF Master Index workflow.

This module WRAPS `extractor.py` without touching its regex patterns or
format-specific readers. All behaviour is driven by two JSON config files:

    config/master_index_template.json  - column schema + classes + rules
    config/document_taxonomy.json      - type/sub-type/discipline lookup

Column classes
--------------
* auto_serial    - system-assigned 1-based row index
* file_derived   - read from the file object (name, path, ext, page count)
* batch_default  - taken directly from the batch's ``batch_defaults`` dict
* ai_extract     - regex / taxonomy / keyword extraction from file text
* derived        - computed from another column via a named rule

The dispatcher returns a plain ``dict`` keyed by column ``key`` — the exact
shape stored in ``NonTeffBatchItem.fields`` and consumed by the exporter.
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .extractor import (
    DATE_PATTERN,
    DOCUMENT_NO_PATTERN,
    EQUIPMENT_NO_PATTERN,
    REVISION_PATTERN,
    _first_match,
    _all_matches,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SOFT-CODED paths & constants
# ---------------------------------------------------------------------------

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
TEMPLATE_PATH = os.path.join(_CONFIG_DIR, 'master_index_template.json')
TAXONOMY_PATH = os.path.join(_CONFIG_DIR, 'document_taxonomy.json')
PATTERNS_PATH = os.path.join(_CONFIG_DIR, 'extraction_patterns.json')

# File format → internal format key (kept separate from views.py on purpose
# so the service stays self-contained and testable).
_FORMAT_BY_EXT = {
    '.pdf':  'pdf',
    '.xlsx': 'excel', '.xls': 'excel',
    '.docx': 'word',  '.doc': 'word',
    '.dwg':  'autocad', '.dxf': 'autocad',
}

# Default cap for text snippets scanned by AI extractors (keeps things fast).
_MAX_SCAN_CHARS = 20_000

# Master Index "File name" column policy — the reference sample
# (Sample Metadata Extraction.xlsx) keeps the extension intact so values look
# like "NM-57-1383-002.pdf", "36-0020-001.pdf", "27-00-20-102.pdf" rather than
# the bare stem. Toggle to False if a future template requires stem-only.
FILE_NAME_INCLUDE_EXTENSION = True

# Master Index "Document Number" column policy — same reference sample shows
# values mirroring the file name *without* the extension
# (e.g. "NM-57-1383-002", "36-0020-001", "27-00-20-102"). Keeping this as the
# single source of truth avoids the drift seen when document number is pulled
# from PDF body text (e.g. "5610Y-STC-01-1381-026") instead of the filename.
# Set to False to allow extractor/vision values to win.
DOCUMENT_NUMBER_FROM_FILENAME = True
DOCUMENT_NUMBER_FIELD_KEY = 'document_number'

# Master Index "Document Type" column policy — values must be one of the
# canonical taxonomy keys defined in document_taxonomy.json (e.g. "Piping",
# "Process", "Civil", "Electrical"). The reference sample shows that ad-hoc
# strings like "VARIABLE SPRING SUPPORTS DATA SHEET" (which is actually the
# document title) must NOT appear here. The sanitiser below snaps any value
# back into the controlled vocabulary, with smart fallback via
# `document_subtype` membership lookup.
DOCUMENT_TYPE_STRICT_TAXONOMY = True
DOCUMENT_TYPE_FIELD_KEY = 'document_type'
DOCUMENT_SUBTYPE_FIELD_KEY = 'document_subtype'

# Master Index "Document Title" column policy.
# The reference sample shows the title is the document's *descriptive* phrase
# pulled from the title block, e.g.:
#   "VARIABLE SPRING SUPPORTS DATA SHEET UNIT-57"
#   "PROCESS FLOW DIAGRAM UNIT 47 & UNIT 48 DEHYDRATION AND DEW POINTING UNIT"
#   "GUIDE-GUIDE GG"
# A naive "first clean line" extractor often picks up company names, dates,
# or drawing numbers instead. The smart extractor below scans the document
# for lines matching subtype-indicator phrases (data sheet, flow diagram,
# support, datasheet, single line diagram, …) auto-derived from the
# taxonomy, then merges adjacent uppercase continuation lines.
DOCUMENT_TITLE_FIELD_KEY = 'document_title'
DOCUMENT_TITLE_SMART_EXTRACT = True

# Master Index "Document issue Date" / "Revision" column policy. The smart
# extractors below understand label-proximity ("DATE", "REV") in real
# title-block layouts and tolerate the wide variety of date shapes seen in
# the reference sample (M/D/YYYY, D-M-YY, ISO, text-month, …).
ISSUE_DATE_FIELD_KEY = 'issue_date'
REVISION_FIELD_KEY = 'revision'

# Master Index "Revision Description/Status", "Area", "Unit",
# "ADNOC Project No." column policy. Soft-coded keyword libraries plus
# title-block / revision-history scanners make these dynamic — adding a new
# canonical phrase or area mapping is a single-line config change.
REVISION_STATUS_FIELD_KEY = 'revision_status'
AREA_FIELD_KEY = 'area'
UNIT_FIELD_KEY = 'unit'
ADNOC_PROJECT_NO_FIELD_KEY = 'adnoc_project_no'
PROJECT_TITLE_FIELD_KEY = 'project_title'

# Master Index document-control reference columns. Soft-coded label
# vocabularies + token-shape validators below — adding a new label is a
# single-line config change.
CONTRACTOR_REF_FIELD_KEY = 'contractor_ref'
VENDOR_REF_FIELD_KEY     = 'vendor_ref'
ORIGINATOR_FIELD_KEY     = 'originator'
AGREEMENT_NO_FIELD_KEY   = 'agreement_no'
AGREEMENT_DESC_FIELD_KEY = 'agreement_desc'

# Tag column field key. The smart extractor below scans body text with a
# soft-coded tuple of tag-shape regexes covering every form seen in the
# reference sample (compact alpha-num modules, hyphen-segmented equipment
# tags, XV valve numbers, material-spec codes, numeric pairs, …).
TAG_FIELD_KEY = 'tag'

# Plant column field key. Soft-coded canonical map (HABSHAN-II family,
# BAB, ASAB, RUWAIS, DAS, DALMA, GASCO etc.) — adding a new plant is
# a single-line config change. Plant is mostly project-derived; the
# smart extractor combines body text + filename + path + project hint.
PLANT_FIELD_KEY = 'plant'

# Purchase Order column field key. PO numbers are typically NA in the
# reference sample; the validator below ensures only labelled, properly
# shaped values are kept (everything else is cleared to NA).
PO_NO_FIELD_KEY = 'po_no'

# Soft-coded extra title indicator phrases that are not subtype names but
# routinely appear in real-world titles. Order matters only for readability —
# all phrases are tried as case-insensitive whole-word regexes.
_TITLE_INDICATOR_PHRASES: Tuple[str, ...] = (
    'data sheet', 'datasheet',
    'flow diagram', 'process flow diagram', 'utility flow diagram',
    'single line diagram', 'single-line diagram',
    'p&id', 'piping and instrumentation',
    'line list', 'equipment list', 'tie-in list', 'tie in list',
    'cause and effect', 'cause & effect',
    'plot plan', 'general arrangement', 'isometric',
    'pipe support', 'special pipe support', 'spring support',
    'sliding plate', 'guide-guide', 'rods-trapeze', 'vessel-guide',
    'vessel-support', 'vessel-insulated',
    'specification', 'philosophy', 'narrative',
    'one line diagram',
)

# Hard-rejection patterns for title candidates — these phrases or shapes
# routinely appear in title blocks but are NEVER the document's title.
_TITLE_REJECT_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\bpage\s+\d+\s+of\s+\d+\b', re.IGNORECASE),
    re.compile(r'\bsheet\s+\d+\s+of\s+\d+\b', re.IGNORECASE),
    re.compile(r'\brev(?:ision)?\s*[:\-]?\s*[a-z0-9]+\b', re.IGNORECASE),
    re.compile(r'\bdoc(?:ument)?\s*(?:no|number|#)\b', re.IGNORECASE),
    re.compile(r'\bdrawing\s*(?:no|number|#)\b', re.IGNORECASE),
    re.compile(r'\bdwg\s*(?:no|number|#|\.|:)?\b', re.IGNORECASE),
    re.compile(r'\bproject\s*(?:no|number|#)\b', re.IGNORECASE),
    re.compile(r'\bjob\s*(?:no|number|#)\b', re.IGNORECASE),
    re.compile(r'\bcontract\s*(?:no|number|#)\b', re.IGNORECASE),
    re.compile(r'\bclient\s*[:\-]', re.IGNORECASE),
    # Pure date lines.
    re.compile(r'^\s*\d{1,2}[\-/\.]\d{1,2}[\-/\.]\d{2,4}\s*$'),
)

# Soft-coded label prefixes that title-blocks place in front of the actual
# title text. The smart extractor strips any of these (with optional ':'/'-'
# / whitespace) from the *start* of the chosen candidate so the reference
# values come out clean (e.g. "TITLE: VARIABLE SPRING SUPPORTS DATA SHEET"
# → "VARIABLE SPRING SUPPORTS DATA SHEET").
_TITLE_LABEL_PREFIXES: Tuple[str, ...] = (
    'document title', 'drawing title', 'dwg title',
    'document description', 'description',
    'title of document', 'title',
    'subject',
)
_TITLE_LABEL_PREFIX_RE = re.compile(
    r'^\s*(?:' + '|'.join(re.escape(p) for p in _TITLE_LABEL_PREFIXES) + r')'
    r'\s*[:\-\u2013\u2014]?\s*',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# OCR-noise sanitiser for title candidates.
#
# Symptom (from production scans of pipe-support / amine drawings):
#   "o; AMINE RECOVERY AMINE SEPARATION SLOP OI"
#   "x | 5 oe TYPICAL PIPE SUPPORT MARK NO. 5 in EL"
# i.e. a couple of stray short tokens / punctuation glyphs in front of an
# otherwise clean upper-case title.  We strip leading tokens that don't
# look like real title words (UPPER‑case word ≥3 chars, ampersand-joined
# acronym etc.) up to a safety limit, then collapse stray pipe / pipe-like
# glyphs that OCR routinely inserts mid-line.
# ---------------------------------------------------------------------------
# A token is "good" when it is an UPPER‑case alphabetic word ≥3 chars (or a
# short numeric/ampersand qualifier following one). The regex anchors at
# token start; whole-token match not required.
_TITLE_LEADING_GOOD_TOKEN_RE = re.compile(r'^[A-Z][A-Z0-9&\-]{2,}$')

# Hard cap on how many leading tokens may be discarded as OCR noise. Beyond
# this and the candidate is probably garbage rather than a few stray prefix
# tokens — we leave the original string untouched (or empty) for the higher
# layer to reject via indicator gating.
_TITLE_MAX_LEADING_NOISE_TOKENS = 6

# Mid-line OCR glyphs that are virtually never real title characters.
# Replaced with a single space, then whitespace re-collapsed.
_TITLE_STRAY_GLYPHS_RE = re.compile(r'(?<=\s)[|;:,¦│║]+(?=\s)|^[|;:,¦│║]+\s|\s[|;:,¦│║]+$')


def _strip_leading_title_noise(s: str) -> str:
    """
    Drop leading OCR-junk tokens ("o;", "x | 5 oe") that sit in front of an
    otherwise clean upper-case title. Walk tokens from the left and stop at
    the first one that matches `_TITLE_LEADING_GOOD_TOKEN_RE`, capped by
    `_TITLE_MAX_LEADING_NOISE_TOKENS` for safety.
    """
    if not s:
        return s
    tokens = s.split()
    if not tokens:
        return s
    drop = 0
    limit = min(len(tokens), _TITLE_MAX_LEADING_NOISE_TOKENS)
    while drop < limit and not _TITLE_LEADING_GOOD_TOKEN_RE.match(tokens[drop]):
        drop += 1
    if drop == 0:
        return s
    if drop >= len(tokens):
        # No good token found within the safety window — leave the original
        # string so higher-layer indicator gating can reject it.
        return s
    return ' '.join(tokens[drop:])


def _sanitise_title_candidate(s: str) -> str:
    """
    Apply the soft-coded title-candidate cleanup chain:
      1. Strip recognised label prefixes (TITLE: …)
      2. Drop leading OCR-noise tokens
      3. Replace stray mid-/edge glyphs (`|`, `;`, `¦`, …) with spaces
      4. Collapse whitespace, strip dangling punctuation
    """
    if not s:
        return ''
    out = _TITLE_LABEL_PREFIX_RE.sub('', s)
    out = _strip_leading_title_noise(out)
    out = _TITLE_STRAY_GLYPHS_RE.sub(' ', out)
    out = _MULTI_SPACE.sub(' ', out).strip(' -:_')
    return out

# Maximum length of a single title candidate (line or merged window) — longer
# than this and it is almost certainly multiple fields concatenated.
_TITLE_MAX_LEN = 160
# Minimum length of a candidate line to be considered without an indicator
# match (used by fallback path only).
_TITLE_MIN_LEN = 8
# How many trailing lines may be merged into the candidate when the current
# match line ends without sentence-style punctuation and the next line is a
# short uppercase continuation. Captures titles like
#   "PROCESS FLOW DIAGRAM UNIT 47 & UNIT 48"
#   "DEHYDRATION AND DEW POINTING UNIT"
_TITLE_MAX_MERGE_LINES = 3

# Keyword → status label (mirror of extractor.NON_TEFF_STATUS_KEYWORDS but
# canonicalised).
_STATUS_KEYWORDS = [
    ('issued for construction', 'IFC'),
    ('issued for approval',     'IFA'),
    ('issued for comment',      'IFR'),
    ('issued for review',       'IFR'),
    ('for information',         'FOR INFORMATION'),
    ('for approval',            'IFA'),
    ('approved',                'APPROVED'),
    ('preliminary',             'PRELIMINARY'),
    ('draft',                   'DRAFT'),
]

# Title-line heuristic: first non-noise line of 5..90 chars containing letters.
_TITLE_NOISE = re.compile(r'^(page|rev|revision|date|sheet|of)\b', re.IGNORECASE)

# ---------------------------------------------------------------------------
# SOFT-CODED text-quality config — used to reject OCR garbage.
#
# Symptoms in scanned PDFs (e.g. "^5 - 50/ A Po A^ -p AyfA=>^ yoyAAp:>yA77^"):
#   • high ratio of non-alphanumeric chars
#   • very few vowels relative to consonants
#   • lots of single-letter "words"
#   • unusual punctuation runs (^^, =>, :>)
# Each threshold below tunes one heuristic. Lower numbers = stricter.
# ---------------------------------------------------------------------------
TEXT_QUALITY_CONFIG = {
    'min_alpha_ratio':         0.55,   # at least 55% letters/digits
    'min_letter_ratio':        0.45,   # at least 45% letters (digits + alpha alone is suspicious)
    'min_vowel_ratio':         0.18,   # vowels / total letters
    'max_special_ratio':       0.30,   # punctuation + symbols cap
    'min_avg_word_len':        2.5,    # average alphabetic-word length
    'max_single_letter_ratio': 0.35,   # single-letter "words" cap
    'forbidden_runs':          re.compile(r'[\^~`]{2,}|=>{2,}|:>|<>{2,}|[\^=]{3,}'),
    # Any single ^, ~, `, |, \, =, < or > embedded inside an alphabetic
    # token is a strong OCR-garbage signal (e.g. "t-i^APiM", "AyfA=>").
    'junk_in_word':            re.compile(r"[A-Za-z][\^~`|\\=<>][A-Za-z]|[A-Za-z][\^~`|\\=<>]+"),
    'max_junk_word_ratio':     0.20,   # how many tokens may carry junk
    'min_length':              5,
    'max_length':              120,
}
# Strip / collapse control characters, mojibake, weird whitespace.
_CONTROL_CHARS    = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')
_MULTI_SPACE      = re.compile(r'\s{2,}')
_LEADING_TRAILING_JUNK = re.compile(r'^[^A-Za-z0-9]+|[^A-Za-z0-9.)]+$')
_VOWEL_SET        = set('aeiouAEIOU')


def _normalize_text(text: str) -> str:
    """Strip control chars, collapse whitespace, trim leading/trailing junk."""
    if not text:
        return ''
    s = _CONTROL_CHARS.sub(' ', text)
    s = _MULTI_SPACE.sub(' ', s).strip()
    s = _LEADING_TRAILING_JUNK.sub('', s)
    return s


def _is_clean_text(text: str, cfg: Dict[str, Any] = TEXT_QUALITY_CONFIG) -> bool:
    """
    Heuristic OCR-garbage detector. Returns True only when the line looks
    like real human-readable text. All thresholds are soft-coded above.
    """
    if not text:
        return False
    n = len(text)
    if n < cfg['min_length'] or n > cfg['max_length']:
        return False
    if cfg['forbidden_runs'].search(text):
        return False

    alnum   = sum(1 for c in text if c.isalnum())
    letters = sum(1 for c in text if c.isalpha())
    vowels  = sum(1 for c in text if c in _VOWEL_SET)
    spaces  = text.count(' ')
    special = n - alnum - spaces

    if alnum / n < cfg['min_alpha_ratio']:           return False
    if letters / n < cfg['min_letter_ratio']:        return False
    if special / n > cfg['max_special_ratio']:       return False
    if letters and vowels / letters < cfg['min_vowel_ratio']:
        return False

    # Word-level checks (ignore tokens that are pure punctuation).
    words = [w for w in re.split(r'\s+', text) if any(c.isalpha() for c in w)]
    if not words:
        return False
    avg_word_len = sum(len(w) for w in words) / len(words)
    if avg_word_len < cfg['min_avg_word_len']:       return False
    single_letter = sum(1 for w in words if len(w) == 1)
    if single_letter / len(words) > cfg['max_single_letter_ratio']:
        return False
    # Reject tokens with embedded junk like "t-i^APiM", "AyfA=>".
    junk_words = sum(1 for w in words if cfg['junk_in_word'].search(w))
    if junk_words / len(words) > cfg['max_junk_word_ratio']:
        return False

    return True


# ---------------------------------------------------------------------------
# SOFT-CODED value-level junk filter — used by every pattern_lookup field
# (document_no, drawing_no, subject, project_title, etc.).
#
# Looser than `_is_clean_text` so legitimate short tokens like 'PT-1234',
# '2"-P-1001-A1A', 'P16093-PR-PFD-001' still pass, but values containing
# OCR-noise characters (^, \, weird angle brackets, multiple > or <) are
# rejected.
# ---------------------------------------------------------------------------
VALUE_JUNK_CONFIG = {
    # Any of these characters anywhere in a value = reject. They almost
    # never appear in real engineering field values.
    'forbidden_chars':       set('^~`|\\'),
    # Run patterns that signal mojibake even when individual chars are valid.
    'forbidden_run_pattern': re.compile(r'>\s*[A-Za-z]|[A-Za-z]\s*<|=>{1,}|:>|<>{2,}|[<>]{2,}'),
    # Reject values where >40% of chars are non-alphanumeric (excluding spaces,
    # hyphens, slashes, dots, parens, ampersands and quotes which are valid).
    'allowed_specials':      set(' -_./()&\'",:'),
    'max_junk_char_ratio':   0.20,
    # Minimum letters or digits (rejects pure-symbol values).  Set to 1 so
    # legitimate single-character codes still pass — e.g. revision "A",
    # class_review "2", review code "B".  The forbidden_chars / junk_ratio
    # / forbidden_run_pattern guards continue to block real noise.
    'min_alnum':             1,
}


def _is_clean_value(val: str, cfg: Dict[str, Any] = VALUE_JUNK_CONFIG) -> bool:
    """
    Lightweight junk gate for individual field values returned by regex
    extractors. Returns False for OCR-noise like 'DRAWING Nos 2^2-\\^.- OSS'
    or 'DRAWING N^c 7^. m->&o2.'.
    """
    if not val:
        return False
    if any(c in cfg['forbidden_chars'] for c in val):
        return False
    if cfg['forbidden_run_pattern'].search(val):
        return False
    alnum = sum(1 for c in val if c.isalnum())
    if alnum < cfg['min_alnum']:
        return False
    junk = sum(1 for c in val
               if not c.isalnum() and c not in cfg['allowed_specials'])
    if junk / len(val) > cfg['max_junk_char_ratio']:
        return False
    return True

# Unit-code patterns (e.g. "Unit 12", "UNIT-05", "U05")
_UNIT_PATTERN = re.compile(r'\b(?:UNIT[-\s]?([0-9]{1,3})|U([0-9]{2,3}))\b', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Config loaders (cached — live-reload by clearing the cache during tests)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_template() -> Dict[str, Any]:
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_taxonomy() -> Dict[str, Any]:
    with open(TAXONOMY_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_patterns() -> Dict[str, Any]:
    """Load soft-coded extraction patterns. Compiled on demand, cached."""
    with open(PATTERNS_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    # Pre-compile each pattern for speed
    flag_map = {
        'IGNORECASE': re.IGNORECASE,
        'MULTILINE':  re.MULTILINE,
        'DOTALL':     re.DOTALL,
    }
    compiled: Dict[str, List[Dict[str, Any]]] = {}
    for field, entries in cfg.get('patterns', {}).items():
        out = []
        for e in entries:
            flags = 0
            for f_name in e.get('flags', []):
                flags |= flag_map.get(f_name, 0)
            try:
                out.append({
                    'regex': re.compile(e['pattern'], flags),
                    'group': int(e.get('group', 1)),
                    'mode':  e.get('mode', 'first'),
                })
            except re.error:
                logger.exception('Invalid pattern for field %s: %s', field, e.get('pattern'))
        compiled[field] = out
    return {
        'compiled':   compiled,
        'stop_words': {w.upper() for w in cfg.get('stop_words', [])},
    }


def get_columns() -> List[Dict[str, Any]]:
    return load_template()['columns']


def get_na_value() -> str:
    return load_template().get('default_na_value', 'NA')


def get_limits() -> Dict[str, Any]:
    return load_template().get('limits', {})


def get_batch_default_hints() -> Dict[str, Any]:
    return load_template().get('batch_default_hints', {})


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def detect_format(file_name: str) -> Optional[str]:
    return _FORMAT_BY_EXT.get(os.path.splitext(file_name.lower())[1])


def read_file_text(file_path: str, fmt: Optional[str] = None) -> str:
    """
    Read raw text from a file for AI extraction.

    Reuses the same libraries that extractor.py uses, but concatenates pages
    into one string capped at _MAX_SCAN_CHARS for responsive extraction.

    For PDFs whose embedded text layer is empty / OCR-garbage (typical for
    scanned drawings and old AutoCAD print-outs) the function transparently
    falls back to Tesseract OCR via ``vision_extractor.ocr_pdf_text``. The
    fallback is gated by soft-coded thresholds in ``VISION_CONFIG`` and is
    a no-op when Tesseract is unavailable.
    """
    fmt = fmt or detect_format(file_path)
    if not fmt:
        return ''
    text = ''
    try:
        if fmt == 'pdf':
            import pdfplumber
            chunks: List[str] = []
            with pdfplumber.open(file_path) as pdf:
                for p in pdf.pages:
                    chunks.append(p.extract_text() or '')
                    if sum(len(c) for c in chunks) > _MAX_SCAN_CHARS:
                        break
            text = '\n'.join(chunks)[:_MAX_SCAN_CHARS]
        elif fmt == 'excel':
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
            out: List[str] = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    out.append(' '.join(str(c) for c in row if c is not None))
                    if sum(len(x) for x in out) > _MAX_SCAN_CHARS:
                        break
                if sum(len(x) for x in out) > _MAX_SCAN_CHARS:
                    break
            text = '\n'.join(out)[:_MAX_SCAN_CHARS]
        elif fmt == 'word':
            import docx
            doc = docx.Document(file_path)
            text = '\n'.join(p.text for p in doc.paragraphs)[:_MAX_SCAN_CHARS]
    except Exception:
        logger.exception('read_file_text failed for %s', file_path)

    # OCR fallback for PDFs whose text layer is empty / unreadable.
    if fmt == 'pdf':
        try:
            from . import vision_extractor
            if vision_extractor.needs_ocr_fallback(text):
                ocr_text = vision_extractor.ocr_pdf_text(file_path)
                if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                    logger.info('OCR fallback engaged for %s (text %d → %d chars)',
                                file_path, len(text), len(ocr_text))
                    text = (text + '\n' + ocr_text)[:_MAX_SCAN_CHARS]
        except Exception:
            logger.exception('OCR fallback failed for %s', file_path)

        # Yellow-highlight extractor — pulls revision/approval/hold stamps
        # that almost never appear in the text layer of older drawings.
        try:
            from . import yellow_region_extractor
            yellow_blob = yellow_region_extractor.extract_yellow_text_blob(file_path)
            if yellow_blob:
                logger.info('Yellow-region OCR contributed %d chars for %s',
                            len(yellow_blob), file_path)
                text = (text + '\n' + yellow_blob)[:_MAX_SCAN_CHARS]
        except Exception:
            logger.exception('Yellow-region OCR failed for %s', file_path)
    return text


def pdf_page_count(file_path: str) -> Optional[int]:
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def detect_paper_size(file_path: str, fmt: Optional[str]) -> str:
    """
    Best-effort paper-size code (A4/A3/A2/A1/A0) inferred from PDF page box.
    Returns empty string when unavailable.
    """
    if fmt != 'pdf':
        return ''
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            if not pdf.pages:
                return ''
            page = pdf.pages[0]
            w_mm = float(page.width) * 25.4 / 72.0
            h_mm = float(page.height) * 25.4 / 72.0
            short, long_ = sorted((w_mm, h_mm))
            # ISO 216 nominal sizes, with ±5 mm tolerance
            iso = {'A4': (210, 297), 'A3': (297, 420), 'A2': (420, 594),
                   'A1': (594, 841), 'A0': (841, 1189)}
            for name, (s, l_) in iso.items():
                if abs(short - s) <= 5 and abs(long_ - l_) <= 5:
                    return name
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# AI extractors
# ---------------------------------------------------------------------------

def _extract_title(text: str) -> str:
    """
    Pick the first 'clean' line from a document's text as its title.

    Resilient to OCR garbage from scanned PDFs: every candidate line is
    normalized then validated with TEXT_QUALITY_CONFIG before being
    returned. If nothing clean is found, returns '' — better an empty
    field than "^5 - 50/ A Po A^ -p AyfA=>^ yoyAAp:>yA77^APs".
    """
    if not text:
        return ''
    for raw in text.splitlines():
        s = _normalize_text(raw)
        if not s:
            continue
        if not (5 <= len(s) <= 90):
            continue
        if not re.search(r'[A-Za-z]', s):
            continue
        if _TITLE_NOISE.match(s):
            continue
        # Skip bare document numbers (e.g. "P16093-PR-PFD-001")
        if DOCUMENT_NO_PATTERN.fullmatch(s):
            continue
        # NEW: reject OCR garbage via soft-coded quality heuristics.
        if not _is_clean_text(s):
            continue
        return s
    return ''


# ---------------------------------------------------------------------------
# Smart title extraction — taxonomy-driven indicator scan with multi-line
# merging. Returns the descriptive document title from the title block,
# matching the reference Master Index examples.
# ---------------------------------------------------------------------------

# Cached regex list built once per taxonomy id (keyed by object id).
_TITLE_INDICATOR_CACHE: Dict[int, List[re.Pattern]] = {}


def _build_title_indicator_regexes(taxonomy: Dict[str, Any]) -> List[re.Pattern]:
    """
    Build a flat ordered list of compiled regex patterns used to flag a line
    as a likely title. Sources, in order:
      1. Explicit phrases in `_TITLE_INDICATOR_PHRASES`
      2. Every subtype alias derived from the taxonomy (reuses the
         singular/plural-tolerant regex builder used by the subtype matcher)
    Cached per taxonomy object so repeated build_row() calls are cheap.
    """
    cache_key = id(taxonomy or {})
    cached = _TITLE_INDICATOR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    seen: set = set()
    patterns: List[re.Pattern] = []

    def _add(raw_pattern: str) -> None:
        if raw_pattern in seen:
            return
        seen.add(raw_pattern)
        try:
            patterns.append(re.compile(raw_pattern, re.IGNORECASE))
        except re.error:
            pass

    for phrase in _TITLE_INDICATOR_PHRASES:
        _add(_alias_to_regex(phrase.lower()))

    for subs in (taxonomy.get('document_types', {}) or {}).values():
        for sub in subs or []:
            for pat, _is_override in _build_subtype_alias_regexes(sub):
                _add(pat)

    _TITLE_INDICATOR_CACHE[cache_key] = patterns
    return patterns


def _line_looks_like_title_noise(line: str) -> bool:
    """Reject obvious title-block noise (drawing numbers, page/rev/date)."""
    if not line:
        return True
    if _TITLE_NOISE.match(line):
        return True
    for pat in _TITLE_REJECT_PATTERNS:
        if pat.search(line):
            return True
    if DOCUMENT_NO_PATTERN.fullmatch(line.strip()):
        return True
    return False


def _is_uppercase_continuation(line: str) -> bool:
    """True when `line` looks like a UPPER-CASE continuation of a title."""
    if not line or len(line) > 90:
        return False
    if _line_looks_like_title_noise(line):
        return False
    letters = [c for c in line if c.isalpha()]
    if len(letters) < 3:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_ratio >= 0.7


def _score_title_candidate(text: str, indicator_hits: int) -> float:
    """
    Score a candidate title. Indicator hits dominate; length and upper-case
    ratio break ties so that descriptive lines beat short acronyms.
    """
    if not text:
        return 0.0
    n = len(text)
    score = indicator_hits * 10.0
    # Reasonable-length bonus.
    score += min(n, _TITLE_MAX_LEN) / 30.0
    letters = [c for c in text if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        score += upper_ratio * 1.5
    return score


def _extract_title_smart(text: str, taxonomy: Dict[str, Any]) -> str:
    """
    Scan the document text for a descriptive title line.

    Strategy:
      1. Walk normalized lines; skip noise (page/rev/date/drawing number).
      2. For each surviving line, count indicator-phrase hits.
      3. When a line has >=1 indicator hit, attempt to merge up to
         _TITLE_MAX_MERGE_LINES adjacent uppercase continuation lines.
      4. Score and pick the best merged candidate.
      5. Fall back to the legacy `_extract_title` heuristic when no
         indicator-bearing line is found.
    """
    if not text:
        return ''
    indicators = _build_title_indicator_regexes(taxonomy)
    if not indicators:
        return _extract_title(text)

    # Normalize lines once; preserve order for merging.
    raw_lines = text.splitlines()
    norm_lines: List[str] = []
    for raw in raw_lines:
        s = _normalize_text(raw)
        norm_lines.append(s)

    best_text = ''

    for i, line in enumerate(norm_lines):
        if not line or len(line) > _TITLE_MAX_LEN:
            continue
        if _line_looks_like_title_noise(line):
            continue
        hits = sum(1 for pat in indicators if pat.search(line))
        if hits == 0:
            continue
        line_indicator_set = {pat.pattern for pat in indicators if pat.search(line)}
        # Merge up to N uppercase continuation lines, but stop as soon as a
        # next line introduces a *new* title indicator — that signals a
        # separate field (category / series label) rather than a true
        # continuation of the current title.
        merged_parts = [line]
        merged_len = len(line)
        for j in range(1, _TITLE_MAX_MERGE_LINES + 1):
            if i + j >= len(norm_lines):
                break
            nxt = norm_lines[i + j]
            if not _is_uppercase_continuation(nxt):
                break
            if merged_len + 1 + len(nxt) > _TITLE_MAX_LEN:
                break
            nxt_indicators = {pat.pattern for pat in indicators if pat.search(nxt)}
            if nxt_indicators - line_indicator_set:
                break
            merged_parts.append(nxt)
            merged_len += 1 + len(nxt)
        candidate = ' '.join(merged_parts).strip()
        # First qualifying candidate wins — title blocks place the
        # document title above category/series labels. Once we have a
        # candidate we exit immediately.
        best_text = candidate
        break

    if best_text:
        # Soft-coded sanitisation chain: strip labels, drop OCR-noise prefix
        # tokens, replace stray glyphs, collapse whitespace.
        best_text = _sanitise_title_candidate(best_text)
        return best_text[:_TITLE_MAX_LEN]
    return _extract_title(text)


# ---------------------------------------------------------------------------
# Smart Issue Date extractor
# ---------------------------------------------------------------------------
# Reference Master Index sample shows real engineering title-block dates in
# many shapes:
#     "3/10/2000", "6/25/1999", "1/28/1999"   (M/D/YYYY)
#     "20-12-99"                              (D-M-YY)
#     "5/29/2017", "11/29/2016", "2/29/2016"  (M/D/YYYY with 1- or 2-digit M)
#     "7/15/2012", "5/30/2013", "7/28/2015"   (M/D/YYYY)
# The legacy `DATE_PATTERN` regex demands two-digit month/day, missing all of
# these. Below is a flexible token regex paired with label-proximity scoring.

_ISSUE_DATE_TOKEN_PATTERNS: Tuple[re.Pattern, ...] = (
    # Slash dates: 3/10/2000, 12/3/99, 1/28/1999, 11/29/2016
    re.compile(r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b'),
    # Dash dates: 20-12-99, 1-28-1999, 11-29-2016
    re.compile(r'\b(\d{1,2}-\d{1,2}-\d{2,4})\b'),
    # Dot dates: 20.12.1999
    re.compile(r'\b(\d{1,2}\.\d{1,2}\.\d{2,4})\b'),
    # ISO: 1999-12-20
    re.compile(r'\b(\d{4}-\d{1,2}-\d{1,2})\b'),
    # Day-Month-Year text: 28-JAN-1999, 7-Feb-2017
    re.compile(r'\b(\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{2,4})\b'),
    # Month-Day-Year text: JAN 28 1999
    re.compile(r'\b([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})\b'),
)

# Words that indicate a date label in the title block. The scanner gives a
# big proximity bonus to date tokens within `_DATE_LABEL_WINDOW` chars of
# any of these words.
_DATE_LABEL_KEYWORDS: Tuple[str, ...] = (
    'date', 'issued', 'issue date', 'date of issue', 'doc date',
    'date issued',
)
_DATE_LABEL_WINDOW = 60


def _date_sort_key(token: str) -> Tuple[int, int, int]:
    """
    Return a (year, month, day) tuple suitable for sorting. Two-digit years
    are expanded with a 50/2050 cutoff so '99' → 1999, '20' → 2020. Tokens
    that fail to parse — OR that parse to an impossible month/day — get
    (0, 0, 0). Strict validation is critical because the date regex is
    lenient enough to match document-number patterns like ``62-00-002``;
    rejecting month=0/day=0 here prevents those from ever being treated as
    a date downstream.
    """
    s = token.strip()
    # Try numeric formats first.
    for sep in ('/', '-', '.'):
        if sep in s and not re.search(r'[A-Za-z]', s):
            parts = s.split(sep)
            if len(parts) != 3:
                continue
            try:
                a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                continue
            # ISO YYYY-MM-DD → first part 4 digits
            if len(parts[0]) == 4:
                year, month, day = a, b, c
            else:
                # Reject patterns like "62-00-002" up-front: a 3-digit third
                # part combined with a 2-digit second part that is zero is a
                # very strong "document number, not date" signal.
                if len(parts[2]) >= 3 and b == 0:
                    return (0, 0, 0)
                # Heuristic: if first or second part > 12 it must be the day
                # (US style M/D/Y vs intl D/M/Y). Default to M/D/Y when
                # neither is > 12 (matches the reference sample format).
                if a > 12 and b <= 12:
                    day, month = a, b
                else:
                    month, day = a, b
                year = c
            if year < 100:
                year = year + (1900 if year >= 50 else 2000)
            # Strict month/day validation — drops doc-number look-alikes.
            if not (1 <= month <= 12) or not (1 <= day <= 31):
                return (0, 0, 0)
            return (year, month, day)
    # Text month formats — try a couple of strptime patterns.
    from datetime import datetime
    for fmt in ('%d-%b-%Y', '%d-%B-%Y', '%d %b %Y', '%d %B %Y',
                '%b %d %Y', '%B %d %Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            dt = datetime.strptime(s, fmt)
            return (dt.year, dt.month, dt.day)
        except ValueError:
            continue
    return (0, 0, 0)


def _extract_issue_date_smart(text: str) -> str:
    """
    Find the document's issue date.

    Strategy:
      1. Collect every date-shaped token in the text along with its offset.
      2. Score each token by proximity to a date-label keyword. Tokens
         within `_DATE_LABEL_WINDOW` chars get a strong boost.
      3. Tie-break: prefer the *latest* parseable date (issue date is
         usually the newest in a title block's revision history).
    Returns '' when no valid date is found.
    """
    if not text:
        return ''
    low = text.lower()
    # Pre-compute label spans for quick proximity check.
    label_spans: List[Tuple[int, int]] = []
    for kw in _DATE_LABEL_KEYWORDS:
        start = 0
        while True:
            idx = low.find(kw, start)
            if idx < 0:
                break
            label_spans.append((idx, idx + len(kw)))
            start = idx + len(kw)

    candidates: List[Tuple[float, Tuple[int, int, int], str]] = []
    seen_offsets: set = set()
    for pat in _ISSUE_DATE_TOKEN_PATTERNS:
        for m in pat.finditer(text):
            off = m.start(1)
            if off in seen_offsets:
                continue
            seen_offsets.add(off)
            tok = m.group(1).strip()
            ymd = _date_sort_key(tok)
            if ymd[0] < 1900 or ymd[0] > 2100:
                continue  # garbage
            # Proximity score
            prox = 0.0
            for ls, le in label_spans:
                gap = max(0, off - le) if off > le else max(0, ls - (off + len(tok)))
                if gap <= _DATE_LABEL_WINDOW:
                    prox = max(prox, 10.0 - gap / 20.0)
            # Year is the strong tie-breaker (issue date = latest)
            year_score = ymd[0] / 1000.0
            score = prox + year_score
            candidates.append((score, ymd, tok))
    if not candidates:
        return ''
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


# ---------------------------------------------------------------------------
# SOFT-CODED standardised issue-date format.
#
# Reference sample uses US "MM/DD/YYYY" (e.g. "10/18/1993"). Whatever shape
# the smart extractor returns (`3/10/2000`, `20-12-99`, `JAN 28 1999`,
# `1993-10-18`, …) we normalise into this single format before writing the
# row, so the UI column is always uniform. To change format globally, edit
# `ISSUE_DATE_OUTPUT_FORMAT` — every consumer reads through here.
# ---------------------------------------------------------------------------
ISSUE_DATE_OUTPUT_FORMAT = '%m/%d/%Y'      # → '10/18/1993'
# When True, any final issue-date value we cannot confidently parse into a
# real (Y,M,D) is dropped to NA rather than left as raw garbage. Flip to
# False to keep the original (possibly invalid) token in the cell.
ISSUE_DATE_DROP_INVALID = True
# Lower bound for a "real" plant document year. Anything < this from the
# parser is treated as garbage and the original token is left untouched
# (which the higher-layer NA filter will later replace if needed).
_ISSUE_DATE_MIN_YEAR = 1900
_ISSUE_DATE_MAX_YEAR = 2100


def _normalise_issue_date(token: str) -> str:
    """
    Return ``token`` reformatted to ``ISSUE_DATE_OUTPUT_FORMAT``.

    Uses the same `_date_sort_key` heuristics so US M/D/Y vs intl D/M/Y is
    resolved consistently with the rest of the pipeline. Returns ``''``
    when the token cannot be confidently parsed — the caller decides
    whether to drop the cell (see ``ISSUE_DATE_DROP_INVALID``).
    """
    if not token:
        return ''
    y, m, d = _date_sort_key(token)
    if not (_ISSUE_DATE_MIN_YEAR <= y <= _ISSUE_DATE_MAX_YEAR):
        return ''
    if not (1 <= m <= 12) or not (1 <= d <= 31):
        return ''
    from datetime import datetime
    try:
        return datetime(y, m, d).strftime(ISSUE_DATE_OUTPUT_FORMAT)
    except ValueError:
        return ''


# ---------------------------------------------------------------------------
# SOFT-CODED Document Title polish.
#
# Even after `_sanitise_title_candidate` runs, OCR/vision pipelines
# occasionally leave behind:
#   • Decorative symbols: *, #, ^, ~, \, =, +, <, >, brackets, mojibake
#   • Stray quotes / asterisks: "**", `''`, `""`
#   • Trailing "N/A", "NA" or "TBD" tokens
#   • Multiple consecutive punctuation chars
#
# This polish runs after the existing sanitiser and is purely cosmetic —
# it never invents content. Every rule below is data-driven so you can
# tune the behaviour without touching the function.
# ---------------------------------------------------------------------------
TITLE_POLISH_CONFIG = {
    # Characters always replaced with a single space (decorative / OCR junk).
    # Legitimate plant-title punctuation -, /, &, (, ), ., ,, comma, ', "
    # is preserved.
    'strip_chars': r'[*#^~\\=+<>\[\]{}@`¬§¶•·»«■□◆◇○●★☆→←↑↓]',
    # Quote-style chars that should be collapsed away entirely.
    'drop_quotes': r'["\']{2,}',
    # Tokens that are NA-equivalents and must not appear inside a title.
    # Whole-token match only (case-insensitive).
    'na_tokens':    ('NA', 'N/A', 'TBD', 'TBA', 'N.A.', 'NOT APPLICABLE'),
    # Maximum length cap (mirrors `_TITLE_MAX_LEN`).
    'max_len':      160,
    # Minimum letter ratio after polishing — below this the title is junk.
    'min_letter_ratio': 0.40,
}
_TITLE_POLISH_STRIP_RE = re.compile(TITLE_POLISH_CONFIG['strip_chars'])
_TITLE_POLISH_QUOTES_RE = re.compile(TITLE_POLISH_CONFIG['drop_quotes'])
_TITLE_POLISH_NA_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in TITLE_POLISH_CONFIG['na_tokens']) + r')\b',
    re.IGNORECASE,
)
# Collapse runs of punctuation like "-- -- :: ,, .." into one char.
_TITLE_POLISH_PUNCT_RUN_RE = re.compile(r'([\-:,.;/&])\1{1,}')


def _polish_title(s: str) -> str:
    """
    Final cosmetic pass for Document Title values.

    Returns '' when the polished output is empty or fails the minimum
    letter-ratio quality gate (caller is expected to fall back to the NA
    placeholder in that case).
    """
    if not s:
        return ''
    out = _TITLE_POLISH_STRIP_RE.sub(' ', s)
    out = _TITLE_POLISH_QUOTES_RE.sub('', out)
    out = _TITLE_POLISH_NA_RE.sub(' ', out)
    out = _TITLE_POLISH_PUNCT_RUN_RE.sub(r'\1', out)
    out = _MULTI_SPACE.sub(' ', out).strip(' -:_,.;|/')
    if not out:
        return ''
    letters = sum(1 for c in out if c.isalpha())
    if letters / max(len(out), 1) < TITLE_POLISH_CONFIG['min_letter_ratio']:
        return ''
    return out[: TITLE_POLISH_CONFIG['max_len']]


# ---------------------------------------------------------------------------
# Smart Revision extractor
# ---------------------------------------------------------------------------
# Reference Master Index sample shows revisions as bare integers — `0`, `1`,
# `2`, `3`, `4`, `11`. We therefore enforce a numeric-only output (toggle
# below). Pre-issue letter revisions (`A`, `B`, `C`, …) are converted to the
# equivalent integer position via `_REVISION_LETTER_MAP` so the column stays
# uniform; flip `REVISION_NUMERIC_ONLY` to False to keep letters as-is.

REVISION_NUMERIC_ONLY = True

# When True, any final revision value that resolves to NA is replaced
# with "0" — the engineering convention for "initial / no revision yet".
# Reference Master Index never leaves the column blank, so we default
# this on. Flip to False to preserve NA semantics.
REVISION_NA_AS_ZERO = True

# Industry convention: pre-issue letter revisions map to negative integers
# in some systems and to 0+ in others. The reference sample treats first
# numeric issue as `0`, so we keep letter-to-position 1:1 (A→0, B→1, …).
_REVISION_LETTER_MAP: Dict[str, str] = {
    chr(ord('A') + i): str(i) for i in range(26)
}

# Token shape for a valid revision value (digits or single letter).
_REVISION_NUMERIC_RE = re.compile(r'^\d{1,2}$')
_REVISION_LETTER_RE  = re.compile(r'^[A-Z]$')

# Plausible revision range. Real plant documents rarely exceed revision
# 20 (most live in 0-9). Larger numeric tokens are usually doc-number
# fragments, dates, or unit codes that have leaked into the column.
# Soft-coded: raise this if you genuinely have docs at higher revisions.
_REVISION_MAX_NUMERIC = 20

# Tokens that match the shape but are never a real revision (status words,
# units, common label fragments, OCR noise like "NM" / "NN").
_REVISION_BLOCKLIST = {
    'NA', 'N/A', 'TBD', 'TBA', 'IFA', 'IFR', 'IFC', 'AFC',
    'NO', 'YES', 'OK', 'PDF', 'DWG', 'DOC', 'REV', 'REF',
    'BY', 'CHK', 'APP', 'APR', 'APD',
    'JAN', 'FEB', 'MAR', 'MAY', 'JUN',
    'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
    'AM', 'PM',
    'NM', 'NN', 'XX', 'TT',
}

# Label keywords that sit immediately before a revision cell. Tuple is
# split into [:N] numeric patterns and [N:] letter patterns where
# N = `_REVISION_NUMERIC_LABEL_COUNT`. Keep this contract when editing.
_REVISION_LABEL_PATTERNS: Tuple[re.Pattern, ...] = (
    # --- Numeric label patterns (must capture an integer) ---
    # "Rev. 11", "Rev No 4", "REVISION: 0"
    re.compile(r'\b[Rr]ev(?:ision)?\s*(?:no\.?|number|#)?\.?\s*[:\-]?\s*(\d{1,2})\b'),
    re.compile(r'\bREV\b\s*[:\-]?\s*(\d{1,2})\b'),
    # Handwritten / old-document numeric variants: extra whitespace,
    # dash/hash separator, hand-printed "Rev-0", "rev 03".
    re.compile(r'\b[Rr]ev(?:ision)?\.?\s*[-#]\s*(\d{1,2})\b'),
    re.compile(r'\b[Rr]ev(?:ision)?\.?\s+(\d{1,2})\b'),
    # Bare "R0", "R1", "R-01" — last-resort numeric form.
    re.compile(r'(?<![A-Z])R[-_]?(\d{1,2})(?![A-Z0-9])'),
    # --- Letter label patterns (must capture a single letter A-Z) ---
    # Only honoured when REVISION_NUMERIC_ONLY=False or when no numeric
    # form exists — the smart extractor decides.
    re.compile(r'\b[Rr]ev(?:ision)?\s*(?:no\.?|number|#)?\.?\s*[:\-]?\s*([A-Z])\b'),
    re.compile(r'\bREV\b\s*[:\-]?\s*([A-Z])\b'),
    re.compile(r'\b[Rr]ev(?:ision)?\.?\s*[-#]\s*([A-Z])\b'),
    re.compile(r'\b[Rr]ev(?:ision)?\.?\s+([A-Z])\b'),
)
# Soft-coded split index — numeric patterns occupy `_REVISION_LABEL_PATTERNS[:N]`.
_REVISION_NUMERIC_LABEL_COUNT = 5

# Maximum chars (or lines) the revision value may sit after the label word
# when stacked vertically in a title-block table.
_REVISION_LABEL_MAX_GAP = 80

# OCR character-doubling detection. Some scanned drawings come back from
# OCR with every character duplicated (e.g. "HEADER" \u2192 "HEEAADDEERR",
# "0" \u2192 "00", "A" \u2192 "AA"). When this signature is present we collapse
# same-character pairs in revision tokens before normalising. Threshold is
# soft-coded \u2014 lower it to be more aggressive, raise it to be safer.
_OCR_DOUBLING_MIN_RATIO = 0.30           # \u22650.30 = 30% of long words doubled
_OCR_DOUBLING_MIN_WORD_LEN = 4           # only inspect tokens \u22654 chars
_OCR_DOUBLING_MIN_SAMPLE = 8             # need at least this many tokens
_OCR_DOUBLE_PAIR_RE = re.compile(r'([A-Za-z])\1')


def _text_has_ocr_doubling(text: str) -> bool:
    """
    Heuristic: True when the document text shows OCR character-doubling
    (each character emitted twice). Uses the ratio of long alphabetic
    tokens that contain at least one consecutive same-character pair.
    """
    if not text:
        return False
    words = [w for w in re.findall(r'[A-Za-z]+', text)
             if len(w) >= _OCR_DOUBLING_MIN_WORD_LEN]
    if len(words) < _OCR_DOUBLING_MIN_SAMPLE:
        return False
    doubled = sum(1 for w in words if _OCR_DOUBLE_PAIR_RE.search(w))
    return (doubled / len(words)) >= _OCR_DOUBLING_MIN_RATIO


def _collapse_doubled_token(tok: str) -> str:
    """
    Collapse same-character runs in `tok` (e.g. "00"\u2192"0", "AA"\u2192"A",
    "111"\u2192"1"). Used only when the surrounding text is flagged as
    OCR-doubled to avoid corrupting legitimate values like "11".
    """
    if not tok:
        return tok
    return re.sub(r'(.)\1+', r'\1', tok)


def _normalise_revision_value(tok: str) -> str:
    """
    Snap a candidate revision token into the canonical output form.

    Returns '' for invalid / blocklisted tokens. When `REVISION_NUMERIC_ONLY`
    is True, single-letter revisions are mapped via `_REVISION_LETTER_MAP`
    so the column always contains a number.
    """
    if not tok:
        return ''
    t = tok.strip().upper()
    if t in _REVISION_BLOCKLIST:
        return ''
    if _REVISION_NUMERIC_RE.fullmatch(t):
        # Strip leading zeros for uniformity ('01' → '1') but keep '0'.
        # Reject implausibly large numbers (likely doc-number fragments,
        # unit codes, dates, etc.) using `_REVISION_MAX_NUMERIC`.
        n = int(t)
        if n > _REVISION_MAX_NUMERIC:
            return ''
        return str(n)
    if _REVISION_LETTER_RE.fullmatch(t):
        if REVISION_NUMERIC_ONLY:
            return _REVISION_LETTER_MAP.get(t, '')
        return t
    return ''


def _is_valid_revision_token(tok: str) -> bool:
    """True when the token can be turned into a valid revision."""
    return bool(_normalise_revision_value(tok))


def _extract_revision_smart(text: str) -> str:
    """
    Find the document revision and return it as a number string ('0', '1',
    '11', …) when `REVISION_NUMERIC_ONLY` is enabled.

    Strategy:
      1. Prefer a numeric label match over a letter match (engineering
         title blocks usually keep both forms — we want the numeric one).
      2. Stacked title-block scan: locate `REV`/`REVISION` headers and
         pick the first numeric token that follows.
      3. Fallback: legacy `REVISION_PATTERN`.

    When the source text shows an OCR character-doubling signature
    (`_text_has_ocr_doubling`), candidate tokens are collapsed
    ("00"→"0", "AA"→"A") before normalisation.
    """
    if not text:
        return ''

    doubled = _text_has_ocr_doubling(text)
    # When the entire document is OCR-doubled (e.g. "RREEVV 00 HEEAADDEERR"),
    # collapse same-character runs across the whole corpus first so that
    # label-anchored regex passes ("REV", "Rev", "Revision") still match.
    scan_text = re.sub(r'(.)\1+', r'\1', text) if doubled else text

    def _norm(tok: str) -> str:
        if doubled:
            tok = _collapse_doubled_token(tok)
        return _normalise_revision_value(tok)

    # Pass 1: numeric label match wins outright.
    numeric_pats = _REVISION_LABEL_PATTERNS[:_REVISION_NUMERIC_LABEL_COUNT]
    letter_pats  = _REVISION_LABEL_PATTERNS[_REVISION_NUMERIC_LABEL_COUNT:]

    for pat in numeric_pats:
        m = pat.search(scan_text)
        if m:
            normed = _norm(m.group(1))
            if normed != '':
                return normed

    # Pass 2: stacked title-block — find each REV header and walk forward
    # for the first valid revision token (numeric or single letter). Label
    # row words (NO, DESCRIPTION, DATE, BY, …) are skipped; long tokens or
    # multi-digit numbers (doc-number fragments) terminate the scan.
    label_re = re.compile(r'\b(?:REV(?:ISION)?\.?|Rev\.?|Revision)\b')
    for m in label_re.finditer(scan_text):
        end = m.end()
        window = scan_text[end:end + _REVISION_LABEL_MAX_GAP]
        for cand in re.findall(r'[A-Za-z0-9]+', window):
            up = cand.upper()
            if up in {'NO', 'NUMBER', 'DESCRIPTION', 'HISTORY',
                       'STATUS', 'DATE', 'BY'}:
                continue
            normed = _norm(up)
            if normed != '':
                return normed
            # Stop once we walk into clearly-different content (long words).
            if len(up) > 4:
                break

    # Pass 3: letter-only label match (only used when numeric absent).
    for pat in letter_pats:
        m = pat.search(scan_text)
        if m:
            normed = _norm(m.group(1))
            if normed != '':
                return normed

    # Pass 4: legacy fallback.
    legacy = _first_match(REVISION_PATTERN, scan_text)
    if legacy:
        normed = _norm(legacy)
        if normed != '':
            return normed
    return ''


def _extract_status(text: str) -> str:
    low = text.lower()
    for kw, label in _STATUS_KEYWORDS:
        if kw in low:
            return label
    return ''


# ---------------------------------------------------------------------------
# Smart Revision Description / Status extractor
# ---------------------------------------------------------------------------
# Reference Master Index sample shows full-phrase status text rather than
# 3-letter acronyms:
#     "AS-BUILT", "AS-BUILT AS PER PROJ. NO. 0400551",
#     "AS-BUILT AS PER PE 1406 & PROJ. 5259",
#     "ISSUED FOR CONSTRUCTION", "RE-ISSUED FOR CONSTRUCTION",
#     "IFP" (Issued For Purchase — kept as acronym in source)
# The matcher below is ordered longest-first so a phrase like
# "AS-BUILT AS PER PROJ. 5259" beats the bare "AS-BUILT" prefix.

# Long compound phrases captured as regex templates so the trailing project
# / PE / PD reference is preserved verbatim. Each pattern returns the entire
# match (group 0); ordering matters — longest / most-specific first.
_REVISION_STATUS_REGEX_PHRASES: Tuple[re.Pattern, ...] = (
    # AS-BUILT AS PER PE 1406 & PROJ. 5259
    re.compile(r'\bAS[-\s]?BUILT\s+AS\s+PER\s+PE\s*\d+\s*&\s*PROJ\.?\s*\d+\b', re.IGNORECASE),
    # AS-BUILT AS PER PROJ./PROJECT NO. 5247 / 0400551 (handles typo "PROJEC")
    re.compile(r'\bAS[-\s]?BUILT\s+AS\s+PER\s+PROJE?C?T?\.?\s*NO\.?\s*[A-Z0-9]+\b', re.IGNORECASE),
    re.compile(r'\bAS[-\s]?BUILT\s+AS\s+PER\s+PROJ(?:ECT)?\.?\s*\d+\b', re.IGNORECASE),
    # AS-BUILT AS PER PD10222
    re.compile(r'\bAS[-\s]?BUILT\s+AS\s+PER\s+P[DE]\s*\d+\b', re.IGNORECASE),
    # Generic "AS-BUILT AS PER ..." — char class excludes '/' so trailing
    # dates like "5/29/2017" don't get swallowed.
    re.compile(r'\bAS[-\s]?BUILT\s+AS\s+PER\s+[A-Z0-9.\s&-]{2,40}\b', re.IGNORECASE),
    # Standalone status phrases, longest first.
    re.compile(r'\bRE[-\s]?ISSUED\s+FOR\s+CONSTRUCTION\b', re.IGNORECASE),
    re.compile(r'\bRE[-\s]?ISSUED\s+FOR\s+APPROVAL\b', re.IGNORECASE),
    re.compile(r'\bRE[-\s]?ISSUED\s+FOR\s+REVIEW\b', re.IGNORECASE),
    re.compile(r'\bRE[-\s]?ISSUED\s+FOR\s+INFORMATION\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+CONSTRUCTION\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+PURCHASE\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+APPROVAL\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+REVIEW\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+COMMENT[S]?\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+INFORMATION\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+DESIGN\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+BID\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+TENDER\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+QUOTATION\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+ENGINEERING\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+HAZOP\b', re.IGNORECASE),
    re.compile(r'\bISSUED\s+FOR\s+IDC\b', re.IGNORECASE),
    re.compile(r'\bAPPROVED\s+FOR\s+CONSTRUCTION\b', re.IGNORECASE),
    re.compile(r'\bAPPROVED\s+FOR\s+DESIGN\b', re.IGNORECASE),
    re.compile(r'\bFOR\s+APPROVAL\b', re.IGNORECASE),
    re.compile(r'\bFOR\s+REVIEW\b', re.IGNORECASE),
    re.compile(r'\bFOR\s+INFORMATION\b', re.IGNORECASE),
    re.compile(r'\bFOR\s+CONSTRUCTION\b', re.IGNORECASE),
    re.compile(r'\bFOR\s+IMPLEMENTATION\b', re.IGNORECASE),
    re.compile(r'\bAS[-\s]?BUILT\b', re.IGNORECASE),
    re.compile(r'\bCANCELLED\b', re.IGNORECASE),
    re.compile(r'\bSUPERS[EI]DED\b', re.IGNORECASE),
    re.compile(r'\bPRELIMINARY\b', re.IGNORECASE),
    re.compile(r'\bDRAFT\b', re.IGNORECASE),
    re.compile(r'\bHOLD\b', re.IGNORECASE),
)

# Acronym → canonical full-form. Reference sample keeps `IFP` as-is, so we
# return the upper-case acronym verbatim when only the acronym is found.
_REVISION_STATUS_ACRONYMS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r'\bIFP\b'), 'IFP'),   # Issued For Purchase
    (re.compile(r'\bIFC\b'), 'IFC'),   # Issued For Construction
    (re.compile(r'\bAFC\b'), 'AFC'),   # Approved For Construction
    (re.compile(r'\bIFA\b'), 'IFA'),   # Issued For Approval
    (re.compile(r'\bIFR\b'), 'IFR'),   # Issued For Review
    (re.compile(r'\bIFI\b'), 'IFI'),   # Issued For Information
    (re.compile(r'\bIFD\b'), 'IFD'),   # Issued For Design
    (re.compile(r'\bAFD\b'), 'AFD'),   # Approved For Design
    (re.compile(r'\bIFB\b'), 'IFB'),   # Issued For Bid
    (re.compile(r'\bIFT\b'), 'IFT'),   # Issued For Tender
    (re.compile(r'\bIFQ\b'), 'IFQ'),   # Issued For Quotation
    (re.compile(r'\bIFE\b'), 'IFE'),   # Issued For Engineering
    (re.compile(r'\bIFH\b'), 'IFH'),   # Issued For HAZOP
    (re.compile(r'\bIDC\b'), 'IDC'),   # Inter-Discipline Check
    (re.compile(r'\bAB\b'), 'AB'),     # As-Built (short)
)

# Title-block tidiness: collapse whitespace, normalise hyphenation, upper-case.
def _clean_status_value(s: str) -> str:
    if not s:
        return ''
    cleaned = re.sub(r'\s+', ' ', s).strip().rstrip('.,;:').upper()
    # Standardise "AS BUILT" → "AS-BUILT" (matches reference Excel).
    cleaned = re.sub(r'\bAS\s+BUILT\b', 'AS-BUILT', cleaned)
    # Standardise "RE ISSUED" → "RE-ISSUED".
    cleaned = re.sub(r'\bRE\s+ISSUED\b', 'RE-ISSUED', cleaned)
    return cleaned


def _extract_revision_status_smart(text: str) -> str:
    """
    Find the document's revision-description / status phrase.

    Strategy:
      1. Long-phrase regex pass (longest first) — captures things like
         "AS-BUILT AS PER PROJ. NO. 0400551" verbatim.
      2. Acronym pass — IFP / AFC / IFC / IFA / IFR.
    Returns '' when nothing recognised.
    """
    if not text:
        return ''
    for pat in _REVISION_STATUS_REGEX_PHRASES:
        m = pat.search(text)
        if m:
            return _clean_status_value(m.group(0))
    for pat, label in _REVISION_STATUS_ACRONYMS:
        if pat.search(text):
            return label
    return ''


# ---------------------------------------------------------------------------
# Smart Unit extractor
# ---------------------------------------------------------------------------
# Reference sample shows numeric units, sometimes multi-unit comma-joined:
#     02, 36, 47,48, 27532, 66, 68,94,95, 72
# Unit codes range from 2-digit standard ("02", "47") through to 5-digit
# project-specific codes ("27532"). Multi-unit titles use either "&" or
# "," as separators ("UNIT 47 & UNIT 48", "UNITS 68, 94, 95").
_UNIT_MIN_DIGITS = 2
_UNIT_MAX_DIGITS = 5
_UNIT_TOKEN_RE   = re.compile(
    rf'\bUNIT[Ss]?[-\s]*([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b',
    re.IGNORECASE,
)
# Extra label variants — applied AFTER `_UNIT_TOKEN_RE` if nothing matched
# the primary pattern. Each must capture the digits in group(1). Soft-coded
# tuple so new vocabulary can be added without touching the extractor body.
_UNIT_EXTRA_LABEL_PATTERNS: Tuple[re.Pattern, ...] = (
    # "UNIT NO: 47", "UNIT NUMBER: 47", "UNIT CODE 47"
    re.compile(rf'\bUNIT[\s_]*(?:NO\.?|NUMBER|CODE|#)[\s_]*[:\-]?[\s_]*'
               rf'([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b',
               re.IGNORECASE),
    # "U-47", "U_47" (ADNOC filename convention)
    re.compile(rf'\bU[-_]([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b',
               re.IGNORECASE),
    # "PLANT 47", "AREA/UNIT 47", "PROCESS UNIT 47"
    re.compile(rf'\bPLANT[\s_]*([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b',
               re.IGNORECASE),
    re.compile(rf'\bPROCESS[\s_]*UNIT[\s_]*'
               rf'([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b',
               re.IGNORECASE),
    # Old / handwritten ADNOC drawings use "Activate-Unit", "Activated Unit",
    # "Active Unit" or "Activity Unit" as the label. OCR sometimes mangles
    # the prefix so we also accept ACTI*, ACTV*. Capture the digit run that
    # follows — alphanumeric suffix (e.g. "7600 L 02") is preserved by the
    # adjacent `_UNIT_ALNUM_TOKEN_RE` scan below.
    re.compile(rf'\bACT(?:IVAT(?:E|ED|ION|ING)?|IVE|IVITY|V)?[-\s_]*UNIT[\s_]*'
               rf'[:\-]?[\s_]*'
               rf'([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b',
               re.IGNORECASE),
)

# ─── ADNOC alphanumeric unit codes ──────────────────────────────────────
# Legacy Habshan / OGD title blocks print the unit as a digit-letter-digit
# compound (e.g. "7600 L 02", "7600L-02", "7300 L 04"). We capture the full
# alphanumeric value as a SECONDARY pass — it never overrides a clean
# numeric unit pulled by `_UNIT_TOKEN_RE`. Soft-coded.
_UNIT_ALNUM_PREFIX_DIGITS = 4
_UNIT_ALNUM_LETTER_RE     = r'[A-Z]'
_UNIT_ALNUM_SUFFIX_DIGITS = 2
_UNIT_ALNUM_TOKEN_RE = re.compile(
    rf'(?<![A-Z0-9])([0-9]{{{_UNIT_ALNUM_PREFIX_DIGITS}}})\s*'
    rf'({_UNIT_ALNUM_LETTER_RE})[\s\-]*'
    rf'([0-9]{{1,{_UNIT_ALNUM_SUFFIX_DIGITS}}})(?![A-Z0-9])',
    re.IGNORECASE,
)
# Label keywords that, when seen within `_UNIT_ALNUM_LABEL_WINDOW` chars
# *before* the alphanumeric token, qualify it as a real unit value.
_UNIT_ALNUM_LABEL_WINDOW = 40
_UNIT_ALNUM_LABEL_RE = re.compile(
    r'\b(?:ACT(?:IVAT(?:E|ED|ION|ING)?|IVE|IVITY|V)?[-\s_]*UNIT|'
    r'UNIT[\s_]*(?:NO\.?|NUMBER|CODE|#)|UNIT|PLANT[\s_]*UNIT)\b',
    re.IGNORECASE,
)
# After a UNIT match, walk the tail for adjacent continuations using either
# "&" or "," as separators. Tolerates "UNIT 47 & 48", "UNITS 68, 94, 95",
# and the looser form "UNIT 68 / 94 / 95".
_UNIT_TAIL_CONT_RE = re.compile(
    rf'[,&/]\s*([0-9]{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})\b'
)
_UNIT_TAIL_MAX_CHARS = 60   # how far past the UNIT token we keep scanning
# Continuation tokens must share the lead-token's digit-width family so a
# stray document number ("…UNITS 47, 48, 55, 1177-XXXX") cannot bleed into
# the unit list. Cap is `max(len(lead), _UNIT_TAIL_MAX_DIGITS)`.
_UNIT_TAIL_MAX_DIGITS = 3

# A real unit code is `_UNIT_MIN_DIGITS`..`_UNIT_MAX_DIGITS` digits (single
# or comma-joined). Anything else (e.g. "ABU DHABI", "Vessel", "Process")
# is OCR / vision noise and gets cleared in the build_row final pass.
_UNIT_VALUE_VALID_RE = re.compile(
    rf'^\d{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}}'
    rf'(?:\s*,\s*\d{{{_UNIT_MIN_DIGITS},{_UNIT_MAX_DIGITS}}})*$'
)

# Secondary validator for ADNOC alphanumeric unit codes ("7600 L 02",
# "7600L 02", "7300L-04"). Used as a fallback when the strict numeric
# validator rejects an otherwise label-anchored value. Soft-coded.
_UNIT_VALUE_ALNUM_VALID_RE = re.compile(
    rf'^\d{{{_UNIT_ALNUM_PREFIX_DIGITS}}}\s*'
    rf'{_UNIT_ALNUM_LETTER_RE}\s*'
    rf'\d{{1,{_UNIT_ALNUM_SUFFIX_DIGITS}}}$',
    re.IGNORECASE,
)

# ─── ADNOC document-number filename prefix → Unit ──────────────────────
# Old QC documents in legacy ADNOC batches (Habshan / Bechtel / Bab) carry
# the unit code as the *first* numeric segment of the filename. Examples
# from `DESIGN CALCULATION (Utility & Offsite's) / QC DOCUMENTS`:
#   "055-17-009.pdf"     → unit 055 → "55"
#   "062-17-016.pdf"     → unit 062 → "62"
#   "096-17-021.pdf"     → unit 096 → "96"
#   "012-95-002_136.pdf" → unit 012 → "12"
#   "02-1540-17-109.pdf" → unit 02   (kept as "02", canonical 2-digit code)
#   "NC-79-18-101.pdf"   → unit 79
# Pattern: an optional 1-3 letter prefix ("NC", "SP", "DC", "U") followed
# by the unit-digit group, then a hyphen+digits continuation. Used ONLY
# when no labelled UNIT match was found in body / title (lowest priority).
_UNIT_FILENAME_PREFIX_MIN_DIGITS = 2
_UNIT_FILENAME_PREFIX_MAX_DIGITS = 3
_UNIT_FILENAME_PREFIX_RE = re.compile(
    rf'^(?:[A-Z]{{1,3}}[-_])?'
    rf'([0-9]{{{_UNIT_FILENAME_PREFIX_MIN_DIGITS},{_UNIT_FILENAME_PREFIX_MAX_DIGITS}}})'
    rf'[-_][0-9]',
    re.IGNORECASE,
)

# ─── JOB NUMBER embedded unit ───────────────────────────────────────────
# Legacy Bechtel calculation sheets format: "JOB NUMBER SP-62-V-002" where
# the digit group between the two hyphens is the unit. We accept the same
# 2-3 digit window and require the surrounding alpha-hyphen / hyphen-alpha
# guards so plain dates like "17-2018" are rejected.
_UNIT_JOBNO_LABEL_WINDOW = 60
_UNIT_JOBNO_LABEL_RE = re.compile(
    r'\bJOB[\s_]*(?:NUMBER|NO\.?|#)?\b',
    re.IGNORECASE,
)
_UNIT_JOBNO_VALUE_RE = re.compile(
    rf'\b[A-Z]{{1,3}}[-_]'
    rf'([0-9]{{{_UNIT_FILENAME_PREFIX_MIN_DIGITS},{_UNIT_FILENAME_PREFIX_MAX_DIGITS}}})'
    rf'[-_][A-Z]',
    re.IGNORECASE,
)


def _is_valid_unit_value(value: str) -> bool:
    """True when `value` is a digit-only single or comma-joined unit code.

    Additionally enforces homogeneity: every comma-joined token must share
    the digit-width family of the first token (cap = max(len(first),
    `_UNIT_TAIL_MAX_DIGITS`)). This rejects upstream noise like
    "47,48,55,1177" where 1177 is a doc-number fragment, while still
    accepting "27532" alone or "68,94,95".

    Also accepts the ADNOC alphanumeric form ("7600 L 02", "7600L-02")
    via `_UNIT_VALUE_ALNUM_VALID_RE` — soft-coded fallback only,
    never weakens the strict numeric check above.
    """
    if not value:
        return False
    v = value.strip()
    if _UNIT_VALUE_ALNUM_VALID_RE.fullmatch(v):
        return True
    if not _UNIT_VALUE_VALID_RE.fullmatch(v):
        return False
    tokens = [t.strip() for t in v.split(',') if t.strip()]
    if not tokens:
        return False
    cap = max(len(tokens[0]), _UNIT_TAIL_MAX_DIGITS)
    return all(len(t) <= cap for t in tokens)


def _extract_unit_smart(text: str, *, title_hint: str = '',
                         relative_path: str = '',
                         file_name: str = '') -> str:
    """
    Smart unit-code extractor. Sources scanned in priority order:
      1. Document title (most reliable)
      2. Body text (first ~_MAX_SCAN_CHARS)
      3. Folder / filename hints

    De-duplicates while preserving order, joins with commas. Strips
    leading zeros for uniformity ('07' → '7') except for the canonical
    "common standards" markers '01' / '02' which are kept verbatim.
    """
    sources: List[str] = [s for s in (title_hint, text, relative_path, file_name) if s]
    seen: set = set()
    units: List[str] = []
    # Priority pass: ADNOC alphanumeric form ("Activate-Unit 7600 L 02") —
    # only when the alphanumeric token follows a UNIT-family label within
    # `_UNIT_ALNUM_LABEL_WINDOW` chars. Wins over the strict-numeric path
    # so we don't truncate "7600 L 02" → "7600". Soft-coded gate.
    for src in sources:
        for m in _UNIT_ALNUM_TOKEN_RE.finditer(src):
            window_start = max(0, m.start() - _UNIT_ALNUM_LABEL_WINDOW)
            preceding = src[window_start:m.start()]
            if not _UNIT_ALNUM_LABEL_RE.search(preceding):
                continue
            return f"{m.group(1)} {m.group(2).upper()} {m.group(3)}"
    for src in sources:
        for m in _UNIT_TOKEN_RE.finditer(src):
            num = m.group(1)
            if num not in seen:
                seen.add(num)
                units.append(num)
            tail = src[m.end():m.end() + _UNIT_TAIL_MAX_CHARS]
            # Continuations must share the lead's digit-width family —
            # caps at max(len(lead), _UNIT_TAIL_MAX_DIGITS) — so unrelated
            # 4-digit tokens (doc numbers, dates, tags) don't bleed in.
            tail_cap = max(len(num), _UNIT_TAIL_MAX_DIGITS)
            for am in _UNIT_TAIL_CONT_RE.finditer(tail):
                a = am.group(1)
                if len(a) > tail_cap:
                    break  # mismatched width → end of unit list
                if a not in seen:
                    seen.add(a)
                    units.append(a)
        if units:
            break  # title hit wins; don't dilute with body matches
    if not units:
        # Fallback pass: try secondary label vocabulary (UNIT NO:, U-47,
        # PLANT 47, PROCESS UNIT 47). These rarely come with multi-unit
        # tails so we only capture the single labelled value per source.
        for src in sources:
            for pat in _UNIT_EXTRA_LABEL_PATTERNS:
                for m in pat.finditer(src):
                    num = m.group(1)
                    if num not in seen:
                        seen.add(num)
                        units.append(num)
            if units:
                break
    if not units:
        # Late fallback: JOB NUMBER embedded unit ("JOB NUMBER SP-62-V-002").
        # Only fire when a JOB label sits within `_UNIT_JOBNO_LABEL_WINDOW`
        # chars BEFORE the alpha-digit-alpha token. Title / body only —
        # filename is handled separately below.
        for src in (title_hint, text):
            if not src:
                continue
            for vm in _UNIT_JOBNO_VALUE_RE.finditer(src):
                window_start = max(0, vm.start() - _UNIT_JOBNO_LABEL_WINDOW)
                if _UNIT_JOBNO_LABEL_RE.search(src[window_start:vm.start()]):
                    num = vm.group(1)
                    if num not in seen:
                        seen.add(num)
                        units.append(num)
                    break
            if units:
                break
    if not units:
        # Last-resort fallback: ADNOC document-number filename prefix
        # ("055-17-009.pdf" → 055). Soft-coded — only triggered when the
        # body / title gave us nothing. Walk `file_name` first so the
        # relative_path's parent folders cannot pollute the match.
        for src in (file_name, relative_path):
            if not src:
                continue
            base = src.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
            fm = _UNIT_FILENAME_PREFIX_RE.match(base)
            if fm:
                num = fm.group(1)
                if num not in seen:
                    seen.add(num)
                    units.append(num)
                break
    if not units:
        # Final fallback: ADNOC alphanumeric form already attempted in the
        # priority pass above; nothing else to try.
        return ''
    out: List[str] = []
    for u in units:
        if len(u) == 2 and u.startswith('0'):
            # Preserve canonical 2-digit common-section markers ('01','02').
            out.append(u)
        else:
            out.append(u.lstrip('0') or '0')
    return ','.join(out)


# ---------------------------------------------------------------------------
# Smart ADNOC Project No. extractor
# ---------------------------------------------------------------------------
# Reference sample shows 3- to 7-digit project codes: 1219, 5231, 5247.
# Strategy: locate the value adjacent to a "PROJECT NO" / "ADNOC PROJECT" /
# "P.O. NO" label. We deliberately accept a range of digit-lengths because
# real ADNOC project numbers vary (4-digit modern, 7-digit legacy "0400551").
_ADNOC_PROJECT_LABEL_PATTERNS: Tuple[re.Pattern, ...] = (
    # `[\s_]*` between label words tolerates filename underscores
    # ("ADNOC_PROJECT_5231_doc.pdf"). Trailing `(?!\d)` is used in place
    # of `\b` because `\b` does not fire between a digit and an
    # underscore (both are word chars in regex).
    re.compile(r'ADNOC[\s_]*PROJECT[\s_]*(?:NO\.?|NUMBER|#)?[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
    re.compile(r'PROJECT[\s_]*(?:NO\.?|NUMBER|#)[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
    re.compile(r'\bP\.?[\s_]*O\.?[\s_]*NO\.?[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
    re.compile(r'\bPROJ\.?[\s_]*NO\.?[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
    re.compile(r'\bJOB[\s_]*(?:NO\.?|NUMBER|#)?[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
    re.compile(r'\bWO[\s_]*(?:NO\.?|NUMBER|#)?[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
    re.compile(r'\bCONTRACT[\s_]*(?:NO\.?|NUMBER|#)?[\s_]*[:\-]?[\s_]*([0-9]{3,7})(?!\d)', re.IGNORECASE),
)

# Reject digit groups that are clearly *not* a project number (e.g. years
# 1900-2099, two-digit junk, page numbers, document-number fragments).
_ADNOC_PROJECT_BAD_RANGE = re.compile(r'^(?:19|20)\d{2}$')


def _extract_adnoc_project_smart(text: str, *, title_hint: str = '',
                                   file_name: str = '',
                                   relative_path: str = '') -> str:
    """
    Find the document's ADNOC project number.

    Sources scanned (first hit wins):
      1. Body text — labelled patterns ("ADNOC PROJECT NO 1219").
      2. Document title — labelled patterns.
      3. Filename / relative path — labelled patterns.

    Returns '' when nothing labelled is found.
    """
    sources = [s for s in (text, title_hint, file_name, relative_path) if s]
    for src in sources:
        for pat in _ADNOC_PROJECT_LABEL_PATTERNS:
            for m in pat.finditer(src):
                tok = m.group(1)
                if _ADNOC_PROJECT_BAD_RANGE.fullmatch(tok):
                    continue
                if 3 <= len(tok) <= 7:
                    return tok.lstrip('0') or tok
    return ''


def _is_valid_adnoc_project_value(value: str) -> bool:
    """True when `value` is a 3-7 digit token that isn't a 4-digit year."""
    if not value:
        return False
    v = value.strip()
    if not v.isdigit():
        return False
    if not (3 <= len(v) <= 7):
        return False
    if _ADNOC_PROJECT_BAD_RANGE.fullmatch(v):
        return False
    return True


# ---------------------------------------------------------------------------
# Smart Project Title / Name extractor
# ---------------------------------------------------------------------------
# Reference sample shows ADNOC project names always written in upper-case
# and mostly drawn from a small catalogue. Strategy:
#   1. Match a soft-coded canonical phrase (longest first) — these win
#      because real OCR often inserts noise around the words.
#   2. Otherwise fall back to a "PROJECT TITLE: …" / "PROJECT NAME: …"
#      label scan.
# Adding a new phrase is a single-line config change.
_PROJECT_TITLE_CANONICAL_PHRASES: Tuple[str, ...] = (
    # Order: most specific first so e.g. "(OGD) PROJECT PHASE II" wins
    # before the bare "ONSHORE GAS DEVELOPMENT PROJECT" prefix matches.
    'ONSHORE GAS DEVELOPMENT PROJECT PHASE-II',
    'ONSHORE GAS DEVELOPMENT PROJECT PHASE II',
    'ONSHORE GAS DEVELOPMENT (OGD) PROJECT PHASE II',
    'ONSHORE GAS DEVELOPMENT (OGD) PROJECT PHASE-II',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE IIB)',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT PHASE IIB',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE II)',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT',
    'HABSHAN-5 UTILITIES AND OFFSITES PROJECT',
    'HABSHAN 5 UTILITIES AND OFFSITES PROJECT',
    'HABSHAN-4 UTILITIES AND OFFSITES PROJECT',
    'HABSHAN UTILITIES AND OFFSITES PROJECT',
    'BAB FIELD DEVELOPMENT PROJECT',
    'BAB COMPRESSION PROJECT',
    'ASAB FULL FIELD DEVELOPMENT PROJECT',
    # Generic / fallback families — kept after specific names so they only
    # fire when no longer-form match was present.
    'ONSHORE GAS DEVELOPMENT PROJECT',
    'HABSHAN DEVELOPMENT PROJECT',
)

# Build a single regex with all canonical phrases — longest first wins.
def _build_project_title_canonical_re() -> re.Pattern:
    phrases = sorted(_PROJECT_TITLE_CANONICAL_PHRASES, key=len, reverse=True)
    # Tolerate variable inner whitespace (`\s+` between every word) and
    # OCR-substituted hyphens/dashes (`[\-\s]?`).
    parts: List[str] = []
    for ph in phrases:
        # Escape, then loosen runs of spaces and hyphens.
        esc = re.escape(ph)
        esc = esc.replace(r'\ ', r'\s+')
        esc = esc.replace(r'\-', r'[\-\s]')
        parts.append(esc)
    return re.compile(r'(' + '|'.join(parts) + r')', re.IGNORECASE)

_PROJECT_TITLE_CANONICAL_RE = _build_project_title_canonical_re()

# Labelled "PROJECT TITLE:" / "PROJECT NAME:" fallback.
_PROJECT_TITLE_LABEL_RE = re.compile(
    r'\bPROJECT\s+(?:TITLE|NAME)\s*[:\-]?\s*'
    r'([A-Z0-9][A-Z0-9 &,\-/()]{5,90})',
    re.IGNORECASE,
)

# Soft-coded canonical normalisation map. Whatever the source matched
# (lower-case, hyphen variants, missing parentheses), we collapse it to
# the canonical reference form. Edit this single dict to retune output.
_PROJECT_TITLE_CANONICAL_MAP: Dict[str, str] = {
    'ONSHORE GAS DEVELOPMENT PROJECT PHASE-II':
        'ONSHORE GAS DEVELOPMENT PROJECT PHASE-II',
    'ONSHORE GAS DEVELOPMENT PROJECT PHASE II':
        'ONSHORE GAS DEVELOPMENT PROJECT PHASE-II',
    'ONSHORE GAS DEVELOPMENT (OGD) PROJECT PHASE II':
        'ONSHORE GAS DEVELOPMENT (OGD) PROJECT PHASE II',
    'ONSHORE GAS DEVELOPMENT (OGD) PROJECT PHASE-II':
        'ONSHORE GAS DEVELOPMENT (OGD) PROJECT PHASE II',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE IIB)':
        'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE IIB)',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT PHASE IIB':
        'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE IIB)',
    'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE II)':
        'HABSHAN CAPACITY ENHANCEMENT PROJECT (PHASE II)',
    'HABSHAN-5 UTILITIES AND OFFSITES PROJECT':
        'HABSHAN-5 UTILITIES AND OFFSITES PROJECT',
    'HABSHAN 5 UTILITIES AND OFFSITES PROJECT':
        'HABSHAN-5 UTILITIES AND OFFSITES PROJECT',
}


def _canonicalise_project_title(raw: str) -> str:
    """Snap an extracted project title to the canonical reference form."""
    if not raw:
        return ''
    # Collapse internal whitespace and uppercase for the lookup.
    folded = re.sub(r'\s+', ' ', raw).strip().upper()
    # Direct hit on the map.
    if folded in _PROJECT_TITLE_CANONICAL_MAP:
        return _PROJECT_TITLE_CANONICAL_MAP[folded]
    # Hyphen / no-hyphen tolerance: try with all hyphens turned into spaces.
    nohyphen = folded.replace('-', ' ').replace('  ', ' ')
    for k, v in _PROJECT_TITLE_CANONICAL_MAP.items():
        if k.replace('-', ' ').replace('  ', ' ') == nohyphen:
            return v
    return folded  # already a known canonical phrase from the regex


def _is_valid_project_title(value: str) -> bool:
    """True when `value` is non-trivial and doesn't look like junk."""
    if not value:
        return False
    v = value.strip()
    if len(v) < 8:
        return False
    if v.upper() in {'NA', 'N/A', 'TBD', 'NONE', 'NULL'}:
        return False
    # Must contain the word PROJECT or DEVELOPMENT to count as a title.
    return bool(re.search(r'\b(?:PROJECT|DEVELOPMENT|FIELD)\b', v, re.IGNORECASE))


def _extract_project_title_smart(text: str, *, title_hint: str = '',
                                   relative_path: str = '',
                                   file_name: str = '') -> str:
    """
    Find the document's project title / name.

    Sources scanned (first hit wins):
      1. Body text — canonical phrase match.
      2. Body text — "PROJECT TITLE:" / "PROJECT NAME:" labelled fallback.
      3. Document title hint, then path / filename.

    All hits are funnelled through `_canonicalise_project_title` so the
    column shows the same wording across the whole batch.
    """
    sources = [s for s in (text, title_hint, file_name, relative_path) if s]
    # Pass 1: canonical-phrase match across all sources.
    for src in sources:
        m = _PROJECT_TITLE_CANONICAL_RE.search(src)
        if m:
            return _canonicalise_project_title(m.group(1))
    # Pass 2: labelled fallback on body text + title only (filenames and
    # paths rarely contain the literal "PROJECT TITLE:" label).
    for src in (text, title_hint):
        if not src:
            continue
        m = _PROJECT_TITLE_LABEL_RE.search(src)
        if m:
            cand = _canonicalise_project_title(m.group(1))
            if _is_valid_project_title(cand):
                return cand
    return ''


# ---------------------------------------------------------------------------
# Smart Document-Control reference extractors
# (Contractor Doc Ref, Vendor Doc Ref, From/Originator,
#  Agreement Number, Agreement Description)
# ---------------------------------------------------------------------------
# Soft-coded label vocabularies. Add a synonym to the tuple — no other
# change is needed. Labels are matched case-insensitively with optional
# punctuation between words, and the value capture is bounded so OCR
# overflow can't drag a whole paragraph in.
_DOCREF_VALUE_SHAPE = r'[A-Z0-9][A-Z0-9 \-/_().,&]{2,80}'

# Contractor Doc Reference — vendor side never owns this.
_CONTRACTOR_REF_LABELS: Tuple[str, ...] = (
    'CONTRACTOR DOC REFERENCE NUMBER', 'CONTRACTOR DOC REF NO',
    'CONTRACTOR DOCUMENT REFERENCE', 'CONTRACTOR DOC NO',
    'CONTRACTOR DOCUMENT NO', 'CONTRACTOR REF NO', 'CONTRACTOR REF',
    'CONTRACTOR DOC NUMBER', 'EPC DOC NO', 'EPC DOCUMENT NO',
    'MAIN CONTRACTOR DOC NO', 'CTR DOC NO',
)

# Vendor Doc Reference.
_VENDOR_REF_LABELS: Tuple[str, ...] = (
    'VENDOR DOC REFERENCE NUMBER', 'VENDOR DOC REF NO',
    'VENDOR DOCUMENT REFERENCE', 'VENDOR DOC NO', 'VENDOR DOCUMENT NO',
    'VENDOR REF NO', 'VENDOR REF', 'SUPPLIER DOC NO',
    'SUPPLIER DOCUMENT NO', 'MANUFACTURER DOC NO', 'MFR DOC NO',
)

# From / Originator.
_ORIGINATOR_LABELS: Tuple[str, ...] = (
    'FROM/ORIGINATOR', 'ORIGINATOR', 'FROM', 'ISSUED BY',
    'PREPARED BY', 'AUTHORED BY', 'ORIGINATING COMPANY',
    'ORIGINATING ORG', 'ORIGINATING ORGANISATION',
    'ORIGINATING ORGANIZATION',
)

# Agreement Number.
_AGREEMENT_NO_LABELS: Tuple[str, ...] = (
    'AGREEMENT NUMBER', 'AGREEMENT NO', 'AGREEMENT REF',
    'AGREEMENT REFERENCE', 'CONTRACT NUMBER', 'CONTRACT NO',
    'CONTRACT REF', 'CONTRACT ID', 'AGREEMENT ID',
    'FRAME AGREEMENT', 'FRAMEWORK AGREEMENT',
)

# Agreement Description / Title.
_AGREEMENT_DESC_LABELS: Tuple[str, ...] = (
    'AGREEMENT DESCRIPTION', 'AGREEMENT TITLE', 'AGREEMENT NAME',
    'AGREEMENT SUBJECT', 'CONTRACT DESCRIPTION', 'CONTRACT TITLE',
    'CONTRACT NAME', 'CONTRACT SUBJECT', 'CONTRACT SCOPE',
    'AGREEMENT SCOPE', 'SCOPE OF WORK', 'AGREEMENT FOR',
)

# Originator value shape: 2-12 word company-ish names. Allows ampersand,
# hyphens, parens (e.g. "ADNOC ONSHORE", "BAKER HUGHES (GE)", "WOOD PLC").
_ORIGINATOR_VALUE_SHAPE = r'[A-Z][A-Z0-9 &.,\-/()\']{2,80}'

# Agreement description value shape — allows longer phrases.
_AGREEMENT_DESC_VALUE_SHAPE = r'[A-Z0-9][A-Z0-9 &.,\-/()\']{3,140}'


def _build_label_regex(labels: Tuple[str, ...], value_shape: str) -> re.Pattern:
    """Compile one regex that matches any of `labels` followed by `value_shape`.

    Whitespace/underscore between label words is tolerated. Trailing
    optional separators (`:`, `-`, `=`) absorb varied punctuation. Longest
    label first prevents a shorter prefix swallowing a longer match.
    """
    sorted_labels = sorted(labels, key=len, reverse=True)
    label_alts = '|'.join(
        re.escape(lbl).replace(r'\ ', r'[\s_]+').replace('/', r'[/\s_]+')
        for lbl in sorted_labels
    )
    pat = (
        r'\b(?:' + label_alts + r')\b'
        r'\s*(?:NO\.?|NUMBER|#)?'
        r'\s*[:\-=.]?\s*'
        r'(' + value_shape + r')'
    )
    return re.compile(pat, re.IGNORECASE)


_CONTRACTOR_REF_RE  = _build_label_regex(_CONTRACTOR_REF_LABELS,  _DOCREF_VALUE_SHAPE)
_VENDOR_REF_RE      = _build_label_regex(_VENDOR_REF_LABELS,      _DOCREF_VALUE_SHAPE)
_ORIGINATOR_RE      = _build_label_regex(_ORIGINATOR_LABELS,      _ORIGINATOR_VALUE_SHAPE)
_AGREEMENT_NO_RE    = _build_label_regex(_AGREEMENT_NO_LABELS,    _DOCREF_VALUE_SHAPE)
_AGREEMENT_DESC_RE  = _build_label_regex(_AGREEMENT_DESC_LABELS,  _AGREEMENT_DESC_VALUE_SHAPE)

# Trim trailing words that almost always follow the real value as part of
# the *next* label (e.g. "VENDOR DOC NO ABC-001 REVISION B" → "ABC-001").
_DOCREF_TAIL_STOP_WORDS: Tuple[str, ...] = (
    'REVISION', 'REV', 'DATE', 'DOC TYPE', 'DOCUMENT TYPE', 'STATUS',
    'PAGE', 'SHEET', 'TITLE', 'VENDOR', 'CONTRACTOR', 'AGREEMENT',
    'CONTRACT', 'PROJECT', 'UNIT', 'AREA', 'CLASS', 'SIZE', 'FROM',
    'TO', 'ISSUED', 'PREPARED', 'CHECKED', 'APPROVED',
)
_DOCREF_TAIL_STOP_RE = re.compile(
    r'\s+(?:' + '|'.join(re.escape(w) for w in _DOCREF_TAIL_STOP_WORDS) + r')\b.*$',
    re.IGNORECASE,
)


def _clean_docref_value(raw: str) -> str:
    """Trim trailing label-noise and surrounding punctuation."""
    if not raw:
        return ''
    v = _DOCREF_TAIL_STOP_RE.sub('', raw).strip()
    # Strip trailing punctuation / dangling separators.
    v = re.sub(r'[\s,;:\-/]+$', '', v).strip()
    return v


# Validators — soft-coded thresholds tunable in one place.
_DOCREF_MIN_LEN = 4
_DOCREF_MAX_LEN = 80
_AGREEMENT_DESC_MIN_LEN = 5
_AGREEMENT_DESC_MAX_LEN = 140
_DOCREF_JUNK_VALUES = {'NA', 'N/A', 'TBD', 'NONE', 'NULL', '-', '--'}


def _is_valid_docref(value: str) -> bool:
    """A doc-ref must be 4-80 chars and contain at least one alnum block."""
    if not value:
        return False
    v = value.strip()
    if v.upper() in _DOCREF_JUNK_VALUES:
        return False
    if not (_DOCREF_MIN_LEN <= len(v) <= _DOCREF_MAX_LEN):
        return False
    # Must contain at least one digit OR a hyphen-joined token (common
    # in document-control numbering schemes).
    return bool(re.search(r'[A-Z0-9]', v, re.IGNORECASE))


def _is_valid_originator(value: str) -> bool:
    """Originator: 2..80 chars, must contain a letter, no junk."""
    if not value:
        return False
    v = value.strip()
    if v.upper() in _DOCREF_JUNK_VALUES:
        return False
    if not (2 <= len(v) <= 80):
        return False
    return bool(re.search(r'[A-Z]', v, re.IGNORECASE))


def _is_valid_agreement_desc(value: str) -> bool:
    """Agreement description: 5..140 chars, must contain a letter."""
    if not value:
        return False
    v = value.strip()
    if v.upper() in _DOCREF_JUNK_VALUES:
        return False
    if not (_AGREEMENT_DESC_MIN_LEN <= len(v) <= _AGREEMENT_DESC_MAX_LEN):
        return False
    return bool(re.search(r'[A-Z]', v, re.IGNORECASE))


def _extract_label_value(regex: re.Pattern, *sources: str) -> str:
    """First-hit-wins scan across `sources` using the compiled label regex."""
    for src in sources:
        if not src:
            continue
        m = regex.search(src)
        if m:
            return _clean_docref_value(m.group(1))
    return ''


# ─── Drawing-Number aliases for Contractor Doc Reference ───────────────
# Old / handwritten ADNOC documents almost never carry the literal
# "CONTRACTOR DOC REFERENCE NUMBER" label — instead the title block uses
# "DRAWING NUMBER", "DRG NO", "DWG NO", "DOC NO" or omits the label
# entirely (the value sits on its own line). Soft-coded so a new synonym
# is a one-line addition.
_DRAWING_NUMBER_LABELS: Tuple[str, ...] = (
    'DRAWING NUMBER', 'DRAWING NO', 'DRAWING #',
    'DRG NUMBER', 'DRG NO', 'DRG #',
    'DWG NUMBER', 'DWG NO', 'DWG #',
    'DOCUMENT NUMBER', 'DOCUMENT NO', 'DOC NUMBER', 'DOC NO',
    'DRAWING / DOC NO', 'DOC / DRAWING NO', 'D.W.G. NO', 'D.R.G. NO',
)
_DRAWING_NUMBER_RE = _build_label_regex(_DRAWING_NUMBER_LABELS, _DOCREF_VALUE_SHAPE)

# ─── Multi-value continuation tail ─────────────────────────────────────
# Legacy ADNOC drawing-number lists appear comma- or semicolon-separated:
#   "NC-26-13-001, NC-26-13-002, NC-26-13-003"
# Walk the tail starting at the first hit and pick up tokens that share
# the same family (alpha-prefix + hyphen-segmented digits). Soft-coded
# digit/segment caps.
_DOCREF_MULTI_TAIL_WINDOW = 200          # how far past the first hit to scan
_DOCREF_MULTI_TOKEN_RE = re.compile(
    r'[,;/]\s*([A-Z]{1,4}(?:[-_][A-Z0-9]{1,6}){1,6}[A-Z0-9]?'
    r'|\d{2,5}(?:[-_][A-Z0-9]{1,6}){1,6})',
    re.IGNORECASE,
)
_DOCREF_MULTI_MAX_TOKENS = 20            # safety cap

# ─── ADNOC drawing-number filename shape ───────────────────────────────
# Old QC documents carry the drawing number AS the filename:
#   "NC-26-13-001.pdf"      → "NC-26-13-001"
#   "055-17-009.pdf"        → "055-17-009"
#   "02-1540-17-109.pdf"    → "02-1540-17-109"
#   "012-95-002_136.pdf"    → "012-95-002" (trailing _136 = sheet/page suffix)
# Pattern: optional 1-3 letter alpha prefix, then a digit-hyphen chain of
# 2..5 segments. Trailing `_NNN` / `.SHT` suffixes are stripped by the
# cleaner. Soft-coded — adjust segment count by changing `{2,5}`.
_DRAWING_FNAME_MIN_SEGMENTS = 2
_DRAWING_FNAME_MAX_SEGMENTS = 5
_DRAWING_FNAME_SHAPE_RE = re.compile(
    rf'^(?:[A-Z]{{1,3}}[-_])?'
    rf'(?:[0-9]{{1,5}}|[A-Z]{{1,4}}[0-9]+)'
    rf'(?:[-_][A-Z0-9]{{1,6}}){{{_DRAWING_FNAME_MIN_SEGMENTS-1},{_DRAWING_FNAME_MAX_SEGMENTS-1}}}',
    re.IGNORECASE,
)
# Trailing filename suffixes that are NOT part of the drawing number.
# Only strips explicit sheet/page/rev markers OR underscore-joined trailing
# digit blocks (legacy ADNOC sheet suffix convention: "012-95-002_136").
# Hyphen-joined digit segments are KEPT — they are part of the drawing
# number itself (e.g. "NC-26-13-001" must not lose "-001").
_DRAWING_FNAME_TAIL_SUFFIX_RE = re.compile(
    r'(?:_\d{1,4}'                          # underscore + 1-4 digits (e.g. "_136")
    r'|[_-](?:SH|SHT|SHEET|PG|PAGE|REV)\d{1,4}'  # explicit sheet markers
    r'|[_-]R\d{1,2}'                        # rev suffix
    r'|[_-]V\d{1,2})$',                     # version suffix
    re.IGNORECASE,
)


def _extract_contractor_ref_smart(text: str, *, title_hint: str = '',
                                    file_name: str = '',
                                    relative_path: str = '') -> str:
    """
    Contractor Doc Reference (a.k.a. Drawing Number) extractor.

    Priority chain (first non-empty wins):
      1. Explicit CONTRACTOR DOC REFERENCE label (body / title).
      2. DRAWING NUMBER / DRG NO / DWG NO / DOC NO label (body / title).
      3. Filename-shape fallback for ADNOC drawing-number filenames
         ("NC-26-13-001.pdf" → "NC-26-13-001"). Used only when no label
         match was found in body / title.

    After picking the lead value, walks a `_DOCREF_MULTI_TAIL_WINDOW`
    window past the hit and appends comma-separated continuations of the
    same family ("NC-26-13-001, NC-26-13-002, NC-26-13-003").
    """
    lead = ''
    lead_src = ''
    lead_end = 0
    # Pass 1 — explicit Contractor Doc Reference label.
    for src in (text, title_hint):
        if not src:
            continue
        m = _CONTRACTOR_REF_RE.search(src)
        if m:
            cand = _clean_docref_value(m.group(1))
            if _is_valid_docref(cand):
                lead, lead_src, lead_end = cand, src, m.end()
                break
    # Pass 2 — Drawing Number / Doc Number label.
    if not lead:
        for src in (text, title_hint):
            if not src:
                continue
            m = _DRAWING_NUMBER_RE.search(src)
            if m:
                cand = _clean_docref_value(m.group(1))
                if _is_valid_docref(cand):
                    lead, lead_src, lead_end = cand, src, m.end()
                    break
    # Pass 3 — filename-shape fallback (ADNOC drawing numbers as the
    # bare filename). Strip extension + sheet/page suffix.
    if not lead and file_name:
        base = os.path.splitext(os.path.basename(file_name))[0]
        fm = _DRAWING_FNAME_SHAPE_RE.match(base)
        if fm:
            cand = _DRAWING_FNAME_TAIL_SUFFIX_RE.sub('', fm.group(0)).strip()
            if _is_valid_docref(cand):
                return cand   # filename never has multi-value tail
    if not lead:
        return ''
    # Multi-value tail walk — only when a body/title lead was found.
    tail = lead_src[lead_end:lead_end + _DOCREF_MULTI_TAIL_WINDOW]
    extras: List[str] = []
    seen = {lead.upper()}
    for tm in _DOCREF_MULTI_TOKEN_RE.finditer(tail):
        extra = _clean_docref_value(tm.group(1))
        if extra and extra.upper() not in seen and _is_valid_docref(extra):
            seen.add(extra.upper())
            extras.append(extra)
            if len(extras) >= _DOCREF_MULTI_MAX_TOKENS:
                break
    return ', '.join([lead, *extras]) if extras else lead


def _extract_vendor_ref_smart(text: str, *, title_hint: str = '',
                                file_name: str = '',
                                relative_path: str = '') -> str:
    val = _extract_label_value(_VENDOR_REF_RE, text, title_hint, file_name, relative_path)
    return val if _is_valid_docref(val) else ''


def _extract_originator_smart(text: str, *, title_hint: str = '',
                                file_name: str = '',
                                relative_path: str = '') -> str:
    val = _extract_label_value(_ORIGINATOR_RE, text, title_hint, file_name, relative_path)
    return val if _is_valid_originator(val) else ''


def _extract_agreement_no_smart(text: str, *, title_hint: str = '',
                                  file_name: str = '',
                                  relative_path: str = '') -> str:
    val = _extract_label_value(_AGREEMENT_NO_RE, text, title_hint, file_name, relative_path)
    return val if _is_valid_docref(val) else ''


def _extract_agreement_desc_smart(text: str, *, title_hint: str = '',
                                    file_name: str = '',
                                    relative_path: str = '') -> str:
    val = _extract_label_value(_AGREEMENT_DESC_RE, text, title_hint, file_name, relative_path)
    return val if _is_valid_agreement_desc(val) else ''


# ---------------------------------------------------------------------------
# Smart Tag extractor
# ---------------------------------------------------------------------------
# Reference sample shows multiple tag shapes per drawing. The current
# `equipment_tag` extractor only catches the legacy "P-101" form, leaving
# 70-90% of real tags unextracted. Soft-coded pattern table below — every
# pattern is a `(name, regex)` pair so logging / debugging stays easy and
# adding a new family is a one-line config change.
_TAG_PATTERN_DEFS: Tuple[Tuple[str, str], ...] = (
    # 1. Long XV (shutdown valve) tags — up to five hyphenated segments.
    #    e.g. "XV-5711N-11030-2201-001", "XV-5713N-11051A-2716-001"
    ('xv_long', r'\bXV-[A-Z0-9]{2,8}-[A-Z0-9]{2,8}-[A-Z0-9]{2,8}(?:-[A-Z0-9]{1,5})?\b'),
    # 2. Short XV tag — "XV-5718R-57C102A-01"
    ('xv_short', r'\bXV-[A-Z0-9]{3,8}-[A-Z0-9]{3,10}-[A-Z0-9]{1,4}\b'),
    # 3. Compact alpha-numeric module tag — "57VS001", "57VS010", "12FT201A"
    ('compact_module', r'\b\d{1,3}[A-Z]{2,4}\d{2,5}[A-Z]?\b'),
    # 4. Numeric-prefix equipment tag (3-segment, optional 4th) —
    #    "36-P-701A", "47-G-112", "66-ME-701", "200-ME-101", "113-T-004",
    #    "532-V-123", "549-V-501", "166-ME-701", "166-ME-701-E01"
    ('num_prefix_eq', r'\b\d{2,3}-[A-Z]{1,4}-\d{2,5}[A-Z]?(?:-[A-Z0-9]{1,5})?\b'),
    # 5. Material / stress spec — "SS-G-170-30", "SS-G-425-42"
    ('material_spec', r'\b[A-Z]{2,4}-[A-Z]{1,2}-\d{2,4}-\d{1,3}\b'),
    # 6. Numeric range tag — "57-0001", "57-0089"
    ('numeric_pair', r'\b\d{2,3}-\d{4,5}\b'),
    # 7. Legacy 2-segment equipment — "P-101", "V-201", "HE-4002"
    ('legacy_eq', r'\b[A-Z]{1,4}-\d{3,5}[A-Z]?\b'),
    # 8. Instrument tag with letter prefix — "FIC-101", "PT-2001A"
    ('instrument', r'\b[A-Z]{2,4}[-_]\d{2,5}[A-Z]?\b'),
)
_TAG_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pat)) for name, pat in _TAG_PATTERN_DEFS
)

# Tokens to drop because they're almost always false positives.
_TAG_BLOCKLIST: frozenset = frozenset({
    # Bare 4-digit years from numeric_pair / instrument when length matches.
    # (Year guard kicks in via the regex digit ranges, but this keeps the
    # dedup list clean if a vendor doc-no fragment leaks in.)
    'NA', 'N/A', 'TBD', 'NONE',
})

# Stop words that, if a candidate equals or starts a known label, we drop.
_TAG_STOPWORD_PREFIXES: Tuple[str, ...] = (
    'REV-', 'REVISION-', 'PAGE-', 'SHEET-', 'DATE-',
)

# Minimum/maximum length of any captured tag — final guard.
_TAG_MIN_LEN = 4
_TAG_MAX_LEN = 40
# Maximum number of distinct tags to return per row (keeps Excel cells sane).
_TAG_MAX_PER_ROW = 200


def _is_valid_tag_token(tok: str) -> bool:
    if not tok:
        return False
    t = tok.strip()
    if not (_TAG_MIN_LEN <= len(t) <= _TAG_MAX_LEN):
        return False
    up = t.upper()
    if up in _TAG_BLOCKLIST:
        return False
    if any(up.startswith(p) for p in _TAG_STOPWORD_PREFIXES):
        return False
    # Must contain at least one digit AND at least one letter, OR be a
    # pure numeric pair like "57-0001". Rejects bare words/years.
    has_digit = any(c.isdigit() for c in t)
    has_alpha = any(c.isalpha() for c in t)
    if has_digit and has_alpha:
        return True
    if has_digit and '-' in t and not has_alpha:
        return True   # "57-0001"
    return False


def _extract_tags_smart(text: str, *, title_hint: str = '',
                          file_name: str = '',
                          relative_path: str = '') -> str:
    """
    Extract every tag-shaped token from `text` (plus title/filename hints).

    Returns a comma-joined, order-preserving deduplicated list capped at
    `_TAG_MAX_PER_ROW`. Empty string when nothing matched.
    """
    sources: List[str] = [s for s in (text, title_hint, file_name, relative_path) if s]
    seen: set = set()
    tags: List[str] = []
    for src in sources:
        # Uppercase a copy for case-insensitive matching but keep original
        # tokens (most tags are already upper-case in source documents).
        upper = src.upper()
        for _name, regex in _TAG_PATTERNS:
            for m in regex.finditer(upper):
                tok = m.group(0).strip()
                if not _is_valid_tag_token(tok):
                    continue
                if tok in seen:
                    continue
                seen.add(tok)
                tags.append(tok)
                if len(tags) >= _TAG_MAX_PER_ROW:
                    break
            if len(tags) >= _TAG_MAX_PER_ROW:
                break
    # Substring-overlap suppression: drop any tag that is a strict substring
    # of another captured tag (e.g. "XV-5712N" inside "XV-5712N-61010-2502-001",
    # "P-101" inside "47-P-101A"). Preserves first-seen ordering.
    filtered: List[str] = []
    for t in tags:
        if any(t != other and t in other for other in tags):
            continue
        filtered.append(t)
    return ','.join(filtered[:_TAG_MAX_PER_ROW])


# ---------------------------------------------------------------------------
# Smart Plant extractor
# ---------------------------------------------------------------------------
# Reference sample shows three distinct Plant labels keyed on filename
# family — they are NOT collapsed to a single canonical form because the
# downstream client preserves the original document-control variant:
#   NM-*.pdf  → "HABSHAN-II"          (uppercase Roman numeral form)
#   STC-*.pdf → "HABSHAN - 2"         (spaced-arabic form)
#   <numeric prefix>.pdf → "HABSHAN-2" (compact-arabic form)
# The filename-prefix table below is the primary source of truth.
# Body-text scan is only used as a fallback when no filename prefix matches
# — in that case we fall back to the compact-arabic form for HABSHAN.
_PLANT_BY_FILENAME_PREFIX: Tuple[Tuple[re.Pattern, str], ...] = (
    # NM- family — Roman numeral form per reference rows 1-2.
    (re.compile(r'^\s*NM[\- ]', re.I),     'HABSHAN-II'),
    # STC- family — spaced-arabic form per reference rows 3-7.
    (re.compile(r'^\s*STC[\- ]', re.I),    'HABSHAN - 2'),
)

# Body / title / path keyword scan. Used only when the filename-prefix
# table above does not match. Edit one line to extend.
_PLANT_KEYWORD_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    # HABSHAN family — phase II / 2 variants. Default to compact-arabic
    # because the bulk of reference rows (numeric-prefixed filenames)
    # use that form.
    (re.compile(r'\bHABSHAN[\s\-]*(?:II|2|PHASE[\s\-]*(?:II|2))\b', re.I),
        'HABSHAN-2'),
    (re.compile(r'\bHABSHAN[\s\-]*5\b', re.I),                'HABSHAN-5'),
    (re.compile(r'\bHABSHAN[\s\-]*4\b', re.I),                'HABSHAN-4'),
    (re.compile(r'\bHABSHAN\b', re.I),                        'HABSHAN'),
    (re.compile(r'\bBAB\b(?![A-Z])', re.I),                   'BAB'),
    (re.compile(r'\bASAB\b', re.I),                           'ASAB'),
    (re.compile(r'\bRUWAIS\b', re.I),                         'RUWAIS'),
    (re.compile(r'\bDAS[\s\-]*ISLAND\b', re.I),               'DAS ISLAND'),
    (re.compile(r'\bDALMA\b', re.I),                          'DALMA'),
    (re.compile(r'\bGASCO\b', re.I),                          'GASCO'),
    (re.compile(r'\bBU[\s\-]*HASA\b', re.I),                  'BU HASA'),
    (re.compile(r'\bSHAH[\s\-]*GAS\b', re.I),                 'SHAH'),
    (re.compile(r'\bSAS\b(?![A-Z])', re.I),                   'SAS'),
)

# Project-number → plant-family canonical mapping (used as a fallback
# when keyword scan finds nothing). Edit one line to extend.
_PLANT_BY_ADNOC_PROJECT_NO: Dict[str, str] = {
    '1219': 'HABSHAN-2',
    '5231': 'HABSHAN',
    '5247': 'HABSHAN-5',
    '5259': 'HABSHAN-2',
}

# "PLANT:" / "FACILITY:" labelled fallback.
_PLANT_LABEL_RE = re.compile(
    r'\b(?:PLANT|FACILITY|SITE)\s*[:\-]\s*'
    r'([A-Z][A-Z0-9 \-/()]{2,40})',
    re.IGNORECASE,
)

_PLANT_VALID_MIN_LEN = 2
_PLANT_VALID_MAX_LEN = 40
_PLANT_JUNK_VALUES = {'NA', 'N/A', 'TBD', 'NONE', 'NULL', '-'}


def _is_valid_plant(value: str) -> bool:
    if not value:
        return False
    v = value.strip()
    if v.upper() in _PLANT_JUNK_VALUES:
        return False
    if not (_PLANT_VALID_MIN_LEN <= len(v) <= _PLANT_VALID_MAX_LEN):
        return False
    return bool(re.search(r'[A-Z]', v, re.IGNORECASE))


def _extract_plant_smart(text: str, *, title_hint: str = '',
                          file_name: str = '',
                          relative_path: str = '',
                          adnoc_project_no: str = '') -> str:
    """
    Plant scan order:
      1. Filename-prefix table (`_PLANT_BY_FILENAME_PREFIX`) — preserves
         the document-control variant per family (NM-*=HABSHAN-II,
         STC-*=HABSHAN - 2).
      2. Keyword pattern (canonical map) over text + title + filename + path.
      3. Labelled "PLANT:" / "FACILITY:" fallback.
      4. ADNOC project-number lookup (`_PLANT_BY_ADNOC_PROJECT_NO`).
    """
    # Pass 1: filename-prefix family lookup.
    if file_name:
        stem = file_name.strip()
        # Strip leading directory components if any leaked into file_name.
        stem = stem.replace('\\', '/').rsplit('/', 1)[-1]
        for regex, canonical in _PLANT_BY_FILENAME_PREFIX:
            if regex.search(stem):
                return canonical
    # Pass 2: keyword canonical map.
    sources = [s for s in (text, title_hint, file_name, relative_path) if s]
    for src in sources:
        for regex, canonical in _PLANT_KEYWORD_PATTERNS:
            if regex.search(src):
                return canonical
    # Pass 3: labelled fallback.
    for src in (text, title_hint):
        if not src:
            continue
        m = _PLANT_LABEL_RE.search(src)
        if m:
            cand = re.sub(r'\s+', ' ', m.group(1)).strip()
            if _is_valid_plant(cand):
                return cand.upper()
    # Pass 4: project-number lookup.
    if adnoc_project_no:
        digits = re.sub(r'\D', '', str(adnoc_project_no))
        if digits in _PLANT_BY_ADNOC_PROJECT_NO:
            return _PLANT_BY_ADNOC_PROJECT_NO[digits]
    return ''


# ---------------------------------------------------------------------------
# Smart Contractor-Doc-Ref body scan (project-prefix codes)
# ---------------------------------------------------------------------------
# Reference sample shows contractor doc refs as project-prefixed codes:
#   "5610Y-STC-01-1381-026"  (Bechtel-Technip / Habshan-2)
#   "NM5610Y-57-1383-02"
#   "5247-HMB-500-00-20-015" (Habshan-5)
#   "9101T-999-RT-0000-03"
# Plus short cross-reference lists ("SPS-017", "SPS-016", …).
# When no labelled value is found, fall back to a soft-coded prefix scan.
_CONTRACTOR_REF_PREFIX_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # 5610Y-STC-01-1381-026 / NM5610Y-57-1383-02
    ('btj_5610y',  r'\b(?:[A-Z]{2})?5610Y-[A-Z0-9\-]{4,40}(?<![\-])'),
    # 5247-HMB-500-00-20-015 (Habshan-5 EPC code)
    ('hmb_5247',   r'\b\d{4}-HMB-\d{2,3}-\d{2}-\d{2}-\d{3,4}\b'),
    # 9101T-999-RT-0000-03 (alpha-numeric project code)
    ('proj_alphanum', r'\b\d{4}[A-Z]-\d{3}-[A-Z]{2}-\d{4}-\d{2}\b'),
    # SPS-017 / SPS-114 cross-reference codes (kept short to avoid noise).
    ('sps_short',  r'\bSPS-\d{2,3}\b'),
    # STC-XX-XXXX-XXX style filename / doc-no codes (Bechtel-Technip).
    ('stc_doc',    r'\bSTC[\- ]\d{2}[\- ]\d{4}[\- ]\d{3}\b'),
    # 36-0020-001, 47-0020-007, 27-00-20-102, 47-00-20-101 numeric chains.
    ('hyphenated_numeric', r'\b\d{2,3}-\d{2,4}-\d{2,4}(?:-\d{1,3})?\b'),
)
_CONTRACTOR_REF_PREFIX_RES: Tuple[Tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pat, re.IGNORECASE))
    for name, pat in _CONTRACTOR_REF_PREFIX_PATTERNS
)
_CONTRACTOR_REF_MAX_PER_ROW = 50


def _scan_contractor_codes_in_body(text: str, *, title_hint: str = '',
                                     file_name: str = '',
                                     relative_path: str = '') -> str:
    """Soft-coded fallback that captures project-prefixed contractor codes
    when no labelled "CONTRACTOR DOC NO:" value is present."""
    sources = [s for s in (text, title_hint, file_name, relative_path) if s]
    seen: set = set()
    out: List[str] = []
    for src in sources:
        for _name, regex in _CONTRACTOR_REF_PREFIX_RES:
            for m in regex.finditer(src):
                tok = re.sub(r'\s+', '-', m.group(0).strip()).upper()
                if tok and tok not in seen:
                    seen.add(tok)
                    out.append(tok)
                    if len(out) >= _CONTRACTOR_REF_MAX_PER_ROW:
                        break
            if len(out) >= _CONTRACTOR_REF_MAX_PER_ROW:
                break
        if len(out) >= _CONTRACTOR_REF_MAX_PER_ROW:
            break
    # Substring-overlap suppression: drop any code that is a strict
    # substring of another captured code.
    filtered: List[str] = []
    for t in out:
        if any(t != other and t in other for other in out):
            continue
        filtered.append(t)
    return ','.join(filtered)


# ---------------------------------------------------------------------------
# Stricter Purchase Order validator
# ---------------------------------------------------------------------------
# A real PO number is alpha-numeric with hyphens / slashes, 4..30 chars,
# always contains at least one digit, and never matches plain prose.
_PO_NO_VALID_RE = re.compile(r'^[A-Z0-9][A-Z0-9\-/]{3,29}$', re.IGNORECASE)

# Structural requirement: a real PO number is one of these shapes —
#   • pure digits (≥ 4)              e.g. "1207491"
#   • digits + separator(s)           e.g. "PO-12345", "4500/00321", "5247-21"
#   • known PO prefix                 e.g. "PO12345", "EPC-PO-7821"
# Anything that's just a jumble of letters and digits with no separator
# and no recognised prefix (e.g. "8oxNumbw", "PoBOX99") is almost
# certainly OCR noise from the body text and must be rejected.
_PO_NO_PURE_DIGITS_RE  = re.compile(r'^\d{4,}$')
_PO_NO_HAS_SEPARATOR   = re.compile(r'[\-/]')
_PO_NO_PREFIX_TOKENS: Tuple[str, ...] = ('PO', 'P.O', 'P-O', 'POR', 'PUR')
# Reject any token containing a *lower-case* letter — engineering PO
# numbers are uppercase by convention; mixed-case = OCR/extraction noise.
_PO_NO_REJECT_MIXED_CASE = True


def _is_valid_po_no(value: str) -> bool:
    if not value:
        return False
    v = value.strip()
    if v.upper() in _DOCREF_JUNK_VALUES:
        return False
    if not _PO_NO_VALID_RE.fullmatch(v):
        return False
    if not any(c.isdigit() for c in v):
        return False
    # Mixed-case tokens (e.g. "8oxNumbw") are OCR junk, never real POs.
    if _PO_NO_REJECT_MIXED_CASE and any(c.islower() for c in v):
        return False
    upper = v.upper()
    # Pure digits — accept.
    if _PO_NO_PURE_DIGITS_RE.fullmatch(upper):
        return True
    # Contains a structural separator — accept.
    if _PO_NO_HAS_SEPARATOR.search(upper):
        return True
    # No separator: only accept when it starts with a known PO prefix.
    if any(upper.startswith(p) for p in _PO_NO_PREFIX_TOKENS):
        return True
    return False



# Reference sample shows three values:
#     "PROCESS"  (uppercase — pipe-support data sheets, unit-specific)
#     "COMMON"   (uppercase — generic pipe-support standards, unit '02')
#     "Process"  (title-case — process flow diagrams, equipment etc.)
# Pattern: derived from document_type + unit + folder hints. The mapping
# below is the single source of truth; add a new row to extend.
_AREA_DERIVATION_RULES: Tuple[Dict[str, Any], ...] = (
    # Generic pipe-support standards live in the COMMON section (unit "02").
    {
        'document_type': 'Piping',
        'unit_in':       {'02', '01', '2', '1'},
        'area':          'COMMON',
    },
    # Other piping support / piping data sheets stay in PROCESS area.
    {
        'document_type': 'Piping',
        'area':          'PROCESS',
    },
    # Process discipline (PFD, datasheet, etc.) → title-case "Process".
    {
        'document_type': 'Process',
        'area':          'Process',
    },
    # Other disciplines mirror the document_type name verbatim.
)

# Title-block label scan — wins over derivation when a literal "AREA: xxx"
# label is present. Only the first alphabetic token after the label is
# captured (no greedy spaces) so trailing OCR noise doesn't poison the
# normalisation step.
_AREA_LABEL_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\bPLANT\s*AREA\s*[:\-]?\s*([A-Za-z]+)\b'),
    re.compile(r'\bAREA\s*[:\-]?\s*([A-Za-z]+)\b'),
)

# Canonical area whitelist. Every smart-extractor return is normalised to
# one of these forms (or kept verbatim when it already matches).
_AREA_CANONICAL: Dict[str, str] = {
    'COMMON':  'COMMON',
    'PROCESS': 'Process',   # title-case for non-piping process docs
    'PIPING':  'PROCESS',
    'PLANT':   'COMMON',
    'GENERIC': 'COMMON',
    'STANDARD': 'COMMON',
    'OVERALL': 'COMMON',
}

# Filename / title keyword → area. Plant-wide registers (equipment list,
# line list, plot plan, master index) live under COMMON. Process discipline
# outputs (PFD, P&ID, datasheets) live under Process. First match wins so
# more-specific patterns must come first.
#
# Word-boundary note: `\b` does not fire between `_` and a letter (because
# underscore is a "word" char in regex). We use lookarounds with `[A-Za-z]`
# so `_PFD.` and `-PID-` etc. still match.
_W = r'(?<![A-Za-z])'   # left edge: not preceded by a letter
_WR = r'(?![A-Za-z])'   # right edge: not followed by a letter
_AREA_KEYWORD_HINTS: Tuple[Tuple[re.Pattern, str], ...] = (
    # ── Plant-wide registers → COMMON
    (re.compile(r'equipment[_\s\-]*list',            re.IGNORECASE), 'COMMON'),
    (re.compile(r'line[_\s\-]*list',                 re.IGNORECASE), 'COMMON'),
    (re.compile(r'tie[_\s\-]*in[_\s\-]*list',        re.IGNORECASE), 'COMMON'),
    (re.compile(r'plot[_\s\-]*plan',                 re.IGNORECASE), 'COMMON'),
    (re.compile(r'master[_\s\-]*(?:index|equipment|line)', re.IGNORECASE), 'COMMON'),
    (re.compile(r'overall[_\s\-]*plot',              re.IGNORECASE), 'COMMON'),
    (re.compile(_W + r'common'    + _WR,             re.IGNORECASE), 'COMMON'),
    # ── Process discipline outputs → Process
    (re.compile(r'process[_\s\-]*flow[_\s\-]*diagram', re.IGNORECASE), 'Process'),
    (re.compile(_W + r'pfd'       + _WR,             re.IGNORECASE), 'Process'),
    (re.compile(_W + r'p\s*&?\s*i\s*d' + _WR,        re.IGNORECASE), 'Process'),
    (re.compile(r'(?:process[_\s\-]*)?data[_\s\-]*sheet', re.IGNORECASE), 'Process'),
    (re.compile(r'datasheet',                        re.IGNORECASE), 'Process'),
    (re.compile(_W + r'checklist' + _WR,             re.IGNORECASE), 'Process'),
    (re.compile(_W + r'legend'    + _WR,             re.IGNORECASE), 'Process'),
)

# Path-only fallback keywords — only consulted when filename / title
# yielded nothing. Maps the parent-folder name to an area. COMMON
# markers (folders that catalogue plant-wide registers) are checked
# before discipline folders so that a generic file like "Equipment.xlsx"
# sitting inside "Process/Equipment List/" still maps to COMMON.
_AREA_PATH_HINTS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r'(?:^|[\\/])common(?:[\\/]|$)',       re.IGNORECASE), 'COMMON'),
    (re.compile(r'equipment[_\s\-]*list',              re.IGNORECASE), 'COMMON'),
    (re.compile(r'line[_\s\-]*list',                   re.IGNORECASE), 'COMMON'),
    (re.compile(r'plot[_\s\-]*plan',                   re.IGNORECASE), 'COMMON'),
    (re.compile(r'master[_\s\-]*(?:index|equipment|line)', re.IGNORECASE), 'COMMON'),
    (re.compile(r'(?:^|[\\/])process(?:[\\/]|$)',      re.IGNORECASE), 'Process'),
    (re.compile(r'(?:^|[\\/])piping(?:[\\/]|$)',       re.IGNORECASE), 'PROCESS'),
)


def _normalise_area_value(value: str) -> str:
    """Map any raw extracted area string to a canonical form."""
    if not value:
        return ''
    cleaned = re.sub(r'[^A-Za-z]', '', value).upper()
    if cleaned in _AREA_CANONICAL:
        return _AREA_CANONICAL[cleaned]
    # If already matches a canonical value verbatim, keep it.
    canon_values = set(_AREA_CANONICAL.values())
    if value in canon_values:
        return value
    return ''


def _extract_area_from_keywords(*sources: str) -> str:
    """
    Scan filename / title corpus for the first keyword hint match.
    Returns canonical area or '' when nothing matches.
    """
    corpus = ' '.join(s for s in sources if s)
    if not corpus:
        return ''
    for pat, area in _AREA_KEYWORD_HINTS:
        if pat.search(corpus):
            return area
    return ''


def _extract_area_from_path(relative_path: str) -> str:
    """Map parent-folder name in the relative path to an area."""
    if not relative_path:
        return ''
    for pat, area in _AREA_PATH_HINTS:
        if pat.search(relative_path):
            return area
    return ''


def _derive_area_from_rules(document_type: str, unit_value: str) -> str:
    """
    Walk `_AREA_DERIVATION_RULES` in order and return the first matching
    rule's `area`. A rule matches when every condition key it sets is
    satisfied by the inputs. Empty rules act as catch-alls.
    """
    dt = (document_type or '').strip()
    u  = (unit_value or '').strip()
    # Normalise multi-unit values to a set of individual unit strings for
    # the `unit_in` membership check.
    unit_tokens = {tok.strip() for tok in re.split(r'[,/&\s]+', u) if tok.strip()}
    for rule in _AREA_DERIVATION_RULES:
        if 'document_type' in rule and rule['document_type'].lower() != dt.lower():
            continue
        if 'unit_in' in rule and not (unit_tokens & set(rule['unit_in'])):
            continue
        return rule.get('area', '')
    # Default: mirror document_type when nothing else matches.
    return dt


def _extract_area_smart(text: str, *, document_type: str = '',
                         unit_value: str = '',
                         relative_path: str = '',
                         file_name: str = '',
                         title_hint: str = '') -> str:
    """
    Smart area extractor.

    Priority (first non-empty wins):
      1. Literal "AREA: xxx" label in the document text — must normalise
         to a canonical value, otherwise ignored.
      2. Filename / title keyword hints (`_AREA_KEYWORD_HINTS`).
      3. Parent-folder keyword hints (`_AREA_PATH_HINTS`).
      4. Rule-based derivation from document_type + unit_value.
    """
    if text:
        for pat in _AREA_LABEL_PATTERNS:
            m = pat.search(text)
            if m:
                canon = _normalise_area_value(m.group(1))
                if canon:
                    return canon
    kw = _extract_area_from_keywords(file_name, title_hint)
    if kw:
        return kw
    path_hit = _extract_area_from_path(relative_path)
    if path_hit:
        return path_hit
    derived = _derive_area_from_rules(document_type, unit_value)
    return _normalise_area_value(derived) or derived


def _extract_unit(text: str) -> str:
    m = _UNIT_PATTERN.search(text)
    if not m:
        return ''
    return f"U{(m.group(1) or m.group(2)).zfill(2)}"


def _classify_type(text: str, taxonomy: Dict[str, Any]) -> str:
    """
    Keyword classifier: return the first document_type whose canonical key
    appears as a whole word in the text (case-insensitive).

    The taxonomy keys are scanned in descending length order so more-specific
    keys win over their substring counterparts:
      * "Civil & Structural" beats "Civil"
      * "Quality Control"    beats "General"
      * "Material Management" beats "Material"
    Word-boundary matching (``\\b...\\b``) prevents partial hits like the
    word "general" inside arbitrary body text from snapping the column to
    the "General" type.
    """
    if not text:
        return ''
    low = text.lower()
    keys = list(taxonomy.get('document_types', {}).keys())
    # Sort by length descending so the longest canonical key matches first.
    keys.sort(key=lambda k: len(k), reverse=True)
    for t in keys:
        pat = r'\b' + re.escape(t.lower()) + r'\b'
        if re.search(pat, low):
            return t
    return ''


def _sanitize_document_type(value: str, subtype_value: str,
                             taxonomy: Dict[str, Any]) -> str:
    """
    Snap a free-text Document Type back into the controlled taxonomy.

    Strategy (soft-coded, runs in this order):
      1. Exact case-insensitive match against a taxonomy key → canonical key.
      2. Reverse lookup: if the row already has a document_subtype that is
         listed under exactly one taxonomy key, use that key.
      3. Substring scan: if the value text contains a taxonomy key as a
         whole-word, return that key.
      4. Otherwise return '' so the NA fallback applies.
    """
    types = taxonomy.get('document_types', {}) or {}
    if not types:
        return value or ''

    canonical_by_lower = {k.lower(): k for k in types.keys()}
    val_clean = (value or '').strip()

    # 1. Direct canonical match (handles correct values & wrong case).
    if val_clean and val_clean.lower() in canonical_by_lower:
        return canonical_by_lower[val_clean.lower()]

    # 2. Reverse lookup via subtype.
    sub_clean = (subtype_value or '').strip().lower()
    if sub_clean:
        owners = [k for k, subs in types.items()
                  if any(sub_clean == (s or '').strip().lower() for s in subs)]
        if len(owners) == 1:
            return owners[0]

    # 3. Whole-word substring scan inside the offending value.
    if val_clean:
        low = val_clean.lower()
        for key_lower, canonical in canonical_by_lower.items():
            if re.search(r'\b' + re.escape(key_lower) + r'\b', low):
                return canonical

    return ''


def _narrow_subtype(text: str, parent_type: str, taxonomy: Dict[str, Any]) -> str:
    if not parent_type or not text:
        return ''
    low = text.lower()
    for sub in taxonomy.get('document_types', {}).get(parent_type, []):
        if sub and sub.lower() in low:
            return sub
    return ''


# ---------------------------------------------------------------------------
# Soft-coded subtype matcher — auto-derives alias regex patterns from each
# canonical taxonomy entry so real-world title-block / folder phrasings are
# recognised without hard-coding mappings. Examples covered:
#   "Process Flow Diagrams (PFD)"  →  "PROCESS FLOW DIAGRAM UNIT 36" / folder
#                                       "ASSOCIATE PROJECT_PFD"
#   "Piping Support Standards"     →  folder "Special Pipe Support (SPS)"
#   "Piping Data Sheet"            →  title "SLIDING PLATES DATA SHEET"
# ---------------------------------------------------------------------------
#   "Process Flow Diagrams (PFD)"  →  "PROCESS FLOW DIAGRAM UNIT 36" / folder
#                                       "ASSOCIATE PROJECT_PFD"
#   "Piping Support Standards"     →  folder "Special Pipe Support (SPS)"
#   "Piping Data Sheet"            →  title "SLIDING PLATES DATA SHEET"
# ---------------------------------------------------------------------------

# Soft-coded alias overrides — extra free-text patterns that map onto a
# canonical subtype. Useful when the real-world title or folder name does
# not contain any token from the canonical name itself. Patterns are plain
# substrings (lower-cased, normalised); each is searched as a whole word
# against the combined corpus (title + folder + filename + body text).
_SUBTYPE_ALIAS_OVERRIDES: Dict[str, List[str]] = {
    # SPS (Special Pipe Support) → Piping Support Standards
    'Piping Support Standards': [
        'pipe support', 'pipe supports', 'special pipe support', 'sps',
        # Common pipe-support equipment classes — promote support
        # classification over the generic "data sheet" signal.
        'spring support', 'variable spring', 'rigid support',
        'shoe support', 'hanger support', 'pipe shoe',
        'guide support', 'anchor support', 'trunnion support',
        'dummy support', 'clamp support', 'sliding support',
    ],
    'Piping Data Sheet': [
        'data sheet', 'datasheet', 'piping datasheet', 'piping data sheet',
        'spec break', 'spec-break', 'material spec', 'pipe class',
    ],
    'Process Flow Diagrams (PFD)': [
        'pfd', 'process flow diagram', 'process flow',
    ],
    'Utility Flow Diagrams (UFD)': [
        'ufd', 'utility flow diagram', 'utility flow',
    ],
    'Process Datasheet': [
        'process datasheet', 'process data sheet',
    ],
    'Single Line Diagrams': [
        'single line diagram', 'single-line diagram', 'sld', 'one line diagram',
    ],
    'Cause and Effect Charts': [
        'cause and effect', 'cause & effect', 'c&e', 'cause-effect',
    ],
    # ─── Common process / piping / instrument deliverables ──────────
    'Piping and Instrumentation Diagrams (P&IDs)': [
        'p&id', 'p & id', 'pid', 'piping and instrumentation',
        'piping & instrumentation',
    ],
    'Equipment List': [
        'equipment list', 'equipment register', 'mechanical equipment list',
    ],
    'Line List': [
        'line list', 'piping line list', 'line schedule',
    ],
    'Tie-In List': [
        'tie-in list', 'tie in list', 'tie-in schedule', 'tie in schedule',
    ],
    'Instrument Index': [
        'instrument index', 'instrument list', 'instrument schedule',
    ],
    'Instrument Datasheet': [
        'instrument datasheet', 'instrument data sheet',
    ],
    # ─── Electrical ─────────────────────────────────────────────────
    'Electrical Cable Schedule': [
        'cable schedule', 'cable list',
    ],
    'Schematic Wiring Diagrams': [
        'schematic wiring', 'wiring diagram', 'schematic diagram',
    ],
    'Electrical Equipment Arrangement Layouts': [
        'electrical equipment arrangement', 'equipment arrangement layout',
    ],
    'Earthing Layouts': [
        'earthing layout', 'grounding layout', 'earth grid',
    ],
    'Cathodic Protection Drawings': [
        'cathodic protection', 'cp drawing', 'cp design',
    ],
    # ─── Civil / Structural ─────────────────────────────────────────
    'Foundation Drawings/Details': [
        'foundation drawing', 'foundation detail', 'foundation plan',
    ],
    'Plot Plan': [
        'plot plan', 'plant plot plan', 'site plot plan',
    ],
    'Topographic Drawing of Site': [
        'topographic drawing', 'topo drawing', 'topographic survey',
    ],
    # ─── Mechanical / Equipment ─────────────────────────────────────
    'General Arrangement Drawings': [
        'general arrangement', 'ga drawing', 'g.a. drawing',
    ],
    'Detail Drawings': [
        'detail drawing', 'fabrication drawing',
    ],
    'Calculations & Performance Curves': [
        'performance curve', 'pump curve', 'compressor curve',
    ],
    # ─── F&G / Safety ───────────────────────────────────────────────
    'F&G Schematic': [
        'fire and gas schematic', 'f&g schematic', 'fg schematic',
    ],
    'Failure Modes Effects Analysis for F&G Systems': [
        'fmea', 'failure modes', 'failure mode effects',
    ],
    # ─── Calculation deliverables (driven by filename/folder when
    # OCR'd title is garbage). These cover the manually-classified
    # Design Calculation batch (Civil, Pipeline, Procurement, Piping
    # stress, Vessel design, generic Piping design calcs).
    'Civil Calculations': [
        'civil calculation', 'civil calculations',
        'foundation calculation', 'foundation to ',
        'pipe support calculation', 'pipe supports calculation',
        'pipe sleeper', 'culvert calculation', 'culvert',
        'drainage calculation', 'storm & firewater', 'open drain',
        'structural calculation', 'structural calculations',
        'concrete sump', 'sump calculation', 'sulphur loading',
        'misc pipe supports', 'equipment foundation',
        'supports and foundation', 'foundation for supports',
    ],
    'Pipeline Calculations': [
        'pipeline calculation', 'pipeline design calculation',
        'pipeline pressure calculation', 'design pressure calculation',
        'upheaval buckling', 'lateral buckling', 'wall thickness calculation',
        'pipeline stress', 'gas pipeline', 'liquid pipeline',
    ],
    'Procurement Documents': [
        'procurement record book', 'engineering-procurement record',
        'engineering procurement record', 'procurement record',
        'engineering record book',
    ],
    'Stress Analysis Reports': [
        'stress analysis', 'stress calculation', 'stress report',
        'stress critical', 'critical stress', 'stress isometric',
        'piping stress', 'caesar', 'caesar ii',
    ],
    'Vessel Design Calculations': [
        'vessel design', 'vessel calculation', 'pressure vessel calc',
        'storage tank calc', 'tank design calc',
        'ngl storage calc', 'storage vessel calc',
        'ngl storage', 'storage calculation', 'tank calculation',
        'pressure vessel',
    ],
    'Design Calculations': [
        # NOTE: the bare phrase 'design calculation(s)' is auto-derived
        # from the canonical name, so we don't list it here — it would
        # otherwise out-match more specific aliases below (stress,
        # vessel, civil, pipeline) when those have shorter overrides.
        'flare line support', 'flare header design',
        'piping design calculation',
    ],
    # ─── HSE / F&G nuances ──────────────────────────────────────────
    'Passive & Active Fire Protection Philosophy': [
        'fire protection enclosure', 'fire protection enclosures',
        'passive fire protection', 'active fire protection',
        'fire protection philosophy', 'fire protection system',
        'on/off valve fire protection', 'pfp', 'afp',
    ],
    # ─── Mechanical instrumentation accessories ─────────────────────
    'Equipment Miscellaneous': [
        'turbine meter', 'turbine meters', 'flow meter spec',
        'misc equipment', 'metering equipment', 'flow metering',
    ],
}

# ---------------------------------------------------------------------------
# Reference-validated alias extensions (Habshan-1 Design Calculation batch).
# These were derived from the manually-checked master index sample and
# correspond to real-world title phrases the OCR pipeline sees. Kept in a
# separate dict and merged at module-load time so the curated overrides
# above stay focused, while additional reference patterns can be tuned
# independently.
# ---------------------------------------------------------------------------
_SUBTYPE_ALIAS_REFERENCE_EXTRA: Dict[str, List[str]] = {
    'Procurement Documents': [
        # Reference title: "ENGINEERING-PROCUREMENT RECORD BOOK-DESIGN
        # CALCULATIONS - PART IX" — matched on either the full phrase or
        # any of its distinctive prefixes.
        'engineering procurement record book',
        'engineering-procurement record book',
        'procurement record book', 'record book design calculation',
        'epc record book', 'engineering record book design',
    ],
    'Civil Calculations': [
        # Reference titles: foundations, building structures, drainage,
        # sumps, sleepers, gantries, sulphur loading shelters.
        'sulphur loading shelter', 'sulphur loading gantry',
        'misc pipe supports and foundation', 'misc pipe supports and foundations',
        'pipe sleepers', 'pipe sleeper calculation',
        'concrete sump', 'concrete foundation',
        'deaerator structure', 'administration building',
        'maintenance building', 'warehouse building', 'stores building',
        'structural calculation for', 'structural calculations for',
        'boiler blow down sump', 'demineraliser building',
        'desuperheater area', 'oily water drain',
        'storm & firewater', 'storm and firewater',
        'foundation for supports', 'mps foundation',
    ],
    'Pipeline Calculations': [
        # Reference titles: pipeline pressure, buckling, ADCO P/L.
        'lp/mp gas pipeline', 'lp mp gas pipeline',
        'gas pipeline design pressure', 'pipeline design pressure',
        'adco habshan p/l', 'adco habshan pl',
        'upheaval buckling check', 'lateral buckling check',
        'upheaval and lateral buckling',
    ],
    'Vessel Design Calculations': [
        # Reference titles: NGL Storage Tank calculation sheets.
        'calculation sheet ngl storage', 'calculation sheet-ngl storage',
        'ngl storage tank', 'storage tank calculation sheet',
        'pressure vessel calculation', 'tank design calculation',
    ],
    'Stress Analysis Reports': [
        'engineering standard stress calculation',
        'stress calculation index', 'stress critical line list',
    ],
    'Design Calculations': [
        # Piping-discipline design calculations beyond stress / vessel.
        'flare line supports calculation', 'flare line supports ngl',
        'line supports ngl storage', 'piping design calculation sheet',
    ],
}

# Merge the reference extension dict into the main overrides dict so the
# matcher sees a single unified source. Done at module-load time (cheap
# dict copy) so the soft-coded data stays readable in two blocks.
for _k, _v in _SUBTYPE_ALIAS_REFERENCE_EXTRA.items():
    _SUBTYPE_ALIAS_OVERRIDES.setdefault(_k, []).extend(_v)

# Single-word aliases below this length are skipped to avoid false positives,
# unless explicitly listed in _SUBTYPE_ALIAS_OVERRIDES (acronyms get a pass).
_SUBTYPE_MIN_AUTO_ALIAS_LEN = 8

# When True, the final-pass replaces any existing document_subtype value
# that is NOT a member of the controlled taxonomy. This is the main fix
# for upstream regex / vision leaks (e.g. free-text titles, OCR junk,
# discipline names) ending up in the column. Set to False to preserve
# whatever upstream produced, regardless of whether it's a known subtype.
SUBTYPE_OVERRIDE_INVALID = True
# When True, the final-pass ALSO re-runs the smart matcher when the
# existing value is valid but the smart matcher finds a *longer*
# (more specific) match in the title — handles the "Piping" leaking
# from folder when the title clearly says "PFD" case.
SUBTYPE_PREFER_TITLE_MATCH = True
# When True, titles that look like OCR-template footer noise (e.g.
# "PLOT PLAN REF. DATA EQUIPMENT Nos. DRAWINGS", "DESIGN CALCULATIONS")
# are demoted: they're no longer used as the priority corpus and the
# filename + relative path become the primary signal. This is the main
# fix for the H1 Design-Calculation batch where the title block is
# garbled by OCR but filenames carry the real classification.
SUBTYPE_DEMOTE_OCR_TITLES = True

# Patterns that mark a title as OCR-template noise (lower-cased subs).
# These are *form template* fields that bleed across many documents and
# are not real titles. Order matters only for readability.
_SUBTYPE_OCR_NOISE_PATTERNS: List[str] = [
    'plot plan',
    'ref. data',
    'ref data',
    'equipment nos',
    'equmwent nos',  # common OCR mis-read of 'equipment nos'
    'kquipment nos',
    'bquipment nos',
    'tquipment nos',
    'commande / purchase order',
    'commande/purchase order',
    'specifications.',
    'joint venture',
    'calculation sheet sheet',
]

# A title that contains only generic calc/report words also counts as
# too-weak signal (the smart matcher matches "design calculations" to
# the generic 'Design Calculations' subtype regardless of discipline).
_SUBTYPE_OCR_GENERIC_TITLES: List[str] = [
    'design calculations',
    'design calculation',
    'calculation sheet',
    'specifications',
    'drawings',
    'general',
]


def _looks_like_ocr_noise_title(title: str) -> bool:
    """True when the title is dominated by OCR-template footer text
    or is so generic it would mis-classify the document.

    Used by the subtype final-pass to swap priority signals from
    title → filename/path when the title is unreliable.
    """
    if not title:
        return False
    t = title.strip().lower()
    if not t or t == 'na':
        return True
    for p in _SUBTYPE_OCR_NOISE_PATTERNS:
        if p in t:
            return True
    # Pure generic title with no other words = noise.
    cleaned = re.sub(r'[^a-z0-9 ]+', ' ', t)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if cleaned in _SUBTYPE_OCR_GENERIC_TITLES:
        return True
    return False


def _is_valid_subtype(value: str, taxonomy: Dict[str, Any]) -> bool:
    """True when `value` matches a canonical subtype in the taxonomy
    (case-insensitive). The taxonomy is loaded once per request so this
    is cheap; ``set`` lookup with a lazy cache keeps repeated calls fast.
    """
    if not value:
        return False
    v = value.strip().lower()
    if v in {'', 'na', 'n/a'}:
        return False
    cache_key = id(taxonomy)
    cache = getattr(_is_valid_subtype, '_cache', {})
    pool = cache.get(cache_key)
    if pool is None:
        pool = set()
        for subs in (taxonomy.get('document_types') or {}).values():
            for s in subs or []:
                if s:
                    pool.add(s.strip().lower())
        cache[cache_key] = pool
        _is_valid_subtype._cache = cache  # type: ignore[attr-defined]
    return v in pool

# Tokens too generic to count as standalone aliases.
_SUBTYPE_GENERIC_TOKENS = {
    'general', 'detail', 'details', 'drawings', 'drawing',
    'specification', 'specifications', 'reports', 'report',
    'standards', 'list', 'lists', 'documents', 'document',
    'compiled', 'dossiers', 'certification', 'calculations',
    'schedules', 'layouts', 'diagrams', 'diagram',
}


def _normalise_corpus(*chunks: str) -> str:
    """Lowercase + collapse non-alphanumeric runs (except parens) to single
    spaces so folder strings like 'H2/ASSOCIATE PROJECT_PFD' become
    'h2 associate project pfd' and acronyms remain detectable."""
    joined = ' '.join(c for c in chunks if c)
    return re.sub(r'[^a-z0-9()]+', ' ', joined.lower()).strip()


def _alias_to_regex(alias: str) -> str:
    """
    Build a word-boundary regex pattern from a lower-cased alias string,
    tolerating plural / singular variations on alphabetic tokens of length
    >= 4 (e.g. 'diagrams' matches both 'diagram' and 'diagrams').
    """
    tokens = re.findall(r"[a-z0-9]+|\(|\)", alias)
    parts: List[str] = []
    for tok in tokens:
        if tok in ('(', ')'):
            parts.append(r'\(' if tok == '(' else r'\)')
        elif tok.isalpha() and len(tok) >= 4:
            stem = tok[:-1] if tok.endswith('s') else tok
            parts.append(re.escape(stem) + r's?')
        else:
            parts.append(re.escape(tok))
    return r'\b' + r'[\s\-]*'.join(parts) + r'\b'


def _build_subtype_alias_regexes(canonical: str) -> List[Tuple[str, bool]]:
    """
    Generate ordered (regex, is_override) tuples for a canonical subtype.
    `is_override=True` marks a curated alias from _SUBTYPE_ALIAS_OVERRIDES;
    the scorer uses this flag to boost curated matches over auto-derived
    ones (so e.g. 'foundation to ' beats a longer 'design calculations'
    auto-alias when both match the same corpus).
    """
    if not canonical:
        return []
    base_low = canonical.strip().lower()
    forms: List[Tuple[str, bool]] = [(base_low, False)]

    paren = re.search(r'\(([^)]+)\)', base_low)
    if paren:
        acronym = paren.group(1).strip()
        stripped = re.sub(r'\s*\([^)]*\)\s*', ' ', base_low).strip()
        if stripped and not any(f == stripped for f, _ in forms):
            forms.append((stripped, False))
        if len(acronym) >= 3 and not any(f == acronym for f, _ in forms):
            forms.append((acronym, False))

    # Soft-coded overrides — flagged True so the scorer can prefer them.
    for extra in _SUBTYPE_ALIAS_OVERRIDES.get(canonical, []):
        e = extra.strip().lower()
        if e and not any(f == e for f, _ in forms):
            forms.append((e, True))

    patterns: List[Tuple[str, bool]] = []
    seen = set()
    for f, is_override in forms:
        if (not is_override) and (' ' not in f):
            if f in _SUBTYPE_GENERIC_TOKENS:
                continue
            if len(f) < _SUBTYPE_MIN_AUTO_ALIAS_LEN:
                continue
        pat = _alias_to_regex(f)
        if pat not in seen:
            seen.add(pat)
            patterns.append((pat, is_override))
    return patterns


def _match_subtype_smart(*, parent_type: str, corpus: str,
                         taxonomy: Dict[str, Any],
                         priority_corpus: str = '') -> Tuple[str, str]:
    """
    Multi-signal subtype matcher.

    Returns (subtype, owner_type). When parent_type is provided, only its
    subtypes are searched. Otherwise scans all taxonomy types and also
    returns the owning type so the caller can backfill document_type.

    `priority_corpus` (typically the document title) is searched first —
    any hit there beats a hit in the general corpus, regardless of pattern
    length. This prevents folder-only hints (e.g. 'Special Pipe Support'
    folder containing a data-sheet PDF) from overriding a clearer title.
    """
    if not corpus and not priority_corpus:
        return '', ''

    types_map = taxonomy.get('document_types', {}) or {}
    if parent_type and parent_type in types_map:
        candidate_types = {parent_type: types_map[parent_type]}
    else:
        candidate_types = types_map

    def _scan(scope_corpus: str) -> Tuple[str, str, int]:
        best_sub, best_own, best_score = '', '', 0
        if not scope_corpus:
            return best_sub, best_own, best_score
        # Soft-coded boost: curated override aliases outrank auto-derived
        # ones regardless of length. This stops the bare canonical name
        # ('design calculations') from beating a more specific override
        # ('foundation to', 'stress calculation') that happens to be shorter.
        OVERRIDE_BONUS = 10_000
        for owner, subs in candidate_types.items():
            for sub in subs:
                sub_best = 0
                for pattern, is_override in _build_subtype_alias_regexes(sub):
                    if re.search(pattern, scope_corpus):
                        score = len(pattern) + (OVERRIDE_BONUS if is_override else 0)
                        if score > sub_best:
                            sub_best = score
                if sub_best > best_score:
                    best_score = sub_best
                    best_sub = sub
                    best_own = owner
        return best_sub, best_own, best_score

    # Priority pass — anything found in the title/priority corpus wins.
    sub, owner, _ = _scan(priority_corpus)
    if sub:
        return sub, owner

    sub, owner, _ = _scan(corpus)
    return sub, owner


def _pattern_lookup(field_key: str, text: str) -> str:
    """
    Soft-coded pattern-based extractor. Looks up patterns by field_key in
    extraction_patterns.json and returns the first / all matches filtered by
    the configured stop-words.
    """
    if not text or not field_key:
        return ''
    cfg = load_patterns()
    entries = cfg['compiled'].get(field_key, [])
    stop = cfg['stop_words']
    for entry in entries:
        regex = entry['regex']
        group = entry['group']
        mode  = entry['mode']
        if mode == 'all_csv':
            hits = []
            seen = set()
            for m in regex.finditer(text):
                try:
                    val = (m.group(group) or '').strip().rstrip('.,;:')
                except IndexError:
                    continue
                if not val or val.upper() in stop or val.upper() in seen:
                    continue
                # Soft-coded junk filter — reject OCR-noise values.
                if not _is_clean_value(val):
                    continue
                seen.add(val.upper())
                hits.append(val)
            if hits:
                return ','.join(hits)
        else:  # 'first'
            for m in regex.finditer(text):
                try:
                    val = (m.group(group) or '').strip().rstrip('.,;:')
                except IndexError:
                    continue
                if val and val.upper() not in stop and _is_clean_value(val):
                    return val
    return ''


# ---------------------------------------------------------------------------
# Column-class dispatcher
# ---------------------------------------------------------------------------

def _value_file_derived(column: Dict[str, Any], *, file_name: str,
                         relative_path: str, file_path: str, fmt: str) -> str:
    key = column['key']
    if key == 'file_name':
        # Preserve the original basename (incl. extension) when configured so —
        # this matches the reference Master Index sample where "File name"
        # values like "NM-57-1383-002.pdf", "36-0020-001.pdf",
        # "27-00-20-102.pdf" retain the ".pdf" suffix.
        return file_name if FILE_NAME_INCLUDE_EXTENSION else os.path.splitext(file_name)[0]
    if key == 'full_path':
        return relative_path or file_name
    if key == 'file_format':
        return os.path.splitext(file_name)[1].lstrip('.').upper()
    if key == 'no_of_sheets':
        pages = pdf_page_count(file_path) if fmt == 'pdf' else None
        return str(pages) if pages else ''
    if key == 'paper_size':
        return detect_paper_size(file_path, fmt)
    return ''


def _value_ai_extract(column: Dict[str, Any], *, text: str, file_name: str,
                      taxonomy: Dict[str, Any], accum: Dict[str, Any]) -> str:
    extractor = column.get('extractor')
    if extractor == 'filename_stem':
        return os.path.splitext(file_name)[0]
    if extractor == 'taxonomy_classifier':
        return _classify_type(text, taxonomy)
    if extractor == 'taxonomy_narrow':
        return _narrow_subtype(text, accum.get('document_type', ''), taxonomy)
    if extractor == 'title_scan':
        if DOCUMENT_TITLE_SMART_EXTRACT:
            return _extract_title_smart(text, taxonomy)
        return _extract_title(text)
    if extractor == 'date_any':
        smart = _extract_issue_date_smart(text)
        if smart:
            return smart
        return _first_match(DATE_PATTERN, text)
    if extractor == 'rev_token':
        smart = _extract_revision_smart(text)
        if smart:
            return smart
        return _first_match(REVISION_PATTERN, text)
    if extractor == 'status_keyword':
        smart = _extract_revision_status_smart(text)
        if smart:
            return smart
        return _extract_status(text)
    if extractor == 'unit_code':
        smart = _extract_unit_smart(
            text, title_hint=accum.get('document_title', ''),
        )
        if smart:
            return smart
        # Legacy single-unit fallback.
        m = _UNIT_PATTERN.search(text or '')
        if m:
            return (m.group(1) or m.group(2) or '').lstrip('0') or '0'
        return _pattern_lookup('unit', text)
    if extractor == 'equipment_tag':
        smart = _extract_tags_smart(text or '')
        if smart:
            return smart
        return _all_matches(EQUIPMENT_NO_PATTERN, text)
    if extractor == 'pattern_lookup':
        # Smart override for contractor_ref — wires the body/title/filename
        # multi-source extractor with ADNOC drawing-number filename
        # fallback. Falls back to JSON pattern_lookup when smart returns ''.
        if column['key'] == 'contractor_ref':
            smart = _extract_contractor_ref_smart(
                text or '',
                title_hint=accum.get('document_title', ''),
                file_name=file_name,
                relative_path=accum.get('full_path', ''),
            )
            if smart:
                return smart
        return _pattern_lookup(column['key'], text)
    # Fallback: try pattern_lookup using the column key — lets us enable
    # extraction on any ai_extract column just by adding patterns to JSON.
    return _pattern_lookup(column['key'], text)


def _value_batch_or_extract(column: Dict[str, Any], *, batch_defaults: Dict[str, Any],
                             text: str, na_value: str) -> str:
    """
    Hybrid class: prefer the batch_default value when meaningfully set;
    otherwise fall back to pattern extraction on document text.
    """
    key = column['key']
    bd = (batch_defaults.get(key) or '').strip()
    if bd and bd.upper() != na_value.upper():
        return bd
    # Try per-field patterns
    return _pattern_lookup(key, text)


def _value_derived(column: Dict[str, Any], *, accum: Dict[str, Any],
                   taxonomy: Dict[str, Any]) -> str:
    rule = column.get('rule')
    source = accum.get(column.get('derive_from', ''), '')
    if rule == 'type_to_discipline':
        return taxonomy.get('type_to_discipline', {}).get(source, '')
    if rule == 'yn_if_present':
        return 'Y' if source and str(source).strip().upper() not in ('', 'NA') else 'N'
    return ''


# ---------------------------------------------------------------------------
# Author ⇄ Originator mirror — soft-coded post-pass.
#
# Reference master-index samples (Habshan-1 NONTEF Design Calculation batch,
# 9000+ rows) show Author and From/Originator are virtually always the same
# value — the EPC contractor that prepared the document (BECHTEL-TECHNIP
# JOINT VENTURE, BECHTEL LIMITED, TECHNIP, …). When one of the two columns
# was successfully extracted but the other remained empty/NA, we mirror the
# value across so the row matches the reference convention without changing
# any extraction algorithm.
#
# Toggleable via AUTHOR_ORIGINATOR_MIRROR — set to False to disable.
# ---------------------------------------------------------------------------
AUTHOR_ORIGINATOR_MIRROR = True
AUTHOR_FIELD_KEY = 'author'


def _apply_author_originator_mirror(row: Dict[str, Any], na: str) -> None:
    """
    If one of (author, originator) holds a meaningful value and the other is
    empty/NA, copy the value across. Never overwrites an existing value.
    """
    if not AUTHOR_ORIGINATOR_MIRROR:
        return
    na_up = (na or '').upper()

    def _is_empty(v: Any) -> bool:
        s = str(v or '').strip()
        return (not s) or s.upper() == na_up

    author     = row.get(AUTHOR_FIELD_KEY, '')
    originator = row.get(ORIGINATOR_FIELD_KEY, '')

    if _is_empty(author) and not _is_empty(originator):
        row[AUTHOR_FIELD_KEY] = str(originator).strip()
    elif _is_empty(originator) and not _is_empty(author):
        row[ORIGINATOR_FIELD_KEY] = str(author).strip()


# ---------------------------------------------------------------------------
# Source Folder — derive from each file's actual location, not the form.
#
# The legacy behaviour treated `source_folder` as a single batch_default
# (whatever the user typed in Advanced Defaults), so every row in a batch
# carried the same value even when the dropped folder contained nested
# sub-folders ("Process/Equipment/", "Mechanical/Datasheets/", …).
# Reference master-index samples show source_folder is the immediate
# **directory path** that contains the document, derived from the file.
#
# Soft-coded knobs in SOURCE_FOLDER_CONFIG:
#   * enabled            : kill-switch
#   * field_key          : column.key in the template (must be batch_default
#                          or batch_or_extract — both work)
#   * use_full_relative  : True  → "Sub1/Sub2"; False → only "Sub2" (parent)
#   * include_top_folder : keep the top-level dropped-folder name in the path
#   * separator          : path joiner in the output value
#   * fallback_to_batch  : when the file lives at the upload root, fall back
#                          to batch_defaults['source_folder'] (the form value)
# ---------------------------------------------------------------------------
SOURCE_FOLDER_CONFIG = {
    'enabled':           True,
    'field_key':         'source_folder',
    'use_full_relative': True,
    'include_top_folder': True,
    'separator':         '/',
    'fallback_to_batch': True,
}


def _derive_source_folder(*, relative_path: str, file_name: str,
                          batch_defaults: Dict[str, Any], na: str) -> str:
    """
    Compute the per-row source_folder value from the file's actual relative
    path within the dropped folder tree. Falls back to the batch default
    when the file lives at the upload root and `fallback_to_batch` is True.
    """
    cfg = SOURCE_FOLDER_CONFIG
    if not cfg.get('enabled'):
        return (batch_defaults.get(cfg['field_key']) or '').strip()

    rp = (relative_path or '').replace('\\', '/').strip('/')
    # Strip the file name itself — we only want the directory portion.
    if rp.endswith(file_name):
        rp = rp[: -len(file_name)].rstrip('/')

    parts = [p for p in rp.split('/') if p]
    if not parts:
        # File at the root — fall back to whatever the user typed in the form,
        # if enabled. Otherwise leave blank so NA fills in.
        if cfg.get('fallback_to_batch'):
            return (batch_defaults.get(cfg['field_key']) or '').strip()
        return ''

    if not cfg.get('include_top_folder') and len(parts) > 1:
        parts = parts[1:]
    if not cfg.get('use_full_relative'):
        parts = parts[-1:]

    sep = cfg.get('separator', '/')
    return sep.join(parts)


def _apply_source_folder_smart(row: Dict[str, Any], *, relative_path: str,
                               file_name: str, batch_defaults: Dict[str, Any],
                               na: str) -> None:
    """
    Replace the batch-default source_folder with a per-file derived value.
    Always wins over the batch default when the file has any sub-path —
    matches reference samples where each row reflects its own folder.
    """
    cfg = SOURCE_FOLDER_CONFIG
    if not cfg.get('enabled'):
        return
    derived = _derive_source_folder(
        relative_path=relative_path,
        file_name=file_name,
        batch_defaults=batch_defaults,
        na=na,
    )
    if derived:
        row[cfg['field_key']] = derived
    else:
        # Nothing to derive AND no batch fallback — leave NA placeholder.
        cur = str(row.get(cfg['field_key'], '') or '').strip()
        if not cur:
            row[cfg['field_key']] = na


def build_row(*, row_index: int, file_name: str, relative_path: str,
              file_path: str, batch_defaults: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point: produce a fully-populated Master Index row for a file.

    Parameters
    ----------
    row_index : 1-based index within the batch (for SR NO).
    file_name : basename on disk.
    relative_path : path relative to the uploaded folder root.
    file_path : absolute path for reading content.
    batch_defaults : column.key -> value applied to batch_default columns.

    Returns
    -------
    dict keyed by column.key. Values are always strings.
    """
    columns = get_columns()
    taxonomy = load_taxonomy()
    na = get_na_value()
    fmt = detect_format(file_name) or ''

    text = read_file_text(file_path, fmt) if fmt in ('pdf', 'excel', 'word') else ''

    row: Dict[str, Any] = {}
    # Two-pass: first resolve non-derived columns so derived rules can read them.
    for col in columns:
        cls = col.get('class')
        key = col['key']
        try:
            if cls == 'auto_serial':
                value = str(row_index)
            elif cls == 'file_derived':
                value = _value_file_derived(
                    col, file_name=file_name, relative_path=relative_path,
                    file_path=file_path, fmt=fmt,
                )
            elif cls == 'batch_default':
                value = batch_defaults.get(key, '')
            elif cls == 'batch_or_extract':
                value = _value_batch_or_extract(
                    col, batch_defaults=batch_defaults, text=text, na_value=na,
                )
            elif cls == 'ai_extract':
                value = _value_ai_extract(
                    col, text=text, file_name=file_name,
                    taxonomy=taxonomy, accum=row,
                )
            elif cls == 'derived':
                value = ''  # filled in second pass
            else:
                value = ''
        except Exception:
            logger.exception('Column %s failed', key)
            value = ''
        row[key] = '' if value is None else str(value).strip()

    # Second pass: derived columns (may reference values above).
    for col in columns:
        if col.get('class') != 'derived':
            continue
        try:
            row[col['key']] = str(_value_derived(col, accum=row, taxonomy=taxonomy)).strip()
        except Exception:
            logger.exception('Derived column %s failed', col['key'])
            row[col['key']] = ''

    # NA fallback — applied to ai_extract, derived, and batch_or_extract columns.
    for col in columns:
        if col.get('class') in ('ai_extract', 'derived', 'batch_or_extract'):
            if not row.get(col['key']):
                row[col['key']] = col.get('fallback', na)

    # ---------------------------------------------------------------
    # Vision AI enrichment (post-pass). Only fills columns still equal
    # to the NA placeholder — never overwrites a regex-extracted value.
    # No-op when OPENAI_API_KEY is missing.
    # ---------------------------------------------------------------
    try:
        from . import vision_extractor
        # Build a sanitized view where 'NA' counts as empty, so the
        # enricher knows which fields still need attention.
        view = {k: ('' if str(v).strip().upper() == na.upper() else v)
                for k, v in row.items()}
        enrichment = vision_extractor.enrich_via_vision(
            file_path=file_path, file_name=file_name,
            current_row=view, na_value=na,
            ocr_text=text,
        )
        if enrichment:
            logger.info('Vision enrichment for %s filled %d field(s): %s',
                        file_name, len(enrichment), list(enrichment.keys()))
            for k, v in enrichment.items():
                if not v:
                    continue
                # Only fill cells the regex pipeline left empty/NA.
                cur = str(row.get(k, '')).strip()
                if (not cur) or cur.upper() == na.upper():
                    row[k] = v
    except Exception:
        logger.exception('vision enrichment failed for %s', file_name)

    # ---------------------------------------------------------------
    # Document Title — final authoritative pass. The taxonomy-driven
    # smart extractor scans the document text for an indicator-bearing
    # title line (data sheet, flow diagram, support, etc.) and merges
    # adjacent uppercase continuation lines. We prefer its result over
    # whatever the regex / vision pipeline produced UNLESS the existing
    # value already contains a strong title indicator (then it is at
    # least as good and we keep it to avoid churn).
    # ---------------------------------------------------------------
    if DOCUMENT_TITLE_SMART_EXTRACT and text:
        cur_title = str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or '').strip()
        cur_is_na = (not cur_title) or cur_title.upper() == na.upper()
        indicators = _build_title_indicator_regexes(taxonomy)
        cur_has_indicator = (not cur_is_na) and any(
            p.search(cur_title) for p in indicators
        )
        if cur_is_na or not cur_has_indicator:
            smart = _extract_title_smart(text, taxonomy)
            if smart and any(p.search(smart) for p in indicators):
                row[DOCUMENT_TITLE_FIELD_KEY] = smart

    # ---------------------------------------------------------------
    # Document Title — unconditional sanitisation pass. Runs on the
    # final stored value regardless of which upstream stage produced
    # it (regex pipeline, vision, smart extractor) so OCR debris like
    # "o; AMINE RECOVERY ..." or "x | 5 oe TYPICAL ..." never reaches
    # the UI. Pure-garbage titles (high single-letter ratio, embedded
    # mojibake) are replaced with the NA placeholder.
    # ---------------------------------------------------------------
    cur_title = str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or '').strip()
    if cur_title and cur_title.upper() != na.upper():
        cleaned = _sanitise_title_candidate(cur_title)
        if cleaned and _is_clean_text(cleaned[:120], TEXT_QUALITY_CONFIG):
            # Final cosmetic polish — strips decorative symbols, NA tokens
            # and punctuation runs. Soft-coded via TITLE_POLISH_CONFIG.
            polished = _polish_title(cleaned)
            if polished:
                row[DOCUMENT_TITLE_FIELD_KEY] = polished[:_TITLE_MAX_LEN]
            else:
                row[DOCUMENT_TITLE_FIELD_KEY] = na
        else:
            # Failed quality gate (high single-letter ratio, mojibake,
            # weird punctuation runs) — do not pollute the UI.
            row[DOCUMENT_TITLE_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Document Issue Date — final authoritative pass. The smart
    # extractor handles flexible date shapes (`3/10/2000`, `20-12-99`,
    # `1/28/1999`, …) and prefers tokens near a "DATE"/"ISSUED" label,
    # falling back to the most-recent valid date when no label sits
    # nearby. Only overrides when the existing value parses to a
    # *worse* (older or invalid) date.
    #
    # Whatever value we settle on (smart pick or kept existing) is
    # finally re-formatted via `_normalise_issue_date` so the UI
    # column always uses `ISSUE_DATE_OUTPUT_FORMAT` (MM/DD/YYYY by
    # default). This is purely a display normalisation — the parsed
    # (year, month, day) is the source of truth.
    # ---------------------------------------------------------------
    if text:
        cur_date = str(row.get(ISSUE_DATE_FIELD_KEY, '') or '').strip()
        cur_is_na = (not cur_date) or cur_date.upper() == na.upper()
        smart_date = _extract_issue_date_smart(text)
        if smart_date:
            if cur_is_na:
                row[ISSUE_DATE_FIELD_KEY] = smart_date
            else:
                cur_ymd = _date_sort_key(cur_date)
                new_ymd = _date_sort_key(smart_date)
                if cur_ymd[0] == 0 and new_ymd[0] > 0:
                    row[ISSUE_DATE_FIELD_KEY] = smart_date

    # Standardise the stored issue-date token to ISSUE_DATE_OUTPUT_FORMAT.
    # Runs whether the value came from the smart pass, an upstream stage,
    # or was left untouched. When `ISSUE_DATE_DROP_INVALID` is True (the
    # default) tokens that fail strict (Y,M,D) validation — e.g.
    # ``62-00-002`` / ``55-18-005`` which are document-number leaks — are
    # replaced with the NA placeholder so they never reach the UI.
    final_date = str(row.get(ISSUE_DATE_FIELD_KEY, '') or '').strip()
    if final_date and final_date.upper() != na.upper():
        normalised = _normalise_issue_date(final_date)
        if normalised:
            row[ISSUE_DATE_FIELD_KEY] = normalised
        elif ISSUE_DATE_DROP_INVALID:
            row[ISSUE_DATE_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Revision — final authoritative pass.
    #
    # Priority:
    #   1. Label-anchored smart extractor (looks for "REV: 1",
    #      "Revision 0", stacked title-block columns, etc.). When it
    #      returns a value it ALWAYS wins — upstream regex and vision
    #      hits are unreliable because they grab any 1-3 char token
    #      after the word "Rev" (e.g. "Rev 27" where 27 is a date).
    #   2. Existing value, if it normalises (in range, not blocklisted,
    #      single letter or numeric ≤ `_REVISION_MAX_NUMERIC`).
    #   3. Otherwise → NA. We deliberately CLEAR garbage like "NM",
    #      "27", "ABU DHABI" rather than leave it for users to fix.
    #
    # OCR character-doubling is collapsed before normalisation so
    # artefacts like "00" / "AA" don't poison the column.
    # ---------------------------------------------------------------
    cur_rev = str(row.get(REVISION_FIELD_KEY, '') or '').strip().upper()
    cur_rev_na = (not cur_rev) or cur_rev == na.upper()
    rev_text_doubled = bool(text) and _text_has_ocr_doubling(text)
    if not cur_rev_na and rev_text_doubled:
        collapsed = _collapse_doubled_token(cur_rev)
        if collapsed != cur_rev:
            cur_rev = collapsed
    smart_rev = _extract_revision_smart(text) if text else ''
    if smart_rev:
        # Smart match is label-anchored — most reliable signal.
        row[REVISION_FIELD_KEY] = smart_rev
    else:
        cur_normed = '' if cur_rev_na else _normalise_revision_value(cur_rev)
        if cur_normed:
            row[REVISION_FIELD_KEY] = cur_normed
        elif not cur_rev_na:
            # Upstream produced something that can't be normalised
            # (e.g. "NM", "27", "ABU DHABI"). Clear it rather than
            # letting it propagate.
            row[REVISION_FIELD_KEY] = na

    # Engineering convention: "no revision recorded" → "0" (initial issue).
    # Soft-coded via REVISION_NA_AS_ZERO so this can be turned off if a
    # downstream consumer needs to distinguish missing-vs-rev-0.
    if REVISION_NA_AS_ZERO:
        final_rev = str(row.get(REVISION_FIELD_KEY, '') or '').strip()
        if (not final_rev) or final_rev.upper() == na.upper():
            row[REVISION_FIELD_KEY] = '0'

    # ---------------------------------------------------------------
    # Revision Description / Status — final authoritative pass. The
    # smart extractor returns full canonical phrases (e.g. "AS-BUILT
    # AS PER PROJ. NO. 5247", "RE-ISSUED FOR CONSTRUCTION", "IFP")
    # rather than 3-letter acronyms. Only overrides when the current
    # value is empty/NA or is shorter than the smart match (i.e. the
    # smart match adds detail).
    # ---------------------------------------------------------------
    if text:
        cur_status = str(row.get(REVISION_STATUS_FIELD_KEY, '') or '').strip()
        cur_status_na = (not cur_status) or cur_status.upper() == na.upper()
        smart_status = _extract_revision_status_smart(text)
        if smart_status:
            if cur_status_na or len(smart_status) > len(cur_status):
                row[REVISION_STATUS_FIELD_KEY] = smart_status

    # ---------------------------------------------------------------
    # ADNOC Project No. — final authoritative pass.
    #
    # Priority:
    #   1. Smart label-anchored extractor scanning text + title +
    #      filename + path. Always wins when it returns a value.
    #   2. Existing value, only if it passes
    #      `_is_valid_adnoc_project_value` (3..7 digits, not a year).
    #   3. Otherwise → NA. Clears upstream noise (vendor names,
    #      addresses, doc-no fragments) instead of leaving it in.
    # ---------------------------------------------------------------
    cur_proj = str(row.get(ADNOC_PROJECT_NO_FIELD_KEY, '') or '').strip()
    cur_proj_na = (not cur_proj) or cur_proj.upper() == na.upper()
    smart_proj = _extract_adnoc_project_smart(
        text or '',
        title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
        file_name=file_name or '',
        relative_path=relative_path or '',
    )
    if smart_proj:
        row[ADNOC_PROJECT_NO_FIELD_KEY] = smart_proj
    elif not cur_proj_na and not _is_valid_adnoc_project_value(cur_proj):
        row[ADNOC_PROJECT_NO_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Project Title / Name — final authoritative pass.
    #
    # Priority:
    #   1. Smart canonical-phrase + labelled-fallback extractor scans
    #      text + title + filename + path. Always wins when it returns
    #      a value (the canonical map normalises hyphen / parenthesis
    #      variants to a single reference form).
    #   2. Existing value, only if `_is_valid_project_title` (≥8 chars,
    #      contains PROJECT/DEVELOPMENT/FIELD, not NA/junk).
    #   3. Otherwise → NA. Avoids leaving partial / noisy values in.
    # ---------------------------------------------------------------
    cur_pt = str(row.get(PROJECT_TITLE_FIELD_KEY, '') or '').strip()
    cur_pt_na = (not cur_pt) or cur_pt.upper() == na.upper()
    smart_pt = _extract_project_title_smart(
        text or '',
        title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
        file_name=file_name or '',
        relative_path=relative_path or '',
    )
    if smart_pt:
        row[PROJECT_TITLE_FIELD_KEY] = smart_pt
    elif not cur_pt_na and not _is_valid_project_title(cur_pt):
        row[PROJECT_TITLE_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Document-Control reference columns — final authoritative pass.
    #
    # Scans body text + title hint + file name + path with a soft-coded
    # label vocabulary. Smart extractor wins when:
    #   • current cell is blank / NA, OR
    #   • current cell fails its validator (junk, too short/long).
    # When current already passes, we leave it alone (manual edits and
    # batch defaults remain authoritative).
    # ---------------------------------------------------------------
    _docref_passes = (
        (CONTRACTOR_REF_FIELD_KEY,  _extract_contractor_ref_smart,  _is_valid_docref),
        (VENDOR_REF_FIELD_KEY,      _extract_vendor_ref_smart,      _is_valid_docref),
        (ORIGINATOR_FIELD_KEY,      _extract_originator_smart,      _is_valid_originator),
        (AGREEMENT_NO_FIELD_KEY,    _extract_agreement_no_smart,    _is_valid_docref),
        (AGREEMENT_DESC_FIELD_KEY,  _extract_agreement_desc_smart,  _is_valid_agreement_desc),
    )
    for _key, _smart_fn, _validator in _docref_passes:
        _cur = str(row.get(_key, '') or '').strip()
        _cur_na = (not _cur) or _cur.upper() == na.upper()
        _smart = _smart_fn(
            text or '',
            title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
            file_name=file_name or '',
            relative_path=relative_path or '',
        )
        if _smart and (_cur_na or not _validator(_cur)):
            row[_key] = _smart
        elif not _cur_na and not _validator(_cur):
            row[_key] = na

    # ---------------------------------------------------------------
    # Plant — final authoritative pass.
    #
    # Reference values are project-derived ("HABSHAN-II",
    # "HABSHAN - 2", "HABSHAN-2") so the smart extractor canonicalises
    # every variant to a single reference form per family. Scan order:
    #   1. Keyword canonical map (`_PLANT_KEYWORD_PATTERNS`) over
    #      body + title + filename + path.
    #   2. Labelled "PLANT:" / "FACILITY:" fallback.
    #   3. ADNOC project-no lookup (`_PLANT_BY_ADNOC_PROJECT_NO`).
    # Smart wins when current is blank / NA / fails validator.
    # ---------------------------------------------------------------
    cur_plant = str(row.get(PLANT_FIELD_KEY, '') or '').strip()
    cur_plant_na = (not cur_plant) or cur_plant.upper() == na.upper()
    smart_plant = _extract_plant_smart(
        text or '',
        title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
        file_name=file_name or '',
        relative_path=relative_path or '',
        adnoc_project_no=str(row.get(ADNOC_PROJECT_NO_FIELD_KEY, '') or ''),
    )
    if smart_plant:
        # Filename-prefix family is the document-control source of truth;
        # always override AI / batch values to keep the variant
        # (HABSHAN-II vs HABSHAN - 2 vs HABSHAN-2) per reference.
        row[PLANT_FIELD_KEY] = smart_plant
    elif not cur_plant_na and not _is_valid_plant(cur_plant):
        row[PLANT_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Purchase Order No. — strict NA-clear pass.
    #
    # Reference sample shows PO=NA on every row. Real PO numbers are
    # alpha-numeric with hyphens / slashes (4..30 chars, must contain
    # a digit). Anything else (prose fragments, OCR noise, lone words)
    # is cleared to NA. We do NOT add a body-scan extractor here —
    # the existing labelled `pattern_lookup` is authoritative when a
    # PO is present; this pass only sanitises noise.
    # ---------------------------------------------------------------
    cur_po = str(row.get(PO_NO_FIELD_KEY, '') or '').strip()
    cur_po_na = (not cur_po) or cur_po.upper() == na.upper()
    if not cur_po_na and not _is_valid_po_no(cur_po):
        row[PO_NO_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Contractor Doc Reference — body-scan fallback.
    #
    # Reference values include project-prefixed codes that aren't
    # always introduced by a "CONTRACTOR DOC NO:" label
    # (e.g. "5610Y-STC-01-1381-026", "5247-HMB-500-00-20-015",
    #  "SPS-017"). When the existing column is blank / NA after the
    # main docref pass above, run the soft-coded prefix scan.
    # ---------------------------------------------------------------
    cur_cref = str(row.get(CONTRACTOR_REF_FIELD_KEY, '') or '').strip()
    cur_cref_na = (not cur_cref) or cur_cref.upper() == na.upper()
    if cur_cref_na:
        scanned_cref = _scan_contractor_codes_in_body(
            text or '',
            title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
            file_name=file_name or '',
            relative_path=relative_path or '',
        )
        if scanned_cref:
            row[CONTRACTOR_REF_FIELD_KEY] = scanned_cref

    # ---------------------------------------------------------------
    # Tag — final authoritative pass.
    #
    # Scans body text + title + filename + path with the soft-coded
    # `_TAG_PATTERNS` table covering every shape seen in the reference
    # sample (compact module, XV valves, numeric-prefix equipment,
    # material spec, numeric pairs, legacy P-101). Smart extractor
    # always wins when it returns >= the existing token count, so
    # under-extraction by `equipment_tag`/AI is automatically corrected.
    # ---------------------------------------------------------------
    cur_tag = str(row.get(TAG_FIELD_KEY, '') or '').strip()
    cur_tag_na = (not cur_tag) or cur_tag.upper() == na.upper()
    smart_tags = _extract_tags_smart(
        text or '',
        title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
        file_name=file_name or '',
        relative_path=relative_path or '',
    )
    if smart_tags:
        cur_tokens = [t for t in re.split(r'[,\s]+', cur_tag) if t and t.upper() != na.upper()]
        new_tokens = smart_tags.split(',')
        # Smart wins when current is empty/NA, or smart has more tokens.
        if cur_tag_na or len(new_tokens) > len(cur_tokens):
            row[TAG_FIELD_KEY] = smart_tags
        else:
            # Merge — preserve manual edits while filling in misses.
            merged: List[str] = []
            seen_m: set = set()
            for tok in cur_tokens + new_tokens:
                if tok and tok not in seen_m:
                    seen_m.add(tok)
                    merged.append(tok)
                if len(merged) >= _TAG_MAX_PER_ROW:
                    break
            row[TAG_FIELD_KEY] = ','.join(merged)

    # ---------------------------------------------------------------
    # Unit — final authoritative pass.
    #
    # Priority:
    #   1. Smart extractor (label-anchored "UNIT 47", "& 48",
    #      filename / title hints) always wins when it returns a value.
    #   2. Existing value, only if it is digit-only / comma-joined
    #      (`_is_valid_unit_value`). Vision/OCR noise like "ABU DHABI",
    #      "Vessel", "Process" is rejected and cleared to NA.
    #   3. Multi-unit smart match overrides single-unit existing value.
    # ---------------------------------------------------------------
    if text or row.get(DOCUMENT_TITLE_FIELD_KEY):
        cur_unit = str(row.get(UNIT_FIELD_KEY, '') or '').strip()
        cur_unit_na = (not cur_unit) or cur_unit.upper() == na.upper()
        smart_unit = _extract_unit_smart(
            text or '',
            title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
            relative_path=relative_path or '',
            file_name=file_name or '',
        )
        if smart_unit:
            cur_tokens = [t for t in re.split(r'[,/&\s]+', cur_unit) if t]
            new_tokens = smart_unit.split(',')
            cur_is_valid = _is_valid_unit_value(cur_unit)
            # Smart wins when current is NA, invalid, or has fewer tokens.
            if cur_unit_na or not cur_is_valid or len(new_tokens) > len(cur_tokens):
                row[UNIT_FIELD_KEY] = smart_unit
        elif not cur_unit_na and not _is_valid_unit_value(cur_unit):
            # No smart match and existing value is non-numeric noise
            # ("ABU DHABI", "Vessel"). Clear it.
            row[UNIT_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Area — final authoritative pass. Combines literal "AREA:" labels,
    # filename / title / path keyword hints, and document_type+unit
    # derivation. Smart value with a canonical form (`_AREA_CANONICAL`)
    # always wins over earlier extractors; otherwise we only fill blanks.
    # ---------------------------------------------------------------
    cur_area = str(row.get(AREA_FIELD_KEY, '') or '').strip()
    cur_area_na = (not cur_area) or cur_area.upper() == na.upper()
    smart_area = _extract_area_smart(
        text or '',
        document_type=str(row.get(DOCUMENT_TYPE_FIELD_KEY, '') or ''),
        unit_value=str(row.get(UNIT_FIELD_KEY, '') or ''),
        relative_path=relative_path or '',
        file_name=file_name or '',
        title_hint=str(row.get(DOCUMENT_TITLE_FIELD_KEY, '') or ''),
    )
    if smart_area:
        canon = _normalise_area_value(smart_area)
        if canon:
            # Canonical form (COMMON / Process / PROCESS) always wins.
            row[AREA_FIELD_KEY] = canon
        elif cur_area_na:
            row[AREA_FIELD_KEY] = smart_area

    # ---------------------------------------------------------------
    # Document Number — final authoritative pass. Per reference sample
    # (Sample Metadata Extraction.xlsx) the document number column always
    # mirrors the file name stem, so we lock it in here regardless of
    # what regex / vision extractors produced.
    # ---------------------------------------------------------------
    if DOCUMENT_NUMBER_FROM_FILENAME:
        stem = os.path.splitext(file_name)[0]
        if stem:
            row[DOCUMENT_NUMBER_FIELD_KEY] = stem

    # ---------------------------------------------------------------
    # Document Sub-Type — final authoritative pass.
    #
    # Strategy (soft-coded via SUBTYPE_OVERRIDE_INVALID /
    # SUBTYPE_PREFER_TITLE_MATCH at the top of this module):
    #   1. If current value is empty / NA → run smart matcher.
    #   2. If current value is NOT a canonical taxonomy subtype
    #      (upstream regex / vision leak, free-text title, OCR junk,
    #      bare discipline name) → replace with smart match.
    #   3. Otherwise (current value is valid) → re-run smart matcher
    #      restricted to the document title; if the title produces a
    #      MORE SPECIFIC canonical subtype (longer alias hit), prefer
    #      it. This handles cases where the folder-level hint won and
    #      the title was clearer.
    # When a subtype is chosen and document_type is missing/NA, the
    # owning taxonomy key is back-filled into document_type as well.
    # ---------------------------------------------------------------
    current_sub_raw = str(row.get(DOCUMENT_SUBTYPE_FIELD_KEY, '') or '').strip()
    current_sub_empty = (not current_sub_raw) or current_sub_raw.upper() == na.upper()
    current_sub_valid = (not current_sub_empty) and _is_valid_subtype(current_sub_raw, taxonomy)
    should_run_smart = current_sub_empty \
        or (SUBTYPE_OVERRIDE_INVALID and not current_sub_valid) \
        or SUBTYPE_PREFER_TITLE_MATCH
    if should_run_smart:
        title_text = str(row.get('document_title', '') or '')
        if title_text.strip().upper() == na.upper():
            title_text = ''
        type_hint = str(row.get(DOCUMENT_TYPE_FIELD_KEY, '') or '').strip()
        if type_hint.upper() == na.upper():
            type_hint = ''
        # Only treat type_hint as authoritative if it is already a valid
        # taxonomy key — otherwise scan all types so subtype can drive type.
        valid_parent = type_hint if type_hint in (taxonomy.get('document_types') or {}) else ''
        # Detect OCR-noise titles. When the title is unreliable, swap
        # the priority corpus to filename + relative path so folder
        # naming wins over OCR garbage. Soft-coded via SUBTYPE_DEMOTE_OCR_TITLES.
        title_is_noise = (
            SUBTYPE_DEMOTE_OCR_TITLES
            and bool(title_text.strip())
            and _looks_like_ocr_noise_title(title_text)
        )
        if title_is_noise:
            # Filename without extension is usually the cleanest signal.
            fname_stem = file_name or ''
            if '.' in fname_stem:
                fname_stem = fname_stem.rsplit('.', 1)[0]
            priority = _normalise_corpus(fname_stem, relative_path)
            # Keep title in the wide corpus (it may still contain real
            # tokens) but it no longer drives the priority pass.
            corpus = _normalise_corpus(fname_stem, relative_path, title_text)
        else:
            priority = _normalise_corpus(title_text)
            corpus = _normalise_corpus(title_text, relative_path, file_name)
        title_corpus = priority  # alias used by the prefer-title pass below
        sub_match, owner = _match_subtype_smart(
            parent_type=valid_parent, corpus=corpus, taxonomy=taxonomy,
            priority_corpus=priority,
        )
        # Fall back to a body-text snippet when nothing matched
        # (titles + paths usually win, body text is noisy).
        if not sub_match and text:
            corpus2 = _normalise_corpus(text[:_MAX_SCAN_CHARS])
            sub_match, owner = _match_subtype_smart(
                parent_type=valid_parent, corpus=corpus2, taxonomy=taxonomy,
            )
        if sub_match:
            # Write when current is empty / NA / invalid.
            if current_sub_empty or (SUBTYPE_OVERRIDE_INVALID and not current_sub_valid):
                row[DOCUMENT_SUBTYPE_FIELD_KEY] = sub_match
                # Back-fill document_type when it was empty / NA / out-of-vocab.
                if owner and (not valid_parent):
                    row[DOCUMENT_TYPE_FIELD_KEY] = owner
            elif SUBTYPE_PREFER_TITLE_MATCH and current_sub_valid:
                # Current is valid; only override if the title gives us a
                # different canonical subtype (i.e. smart found a more
                # specific signal in the title than what's stored).
                if sub_match.strip().lower() != current_sub_raw.strip().lower():
                    # Title hit wins only when it differs AND came from the
                    # title corpus (priority pass). A second-pass body-text
                    # hit is too weak to overturn a valid stored value.
                    title_only_match, _ = _match_subtype_smart(
                        parent_type=valid_parent, corpus=title_corpus,
                        taxonomy=taxonomy, priority_corpus='',
                    )
                    if title_only_match and title_only_match == sub_match:
                        row[DOCUMENT_SUBTYPE_FIELD_KEY] = sub_match
                        if owner and (not valid_parent):
                            row[DOCUMENT_TYPE_FIELD_KEY] = owner
        elif current_sub_empty or (SUBTYPE_OVERRIDE_INVALID and not current_sub_valid):
            # Couldn't find a match AND the current value is invalid /
            # empty — clear to NA rather than leaving garbage.
            row[DOCUMENT_SUBTYPE_FIELD_KEY] = na

    # ---------------------------------------------------------------
    # Document Type — final sanitiser pass. Reference sample only ever
    # contains canonical taxonomy keys (e.g. "Piping", "Process"). When
    # the regex/vision pipeline leaks a free-text value (typically the
    # document title, like "VARIABLE SPRING SUPPORTS DATA SHEET"), snap
    # it back to the closest valid taxonomy key — using the row's own
    # document_subtype as the strongest hint.
    # ---------------------------------------------------------------
    if DOCUMENT_TYPE_STRICT_TAXONOMY:
        current_type = row.get(DOCUMENT_TYPE_FIELD_KEY, '') or ''
        current_sub  = row.get(DOCUMENT_SUBTYPE_FIELD_KEY, '') or ''
        # Treat NA placeholder as empty for sanitiser purposes.
        if current_type.strip().upper() == na.upper():
            current_type = ''
        if current_sub.strip().upper() == na.upper():
            current_sub = ''
        snapped = _sanitize_document_type(current_type, current_sub, taxonomy)
        if snapped:
            row[DOCUMENT_TYPE_FIELD_KEY] = snapped
        elif current_type:
            # Out-of-vocabulary value with no recoverable hint — drop it
            # rather than letting it pollute the column.
            logger.info(
                'Document Type "%s" for %s not in taxonomy and unrecoverable '
                '— resetting to NA', current_type, file_name,
            )
            row[DOCUMENT_TYPE_FIELD_KEY] = na

        # Re-derive any column that depends on document_type (e.g. discipline)
        # so the final row stays internally consistent after the sanitiser.
        for col in columns:
            if col.get('class') == 'derived' and col.get('derive_from') == DOCUMENT_TYPE_FIELD_KEY:
                try:
                    new_val = _value_derived(col, accum=row, taxonomy=taxonomy)
                except Exception:
                    new_val = ''
                row[col['key']] = (str(new_val).strip()
                                   if new_val else col.get('fallback', na))

    # ---------------------------------------------------------------
    # Author ⇄ Originator mirror — soft-coded post-pass. Reference
    # samples show both columns hold the same EPC contractor name. When
    # the regex pipeline only hit one of the two, copy the value across.
    # ---------------------------------------------------------------
    _apply_author_originator_mirror(row, na)

    # ---------------------------------------------------------------
    # Source Folder — derive from the file's actual relative_path so
    # each row reflects its own sub-folder, not the form's "top folder".
    # Falls back to the batch default only when the file is at the root.
    # Soft-coded via SOURCE_FOLDER_CONFIG.
    # ---------------------------------------------------------------
    _apply_source_folder_smart(
        row,
        relative_path=relative_path or '',
        file_name=file_name or '',
        batch_defaults=batch_defaults or {},
        na=na,
    )

    return row
