"""
Deterministic Rule Engine
==========================
ALL validation logic is pure Python with no AI/ML calls.
Same extraction input ALWAYS produces identical findings.

Rule catalogue:
  TAG-001  Missing tags on instruments / valves
  TAG-002  Duplicate tag within drawing
  TAG-003  Tag format inconsistency  
  TAG-004  Tag referenced in notes but absent from drawing

  CON-001  Isolated instrument (no pipeline connection)
  CON-002  Isolated valve     (no pipeline connection)
  CON-003  Orphan node        (no connections at all in graph)

  VLV-001  Valve without a tag
  VLV-002  Globe valve bore exceeds maximum recommended NPS (default 16")
  VLV-003  Control valve bore exceeds maximum recommended NPS (default 16")

  EQP-001  Equipment tag present but not in master list pattern
  EQP-002  Equipment item code not in project equipment catalogue

  LSZ-001  Missing line size text for known pipelines
  LSZ-002  Conflicting line sizes on the same line segment
  LSZ-003  Valve bore size does not match connected line size
  LSZ-004  Conflicting inline size annotations on the same OCR line reference
           (multiple distinct NPS sizes on a line containing a line-designation token)
  LSZ-005  3+ distinct nominal sizes on the drawing — possible undocumented spec-breaks
  LSZ-006  Same pipeline base with conflicting NPS sizes
  LSZ-007  Same pipeline designation 3+ times in one orientation
  LSZ-008  Pipeline designation confirmed in both H and V orientations
  LSZ-009  Cloud-truncated duplicate pipeline designation
  LSZ-010  Shared sequence-number / pipe-class / insulation suffix across
           different pipeline identities (area codes) on the same drawing
           -- strong indicator of a copy-paste error in line numbering
  LSZ-011  Extreme reducer annotation — size reduction ratio exceeds soft-coded maximum
           (default 2.5:1, e.g. 6"x2" = 3:1 flags)
  LSZ-012  Equipment size annotation does not match any line designation NPS on drawing
           (e.g. "20\" in VORTEX BREAKER" with no 20" line tag)

  NTS-001  NOTES section present but no tag references found
  NTS-002  HOLD item detected – requires action

  LN-001   Invalid service/fluid code in line designation
  LN-002   Invalid insulation-class suffix in line designation

  RED-001  Red-colored annotation detected (revision mark / HOLD / scope-change indicator)
           Vector PDFs only — scanned drawings return no color metadata.

  ANN-001  Pressure annotation exceeds soft-coded threshold (default 50 bar)
           Pattern: "{class} @ {N} bar" — validate piping class handles stated pressure.
"""
import re
import math
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Pipeline line-designation pattern (mirrors extraction.py — kept local so
#    rule_engine remains self-contained and importable without extraction).
#    Matches e.g.  2"-D-6156-033842-X-N  /  4"-BD-4860-013842-X
_PIPELINE_DESIG_RE = re.compile(
    r'(?<![A-Za-z0-9])'
    r'(\d+(?:\.\d+)?)'
    r'\s*["\u201c\u201d\u2019\u2018\'`]{1,2}'
    r'[\s\-_]{0,3}'
    r'([A-Z]{1,4})'
    r'[\s\-_]+'
    r'(\d{3,6})'
    r'[\s\-_]+'
    r'(\d{4,8})'
    r'(?:[\s\-_]+([A-Z0-9]{1,8}))?'
    r'(?:[\s\-_]+([A-Z0-9]{1,4}))?'
    r'(?![A-Za-z0-9])',
    re.IGNORECASE,
)

# ── Reducer notation pattern (mirrors extraction.py — kept local for same reason).
#    Matches e.g.  6"x2"  /  6X2  /  6"×2"
_REDUCER_RE = re.compile(
    r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')?'
    r'\s*[xX×]\s*'
    r'(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')?',
)

# ── Expected tag format:  PREFIX-NUMBER(optional letter)
_TAG_FORMAT_RE = re.compile(r'^[A-Z]{1,4}-[0-9]{3,5}[A-Z]?$')
# Prefixes that MUST have a tag
_TAGGED_VALVE_PREFIXES = {
    'HV', 'FV', 'XV', 'PV', 'SDV', 'BDV', 'CV', 'LV', 'TV',
    # Mubarraz project additions (PJ6-EXD-GEN-BQDA-0002)
    'SSV', 'MSV', 'MOV', 'SOV', 'DBB',
}
_INSTRUMENT_PREFIXES = {
    'FT', 'FI', 'FIC', 'PT', 'PI', 'PIC', 'LT', 'LI', 'LIC',
    'TT', 'TI', 'TIC', 'AT', 'AI', 'FY', 'PY', 'LY',
    # Mubarraz project additions (PJ6-EXD-GEN-BQDA-0002)
    'ZT', 'ZSH', 'ZSL', 'ZSHH', 'ZSLL', 'ZI',
    'ST', 'SI', 'IT', 'II', 'VT', 'VI', 'VSH', 'VSHH',
    'DT', 'DI', 'DPT', 'DPI',
    'WT', 'WI', 'JT', 'JI', 'OT', 'OI', 'UT', 'UI', 'RT',
    'NOC', 'GWR', 'WC', 'GVF',
}

# ---------------------------------------------------------------------------
# Soft-coded project-specific legend knobs (LN-001, LN-002, EQP-002)
# Source: PJ6-EXD-GEN-BQDA-0002 Rev 1 — all edits here, no code deploy needed.
# ---------------------------------------------------------------------------
_VALID_SERVICE_CODES = {
    'AM', 'BD', 'CH', 'CL', 'CR', 'D', 'DC', 'DF', 'DL', 'DO', 'DR', 'DW',
    'FG', 'FL', 'FW', 'G', 'GL', 'HC', 'HO', 'HL', 'IA', 'IW', 'ME', 'N',
    'O', 'P', 'RW', 'SG', 'SW', 'TW', 'UA', 'UW', 'VG', 'W', 'XN',
    'AC', 'CA', 'SA',  # miscellaneous chemical services
}
# N = No insulation / Normal — standard Mubarraz project suffix for uninsulated lines.
# Every normal line designation on this project ends in -N (e.g. 4"-BD-4860-033842-X-N).
_VALID_INSULATION_CLASSES = {'C', 'H', 'P', 'T', 'N'}  # Cold / Heat / Personnel / Tracing / None

# Equipment item-code catalogue from legend Sheet 001
_VALID_EQUIPMENT_ITEM_CODES = {
    'P', 'V', 'E', 'K', 'T', 'C', 'J', 'G', 'M', 'F', 'R', 'D', 'B', 'H',
    'W', 'S', 'N',
    'OF', 'OP', 'MG', 'MX', 'PY', 'SG', 'TR', 'WM', 'FL', 'FV',
}

# Soft-coding policy for orphan connectivity findings.
# Instead of hard yes/no only, we score confidence and map to severity.
_ORPHAN_CONFIDENCE_HIGH = 0.75
_ORPHAN_CONFIDENCE_MEDIUM = 0.45
_ORPHAN_LOW_TEXT_CUTOFF = 60

# ── LSZ-004 soft-coded knobs ─────────────────────────────────────────────────
# Maximum NPS size (inches) to accept in a line-size token.
# Anything above this is treated as OCR noise (e.g. drawing sheet dimensions).
_LSZ004_MAX_NPS_INCH = 24.0
# Regex to extract well-formed pipeline designation tokens from a noisy OCR line.
# Matches patterns like  4"-D-5749-013842-X-N  or  2"-BD-6003-033842-X
_LSZ004_LINE_TAG_RE = re.compile(
    r'\d{1,3}(?:\.\d+)?["\u201c\u201d]\s*-[A-Z]{1,4}-\d{3,6}-\d{4,10}(?:-[A-Z0-9]+)*',
    re.IGNORECASE,
)
# Maximum LSZ-004 findings emitted per drawing (avoids flooding the report).
_LSZ004_MAX_FINDINGS = 5
# Minimum distinct NPS sizes on a single OCR line to raise LSZ-004.
_LSZ004_MIN_SIZES = 2

# ── LSZ-005 soft-coded knobs ─────────────────────────────────────────────────
# Minimum number of *distinct* nominal sizes on a drawing to trigger LSZ-005.
# Increase to suppress; decrease to be more sensitive.
_LSZ005_MIN_DISTINCT_SIZES = 3
# Valid pipe-size range (inches) — filters out OCR artefacts.
_LSZ005_SIZE_MIN_INCH = 0.5   # ½" is the smallest common instrument line
_LSZ005_SIZE_MAX_INCH = 24.0  # 24" is the largest standard process-plant pipe (NPS)
# ── LSZ-010 soft-coded knobs ─────────────────────────────────────────────────
# Fields from line_tag that form the "shared suffix" used for grouping.
# Reorder or remove fields to tune sensitivity:
#   ('sequence_no', 'pipe_class', 'insulation') catches  013842-X-N  matches
#   ('sequence_no',)                             catches  013842      matches (wider net)
_LSZ010_SUFFIX_FIELDS = ('sequence_no', 'pipe_class', 'insulation')
# When True: only flag shared-suffix conflicts where fluid codes also match.
# Recommended True -- different fluid systems legitimately reuse sequence numbers.
_LSZ010_SAME_FLUID_ONLY = True
# When True: only flag when NPS sizes also match (tighter, fewer false positives).
_LSZ010_REQUIRE_SAME_SIZE = False
# Maximum LSZ-010 findings per drawing (avoids flooding the report on dense sheets).
_LSZ010_MAX_FINDINGS = 10
# Minimum non-empty suffix fields required to consider an entry checkable.
# Prevents empty-field entries from creating spurious cross-matches.
_LSZ010_MIN_SUFFIX_PARTS = 1

# ── LSZ-007 soft-coded knob ──────────────────────────────────────────────────
# Minimum occurrences of the same designation in a SINGLE orientation before
# LSZ-007 fires.  Raised from 2 → 3: two occurrences is normal on any pipe that
# is annotated at both its source and destination connection points on the sheet.
# Three in the same direction is the first reliable indicator of a copy-paste error.
_LSZ007_MIN_SAME_DIR_OCCURRENCES = 3

# ── LSZ-007 / LSZ-008 spatial-accuracy guards ────────────────────────────────
# LSZ-007: minimum spatial spread (any axis, % of drawing) between same-direction
# occurrences for the finding to be raised.  Below this threshold all occurrences
# sit within a tight cluster → OCR noise from one physical label read multiple
# times, NOT a copy-paste error across different pipe sections.
# Raise to reduce sensitivity; lower to catch smaller gaps.
_LSZ007_MIN_SPATIAL_SPREAD_PCT = 12.0

# LSZ-008: minimum Euclidean distance (% of drawing) between the nearest H and
# nearest V occurrence.  Below this distance the tag sits at a single pipe bend
# (normal P&ID routing where one label straddles the horizontal and vertical
# segments of the same pipe).  Only flag when the H and V labels are in genuinely
# different regions of the drawing, suggesting a copy-paste duplication rather
# than normal pipe routing.
_LSZ008_MIN_HV_DISTANCE_PCT = 18.0

