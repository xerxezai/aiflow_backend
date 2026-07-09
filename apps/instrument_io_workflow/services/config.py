"""
Soft-coded configuration for the Instrument IO List Workflow.

Every threshold, regex, keyword list, header alias, and feature flag lives
here. NEVER hardcode these constants inside service modules.
"""

from django.conf import settings


# ───────────────────────────────────────────────────────────────────────
# Feature flags
# ───────────────────────────────────────────────────────────────────────
# Vision fallback is OFF by default to keep extraction free.
# Set INSTRUMENT_IO_ENABLE_VISION_FALLBACK=true in env to enable.
ENABLE_VISION_FALLBACK = getattr(
    settings, 'INSTRUMENT_IO_ENABLE_VISION_FALLBACK', False
)

# Vision model used only when the heuristic + table extractor fails on a page.
# gpt-4o-mini is ~10x cheaper than gpt-4o and sufficient for tabular OCR.
VISION_MODEL = getattr(settings, 'INSTRUMENT_IO_VISION_MODEL', 'gpt-4o-mini')

# Rendering DPI for vision fallback. 100 DPI keeps tokens low; 150 if text dense.
VISION_RENDER_DPI = int(getattr(settings, 'INSTRUMENT_IO_VISION_DPI', 110))

# Per-document hard cap on vision pages to prevent runaway cost.
VISION_MAX_PAGES_PER_DOC = int(getattr(settings, 'INSTRUMENT_IO_VISION_MAX_PAGES', 6))

# Result caching — if a previously-uploaded PDF hash matches, return cached rows
# instead of re-extracting. Major cost saver on repeat uploads / reviews.
ENABLE_HASH_CACHE = getattr(settings, 'INSTRUMENT_IO_HASH_CACHE', True)


# ───────────────────────────────────────────────────────────────────────
# Page classifier — heuristic keywords (case-insensitive, regex-friendly)
# ───────────────────────────────────────────────────────────────────────
PAGE_TYPES = {
    'cover':           ['cover sheet', 'document title', 'revision history'],
    'index':           ['index', 'table of contents', 'list of contents'],
    'notes':           ['general notes', 'abbreviation', 'reference', 'legend'],
    'comments_sheet':  [
        'comments resolution sheet',
        'comment resolution sheet',
        'company comment',
        'contractor reply',
        'company decision',
        's.no',  # very common header in comment sheets
    ],
    'io_table':        [
        'tag number', 'loop number', 'i/o type', 'signal type',
        'hmi description', 'instrument type', 'dcs', 'esd',
        'marsh cab', 'jb number', 'cable no',
    ],
}

# Minimum heuristic hits per page to declare a classification confident.
PAGE_TYPE_MIN_HITS = 2


# ───────────────────────────────────────────────────────────────────────
# Comments Resolution Sheet schema — 5-column ADNOC standard
# ───────────────────────────────────────────────────────────────────────
COMMENT_SHEET_COLUMNS = [
    's_no',
    'company_comment',
    'contractor_reply',
    'company_decision',
    'status_code',
]

# Heuristic for status code mapping (ADNOC convention)
STATUS_CODE_MEANING = {
    '1': 'Rejected — Revise & Resubmit',
    '2': 'Comments as Noted',
    '3': 'No Comments',
    '4': 'Information Only',
}

# Header aliases — used to detect the comment table header row.
COMMENT_HEADER_ALIASES = {
    's_no':              ['s.no', 'sno', 'sr.no', 'sl.no', 'no.', 'sl no', 'item'],
    'company_comment':   ['company comment', 'reviewer comment', 'comment', 'company observation'],
    'contractor_reply':  ['contractor reply', 'vendor reply', 'reply', 'contractor response'],
    'company_decision':  ['company decision', 'decision', 'final decision', 'status'],
    'status_code':       ['status code', 'code', 'class', 'category'],
}


# ───────────────────────────────────────────────────────────────────────
# IO List table schema — canonical ADNOC 25+ columns
# (kept in parity with frontend src/config/ioList* IO_LIST_COLUMNS)
# ───────────────────────────────────────────────────────────────────────
IO_LIST_CANONICAL_COLUMNS = [
    'tag_number', 'loop_number', 'pid_no', 'instrument_type',
    'service_description', 'hmi_description', 'from_location', 'to_location',
    'status', 'io_type', 'system', 'hmi_tag', 'signal_type',
    'is_nis', 'voltage_level', 'wire_type', 'wet_dry', 'no_nc',
    'mos', 'oos', 'sys_range_min', 'sys_range_max', 'unit',
    'alarm_h', 'alarm_hh', 'alarm_l', 'alarm_ll',
    'state_text_0', 'state_text_1', 'alarm_priority', 'voting',
    'marsh_cab_no', 'io_group_no', 'sys_cab_no', 'jb_number',
    'intercon_dwg', 'loop_dwg', 'pri_cable_no', 'cable_size',
    'pr_tr_core', 'remarks', 'revision',
]