# ── LSZ-011 soft-coded knobs ─────────────────────────────────────────────────
# Maximum size-reduction ratio for a single reducer transition.
# Example: 6"x2" = 3.0 (flags), 6"x4" = 1.5 (OK), 6"x3" = 2.0 (OK at threshold 2.5).
# Increase to be more permissive; decrease to catch smaller reductions.
_LSZ011_MAX_REDUCTION_RATIO = 2.5

# ── VLV-002 / VLV-003 soft-coded knobs ──────────────────────────────────────
# Maximum NPS bore (inches) for a globe valve before flagging as oversized.
# Globe valves have high pressure drop and body-cavity weight issues above NPS 16".
# References: ASME B16.10, vendor catalogues (Crane, Cameron, Flowserve).
_VLV_GLOBE_MAX_INCH = 16.0
# Maximum NPS bore for a control valve before flagging for verification.
# Large control valves (>16") require individual hydraulic sizing review.
_VLV_CONTROL_MAX_INCH = 16.0
# Valve-type keywords tied to VLV-002 / VLV-003 checks.
# Add new type strings here (matched case-insensitively) to extend coverage.
_VLV_GLOBE_KEYWORDS    = {'GLOBE VALVE', 'GLOBE'}
_VLV_CONTROL_KEYWORDS  = {
    'CONTROL VALVE', 'DISTRIBUTED CONTROL VALVE',
    'MODULATING VALVE', 'THROTTLE VALVE',
}

# ── ANN-001 soft-coded knobs ─────────────────────────────────────────────────
# Pressure annotation pattern: matches "{letter} @ {N} bar" or standalone "{N} bar"
# near a valve / line context.  Adjust regex or threshold as needed.
_ANN_PRESSURE_MAX_BAR  = 50.0   # Pressures above this trigger a critical finding
_ANN_PRESSURE_RE = re.compile(
    r'(?:[A-Z]\s*@\s*)?(\d+(?:\.\d+)?)\s*(?:bar[ga]?|BARG?|BAR)',
    re.IGNORECASE,
)

# ── CMP-001…CMP-008 soft-coded knobs ────────────────────────────────────────
# Keyword sets used by _check_compressor_equipment() to detect compressor tags,
# intercooler/aftercooler tokens, isolation-valve tokens, check-valve tokens,
# anti-surge / recycle / hot-gas bypass tokens, relief / blowdown tokens,
# ESD tokens, temperature measurement tokens, and driver keywords.
# Extend each set here — no logic changes needed.
_CMP_EQUIP_CODES = {'K', 'C', 'CM', 'CP', 'CMP'}           # ISA equipment prefix letters for compressors
_CMP_KEYWORDS    = {                                          # text patterns that identify a compressor block
    'COMPRESSOR', 'COMP ', 'CENTRIFUGAL COMPRESSOR',
    'RECIPROCATING COMPRESSOR', 'SCREW COMPRESSOR',
    'COMPRESSION UNIT', 'GAS COMPRESSOR',
}
_CMP_TYPE_KEYWORDS = {                                        # CMP-001: compressor-type tokens
    'CENTRIFUGAL', 'RECIPROCATING', 'SCREW', 'ROTARY',
    'AXIAL', 'DIAPHRAGM', 'TURBO', 'PISTON',
}
_CMP_COOLER_KEYWORDS = {                                      # CMP-002: intercooler / aftercooler
    'INTERCOOLER', 'INTER-COOLER', 'AFTERCOOLER', 'AFTER-COOLER',
    'LUBE OIL COOLER', 'SEAL GAS COOLER',
}
_CMP_ISOLATION_KEYWORDS = {'ISOLATION VALVE', 'BLOCK VALVE', 'ISOL', 'BLOCK'}  # CMP-002: isolation valves
_CMP_TEMP_KEYWORDS      = {'TI', 'TT', 'TE', 'TIC', 'TAH', 'TAL', 'TAHH', 'TALL',
                            'TEMPERATURE INDICATOR', 'TEMPERATURE TRANSMITTER'}  # CMP-002: temp instruments
_CMP_STRAINER_KEYWORDS  = {                                   # CMP-003: temporary strainers
    'TEMPORARY STRAINER', 'TEMP STRAINER', 'START-UP STRAINER',
    'COMMISSIONING STRAINER', 'CONE STRAINER', 'BASKET STRAINER',
}
_CMP_CHECK_VALVE_KEYWORDS = {                                 # CMP-004: check valves
    'CHECK VALVE', 'CHECK V', 'NRV', 'NON-RETURN VALVE',
    'NON RETURN VALVE', 'SWING CHECK', 'LIFT CHECK',
    'DUAL-PLATE CHECK', 'TILTING DISC CHECK',
}
_CMP_ANTISURGE_KEYWORDS = {                                   # CMP-005: anti-surge / recycle / hot-gas bypass
    'ANTI-SURGE', 'ANTISURGE', 'SURGE CONTROL',
    'RECYCLE', 'RECIRCULATION', 'HOT GAS BYPASS', 'HOT-GAS BYPASS',
}
_CMP_RELIEF_KEYWORDS = {                                      # CMP-006: relief and blowdown
    'RELIEF VALVE', 'PSV', 'PRV', 'PRESSURE SAFETY VALVE',
    'BLOWDOWN', 'BLOW-DOWN', 'BDV', 'BLOW DOWN VALVE',
    'RELIEF', 'PRESSURE RELIEF',
}
_CMP_ESD_KEYWORDS = {                                         # CMP-007: ESD / shutdown valves
    'ESD', 'ESDV', 'EMERGENCY SHUTDOWN', 'SHUTDOWN VALVE',
    'SDV', 'EMERGENCY STOP', 'TRIP VALVE',
}
_CMP_DRIVER_KEYWORDS = {                                      # CMP-008: driver identification
    'GAS TURBINE', 'GT', 'MOTOR', 'ELECTRIC MOTOR',
    'STEAM TURBINE', 'ENGINE', 'DIESEL ENGINE',
    'MECHANICAL DRIVE', 'TURBINE DRIVER',
}

# ── RED-001 soft-coded knobs ─────────────────────────────────────────────────
# Maximum number of RED-001 findings per drawing (cap to avoid flooding report
# when the entire drawing uses red as a default annotation color).
_RED001_MAX_FINDINGS = 15
# Minimum text length (characters) for a red annotation to be reported.
# Filters out single-character or empty stray marks.
_RED001_MIN_TEXT_LEN = 2

# ── RED-001 fragment-suppression patterns ────────────────────────────────────
# Vector PDF text rendering splits a full line designation like
# '2"-BD-6156-033842-X-N' into multiple colour spans.  Fragments such as
# '013842-X', 'BD', 'X-N' are meaningless on their own and must be suppressed.
# Patterns are fullmatch (anchored) and case-insensitive.
# Extend this list for future P&ID projects without touching rule logic.
_RED001_FRAGMENT_PATTERNS = [
    re.compile(r'^\d{3,8}$'),                           # pure digit seq/area code  e.g. 013842, 4860
    re.compile(r'^\d{4,8}(?:-[A-Z0-9]{1,8})+$', re.I), # seq+class suffix  e.g. 013842-X, 013842-X-N
    re.compile(r'^[A-Z]{1,3}$'),                        # short abbreviation alone  e.g. N, BD, VG
    re.compile(r'^[A-Z]{1,4}-[A-Z0-9]{1,4}$', re.I),   # two-part code  e.g. X-N, BD-X
    re.compile(r'^-[A-Z0-9](?:-[A-Z0-9]+)*$', re.I),   # dash-prefix fragment  e.g. -N, -X-N
    re.compile(r'^[\s\-_./:;,]+$'),                     # separators / punctuation only
    re.compile(r'^\d+(?:\.\d+)?$'),                    # bare decimal number, no unit
]


# ═══════════════════════════════════════════════════════════════════════════
# Soft-coded ACCURACY POST-FILTERS (applied after every rule has emitted).
# ─────────────────────────────────────────────────────────────────────────────
# These knobs let engineering tune the report quality WITHOUT touching any rule
# logic.  All filters are pure post-processing — they only ever drop / merge /
# re-label findings; they NEVER invent new findings.
#
#   _DISABLED_RULE_IDS         : rule_ids whose findings are dropped entirely.
#                                Use to silence chronically noisy rules per-project.
#   _RULE_SEVERITY_OVERRIDES   : rule_id → final severity.  Use to demote a rule
#                                from 'major' to 'minor' (or promote) without
#                                editing each emit-site.
#   _GLOBAL_EVIDENCE_NOISE_RES : list of compiled regex; if a finding's evidence
#                                FULLMATCHES any of these, the finding is dropped.
#                                Catches OCR fragments that survive rule-level
#                                filters (e.g. stray '-N', '013842').
#   _MIN_EVIDENCE_LEN          : evidence shorter than this (after strip) is
#                                dropped.  0 = disabled.
#   _DEDUPE_BY_RULE_AND_EVIDENCE: when True, keeps only the FIRST finding per
#                                (rule_id, normalised-evidence) pair.  Prevents
#                                the same tag being flagged 3× by the same rule
#                                because it appeared at 3 OCR positions.
#   _DEDUPE_NORMALISE_EVIDENCE : function applied before dedupe-comparison.
#                                Default: upper-case + collapse whitespace.
# ═══════════════════════════════════════════════════════════════════════════
_DISABLED_RULE_IDS: set = set()        # e.g. {'TAG-003'} silences "non-standard format" globally
_RULE_SEVERITY_OVERRIDES: Dict[str, str] = {
    # Example (commented — uncomment per-project):
    # 'TAG-003': 'minor',
    # 'LSZ-007': 'minor',
}
# Evidence patterns that indicate OCR noise / unparseable fragments.  Findings
# whose evidence fullmatches ANY of these are dropped before the report is
# returned.  Reuses the same fragment patterns already trusted by RED-001 and
# adds a few generally noisy tokens.
_GLOBAL_EVIDENCE_NOISE_RES: list = list(_RED001_FRAGMENT_PATTERNS) + [
    re.compile(r'^[A-Z]\.?$'),                     # single letters like 'N.' or 'X'
    re.compile(r'^[\s\-_./:;,\(\)\[\]]+$'),       # punctuation soup
    re.compile(r'^\d+(?:\.\d+)?\s*(?:mm|cm|m)?$'), # bare numbers ± unit only
]
_MIN_EVIDENCE_LEN: int = 0             # 0 disables; 2 drops single-char evidence
_DEDUPE_BY_RULE_AND_EVIDENCE: bool = True
def _DEDUPE_NORMALISE_EVIDENCE(s: str) -> str:
    return ' '.join((s or '').upper().split())


def _apply_accuracy_filters(findings: List['RuleFinding']) -> List['RuleFinding']:
    """
    Pure post-processing filter — never adds findings, only drops/merges/re-labels.
    Soft-coded via the constants above.  Logs a one-line summary of what was filtered.
    """
    if not findings:
        return findings
    before = len(findings)
    out: List['RuleFinding'] = []
    seen_keys: set = set()
    dropped_disabled = dropped_noise = dropped_short = dropped_dup = severity_changed = 0

    for f in findings:
        # 1. Hard rule-id suppression
        if f.rule_id in _DISABLED_RULE_IDS:
            dropped_disabled += 1
            continue
        # 2. Evidence-noise filter
        ev = (f.evidence or '').strip()
        if ev and any(p.fullmatch(ev) for p in _GLOBAL_EVIDENCE_NOISE_RES):
            dropped_noise += 1
            continue
        # 3. Minimum evidence length (only enforced when evidence is non-empty)
        if _MIN_EVIDENCE_LEN > 0 and ev and len(ev) < _MIN_EVIDENCE_LEN:
            dropped_short += 1
            continue
        # 4. Severity override
        new_sev = _RULE_SEVERITY_OVERRIDES.get(f.rule_id)
        if new_sev and new_sev != f.severity:
            f.severity = new_sev
            severity_changed += 1
        # 5. (rule_id, evidence) dedupe
        if _DEDUPE_BY_RULE_AND_EVIDENCE:
            key = (f.rule_id, _DEDUPE_NORMALISE_EVIDENCE(ev))
            if key in seen_keys:
                dropped_dup += 1
                continue
            seen_keys.add(key)
        out.append(f)

    if before != len(out) or severity_changed:
        logger.info(
            "[rule_engine] accuracy filter: %d → %d findings "
            "(disabled=%d noise=%d short=%d dup=%d severity_changed=%d)",
            before, len(out), dropped_disabled, dropped_noise,
            dropped_short, dropped_dup, severity_changed,
        )
    return out


@dataclass
class RuleFinding:
    category:        str
    rule_id:         str
    issue_observed:  str
    action_required: str
    evidence:        str  = ''
    direction:       str  = 'N/A'
    severity:        str  = 'major'


def run_rules(extraction: Dict[str, Any], graph) -> List[RuleFinding]:
    """
    Execute all rules against a single drawing's extraction + graph.
    Returns a sorted, deterministic list of RuleFinding objects.
    """
    findings: List[RuleFinding] = []

    findings.extend(_check_tag_issues(extraction))
    findings.extend(_check_connectivity(extraction, graph))
    findings.extend(_check_valve_equipment(extraction))
    findings.extend(_check_line_sizes(extraction))
    findings.extend(_check_notes_holds(extraction))
    findings.extend(_check_pipeline_tag_duplicates(extraction))
    findings.extend(_check_shared_suffix_across_identities(extraction))
    findings.extend(_check_line_designation_semantics(extraction))
    findings.extend(_check_equipment_item_codes(extraction))
    # New rules: visual / annotation patterns discovered from red-marked drawings.
    findings.extend(_check_red_annotations(extraction))
    findings.extend(_check_valve_type_size(extraction))
    findings.extend(_check_reducers(extraction))
    findings.extend(_check_pressure_annotations(extraction))
    findings.extend(_check_equipment_size_annotations(extraction))
    findings.extend(_check_compressor_equipment(extraction))

    # Soft-coded accuracy post-filter (drops noise, dedupes, applies severity overrides).
    findings = _apply_accuracy_filters(findings)

    # Sort deterministically: rule_id → issue_observed
    findings.sort(key=lambda f: (f.rule_id, f.issue_observed))
    return findings


# ---------------------------------------------------------------------------
# TAG RULES
# ---------------------------------------------------------------------------

def _check_tag_issues(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []
    tags = extraction.get('tags', [])

    # TAG-001: Instrument/valve without a tag in the tag list
    for item in extraction.get('instruments', []) + extraction.get('valves', []):
        if item.get('tag') and item['tag'] not in tags:
            out.append(RuleFinding(
                category='tag',
                rule_id='TAG-001',
                issue_observed=f"Element '{item['tag']}' detected via OCR but not in consolidated tag list",
                action_required='Verify tag label on drawing; re-issue if missing',
                evidence=item['tag'],
                severity='major',
            ))

    # TAG-002: Duplicate tags
    seen = set()
    for tag in tags:
        if tag in seen:
            out.append(RuleFinding(
                category='tag',
                rule_id='TAG-002',
                issue_observed=f"Duplicate tag '{tag}' found on drawing",
                action_required='Remove or renumber duplicate tag to maintain uniqueness',
                evidence=tag,
                severity='critical',
            ))
        seen.add(tag)

    # TAG-003: Non-standard tag format
    for tag in tags:
        if not _TAG_FORMAT_RE.match(tag):
            out.append(RuleFinding(
                category='tag',
                rule_id='TAG-003',
                issue_observed=f"Tag '{tag}' does not match standard format PREFIX-NNNN",
                action_required='Rename tag to conform to instrument tag naming convention',
                evidence=tag,
                severity='minor',
            ))

    # TAG-004: Tags in notes not present on drawing
    all_note_text = ' '.join(extraction.get('notes', []) + extraction.get('holds', []))
    note_tags = set(re.findall(r'\b[A-Z]{1,4}-[0-9]{3,5}[A-Z]?\b', all_note_text))
    drawing_tags = set(tags)
    for ntag in sorted(note_tags - drawing_tags):
        out.append(RuleFinding(
            category='tag',
            rule_id='TAG-004',
            issue_observed=f"Tag '{ntag}' referenced in notes/HOLDs but not found on drawing",
            action_required='Add missing tag to drawing or update note reference',
            evidence=ntag,
            severity='major',
        ))

    return out


# ---------------------------------------------------------------------------
# CONNECTIVITY RULES
# ---------------------------------------------------------------------------

def _check_connectivity(extraction: Dict[str, Any], graph) -> List[RuleFinding]:
    out = []

    try:
        from apps.pid_verification.services.graph_builder import get_isolated_nodes
        isolated = get_isolated_nodes(graph)
    except Exception:
        isolated = []

    instr_tags = {i.get('tag') for i in extraction.get('instruments', []) if i.get('tag')}
    valve_tags = {v.get('tag') for v in extraction.get('valves', []) if v.get('tag')}
    raw_text_len = len(extraction.get('raw_text', '') or '')

    for node in sorted(isolated):
        if node in instr_tags:
            kind = 'instrument'
            rule_id = 'CON-001'
            noun = 'Instrument'
            action = 'Connect instrument to process line or verify if stand-alone'
        elif node in valve_tags:
            kind = 'valve'
            rule_id = 'CON-002'
            noun = 'Valve'
            action = 'Connect valve to upstream and downstream pipelines'
        else:
            kind = 'other'
            rule_id = 'CON-003'
            noun = 'Node'
            action = 'Verify element belongs to this drawing; connect or remove'

        confidence = _orphan_confidence(node, extraction, kind)
        band = _confidence_band(confidence, raw_text_len)
        severity = _orphan_severity_for_band(kind, band)

        out.append(RuleFinding(
            category='connectivity',
            rule_id=rule_id,
            issue_observed=(
                f"Possible orphan {noun.lower()} '{node}' has no connections in graph "
                f"(confidence: {band}, score: {confidence:.2f})"
            ),
            action_required=(
                f"{action}. Perform a quick visual check on drawing before closing issue "
                "(soft rule)."
            ),
            evidence=node,
            severity=severity,
        ))

    return out


def _orphan_confidence(node: str, extraction: Dict[str, Any], kind: str) -> float:
    """Return 0..1 confidence that orphan finding is real and not extraction noise."""
    score = 0.0

    tags = set(extraction.get('tags', []) or [])
    raw_text = extraction.get('raw_text', '') or ''

    # Evidence 1: canonical tag list contains this exact node.
    if node in tags:
        score += 0.45

    # Evidence 2: appears in OCR text one or more times.
    if raw_text:
        count = len(re.findall(rf'\b{re.escape(node)}\b', raw_text, flags=re.IGNORECASE))
        if count >= 2:
            score += 0.30
        elif count == 1:
            score += 0.18

    # Evidence 3: type-specific weight.
    if kind in {'instrument', 'valve'}:
        score += 0.18
    else:
        score += 0.10

    # Evidence 4: tag format looks valid.
    if _TAG_FORMAT_RE.match(node):
        score += 0.07

    return min(score, 1.0)


def _confidence_band(score: float, raw_text_len: int) -> str:
    """Map confidence score into low/medium/high and degrade if OCR text is sparse."""
    if score >= _ORPHAN_CONFIDENCE_HIGH:
        band = 'high'
    elif score >= _ORPHAN_CONFIDENCE_MEDIUM:
        band = 'medium'
    else:
        band = 'low'

    # If OCR extracted very little text, avoid aggressive confidence.
    if raw_text_len < _ORPHAN_LOW_TEXT_CUTOFF:
        if band == 'high':
            return 'medium'
        if band == 'medium':
            return 'low'
    return band


def _orphan_severity_for_band(kind: str, band: str) -> str:
    """Soft severity policy by type + confidence band."""
    if band == 'high':
        if kind == 'valve':
            return 'major'
        if kind == 'instrument':
            return 'major'
        return 'major'
    if band == 'medium':
        return 'minor'
    return 'info'


# ---------------------------------------------------------------------------
# VALVE & EQUIPMENT RULES
# ---------------------------------------------------------------------------

def _check_valve_equipment(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []

    for valve in extraction.get('valves', []):
        tag = valve.get('tag', '')
        prefix = tag.split('-')[0] if '-' in tag else ''
        if not tag:
            out.append(RuleFinding(
                category='valve',
                rule_id='VLV-001',
                issue_observed='Valve symbol detected without a tag label',
                action_required='Add tag to valve per instrument tag numbering system',
                evidence='',
                severity='critical',
            ))
        elif prefix in _TAGGED_VALVE_PREFIXES and tag not in extraction.get('tags', []):
            out.append(RuleFinding(
                category='valve',
                rule_id='VLV-001',
                issue_observed=f"Valve '{tag}' not found in consolidated tag list",
                action_required='Add valve tag to tag list or correct label',
                evidence=tag,
                severity='major',
            ))

    return out


# ---------------------------------------------------------------------------
# LINE SIZE RULES
# ---------------------------------------------------------------------------

def _check_line_sizes(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []
    pipelines  = extraction.get('pipelines', [])
    line_sizes = extraction.get('line_sizes', [])
    raw_text = extraction.get('raw_text', '')

    # LSZ-001: Pipelines with no recorded size
    for pipeline in pipelines:
        if not pipeline.get('size'):
            out.append(RuleFinding(
                category='line_size',
                rule_id='LSZ-001',
                issue_observed=f"Pipeline '{pipeline.get('line_id', 'unknown')}' has no line size annotation",
                action_required='Add nominal pipe size to line designation',
                evidence=pipeline.get('line_id', ''),
                severity='major',
            ))

    # LSZ-002: Conflicting sizes on same pipeline (requires pipeline.size list)
    pipeline_sizes: dict = {}
    for pipeline in pipelines:
        lid  = pipeline.get('line_id', '')
        size = pipeline.get('size', '')
        if lid and size:
            if lid in pipeline_sizes and pipeline_sizes[lid] != size:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-002',
                    issue_observed=f"Conflicting line sizes on pipeline '{lid}': "
                                   f"'{pipeline_sizes[lid]}' vs '{size}'",
                    action_required='Resolve conflicting sizes; verify pipeline continuity',
                    evidence=lid,
                    severity='critical',
                ))
            else:
                pipeline_sizes[lid] = size

    # Flag line size texts that could not be attributed to any pipeline.
    # If no pipelines are extracted, this becomes noisy and misleading.
    if pipelines:
        attributed_sizes = {p.get('size') for p in pipelines if p.get('size')}
        for ls in line_sizes:
            if ls['text'] not in attributed_sizes:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-001',
                    issue_observed=f"Line size '{ls['text']}' found on drawing but not mapped to any pipeline",
                    action_required='Associate line size annotation with its pipeline designation',
                    evidence=ls['text'],
                    direction=ls.get('direction', 'unknown'),
                    severity='minor',
                ))

    # LSZ-003: Explicit valve-size vs line-size mismatch found in text
    out.extend(_check_valve_line_size_mismatch(raw_text, line_sizes))

    # LSZ-005: Drawing-specific multi-size transition observation.
    out.extend(_check_multi_size_transition_observation(raw_text, line_sizes))

    return out