# Header alias map — every variation the extractor will encounter.
# Keys = canonical name, values = list of textual variants (lower-cased compare).
IO_HEADER_ALIASES = {
    'tag_number':         ['tag number', 'tag no', 'tag', 'instrument tag'],
    'loop_number':        ['loop number', 'loop no', 'loop'],
    'pid_no':             ['p&id no', 'p&id number', 'pid no', 'p&id'],
    'instrument_type':    ['instrument type', 'inst type', 'type'],
    'service_description':['service description', 'service', 'description'],
    'hmi_description':    ['hmi description', 'hmi desc'],
    'from_location':      ['from'],
    'to_location':        ['to'],
    'status':             ['status'],
    'io_type':            ['i/o type', 'io type', 'i o type'],
    'system':             ['system', 'sys', 'dcs/esd'],
    'hmi_tag':            ['hmi tag'],
    'signal_type':        ['signal type', 'signal'],
    'is_nis':             ['is/nis', 'is nis'],
    'voltage_level':      ['voltage lvl', 'voltage level', 'voltage'],
    'wire_type':          ['wire type', 'wire'],
    'wet_dry':            ['wet/dry', 'wet dry'],
    'no_nc':              ['no/nc', 'no nc'],
    'mos':                ['mos'],
    'oos':                ['oos'],
    'sys_range_min':      ['sys range min', 'range min', 'min'],
    'sys_range_max':      ['sys range max', 'range max', 'max'],
    'unit':               ['unit', 'engineering unit', 'eng unit'],
    'alarm_h':            ['h', 'alarm h', 'high'],
    'alarm_hh':           ['hh', 'alarm hh', 'high high'],
    'alarm_l':            ['l', 'alarm l', 'low'],
    'alarm_ll':           ['ll', 'alarm ll', 'low low'],
    'state_text_0':       ['state text 0', 'state 0', 'text 0'],
    'state_text_1':       ['state text 1', 'state 1', 'text 1'],
    'alarm_priority':     ['alarm priority', 'priority'],
    'voting':             ['voting', 'voting no', '1oo2', '2oo3'],
    'marsh_cab_no':       ['marsh cab no', 'marshalling cab', 'marsh cabinet'],
    'io_group_no':        ['io group no', 'io group'],
    'sys_cab_no':         ['sys cab no', 'system cabinet', 'sys cabinet'],
    'jb_number':          ['jb number', 'jb no', 'junction box'],
    'intercon_dwg':       ['intercon dwg no', 'interconnect dwg', 'intercon drawing'],
    'loop_dwg':           ['loop dwg no', 'loop drawing'],
    'pri_cable_no':       ['pri cable no', 'primary cable', 'cable no'],
    'cable_size':         ['cable size'],
    'pr_tr_core':         ['pr./tr./core', 'pr tr core', 'pair core'],
    'remarks':            ['remarks', 'remark', 'notes'],
    'revision':           ['rev', 'rev.', 'revision'],
}


# ───────────────────────────────────────────────────────────────────────
# Tag-number regex (used by comment-to-row linker, FREE)
# ───────────────────────────────────────────────────────────────────────
# ADNOC tag patterns: 113-PT-3193A, 113-XV-9501, 113-FT-1234B
TAG_NUMBER_REGEX = r'\b\d{2,4}-[A-Z]{1,4}-\d{3,5}[A-Z]?\b'


# ───────────────────────────────────────────────────────────────────────
# Revision-chain integration
# ───────────────────────────────────────────────────────────────────────
# When a user links a document into a CRS chain, these defaults seed the chain.
CHAIN_DEFAULT_MAX_REVISIONS = 10
CHAIN_RISK_THRESHOLDS = {'low': 3, 'medium': 5, 'high': 7, 'critical': 9}


# ───────────────────────────────────────────────────────────────────────
# Performance limits
# ───────────────────────────────────────────────────────────────────────
MAX_PAGES_PER_PDF = int(getattr(settings, 'INSTRUMENT_IO_MAX_PAGES', 200))
MAX_ROWS_PER_DOC  = int(getattr(settings, 'INSTRUMENT_IO_MAX_ROWS',  20000))