def _normalize_size_token(token: str) -> str:
    """Normalize size token to canonical display (e.g., 6 -> 6\")."""
    t = token.strip().lower().replace(' ', '')
    t = t.replace("''", '"')
    if t.endswith('mm'):
        return t
    if t.endswith('"'):
        return t
    return f'{t}"'


def _check_valve_line_size_mismatch(raw_text: str, line_sizes: List[Dict[str, Any]] | None = None) -> List[RuleFinding]:
    """
    Detect mismatch patterns like:
      6" valve ... 4" line
    and return a critical, actionable finding.
    """
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    for line in raw_text.splitlines():
        line_lower = line.lower()
        if 'valve' not in line_lower:
            continue
        if 'line' not in line_lower and 'pipe' not in line_lower:
            continue

        size_tokens = re.findall(r'(\d+(?:\.\d+)?(?:\s*(?:"|\'\'|mm))?)', line, flags=re.IGNORECASE)
        normalized = [_normalize_size_token(s) for s in size_tokens if s.strip()]

        unique_sizes = []
        for s in normalized:
            if s not in unique_sizes:
                unique_sizes.append(s)

        if len(unique_sizes) >= 2:
            valve_size = unique_sizes[0]
            line_size = unique_sizes[1]
            if valve_size != line_size:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-003',
                    issue_observed=f"Valve size '{valve_size}' does not match connected line size '{line_size}'",
                    action_required='Verify valve bore size against line size and correct drawing/specification mismatch',
                    evidence=line.strip()[:240],
                    direction='N/A',
                    severity='critical',
                ))

    # Fallback heuristic for noisy OCR where "valve" word is not detected but
    # valve callouts often end with "-V" and include inch-size text.
    if not out:
        fallback = _check_valve_line_size_mismatch_fallback(raw_text, line_sizes or [])
        out.extend(fallback)

    # Secondary deterministic fallback: if a single OCR line contains
    # multiple distinct inch sizes plus a pipeline-like token, flag it.
    # Example caught: "... 6\" 4\"-BD-4860-033842-X-N ..."
    if not out:
        out.extend(_check_inline_size_conflict_with_line_token(raw_text))

    return out


def _check_inline_size_conflict_with_line_token(raw_text: str) -> List[RuleFinding]:
    """
    LSZ-004  An OCR text-line contains a pipeline-designation token AND two or
    more distinct NPS inch-sizes.  Classic symptom: a valve callout sitting next
    to a line designation where the valve bore differs from the pipe nominal size,
    or two different line designations with different sizes on the same text line.

    Soft-coded via module-level constants:
      _LSZ004_LINE_TAG_RE  -- pattern that recognises a line-designation token
      _LSZ004_MAX_FINDINGS -- cap to avoid flooding the report with noise
      _LSZ004_MIN_SIZES    -- minimum distinct sizes required to raise the finding
    """
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    size_token_re = re.compile(r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')')

    # Track which size-conflict frozensets we have already reported so that
    # repeated OCR lines do not generate identical duplicate findings.
    seen_conflict_keys: set = set()

    for ocr_line in raw_text.splitlines():
        # Only examine lines that contain at least one line-designation token.
        if not _LSZ004_LINE_TAG_RE.search(ocr_line):
            continue

        # Collect distinct NPS sizes found on this OCR line.
        # Sizes above _LSZ004_MAX_NPS_INCH are discarded as OCR noise
        # (e.g. drawing sheet border dimensions like "48\"").
        sizes: list = []
        for m in size_token_re.finditer(ocr_line):
            try:
                if float(m.group(1)) > _LSZ004_MAX_NPS_INCH:
                    continue
            except ValueError:
                continue
            s = f'{m.group(1)}"'
            if s not in sizes:
                sizes.append(s)

        if len(sizes) < _LSZ004_MIN_SIZES:
            continue

        conflict_key = frozenset(sizes)
        if conflict_key in seen_conflict_keys:
            continue
        seen_conflict_keys.add(conflict_key)

        # Build clean evidence: prefer extracted line-tag tokens over raw OCR.
        tag_tokens = _LSZ004_LINE_TAG_RE.findall(ocr_line)
        if tag_tokens:
            sizes_str = ", ".join(sizes)
            tags_str  = "  ·  ".join(list(dict.fromkeys(tag_tokens))[:4])
            evidence  = f"Sizes [{sizes_str}] on: {tags_str}"
        else:
            evidence = re.sub(r'[^\w\s"./%-]', " ", ocr_line).strip()[:160]

        if len(sizes) == 2:
            sizes_label = f'{sizes[0]} and {sizes[1]}'
        else:
            sizes_label = ", ".join(sizes[:-1]) + f' and {sizes[-1]}'

        out.append(RuleFinding(
            category="line_size",
            rule_id="LSZ-004",
            issue_observed=(
                f"Conflicting inline size annotations {sizes_label} "
                "detected on the same line reference"
            ),
            action_required=(
                "Verify valve/line nominal sizes and add a reducer or correct "
                "the line designation as required."
            ),
            evidence=evidence,
            direction="N/A",
            severity="critical",
        ))

        if len(out) >= _LSZ004_MAX_FINDINGS:
            break

    return out
def _check_valve_line_size_mismatch_fallback(raw_text: str, line_sizes: List[Dict[str, Any]]) -> List[RuleFinding]:
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    inch_pattern = re.compile(r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')')
    valve_inch_pattern = re.compile(r'\b(\d{1,4}(?:\.\d+)?)\s*(?:"|\'\')')
    valve_like_line = re.compile(r'\b\S*-V\b', flags=re.IGNORECASE)

    def _is_reasonable_size(s: str) -> bool:
        try:
            v = float(s.replace('"', '').strip())
            return 2.0 <= v <= 24.0
        except Exception:
            return False

    def _coerce_ocr_size(raw_num: str) -> str | None:
        """Accept only direct, reasonable OCR sizes (no trailing-digit recovery)."""
        try:
            v = float(raw_num)
        except Exception:
            return None

        if not (2.0 <= v <= 24.0):
            return None
        if float(v).is_integer():
            return f'{int(v)}"'
        return f'{v}"'

    def _size_value(s: str) -> float:
        try:
            return float(s.replace('"', '').strip())
        except Exception:
            return 0.0

    # Prefer extracted line-size annotations for line side of the comparison.
    drawing_line_sizes = []
    for ls in line_sizes:
        text = str(ls.get('text', '')).strip()
        if text.endswith('"') and _is_reasonable_size(text) and text not in drawing_line_sizes:
            drawing_line_sizes.append(text)

    # Fallback if extractor could not map line_sizes list.
    if not drawing_line_sizes:
        all_inch_sizes = [f"{m.group(1)}\"" for m in inch_pattern.finditer(raw_text)]
        for s in all_inch_sizes:
            if _is_reasonable_size(s) and s not in drawing_line_sizes:
                drawing_line_sizes.append(s)

    # Candidate valve sizes from lines that look like valve callouts
    valve_size_candidates = []
    valve_evidence_line = ''
    for line in raw_text.splitlines():
        if not valve_like_line.search(line):
            continue
        matches = [m.group(1) for m in valve_inch_pattern.finditer(line)]
        for raw_num in matches:
            size = _coerce_ocr_size(raw_num)
            if size and _is_reasonable_size(size) and size not in valve_size_candidates:
                valve_size_candidates.append(size)
                valve_evidence_line = line.strip()[:240]

        # Prefer local comparison on the same OCR line to avoid blended data
        # from unrelated parts of the diagram.
        local_sizes = []
        for m in inch_pattern.finditer(line):
            s = f"{m.group(1)}\""
            if _is_reasonable_size(s) and s not in local_sizes:
                local_sizes.append(s)

        if len(local_sizes) >= 2:
            local_sorted = sorted(local_sizes, key=_size_value)
            valve_size_local = local_sorted[-1]
            line_size_local = local_sorted[0]
            if valve_size_local != line_size_local:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-003',
                    issue_observed=f"Valve size '{valve_size_local}' does not match connected line size '{line_size_local}'",
                    action_required='Verify valve bore size against line size and correct drawing/specification mismatch',
                    evidence=line.strip()[:240],
                    direction='N/A',
                    severity='critical',
                ))
                return out

    # Secondary fallback: raw drawing sizes not present in primary line-size set.
    if not valve_size_candidates:
        all_inch_sizes = [f"{m.group(1)}\"" for m in inch_pattern.finditer(raw_text)]
        for size in all_inch_sizes:
            if _is_reasonable_size(size) and size not in drawing_line_sizes and size not in valve_size_candidates:
                valve_size_candidates.append(size)

    if not valve_size_candidates or not drawing_line_sizes:
        return out

    # If no local valve line comparison was possible, use a conservative
    # fallback that only compares dominant valve candidate vs dominant line size.
    # This remains deterministic but avoids aggressive global min/max blending.
    valve_size = max(valve_size_candidates, key=_size_value)
    # Guard against synthetic OCR candidates that are not present in extracted
    # diagram line-size annotations (prevents blended false positives).
    if valve_size not in drawing_line_sizes:
        return out
    # Use most frequent extracted line size first, then larger value tie-breaker.
    freq = {}
    for s in drawing_line_sizes:
        freq[s] = freq.get(s, 0) + 1
    line_size = sorted(drawing_line_sizes, key=lambda s: (freq.get(s, 0), _size_value(s)), reverse=True)[0]

    if valve_size != line_size:
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-003',
            issue_observed=f"Valve size '{valve_size}' does not match connected line size '{line_size}'",
            action_required='Verify valve bore size against line size and correct drawing/specification mismatch',
            evidence=valve_evidence_line or 'OCR fallback: valve-like callout vs line-size annotation',
            direction='N/A',
            severity='critical',
        ))

    return out


def _check_multi_size_transition_observation(raw_text: str, line_sizes: List[dict]) -> List[RuleFinding]:
    """
    LSZ-005  Three or more *distinct* nominal pipe sizes are present on the drawing.

    This observation flags drawings with many size transitions so the engineer
    can confirm every spec-break / reducer is documented.  The exact set of
    sizes detected is reported dynamically rather than hard-coding a specific triplet.

    Soft-coded via module constants:
      _LSZ005_MIN_DISTINCT_SIZES  -- number of distinct sizes required to fire
      _LSZ005_SIZE_MIN_INCH       -- lower bound for a valid pipe size (inches)
      _LSZ005_SIZE_MAX_INCH       -- upper bound for a valid pipe size (inches)
    """
    out: List[RuleFinding] = []
    if not line_sizes:
        return out

    def _inch_value(txt: str):
        """Return float inch value from '4"'  '25mm', or None if unparseable."""
        txt = txt.strip().replace("\u201c", "\"").replace("\u201d", "\"").replace("''", "\"")
        if txt.endswith("\""):
            try:
                return float(txt.rstrip("\"").strip())
            except ValueError:
                return None
        if txt.lower().endswith("mm"):
            try:
                return float(txt[:-2].strip()) / 25.4
            except ValueError:
                return None
        return None

    # Collect distinct validated sizes from the extraction line_sizes list.
    valid_sizes: dict = {}   # canonical_text -> inch_value
    for ls in line_sizes:
        raw = str(ls.get("text", "")).strip()
        # Normalise curly / smart quotes to straight double-quote.
        canonical = raw.replace("\u201c", "\"").replace("\u201d", "\"").replace("''", "\"")
        if not canonical.endswith("\""):
            continue
        val = _inch_value(canonical)
        if val is None:
            continue
        if _LSZ005_SIZE_MIN_INCH <= val <= _LSZ005_SIZE_MAX_INCH:
            if canonical not in valid_sizes:
                valid_sizes[canonical] = val

    if len(valid_sizes) < _LSZ005_MIN_DISTINCT_SIZES:
        return out

    # Sort sizes smallest to largest for a readable display.
    sorted_sizes = sorted(valid_sizes.keys(), key=lambda s: valid_sizes[s])

    # Human-friendly label: '2", 4", and 8"'
    if len(sorted_sizes) == _LSZ005_MIN_DISTINCT_SIZES:
        sizes_label = f'{sorted_sizes[0]}, {sorted_sizes[1]}, and {sorted_sizes[2]}'
    else:
        sizes_label = ", ".join(sorted_sizes[:-1]) + f', and {sorted_sizes[-1]}'

    out.append(RuleFinding(
        category="line_size",
        rule_id="LSZ-005",
        issue_observed=(
            f"Multiple nominal sizes {sizes_label} detected on this drawing segment"
        ),
        action_required=(
            "Verify intended reducers / spec-breaks and confirm each size "
            "transition is documented on the line route with a reducer symbol "
            "and updated line designations."
        ),
        evidence=f"Detected sizes: {sizes_label} on same diagram context",
        direction="N/A",
        severity="major",
    ))

    return out
# ---------------------------------------------------------------------------
# NOTES & HOLDs RULES
# ---------------------------------------------------------------------------

def _check_notes_holds(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []
    notes  = extraction.get('notes', [])
    holds  = extraction.get('holds', [])
    tags   = set(extraction.get('tags', []))

    # NTS-001: Notes present but no tag references
    if notes:
        note_text = ' '.join(notes)
        note_tags = set(re.findall(r'\b[A-Z]{1,4}-[0-9]{3,5}[A-Z]?\b', note_text))
        if not note_tags:
            out.append(RuleFinding(
                category='notes',
                rule_id='NTS-001',
                issue_observed='drawing notes present but contain no tag references',
                action_required='Review notes and associate each note with the relevant tag(s) or equipment',
                evidence=notes[0][:120] if notes else '',
                severity='minor',
            ))

    # NTS-002: Every HOLD item is flagged as requiring action
    for hold in holds:
        out.append(RuleFinding(
            category='notes',
            rule_id='NTS-002',
            issue_observed=f"HOLD detected: {hold[:120]}",
            action_required='Resolve HOLD item and update drawing revision',
            evidence=hold[:200],
            severity='major',
        ))

    return out


# ---------------------------------------------------------------------------
# PIPELINE LINE DESIGNATION RULES  (LSZ-006, LSZ-007)
# ---------------------------------------------------------------------------

def _check_pipeline_tag_duplicates(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    LSZ-006  Same pipeline base (fluid + area + seq + class + insulation) detected
             with conflicting NPS sizes → likely labelling error or missing reducer.
    LSZ-007  Same designation detected 3+ times in a single orientation → possible
             label-copy error (warning only; legitimate on multi-sheet drawings).
    """
    out: List[RuleFinding] = []
    line_tags = extraction.get('line_tags', [])
    if not line_tags:
        return out

    # LSZ-006 ────────────────────────────────────────────────────────────
    # Group by base (everything except NPS size)
    base_groups: dict = {}
    for lt in line_tags:
        base_key = '-'.join([
            lt.get('fluid_code', ''),
            lt.get('area_code',   ''),
            lt.get('sequence_no', ''),
            lt.get('pipe_class',  ''),
            lt.get('insulation',  ''),
        ]).upper().strip('-')
        if not base_key:
            continue
        base_groups.setdefault(base_key, []).append(lt)

    for base_key, entries in base_groups.items():
        sizes = list({e.get('size', '') for e in entries if e.get('size')})
        if len(sizes) > 1:
            texts = [e.get('text', '') for e in entries]
            out.append(RuleFinding(
                category='line_size',
                rule_id='LSZ-006',
                issue_observed=(
                    f"Pipeline base '{base_key}' found with conflicting NPS sizes: "
                    f"{', '.join(sorted(sizes))} — possible reducer or labelling error"
                ),
                action_required=(
                    'Confirm whether a size transition (reducer) is intended. '
                    'If so, add a reducer symbol and update line designations. '
                    'Otherwise correct the mislabelled tag.'
                ),
                evidence='; '.join(texts[:3]),
                severity='major',
            ))

    # LSZ-007 ────────────────────────────────────────────────────────────
    # Same full designation appearing ≥_LSZ007_MIN_SAME_DIR_OCCURRENCES times
    # in a single orientation (soft-coded via module-level constant).
    for lt in line_tags:
        for direction in ('H', 'V'):
            same_dir = [o for o in lt.get('occurrences', []) if o['direction'] == direction]
            if len(same_dir) >= _LSZ007_MIN_SAME_DIR_OCCURRENCES:
                # Spatial spread guard: if all same-direction occurrences form a
                # tight cluster they are OCR noise from the same physical label
                # read multiple times — not a genuine copy-paste across the sheet.
                positions = [(o['x_pct'], o['y_pct']) for o in same_dir]
                x_spread  = max(p[0] for p in positions) - min(p[0] for p in positions)
                y_spread  = max(p[1] for p in positions) - min(p[1] for p in positions)
                if max(x_spread, y_spread) < _LSZ007_MIN_SPATIAL_SPREAD_PCT:
                    continue  # tight cluster → noise, skip
                dir_label = 'horizontal' if direction == 'H' else 'vertical'
                coords = '; '.join(
                    f"({o['x_pct']:.1f}%, {o['y_pct']:.1f}%)" for o in same_dir[:3]
                )
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-007',
                    issue_observed=(
                        f"Pipeline tag '{lt.get('text', '')}' appears {len(same_dir)} times "
                        f"in {dir_label} orientation — possible label-copy error"
                    ),
                    action_required=(
                        'Verify intended multiplicity. Remove duplicate labels if the line '
                        'does not re-enter this drawing area. Multiple occurrences are '
                        'normal on multi-sheet or continuation drawings.'
                    ),
                    evidence=f"{lt.get('text','')} @ {coords}",
                    severity='minor',
                ))

    # LSZ-008 ────────────────────────────────────────────────────────────
    # Same designation confirmed in BOTH H and V orientations (multi-angle duplicate)
    # This is the most common duplicate type: the label runs along the pipe in one
    # direction and also appears as a cross-reference note in the perpendicular axis.
    for lt in line_tags:
        if not lt.get('multi_angle'):
            continue
        occs = lt.get('occurrences', [])
        h_occs = [o for o in occs if o['direction'] == 'H' and o.get('x_pct') is not None]
        v_occs = [o for o in occs if o['direction'] == 'V' and o.get('x_pct') is not None]
        if not h_occs or not v_occs:
            continue
        # Distance guard: H+V labels in close proximity indicate a pipe bend
        # (one label straddles a corner) — completely normal P&ID routing.
        # Only raise the finding when the H and V occurrences are genuinely far
        # apart, suggesting the label was placed on a different, unrelated pipe.
        h_x, h_y = h_occs[0]['x_pct'], h_occs[0]['y_pct']
        v_x, v_y = v_occs[0]['x_pct'], v_occs[0]['y_pct']
        hv_dist = math.sqrt((h_x - v_x) ** 2 + (h_y - v_y) ** 2)
        if hv_dist < _LSZ008_MIN_HV_DISTANCE_PCT:
            continue  # close H+V → normal pipe bend, not a copy-paste error
        h_coord = f"({h_occs[0]['x_pct']:.1f}%, {h_occs[0]['y_pct']:.1f}%)"
        v_coord = f"({v_occs[0]['x_pct']:.1f}%, {v_occs[0]['y_pct']:.1f}%)"
        tag_text = lt.get('text', '')
        # Soft-coded: evidence STARTS with the full tag text so that the frontend
        # overlay can extract the NPS size prefix (e.g. '4"') and map it to a
        # diagram-anchored position via tag_positions.  The coordinate detail
        # follows for audit traceability.
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-008',
            issue_observed=(
                f"Pipeline tag '{tag_text}' detected in both horizontal "
                f"and vertical orientations — confirmed duplicate label on this drawing"
            ),
            action_required=(
                'Verify the line physically re-enters this drawing area in a different '
                'direction. If the label is a continuation reference, ensure arrows and '
                'sheet cross-references are present per engineering drafting standard.'
            ),
            evidence=f"{tag_text}  H @ {h_coord}  ·  V @ {v_coord}",
            severity='minor',
        ))

    # LSZ-009 ────────────────────────────────────────────────────────────
    # Cloud-truncated duplicate pipeline designation.
    # Fired when extraction finds the same line identity (size + fluid + area +
    # sequence) twice: once with a full pipe_class/insulation suffix and once
    # without — the truncated form is almost certainly the same physical label
    # partially covered by a revision cloud.
    # The full entry's occurrences include both the original and the merged
    # truncated occurrence (set by the cloud-truncation resolution pass in
    # extraction.py), so the exact drawing positions are already available.
    for lt in line_tags:
        if not lt.get('cloud_truncation_detected'):
            continue
        tag_text = lt.get('text', '')
        occ_count = len(lt.get('occurrences', []))
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-009',
            issue_observed=(
                f"Cloud-truncated duplicate detected: pipeline tag '{tag_text}' (full designation) "
                f"appears alongside a second truncated occurrence missing the pipe-class / "
                f"insulation suffix. A revision cloud is likely obscuring the trailing suffix "
                f"on one label. Tag found at {occ_count} location(s) on this drawing."
            ),
            action_required=(
                'Visually inspect all occurrences of this line tag on the drawing. '
                'Confirm whether the truncated label is the same physical line with its '
                'suffix obscured by a revision cloud, or a genuinely separate line. '
                'If it is the same line, update the truncated label to show the complete '
                'designation including the full pipe-class and insulation/tracing suffix.'
            ),
            evidence=tag_text,
            severity='critical',
        ))

    return out

# ---------------------------------------------------------------------------
# LSZ-010  Shared sequence-number / suffix across different pipeline identities
# ---------------------------------------------------------------------------

def _check_shared_suffix_across_identities(extraction: dict) -> list:
    """
    LSZ-010  Two or more pipeline tags on the same drawing share an identical
    trailing suffix (sequence_no + pipe_class + insulation, configurable via
    _LSZ010_SUFFIX_FIELDS) but belong to DIFFERENT pipeline identities
    (different area codes, and optionally different fluid codes).

    This pattern is the most common copy-paste error in P&ID line numbering:
    an engineer copies a line designation, updates the fluid/area segment, but
    forgets to change the sequence number and pipe-class suffix.

    Example (the case that motivated this rule):
      4\"-D-5749-013842-X-N   area 5749
      4\"-D-5690-013842-X-N   area 5690
      Shared suffix: 013842-X-N  |  Different areas: 5749 vs 5690

    Soft-coded via module-level constants:
      _LSZ010_SUFFIX_FIELDS       -- tuple of line_tag keys forming the "shared suffix"
      _LSZ010_SAME_FLUID_ONLY     -- only flag when fluid codes also match
      _LSZ010_REQUIRE_SAME_SIZE   -- only flag when NPS sizes also match
      _LSZ010_MAX_FINDINGS        -- cap on findings per drawing
      _LSZ010_MIN_SUFFIX_PARTS    -- minimum populated suffix parts to consider
    """
    out: list = []
    line_tags = extraction.get("line_tags", [])
    if not line_tags:
        return out

    # ── Build suffix_key -> list[line_tag] map ────────────────────────────
    suffix_groups: dict = {}
    for lt in line_tags:
        parts = [
            str(lt.get(f) or "").upper().strip()
            for f in _LSZ010_SUFFIX_FIELDS
        ]
        # Skip entries where too few suffix fields are populated.
        populated = sum(1 for p in parts if p)
        if populated < _LSZ010_MIN_SUFFIX_PARTS:
            continue
        # Use only populated parts in the key so partial entries don't dilute.
        suffix_key = "-".join(p for p in parts if p)
        suffix_groups.setdefault(suffix_key, []).append(lt)

    seen_groups: set = set()

    for suffix_key, entries in suffix_groups.items():
        if len(entries) < 2:
            continue

        # ── Build distinct (fluid_code, area_code) identity pairs ─────────
        identities = []
        for e in entries:
            idn = (
                str(e.get("fluid_code") or "").upper().strip(),
                str(e.get("area_code")  or "").upper().strip(),
            )
            if idn not in identities:
                identities.append(idn)

        # Must have at least two DIFFERENT identities to be a conflict.
        if len(identities) < 2:
            continue

        # ── Apply optional filters ─────────────────────────────────────────
        if _LSZ010_SAME_FLUID_ONLY:
            # Only flag when all conflicting tags share the same fluid code.
            fluids = {i[0] for i in identities if i[0]}
            if len(fluids) > 1:
                # Different fluid systems legitimately share sequence numbers.
                continue

        if _LSZ010_REQUIRE_SAME_SIZE:
            sizes = {str(e.get("size") or "").upper().strip() for e in entries if e.get("size")}
            if len(sizes) > 1:
                continue

        # ── Deduplication: same set of identities ─────────────────────────
        group_key = frozenset(f"{i[0]}-{i[1]}" for i in identities)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)

        # ── Build human-readable display strings ──────────────────────────
        # Collect unique texts in insertion order, capped at 4 for readability.
        texts = list(dict.fromkeys(e.get("text", "") for e in entries if e.get("text")))

        areas_display  = ", ".join(sorted({i[1] for i in identities if i[1]}))
        fluids_display = ", ".join(sorted({i[0] for i in identities if i[0]}))

        # Build the human-readable suffix from the first entry's actual values.
        suffix_display = "-".join(
            str(entries[0].get(f) or "").strip()
            for f in _LSZ010_SUFFIX_FIELDS
            if entries[0].get(f)
        )

        evidence = "  ·  ".join(texts[:4])

        out.append(RuleFinding(
            category="line_size",
            rule_id="LSZ-010",
            issue_observed=(
                f"Pipeline suffix '{suffix_display}' shared across different area codes "
                f"({areas_display}) on the same drawing -- "
                "possible copy-paste error in line numbering"
            ),
            action_required=(
                "Verify that each pipeline has a unique sequence number within its "
                "area / fluid combination. If these are separate physical lines, "
                "assign distinct sequence numbers per the project line-numbering "
                "convention. If intentional (e.g. shared-service line), add a note "
                "or cross-reference to justify the identical suffix."
            ),
            evidence=evidence,
            severity="major",
        ))

        if len(out) >= _LSZ010_MAX_FINDINGS:
            break

    return out


# ---------------------------------------------------------------------------
# LN-001 / LN-002  LINE DESIGNATION SEMANTIC RULES
# Source: PJ6-EXD-GEN-BQDA-0002 Rev 1 Sheet 001 Line Numbering System
# Soft-coded via _VALID_SERVICE_CODES and _VALID_INSULATION_CLASSES above.
# ---------------------------------------------------------------------------

def _check_line_designation_semantics(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    LN-001  Pipeline designation contains a fluid/service code that is not in
            the project service code registry (_VALID_SERVICE_CODES).
    LN-002  Pipeline designation has an insulation-class suffix that is not a
            valid project insulation code (must be one of C, H, P, T).

    Both rules use only the already-parsed line_tags list produced by extraction.py
    so there is no additional OCR / AI cost.
    """
    out: List[RuleFinding] = []
    line_tags = extraction.get('line_tags', [])
    if not line_tags:
        return out

    seen_ln001: set = set()
    seen_ln002: set = set()

    for lt in line_tags:
        text       = lt.get('text', '')
        fluid_code = str(lt.get('fluid_code') or '').strip().upper()
        insulation = str(lt.get('insulation') or '').strip().upper()

        # LN-001: Unknown fluid/service code
        if fluid_code and fluid_code not in _VALID_SERVICE_CODES:
            key = fluid_code
            if key not in seen_ln001:
                seen_ln001.add(key)
                out.append(RuleFinding(
                    category='line_designation',
                    rule_id='LN-001',
                    issue_observed=(
                        f"Line designation '{text}' uses service code '{fluid_code}' "
                        "which is not listed in the project service code registry "
                        "(PJ6-EXD-GEN-BQDA-0002 Sheet 001)"
                    ),
                    action_required=(
                        f"Verify service code '{fluid_code}' against the project legends "
                        "sheet (PJ6-EXD-GEN-BQDA-0002). Correct or register the code."
                    ),
                    evidence=text,
                    severity='major',
                ))

        # LN-002: Invalid insulation class
        if insulation and insulation not in _VALID_INSULATION_CLASSES:
            key = insulation
            if key not in seen_ln002:
                seen_ln002.add(key)
                out.append(RuleFinding(
                    category='line_designation',
                    rule_id='LN-002',
                    issue_observed=(
                        f"Line designation '{text}' has insulation-class suffix '{insulation}' "
                        "which is not a valid project insulation code. "
                        "Valid codes: C (Cold), H (Heat), P (Personnel), T (Tracing)"
                    ),
                    action_required=(
                        f"Replace suffix '{insulation}' with the correct insulation class "
                        "(C, H, P, or T) or remove the suffix if no insulation applies."
                    ),
                    evidence=text,
                    severity='minor',
                ))

    return out


# ---------------------------------------------------------------------------
# EQP-002  EQUIPMENT ITEM CODE VALIDATION
# Source: PJ6-EXD-GEN-BQDA-0002 Rev 1 Sheet 001 Equipment Numbering System
# Soft-coded via _VALID_EQUIPMENT_ITEM_CODES above.
# ---------------------------------------------------------------------------

def _check_equipment_item_codes(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    EQP-002  Equipment tag prefix does not match any known equipment item code
             in the project equipment catalogue.

    Only fires when the prefix is 1-2 letters (typical equipment code length);
    longer prefixes are likely instrument acronyms handled by naming_check.
    """
    out: List[RuleFinding] = []
    seen: set = set()

    for item in extraction.get('equipment', []):
        tag = str(item.get('tag') or '').strip()
        if not tag or '-' not in tag:
            continue
        prefix = tag.split('-')[0].upper()
        if len(prefix) > 2:
            # Longer than 2 letters are instrument-like and handled elsewhere
            continue
        if prefix in _INSTRUMENT_PREFIXES or prefix in _TAGGED_VALVE_PREFIXES:
            # Already checked by instruments/valves rules
            continue
        if prefix not in _VALID_EQUIPMENT_ITEM_CODES:
            if prefix not in seen:
                seen.add(prefix)
                out.append(RuleFinding(
                    category='equipment',
                    rule_id='EQP-002',
                    issue_observed=(
                        f"Equipment tag '{tag}' uses item code '{prefix}' "
                        "which is not in the project equipment catalogue "
                        "(PJ6-EXD-GEN-BQDA-0002 Sheet 001)"
                    ),
                    action_required=(
                        f"Verify equipment item code '{prefix}' against the project legends "
                        "sheet. Use the correct code from the catalogue or register a new one."
                    ),
                    evidence=tag,
                    severity='minor',
                ))

    return out


# ---------------------------------------------------------------------------
# RED-001  RED-COLORED ANNOTATION DETECTION
# Applies to vector PDFs only (PyMuPDF span color metadata).
# Soft-coded via _RED001_* constants above.
# ---------------------------------------------------------------------------

def _is_red_fragment(text: str) -> bool:
    """
    Return True when *text* is a sub-token of a larger pipeline annotation
    that was split into separate color spans by the PDF renderer.

    Examples that are suppressed: '013842-X', 'BD', 'X-N', '4860', '-N'
    Examples that are kept:       '2"-BD-6156-033842-X-N', 'H @ 65 bar',
                                  '18"', '6"x2"', 'HOLD 3'

    Soft-coded via _RED001_FRAGMENT_PATTERNS — extend the list for new projects.
    """
    t = text.strip()
    for pat in _RED001_FRAGMENT_PATTERNS:
        if pat.fullmatch(t):
            return True
    return False


def _classify_red_annotation(text: str) -> tuple:
    """
    Return (issue_observed, action_required, severity) for a meaningful red annotation.

    Classification priority (first match wins):
      1. Full pipeline line designation  → revised/new line
      2. Pressure annotation             → PMC check
      3. Reducer notation                → verify reducer
      4. HOLD keyword                    → resolve HOLD
      5. Revision keyword                → check change register
      6. Bare NPS size (e.g. 18")        → revised pipe size
      7. Fallback                        → general annotation

    Soft-coded: all patterns reuse existing module-level constants so they
    automatically stay in sync as those constants are tuned.
    """
    t = text.strip()

    # 1. Full pipeline line designation
    if _PIPELINE_DESIG_RE.search(t):
        return (
            f"Revised/new pipeline designation '{t}' in red "
            "— added or changed in this revision's scope cloud.",
            "Confirm this line designation appears in the revision change register. "
            "Ensure the line list, MTO, and associated isometrics are updated.",
            'major',
        )

    # 2. Pressure annotation
    pm = _ANN_PRESSURE_RE.search(t)
    if pm:
        try:
            bar_val = float(pm.group(1))
        except (ValueError, TypeError):
            bar_val = 0.0
        psi = round(bar_val * 14.504)
        sev = 'critical' if bar_val > _ANN_PRESSURE_MAX_BAR else 'info'
        return (
            f"Pressure annotation '{t}' in red "
            f"({bar_val} bar / {psi} psi) — verify piping class is rated for this pressure.",
            "Cross-check the piping material class (PMC) design pressure. "
            "Confirm all valves, instruments, and flanges on this line or nozzle are rated accordingly.",
            sev,
        )

    # 3. Reducer notation
    rm = _REDUCER_RE.search(t)
    if rm:
        try:
            a, b = float(rm.group(1)), float(rm.group(2))
            larger, smaller = (a, b) if a >= b else (b, a)
            ratio = larger / smaller if smaller > 0 else 0
            return (
                f"Reducer annotation '{t}' in red "
                f"({larger:.4g}\"\u00d7{smaller:.4g}\", ratio {ratio:.2f}:1) "
                "— verify the reducer is specified and sized correctly.",
                "Confirm the reducer is included in the piping line specification and stress model. "
                "Check velocity and pressure drop across the transition.",
                'major',
            )
        except (ValueError, TypeError):
            pass

    # 4. HOLD item
    if re.search(r'\bHOLD\b', t, re.I):
        return (
            f"HOLD annotation in red: '{t}' — action item requires resolution before IFC.",
            "Resolve the HOLD and update the drawing revision. "
            "Document the resolution in the project HOLD register.",
            'critical',
        )

    # 5. Revision / change keyword
    if re.search(r'\b(?:REV|REVISION|ISSUED|ADDED|DELETED|MODIFIED|CHANGED|NEW)\b', t, re.I):
        return (
            f"Revision marker in red: '{t}' — verify this change is in the revision record.",
            "Confirm this annotation is documented in the drawing revision register. "
            "Ensure affected systems (MTO, datasheets, isometrics) are updated.",
            'major',
        )

    # 6. Bare NPS pipe size  e.g. '18"', '6"'
    if re.fullmatch(r'\d{1,2}(?:\.\d+)?\s*(?:"|\'\')', t):
        return (
            f"Revised pipe-size annotation '{t}' in red inside scope cloud "
            "— verify line designations on the affected segment match this size.",
            "Check all line designations on the affected pipe segment agree with this annotated size. "
            "Confirm reducers and spec-breaks are documented.",
            'major',
        )

    # 7. Fallback — general meaningful annotation
    return (
        f"Red-colored annotation: '{t}' — review against the revision change record.",
        "Verify this annotation is intentional and covered by the current revision record. "
        "If it is a scope-change item, confirm alignment with the revision cloud on the drawing.",
        'major',
    )


def _check_red_annotations(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    RED-001  A meaningful red-colored text span was detected on the drawing.

    Fragment spans produced by the PDF renderer splitting a single annotation
    across multiple color runs are automatically suppressed via
    _is_red_fragment().  Only self-contained annotations are reported.

    Each finding carries a specific issue message and action derived from the
    annotation type (line tag, pressure, reducer, HOLD, revision marker, etc.)
    so the engineer can act immediately without manual interpretation.

    Soft-coded via _RED001_MAX_FINDINGS, _RED001_MIN_TEXT_LEN, and
    _RED001_FRAGMENT_PATTERNS — all adjustable per project.
    """
    out: List[RuleFinding] = []
    red_anns = extraction.get('red_annotations', [])
    if not red_anns:
        return out

    for ann in red_anns:
        text = str(ann.get('text', '')).strip()
        if len(text) < _RED001_MIN_TEXT_LEN:
            continue
        # Suppress sub-token fragments (e.g. '013842-X', 'BD', 'X-N')
        if _is_red_fragment(text):
            continue
        issue, action, sev = _classify_red_annotation(text)
        out.append(RuleFinding(
            category='line_designation',
            rule_id='RED-001',
            issue_observed=issue,
            action_required=action,
            evidence=text,
            severity=sev,
        ))
        if len(out) >= _RED001_MAX_FINDINGS:
            break

    return out


# ---------------------------------------------------------------------------
# VLV-002 / VLV-003  VALVE TYPE SIZE LIMITS
# Soft-coded via _VLV_GLOBE_MAX_INCH, _VLV_CONTROL_MAX_INCH above.
# ---------------------------------------------------------------------------

def _check_valve_type_size(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    VLV-002  Globe valve bore exceeds the recommended maximum NPS.
             Globe valves have high pressure drop and significant weight above NPS 16".
             A 30" globe valve is practically never specified in process plants —
             investigate for drawing annotation error or wrong valve type.

    VLV-003  Control valve bore exceeds the recommended maximum NPS for vendor
             shop-drawing review.  Large control valves require individual hydraulic
             sizing confirmation and extended-body vendor approval.

    Both thresholds are soft-coded and independent.  Edit the module-level constants
    _VLV_GLOBE_MAX_INCH and _VLV_CONTROL_MAX_INCH to tune without redeploying.
    """
    out: List[RuleFinding] = []
    valve_size_contexts = extraction.get('valve_size_contexts', [])
    if not valve_size_contexts:
        return out

    seen_globe:   set = set()
    seen_control: set = set()

    for ctx in valve_size_contexts:
        equip_type = ctx.get('equipment_type', '').upper().strip()
        size_val   = float(ctx.get('size_inch', 0) or 0)
        evidence   = ctx.get('text', '')[:120]

        # VLV-002: Globe valve limit
        if equip_type in _VLV_GLOBE_KEYWORDS and size_val > _VLV_GLOBE_MAX_INCH:
            key = round(size_val)
            if key not in seen_globe:
                seen_globe.add(key)
                out.append(RuleFinding(
                    category='valve',
                    rule_id='VLV-002',
                    issue_observed=(
                        f"Globe valve annotated at {size_val:.4g}\" NPS — exceeds the "
                        f"soft-coded maximum of {_VLV_GLOBE_MAX_INCH:.4g}\" for globe valves. "
                        "Globe valves above NPS 16\" are rarely specified due to pressure "
                        "drop, body weight, and vendor availability constraints. "
                        "Likely drawing annotation error (wrong valve type or wrong size)."
                    ),
                    action_required=(
                        "Verify the valve type: should this be a gate, butterfly, or ball "
                        "valve at this bore? If globe valve is intentional, obtain a specific "
                        "vendor quotation and update the piping stress analysis."
                    ),
                    evidence=evidence,
                    severity='critical',
                ))

        # VLV-003: Control valve limit
        if equip_type in _VLV_CONTROL_KEYWORDS and size_val > _VLV_CONTROL_MAX_INCH:
            key = round(size_val)
            if key not in seen_control:
                seen_control.add(key)
                out.append(RuleFinding(
                    category='valve',
                    rule_id='VLV-003',
                    issue_observed=(
                        f"Control valve annotated at {size_val:.4g}\" NPS — exceeds the "
                        f"soft-coded maximum of {_VLV_CONTROL_MAX_INCH:.4g}\" for automatic "
                        "sizing review. Large control valves require hydraulic model "
                        "confirmation and individual vendor shop drawing approval."
                    ),
                    action_required=(
                        "Provide control valve sizing calculation to vendor. Confirm the "
                        "selected Cv / Kv at design flow and verify the body size against "
                        "the line sizing engineer's data sheet."
                    ),
                    evidence=evidence,
                    severity='major',
                ))

    return out


# ---------------------------------------------------------------------------
# LSZ-011  EXTREME REDUCER RATIO
# Soft-coded via _LSZ011_MAX_REDUCTION_RATIO above.
# ---------------------------------------------------------------------------

def _check_reducers(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    LSZ-011  A reducer annotation on this drawing has a larger:smaller NPS ratio
             that exceeds the soft-coded maximum (default 2.5:1).

    Example:  6"x2"  = 3.0 : 1  →  flags (extreme reduction)
              6"x4"  = 1.5 : 1  →  OK
              8"x3"  = 2.67: 1  →  flags

    An extreme reduction can indicate:
      • Copy-paste drawing error (wrong smaller bore entered)
      • Missing intermediate spec-break pipe segment
      • A pump suction/discharge transition documented as a single reducer
        when two reducers in series are required by stress analysis

    Soft-coded via _LSZ011_MAX_REDUCTION_RATIO.
    """
    out: List[RuleFinding] = []
    seen: set = set()

    for red in extraction.get('reducers', []):
        ratio  = float(red.get('ratio', 0) or 0)
        if ratio < _LSZ011_MAX_REDUCTION_RATIO:
            continue
        larger  = red.get('larger_inch', 0)
        smaller = red.get('smaller_inch', 0)
        key = (round(larger, 1), round(smaller, 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-011',
            issue_observed=(
                f"Extreme reducer annotation {red.get('text', '')} detected — "
                f"size ratio {ratio:.2f}:1 exceeds the soft-coded maximum of "
                f"{_LSZ011_MAX_REDUCTION_RATIO}:1 for a single reducer. "
                "Standard practice limits single-step reducers to 2:1 or 2.5:1 "
                "to avoid cavitation and high local velocities."
            ),
            action_required=(
                "Verify the reducer ratio is correct. If intentional, confirm with the "
                "piping stress engineer and hydraulic designer. Consider splitting into "
                "two reducers in series if the ratio exceeds project specification limits."
            ),
            evidence=red.get('text', ''),
            severity='major',
        ))

    return out


# ---------------------------------------------------------------------------
# ANN-001  PRESSURE ANNOTATION CHECK
# Soft-coded via _ANN_PRESSURE_MAX_BAR and _ANN_PRESSURE_RE above.
# ---------------------------------------------------------------------------

def _check_pressure_annotations(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    ANN-001  A pressure annotation on the drawing (format: ``{class} @ {N} bar``)
             was detected.

    Patterns caught::
        H @ 65 bar   (heat-insulated line at 65 barg — check PMC covers 65 bar)
        P @ 120 bar  (personnel protection insulation at 120 barg — critical)
        @ 65 bar     (standalone pressure callout near a valve or nozzle)

    Annotations ≤ _ANN_PRESSURE_MAX_BAR are reported as 'info' (informational).
    Annotations > _ANN_PRESSURE_MAX_BAR are reported as 'critical' and require
    the engineer to confirm the piping material class (PMC) is rated accordingly.

    Soft-coded via _ANN_PRESSURE_MAX_BAR (default 50 bar) and _ANN_PRESSURE_RE.
    """
    out: List[RuleFinding] = []
    raw_text = extraction.get('raw_text', '')
    if not raw_text:
        return out

    seen: set = set()
    for line in raw_text.splitlines():
        for m in _ANN_PRESSURE_RE.finditer(line):
            try:
                pressure = float(m.group(1))
            except (ValueError, TypeError):
                continue
            if pressure <= 0 or pressure > 999:
                continue
            key = round(pressure, 1)
            if key in seen:
                continue
            seen.add(key)
            evidence_text = line.strip()[:120]

            if pressure > _ANN_PRESSURE_MAX_BAR:
                out.append(RuleFinding(
                    category='line_designation',
                    rule_id='ANN-001',
                    issue_observed=(
                        f"High-pressure annotation detected: {pressure} bar "
                        f"({pressure * 14.504:.0f} psi). "
                        f"Exceeds the soft threshold of {_ANN_PRESSURE_MAX_BAR} bar. "
                        "Verify the piping material class (PMC) and all inline components "
                        "(valves, instruments, nozzles) are rated for this pressure."
                    ),
                    action_required=(
                        "Cross-check the stated pressure against the project Piping Class "
                        "index. Confirm all valves, instruments, and flanges on this line "
                        "are rated to the stated design pressure. Add a pressure basis note "
                        "if this deviates from the standard class."
                    ),
                    evidence=evidence_text,
                    severity='critical',
                ))
            else:
                out.append(RuleFinding(
                    category='line_designation',
                    rule_id='ANN-001',
                    issue_observed=(
                        f"Pressure annotation detected: {pressure} bar. "
                        "Verify this matches the line's design pressure in the piping class."
                    ),
                    action_required=(
                        "Confirm the annotated pressure matches the piping material class "
                        "design pressure for this service. No action needed if pressure matches."
                    ),
                    evidence=evidence_text,
                    severity='info',
                ))

    return out


# ---------------------------------------------------------------------------
# LSZ-012  EQUIPMENT SIZE ANNOTATION MISMATCH
# Soft-coded via _EQUIPMENT_SIZE_CTX_RE below.
# ---------------------------------------------------------------------------

# Equipment names that can carry a size annotation directly adjacent.
# Add new names here to detect more equipment-type size annotation patterns.
_EQUIPMENT_SIZE_CTX_RE = re.compile(
    r'(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')\s*(?:in\s+)?'
    r'(VORTEX\s+BREAKER'
    r'|IMPINGEMENT\s+PLATE'
    r'|VORTEX\s+PLATE'
    r'|DISTRIBUTION\s+TRAY'
    r'|ANNULAR\s+DISTRIBUTOR'
    r'|RISER\s+PIPE'
    r'|DOWNCOMER'
    r'|SUCTION\s+NOZZLE'
    r'|DISCHARGE\s+NOZZLE'
    r')',
    re.IGNORECASE,
)

def _check_equipment_size_annotations(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    LSZ-012  A size annotation appears directly adjacent to an equipment-type
             keyword (e.g. "20\\" in VORTEX BREAKER") but the stated size does
             not match any line designation NPS currently extracted from this drawing.

    This pattern catches common transcription errors where the equipment size
    annotation was copied from another drawing or vessel nozzle schedule without
    updating to the actual connected pipe size.

    Soft-coded via _EQUIPMENT_SIZE_CTX_RE — add new equipment names without
    touching rule logic.
    """
    out: List[RuleFinding] = []
    raw_text = extraction.get('raw_text', '')
    if not raw_text:
        return out

    # Build the set of NPS sizes confirmed by pipeline line designations.
    line_tag_sizes: set = set()
    for lt in extraction.get('line_tags', []):
        sz = str(lt.get('size') or '').strip()
        if sz:
            try:
                line_tag_sizes.add(float(sz.rstrip('"')))
            except ValueError:
                pass

    seen: set = set()
    for m in _EQUIPMENT_SIZE_CTX_RE.finditer(raw_text):
        try:
            size_val = float(m.group(1))
        except ValueError:
            continue
        if size_val <= 0 or size_val > 60:
            continue
        equip_name = ' '.join(m.group(2).upper().split())
        key = (round(size_val, 1), equip_name)
        if key in seen:
            continue
        seen.add(key)

        # Only flag if we have extracted line tags and the size is absent from them.
        if line_tag_sizes and size_val not in line_tag_sizes:
            sorted_known = sorted(
                f'{int(s)}"' if s == int(s) else f'{s}"' for s in line_tag_sizes
            )
            out.append(RuleFinding(
                category='equipment',
                rule_id='LSZ-012',
                issue_observed=(
                    f"Equipment size annotation '{size_val:.4g}\" {equip_name}' "
                    "does not match any pipeline line designation NPS on this drawing. "
                    f"Known NPS sizes from line tags: {', '.join(sorted_known[:6])}."
                ),
                action_required=(
                    "Verify the equipment size annotation matches the nozzle or pipe "
                    "it connects to. If the connected line designation was not extracted, "
                    "re-run OCR at higher DPI or check for non-standard label format. "
                    "Correct the equipment annotation or add the missing line designation."
                ),
                evidence=m.group(0).strip()[:120],
                severity='major',
            ))

    return out


# ---------------------------------------------------------------------------
# COMPRESSOR EQUIPMENT RULES  (CMP-001 … CMP-008)
# ---------------------------------------------------------------------------

def _check_compressor_equipment(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    CMP-001  Verify the type of compressor is shown correctly on the P&ID.
    CMP-002  Check intercooler/aftercooler connections include isolation valves
             and temperature measurements.
    CMP-003  Confirm temporary strainers are provided for start-up and commissioning.
    CMP-004  Identify check valve(s) installed downstream of the compressor,
             including their type.
    CMP-005  Verify anti-surge / recycle / hot-gas bypass arrangements are shown.
    CMP-006  Identify relief and blowdown requirements.
    CMP-007  Identify ESD valves on compressor suction and discharge.
    CMP-008  Verify correct driver is identified (e.g. GT, Motor, Steam Turbine).

    All keyword sets are soft-coded at module level (_CMP_* constants).
    Add entries to those sets to extend detection without changing this function.
    """
    out: List[RuleFinding] = []
    raw_text = (extraction.get('raw_text', '') or '').upper()
    tag_positions = extraction.get('tag_positions', {})

    # ── Detect whether this drawing contains compressor equipment ──────────────
    # Match either a tag-position entry whose prefix is in _CMP_EQUIP_CODES OR
    # a raw-text keyword match so we also catch drawings without tag extraction.
    has_compressor = any(
        any(tag.upper().startswith(code) for code in _CMP_EQUIP_CODES)
        for tag in tag_positions
    ) or any(kw in raw_text for kw in _CMP_KEYWORDS)

    if not has_compressor:
        return out   # No compressor on this drawing — nothing to check

    def _any(keyword_set: set) -> bool:
        return any(kw in raw_text for kw in keyword_set)

    # ── CMP-001: Compressor type clearly shown ─────────────────────────────────
    if not _any(_CMP_TYPE_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-001',
            issue_observed=(
                'A compressor is present on this drawing but no compressor type '
                '(e.g. Centrifugal, Reciprocating, Screw, Axial) was detected in '
                'the P&ID annotations or text. The type of compressor must be '
                'identified on the drawing per project standards.'
            ),
            action_required=(
                'Add the compressor type designation to the equipment tag label or '
                'adjacent drawing note (e.g. "CENTRIFUGAL COMPRESSOR", '
                '"RECIPROCATING COMPRESSOR"). Update the equipment list accordingly.'
            ),
            severity='major',
        ))

    # ── CMP-002: Intercooler/Aftercooler with isolation valves + temp measurement ─
    if _any(_CMP_COOLER_KEYWORDS):
        missing = []
        if not _any(_CMP_ISOLATION_KEYWORDS):
            missing.append('isolation valves')
        if not _any(_CMP_TEMP_KEYWORDS):
            missing.append('temperature measurement instruments (TI/TT/TE)')
        if missing:
            out.append(RuleFinding(
                category='equipment',
                rule_id='CMP-002',
                issue_observed=(
                    f'Intercooler/Aftercooler detected but the following items appear '
                    f'to be missing: {", ".join(missing)}. '
                    'Intercooler/aftercooler piping must include isolation valves to '
                    'allow maintenance isolation and temperature instruments to confirm '
                    'cooling duty is being achieved.'
                ),
                action_required=(
                    'Add isolation (block) valves on the inlet and outlet nozzles of '
                    'the intercooler/aftercooler. Add temperature indicators or '
                    'transmitters (TI/TT) on the process outlet to verify cooling '
                    'performance. Update the instrument index.'
                ),
                severity='major',
            ))

    # ── CMP-003: Temporary strainers for start-up / commissioning ─────────────
    if not _any(_CMP_STRAINER_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-003',
            issue_observed=(
                'No temporary strainer provision detected on the compressor suction '
                'piping. Temporary strainers are required during start-up and '
                'commissioning to protect compressor internals from construction '
                'debris and pipework contamination.'
            ),
            action_required=(
                'Add a temporary strainer (cone or basket type) to the compressor '
                'suction line upstream of the compressor package boundary. Note the '
                'strainer as "TEMPORARY — REMOVE AFTER COMMISSIONING" with a tie-in '
                'point or spool provision shown on the P&ID.'
            ),
            severity='major',
        ))

    # ── CMP-004: Check valve(s) downstream of compressor ───────────────────────
    if not _any(_CMP_CHECK_VALVE_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-004',
            issue_observed=(
                'No check valve (NRV / non-return valve) was detected on the '
                'compressor discharge. Check valves are mandatory on compressor '
                'discharge lines to prevent reverse flow and protect the compressor '
                'from backpressure during shutdown or trip conditions.'
            ),
            action_required=(
                'Add a check valve (specify type: swing, dual-plate, or lift-check) '
                'on the compressor discharge line downstream of the discharge '
                'isolation valve. Confirm the check-valve type with the mechanical '
                'datasheet and update the line list.'
            ),
            severity='critical',
        ))

    # ── CMP-005: Anti-surge / recycle / hot-gas bypass ────────────────────────
    if not _any(_CMP_ANTISURGE_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-005',
            issue_observed=(
                'No anti-surge, recycle, or hot-gas bypass arrangement was detected '
                'for the compressor. These systems are required to protect the '
                'compressor from surge conditions during start-up, shutdown, and '
                'process upsets.'
            ),
            action_required=(
                'Show the anti-surge or recycle line with its control valve (FCV/PCV) '
                'and the hot-gas bypass arrangement (if applicable) on the P&ID. '
                'The surge control strategy must be agreed with the process licensor '
                'and compressor vendor before P&ID issue.'
            ),
            severity='major',
        ))

    # ── CMP-006: Relief and blowdown requirements ──────────────────────────────
    if not _any(_CMP_RELIEF_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-006',
            issue_observed=(
                'No relief valve or blowdown provision detected for the compressor. '
                'Pressure relief protection and blowdown facilities are mandatory '
                'for compressor packages to comply with pressure safety regulations '
                '(PED, ASME VIII, local codes).'
            ),
            action_required=(
                'Add relief valve (PSV/PRV) sizing note and blowdown valve (BDV) to '
                'the compressor discharge piping. Verify the relief valve set pressure '
                'against the MAWP of the downstream piping and confirm routing to '
                'the flare / blowdown header.'
            ),
            severity='critical',
        ))

    # ── CMP-007: ESD on suction and discharge ─────────────────────────────────
    if not _any(_CMP_ESD_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-007',
            issue_observed=(
                'No Emergency Shutdown Device (ESD/ESDV) or shutdown valve (SDV) '
                'detected on compressor suction or discharge. ESD valves are required '
                'subject to compressor configuration to enable emergency isolation '
                'during process upset or fire/gas detection events.'
            ),
            action_required=(
                'Confirm the ESD philosophy with the safety case / HAZOP action list. '
                'If ESD valves are required, add SDV/ESDV tags to suction and '
                'discharge piping and link them to the compressor trip logic in the '
                'cause-and-effect matrix.'
            ),
            severity='major',
        ))

    # ── CMP-008: Driver identification (GT, Motor, Steam Turbine, etc.) ────────
    if not _any(_CMP_DRIVER_KEYWORDS):
        out.append(RuleFinding(
            category='equipment',
            rule_id='CMP-008',
            issue_observed=(
                'The driver type for the compressor could not be identified in the '
                'P&ID text. The driver (Gas Turbine, Electric Motor, Steam Turbine, '
                'etc.) must be clearly labelled on the drawing to define the utility '
                'requirements and hook-up connections.'
            ),
            action_required=(
                'Add the driver type and tag to the compressor block symbol on the '
                'P&ID. Where a motor drive is used, show the MCC reference tag. '
                'Where a gas turbine is used, show the GT tag and associated '
                'auxiliary connections (fuel gas, lube oil, air intake).'
            ),
            severity='major',
        ))

    return out
