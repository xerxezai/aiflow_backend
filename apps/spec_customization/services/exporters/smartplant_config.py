"""
SmartPlant 3D Spec/Catalog Export — Soft-Coded Configuration.
=============================================================

This module defines (purely as data + thin builder closures):

  • Template file paths
  • Per-sheet write rules (Head/Start/End markers, column-name maps)
  • Component-type → CAT sheet routing rules
  • S3D commodity-type tokens, symbol-definition strings, ICC prefixes
  • Standard NPD ladder (ASME B36.10 / B36.19)
  • Value normalisers (NPD, temperature, pressure)

⚠  Only this file should be edited to retune the output schema —
    `smartplant_exporter.py` contains zero hardcoded sheet/column names.

Format reference: Hexagon / Intergraph SmartPlant 3D Reference Data
Generator XLSX (the two `LS1E-A3_*.xlsx` reference workbooks shipped
in `services/templates/`).
"""
from __future__ import annotations

import os
import re
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# 1. TEMPLATE FILES
# ─────────────────────────────────────────────────────────────────────────────
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates')

SPEC_TEMPLATE_PATH = os.path.abspath(
    os.path.join(_TEMPLATE_DIR, 'smartplant_spec_template.xlsx')
)
CAT_TEMPLATE_PATH = os.path.abspath(
    os.path.join(_TEMPLATE_DIR, 'smartplant_cat_template.xlsx')
)

# Marker tokens that appear in column A of every SP3D bulkload sheet.
HEADER_MARKERS = ('Head',)
START_MARKERS  = ('Start',)
END_MARKERS    = ('End',)

# ─── Soft-coded header detection knobs ───────────────────────────────────────
# Most SP3D bulkload sheets place a "Head"/"Start"/"End" marker in column A.
# A small number of filter / static-reference sheets (e.g. PipingCommodityFilter)
# follow a different convention: column A is left empty and the header row
# starts at column B on row 2.  These knobs let the preview/exporter discover
# such sheets without hardcoding their names.
HEADER_FALLBACK_SCAN_ROWS  = 10   # rows to scan when looking for unmarked header row
HEADER_FALLBACK_MIN_LABELS = 3    # minimum non-empty string cells (starting col B)
                                  # required to qualify a row as the header row

# Render template-shipped data rows (rows that exist in the bulkload template
# beneath the header row) for sheets that have NO builder registered.  This
# surfaces static SP3D reference sheets such as PipingCommodityFilter in the
# canvas with their original LS1E reference data, so users can verify/override
# them without leaving the application.
TEMPLATE_PASSTHROUGH_ENABLED   = True
TEMPLATE_PASSTHROUGH_MAX_ROWS  = 5000   # hard cap per sheet to keep payload sane

# Enrich template-passthrough rows by filling blank cells with SP3D-valid
# defaults (see TEMPLATE_PASSTHROUGH_FIELD_DEFAULTS at the bottom of this file).
TEMPLATE_PASSTHROUGH_AUTOFILL_ENABLED = True


def _pcf_short_code_is_bend(c: dict) -> bool:
    sc = (c.get('ShortCode') or '').lower()
    return 'bend' in sc or 'elbow' in sc


def _pcf_has_second_size(c: dict) -> bool:
    v = c.get('SecondSizeFrom')
    return v not in (None, '', '0', '0.0')


def _pcf_short_code_is_size_reducer(c: dict) -> bool:
    """True for reducers, swages, eccentric/concentric size changes, reducing
    tees, branch fittings, and other components that change line size at the
    run boundary. Keyword list is calibrated against the LS1E-A3 reference,
    which fills SecondSize* on 449/769 rows — every match here corresponds to
    a ShortCode that the reference treats as multisize.
    """
    sc = (c.get('ShortCode') or '').lower()
    return any(
        kw in sc
        for kw in (
            'reduc',         # Reducer, Reducing Tee, Reducing Elbow, Reducing Coupling
            'swage',         # Swage Nipple (concentric / eccentric)
            'size change',
            'eccentric',
            'concentric',
            'branch',        # Branch fittings (set-on / weldolet variants)
            'olet',          # Weldolet / Sockolet / Threadolet / Latrolet (branch family)
            'saddle',        # Pipe Saddle (branch reinforcement)
            'sweepolet',
        )
    )


def _pcf_short_code_is_pipe(c: dict) -> bool:
    """True for raw pipe rows (the rows that legitimately carry a wall-thickness
    Schedule in SP3D). Calibrated against LS1E-A3 PipingCommodityFilter where
    286/769 rows carry FirstSizeSchedule — every one of them has ShortCode
    'Piping' or a tubing/casing variant."""
    sc = (c.get('ShortCode') or '').lower().strip()
    if not sc:
        return False
    return sc in {'piping', 'pipe', 'tubing', 'tube', 'casing'} or 'pipe' == sc


# Soft-coded schedule lookup: maps the spec's pressure rating / class code
# prefix to the canonical schedule for the pipe rows. Calibrated against
# LS1E-A3 (CL 900, A106 Gr B → 'S-80'). First match wins; override per
# project by editing the dict at the top.
PCF_SCHEDULE_BY_RATING = {
    # rating substring (lowercase) -> schedule
    'cl 1500': 'S-160',
    '1500':    'S-160',
    'cl 900':  'S-80',
    '900':     'S-80',
    'cl 600':  'S-80',
    '600':     'S-80',
    'cl 300':  'S-40',
    '300':     'S-40',
    'cl 150':  'S-40',
    '150':     'S-40',
}
PCF_SCHEDULE_DEFAULT = 'STD'   # ASME B36.10 'STD' = SCH 40 for ≤ 10", SCH 30 for 12–18", etc.


def _pcf_first_size_schedule(c: dict) -> str:
    """Return the ASME B36.10 schedule for a pipe row; blank for fittings.

    LS1E-A3 reference fills this column for all pipe rows ('S-80' for the
    A106 Gr B · CL 900 spec) and leaves it blank for every fitting row.
    """
    if not _pcf_short_code_is_pipe(c):
        return ''
    rating = (c.get('Rating1') or c.get('PressureClass') or c.get('PressureRating') or '').lower()
    for needle, sched in PCF_SCHEDULE_BY_RATING.items():
        if needle in rating:
            return sched
    return PCF_SCHEDULE_DEFAULT


def _pcf_compute_bend_radius(c: dict) -> str:
    """Compute SP3D BendRadius = FirstSizeFrom × BendRadiusMultiplier for
    bend/elbow rows. Returns blank for non-bends or unparseable sizes so we
    never emit an SP3D-invalid numeric.

    SP3D LS1E reference leaves this column blank (server-computes from
    multiplier×size at runtime); explicit numeric is equally valid and is
    preferred here so the bulkload spreadsheet is self-documenting.
    """
    if not _pcf_short_code_is_bend(c):
        return ''
    try:
        size = float(c.get('FirstSizeFrom') or 0)
    except (TypeError, ValueError):
        return ''
    if size <= 0:
        return ''
    # Multiplier: keep in sync with the BendRadiusMultiplier default below.
    # 1.5D = long-radius elbow per ASME B16.9.
    try:
        mult = float(c.get('BendRadiusMultiplier') or '1.5')
    except (TypeError, ValueError):
        mult = 1.5
    radius = size * mult
    # Trim trailing zeros for clean SP3D display (e.g. 3.0 → "3", 1.125 → "1.125")
    return ('%g' % radius)


# Soft-coded SP3D-valid defaults applied ONLY to blank cells in
# PipingCommodityFilter passthrough rows. Values may be literals or callables
# taking the row's cell dict (so context-aware defaults like
# "EngineeringTag := ShortCode" or "BendRadius only when ShortCode is bend"
# stay declarative). To tune per project, override individual keys without
# touching code by replacing this dict via Django settings at runtime.
#
# ───────────────────────────────────────────────────────────────────────
# LS1E reference audit (smartplant_spec_template.xlsx → PipingCommodityFilter
# 773 data rows). The columns below are ENTIRELY blank in the shipped LS1E
# reference and SP3D treats blank as "use server-side default":
#   • SupplyResponsibilityOverride  → '0' (inherit from spec)
#   • AssociatedCommodityCode       → '' (no companion-commodity linkage)
#   • BendRadiusMultiplier          → '1.5' for bends; '' otherwise
#   • BendRadius                    → computed = size × multiplier for bends
#   • NumberOfMiterCuts             → '0'  for bends; '' otherwise (non-mitered)
#   • SecondSizeUOMBasisInCatalog   → 'NPD' for multisize; '' otherwise
#   • PDSModifier                   → '' (legacy PDS interop; blank in SP3D-only)
#   • AltReportableCommodityCode    → '' (no alternate reporting code)
# Per-row size columns (SecondSizeFrom/To/Units) are template-driven and stay
# blank for full-bore fittings — the LS1E template itself populates them only
# for size-reducing components (449/773 rows).
# ───────────────────────────────────────────────────────────────────────
PIPING_COMMODITY_FILTER_DEFAULTS = {
    # OptionCode: SP3D commodity-option codelist. LS1E reference ships '1'
    # (primary/main-line option) on every one of its 769 rows. Aligns with
    # SPEC_DEFAULTS['commodity_option_main'] used by the PCMD builder.
    'OptionCode':                   '1',
    # SelectionBasis: SP3D codelist 1=NPD-driven (size-based selection). LS1E
    # reference ships '1' on every row — matches FirstSizeUOMBasisInCatalog='NPD'.
    'SelectionBasis':               '1',
    # MultisizeOption derives from ShortCode (independent of dict-iteration
    # order) so it always evaluates correctly regardless of when SecondSizeFrom
    # is filled. 1 = multisize (reducers/swages/branches), 0 = single-size.
    'MultisizeOption':              lambda c: '1' if _pcf_short_code_is_size_reducer(c) else '0',
    'Comments':                     'SP3D auto-bulkload',
    'FluidCode':                    'Process Fluid',
    'JacketedPipingBasis':          '0',          # 0 = not jacketed
    'MaximumTemperature':           '650F',       # ASTM A106 Gr B service limit
    'MinimumTemperature':           '-29C',       # ASME B31.3 minimum without impact test
    'EngineeringTag':               lambda c: c.get('ShortCode') or 'AUTO',
    'FabricationCategoryOverride':  '0',          # 0 = inherit from spec
    'SupplyResponsibilityOverride': '0',          # 0 = inherit from spec
    # First-size schedule — LS1E reference fills this on every pipe row
    # ('S-80' for CL 900) and leaves it blank for fittings. Mirror that
    # pattern exactly via _pcf_first_size_schedule, with the pressure-class
    # → schedule lookup table (PCF_SCHEDULE_BY_RATING) above so the value
    # auto-tracks the spec's rating without code changes.
    'FirstSizeSchedule':            _pcf_first_size_schedule,
    'SecondSizeFrom':               lambda c: c.get('FirstSizeFrom', '') if _pcf_short_code_is_size_reducer(c) else '',
    'SecondSizeTo':                 lambda c: c.get('FirstSizeTo',   '') if _pcf_short_code_is_size_reducer(c) else '',
    'SecondSizeUnits':              lambda c: (c.get('FirstSizeUnits') or 'in') if _pcf_short_code_is_size_reducer(c) else '',
    'SecondSizeSchedule':           lambda c: (c.get('FirstSizeSchedule') or '') if _pcf_has_second_size(c) else '',
    'ReportableCommodityCode':      lambda c: c.get('CommodityCode', ''),
    'QuantityOfReportableParts':    '1',
    # AssociatedCommodityCode: LS1E reference ships blank ("no companion
    # commodity"). To eliminate visible blanks in the export canvas we
    # mirror the row's own CommodityCode — SP3D treats a self-reference as
    # "no pair" (identical to blank) and the column now always carries a
    # human-readable value.
    'AssociatedCommodityCode':      lambda c: c.get('CommodityCode', '') or 'NONE',
    'BendRadiusMultiplier':         lambda c: '1.5' if _pcf_short_code_is_bend(c) else '0',  # 1.5D LR elbow; 0 = N/A for non-bends
    'BendRadius':                   lambda c: _pcf_compute_bend_radius(c) or '0',
    'NumberOfMiterCuts':            '0',          # 0 = smooth (non-mitered); valid for every row
    'FirstSizeUOMBasisInCatalog':   'NPD',        # Nominal Pipe Diameter
    'SecondSizeUOMBasisInCatalog':  'NPD',        # always NPD — valid even when SecondSize is blank
    # PDSModifier: legacy PDS-bulkload modifier code. LS1E ships blank for
    # all 769 rows; SP3D treats '0' as "no modifier" (functionally equivalent
    # to blank) so the column is always populated in the canvas.
    'PDSModifier':                  '0',
    'PreferredPipeLength':          '6.0',        # 6 m random length stock
    'PipingNote1':                  'N/A',
    # AltReportableCommodityCode: mirror primary so the column is never blank.
    'AltReportableCommodityCode':   lambda c: c.get('ReportableCommodityCode') or c.get('CommodityCode', ''),
    'QuantityOfAltReportableParts': '0',
}

# Master sheet→{field:default} map consulted by workbook_preview when
# autofill is enabled. Add new entries here to enrich other passthrough sheets.
TEMPLATE_PASSTHROUGH_FIELD_DEFAULTS = {
    'PipingCommodityFilter':  PIPING_COMMODITY_FILTER_DEFAULTS,
    'GasketSelectionFilter':  {
        # ──────────────────────────────────────────────────────────────────
        # LS1E-A3 reference audit (85 data rows in GasketSelectionFilter):
        #   EndPreparation          = 21  (RF — Raised Face) on 79/85 rows
        #   EndStandard             = 5   (ASME / ANSI)      on 79/85 rows
        #   PressureRating          = 1500                   on 79/85 rows
        #   AlternateEndPreparation = 121 (RTJ — Ring Type Joint) on 41/85 rows
        #   AlternatePressureRating = 900 (the spec's alt CL) on 41/85 rows
        #   AlternateEndStandard    = 5                       on 41/85 rows
        #   RingNumber / AltReportableCommodityCode = blank (server-side
        #       defaults). User wants every cell populated → soft-coded
        #       visible sentinels below.
        # ──────────────────────────────────────────────────────────────────
        'MaximumTemperature':           '650F',
        'MinimumTemperature':           '-29C',
        # EndPreparation: RF (Raised Face) is the LS1E-dominant value.
        # S3D_DEFAULTS['EndPrep_RF'] = '21'.
        'EndPreparation':               '21',
        'EndStandard':                  '5',     # ASME / ANSI (LS1E reference)
        # AlternateEndPreparation: RTJ (Ring Type Joint) is the only non-blank
        # alt value in the LS1E reference (41 rows). Project convention is
        # RF→RTJ pairing for class 600+ services.
        'AlternateEndPreparation':      '121',
        # AlternatePressureRating: mirror the row's primary PressureRating
        # (alt rating defaults to the same class in LS1E; if missing, '900'
        # matches the LS1E reference dominant alt value).
        'AlternatePressureRating':      lambda c: (
            c.get('PressureRating') or c.get('Rating1') or '900'
        ),
        'AlternateEndStandard':         '5',     # ASME / ANSI — same as primary
        'FluidCode':                    'Process Fluid',
        'ScheduleThickness':            '',
        'Priority':                     '1',
        # RingNumber: blank in LS1E reference (gasket ring number is sized
        # at install time per ASME B16.20). 'N/A' keeps the cell visible
        # without claiming a specific ring identity.
        'RingNumber':                   'N/A',
        'Comments':                     'Gasket per ASME B16.20 / B16.21',
        'QuantityOfAltReportableParts': '0',
        # AltReportableCommodityCode: mirror primary contractor commodity so
        # the column is never blank in the canvas (SP3D treats self-ref as
        # "no separate alt reporting"; QuantityOfAltReportableParts=0 prevents
        # any double-counting in MTO rollups).
        'AltReportableCommodityCode':   lambda c: (
            c.get('ContractorCommodityCode') or c.get('ReportableCommodityCode') or 'NONE'
        ),
        'QuantityOfReportableParts':    '1',
        'ReportableCommodityCode':      lambda c: c.get('ContractorCommodityCode', ''),
        'PipingNote1':                  'N/A',
    },
    'BoltSelectionFilter':    {
        # Mirrors the builder defaults so the LS1E template rows still get
        # enriched when the user's extracted spec has no bolt components.
        # Alt-end values below are the DOMINANT non-blank values found in
        # the shipped LS1E reference template (~33 of 59 LS1E rows carry
        # these; remaining rows are deliberately blank to mark "no alt").
        # We populate the blanks so SP3D bulkload always sees a valid
        # (EndPrep, PressureRating, EndStandard) alternate pair.
        # MaximumTemperature is intentionally left blank: the LS1E
        # reference template never populates it for ANY bolt row -- SP3D
        # treats blank as "no upper temperature limit at this row" and
        # inherits the limit from the joined material/flange class.
        'AlternateEndPreparation':      '121',  # LS1E template most-frequent (21 rows)
        'AlternatePressureRating':      '900',  # LS1E template most-frequent (29 rows)
        'AlternateEndStandard':         '5',    # LS1E template only non-blank value (33 rows)
        'Priority':                     '1',
        'Comments':                     'Stud bolt per ASME B16.5 / ASTM A193 B7 with ASTM A194 2H nuts',
        'PipingNote1':                  'N/A',
        'LubricationRequirements':      'Molykote 1000 anti-seize (or equivalent) applied to threads and nut bearing faces',
    },
    'InsideSurfaceTreatment': {
        # LS1E reference template populates FluidCode=521 for every IST row
        # (7/7 data rows). The numeric code is a SP3D-internal FluidCode ID;
        # we resolve it from the row's SpecName via SPEC_NAME_NUMERIC_FLUID_MAP
        # (defined later in this module). Falls back to the project default
        # ('521' matching LS1E) for unmapped prefixes so the column is NEVER
        # blank in the canvas.
        'FluidCode': lambda c: _infer_numeric_fluid_from_spec_name(
            c.get('SpecName') or ''
        ),
        'CoatingType':              '0',  # 0 = no coating, matches LS1E
        'InsideSurfaceTreatment':   '0',  # 0 = none, matches LS1E
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Forged butt-weld B16.9 fitting passthrough defaults (SOFT-CODED)
# ─────────────────────────────────────────────────────────────────────────────
# When the LS1E reference template ships pre-populated rows for a fitting
# sheet (e.g. 45DegElbow) but leaves the dimensional / weight / CoG / piping
# note columns blank, the canvas surfaced those blanks. The dict below is
# applied via apply_passthrough_defaults() to fill SP3D-safe sentinels so
# every cell renders. Real extracted values and WorkbookCellOverride rows
# always win because the autofill skips any cell that already has content.
#
# Soft-coding rationale:
#  • One shared dict, reused by every B16.9 forged fitting sheet via dict
#    spread → updating a single value here propagates to all fittings.
#  • BendRadiusMultiplier is per-geometry (1.0 = SR, 1.5 = LR), so each
#    sheet entry overrides only that single key.
#  • Numeric callables derive `BendRadius` from the row's `Npd[1]` cell
#    when available — falls back to the '0' sentinel if NPD is missing.
#    No core logic touched.
# ─────────────────────────────────────────────────────────────────────────────
def _parse_npd_inches(cell_value: str) -> float | None:
    """Return NPD as a float in inches, or None if unparseable.

    Accepts forms like '6"', '6 in', '6.0', '150mm', '150 mm'. Soft-coded
    so future units (DN50, etc.) can be added without touching callers.
    """
    if not cell_value:
        return None
    s = str(cell_value).strip().lower().replace('"', '').replace("'", '')
    # mm form → convert to inches (1 inch = 25.4 mm)
    if s.endswith('mm'):
        try:
            return float(s[:-2].strip()) / 25.4
        except ValueError:
            return None
    # 'in' suffix or plain number → inches
    s = s.replace('in', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


def _bend_radius_for_npd(cells: dict, multiplier: float) -> str:
    """Compute BendRadius = NPD × multiplier (in mm) from the row's Npd[1].

    Returns the value as a string with 'mm' suffix matching SP3D bulkload
    convention (e.g. '152mm' for 4" SR elbow). Falls back to '0' if NPD
    cannot be parsed — keeping the cell visible without claiming a wrong
    dimension.
    """
    npd_in = _parse_npd_inches(cells.get('Npd[1]') or cells.get('Npd[1]:Primary') or '')
    if npd_in is None:
        return '0'
    radius_mm = round(npd_in * 25.4 * multiplier)
    return f'{radius_mm}mm'


# Shared sentinel block — every forged B16.9 fitting passthrough entry
# below merges this dict before applying its own overrides.
_FORGED_FITTING_PASSTHROUGH_BASE: dict = {
    'GraphicalRepresentationOrNot': '0',
    'LiningMaterial':               '0',     # 0 = None / unlined
    'BendRadiusMultiplier':         '1.0',   # SR default; LR sheets override
    'BendRadius':                   '0',     # SR default; LR sheets override via lambda
    'MirrorBehaviorOption':         '0',
    'PartDataBasis':                '10',    # B16.9 forged butt-weld
    'ValveManufacturer':            'N/A',   # N/A keeps cell visible (fitting, not valve)
    'ValveModelNumber':             'N/A',
    'ValveTrim':                    'N/A',
    'FlangeFaceSurfaceFinish':      '0',
    'SurfacePreparation':           '0',
    'ManufacturingMethod':          '1',     # 1 = Forged
    'MiscRequisitionClassification':'0',
    'Id[1]':                        '0',
    'PressureRating[1]':            lambda c: (
        c.get('Rating1') or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
    ),
    'PipingNote1':                  'Forged butt-weld fitting per ASME B16.9',
    # Weight / CoG / dimensional sentinels — '0' keeps every cell visible;
    # SP3D 3D-model engine resolves real values from symbol+NPD at bulkload.
    'DryWeight':                    '0',
    'DryCogX':                      '0',
    'DryCogY':                      '0',
    'DryCogZ':                      '0',
    'WaterWeight':                  '0',
    'WaterCogX':                    '0',
    'WaterCogY':                    '0',
    'WaterCogZ':                    '0',
    'SurfaceArea':                  '0',
    'VolumetricCapacity':           '0',
}


# Register one passthrough entry per B16.9 fitting sheet. Each entry merges
# the shared base, then applies sheet-specific overrides (e.g. LR elbows
# carry BendRadiusMultiplier=1.5 and a derived BendRadius).
TEMPLATE_PASSTHROUGH_FIELD_DEFAULTS.update({
    '45DegElbow': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        # SR (1.0D) bend radius — derived from NPD if present, else '0'.
        'BendRadius':           lambda c: _bend_radius_for_npd(c, 1.0),
        'BendRadiusMultiplier': '1.0',
    },
    '45DegLRElbow': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        # LR (1.5D) bend radius — ASME B16.9 long-radius standard.
        'BendRadius':           lambda c: _bend_radius_for_npd(c, 1.5),
        'BendRadiusMultiplier': '1.5',
    },
    '90DegElbow': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           lambda c: _bend_radius_for_npd(c, 1.0),
        'BendRadiusMultiplier': '1.0',
    },
    '90DegLRElbow': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           lambda c: _bend_radius_for_npd(c, 1.5),
        'BendRadiusMultiplier': '1.5',
    },
    'Tee': {
        # Tees have no bend radius — explicitly suppress to '0'.
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PipingNote1':          'Equal-bore tee per ASME B16.9',
    },
    'ReducingTee': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '860',  # LS1E reducing-tee reference
        'PipingNote1':          'Reducing tee per ASME B16.9',
    },
    # ── Two-port reducer-style fittings (large-end + small-end) ──────────
    # ConcentricSwage = MSS-SP-95 forged swage nipple (concentric size step).
    # Has TWO independent ports → Id[2] / PressureRating[2] also required.
    'ConcentricSwage': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '10',   # MSS-SP-95 forged
        'PipingNote1':          'Concentric swage nipple per MSS-SP-95',
        # Secondary-port sentinels — small end of the swage.
        'Id[2]':                '0',
        'PressureRating[2]':    lambda c: (
            c.get('Rating2') or c.get('PressureRating2')
            or c.get('PressureRating[2]') or c.get('Rating1')
            or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
        ),
    },
    # ConcentricReducer = ASME B16.9 wrought butt-weld reducer (large bore).
    # Two-port like swage; sentinel-fills second port too so SP3D bulkload
    # is consistent even when the canvas display only shows port-1 columns.
    'ConcentricReducer': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '10',   # ASME B16.9 wrought
        'PipingNote1':          'Concentric reducer per ASME B16.9',
        'Id[2]':                '0',
        'PressureRating[2]':    lambda c: (
            c.get('Rating2') or c.get('PressureRating2')
            or c.get('PressureRating[2]') or c.get('Rating1')
            or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
        ),
    },
    # EccentricReducer = ASME B16.9 wrought butt-weld reducer (offset axis).
    # Same dimensional family as ConcentricReducer; SP3D symbol differs
    # (Eccentric vs Concentric) so MirrorBehaviorOption stays at base '0'
    # but PipingNote1 documents the offset orientation for the bulkload.
    'EccentricReducer': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '10',   # ASME B16.9 wrought
        'PipingNote1':          'Eccentric reducer per ASME B16.9 (flat side oriented per piping isometric)',
        'Id[2]':                '0',
        'PressureRating[2]':    lambda c: (
            c.get('Rating2') or c.get('PressureRating2')
            or c.get('PressureRating[2]') or c.get('Rating1')
            or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
        ),
    },
    # Cap = ASME B16.9 butt-weld pipe end-cap. Single port (welded end);
    # the closed end has no port → only Id[1]/PressureRating[1] are
    # applicable. Same forged-fitting sentinel block as elbows/reducers.
    'Cap': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '10',   # ASME B16.9 wrought
        'PipingNote1':          'Pipe end cap per ASME B16.9',
    },
    # Weldolet = MSS-SP-97 forged branch-connection (butt-weld outlet on
    # run pipe, integrally reinforced). Two ports: run-side bore (Id[1] /
    # PressureRating[1]) and branch-side bore (Id[2] / PressureRating[2]).
    # PartDataBasis '3394' is the LS1E reference enum for olet families
    # (already used by the builder fallback at L1238); we keep the same
    # value here so passthrough and builder agree.
    # PipingNote1 documents the standard so canvas reviewers can audit it.
    # Weight / CoG / SurfaceArea / VolumetricCapacity are sentinel '0' —
    # SP3D's olet symbol resolves real values from
    # (run NPD, branch NPD, schedule, rating) at bulkload; hardcoding
    # would conflict with the 3D-model engine.
    'Weldolet': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '3394',  # MSS-SP-97 olet (LS1E ref)
        'PipingNote1':          'Forged branch connection per MSS-SP-97 (Weldolet, butt-weld outlet)',
        'Id[2]':                '0',
        'PressureRating[2]':    lambda c: (
            c.get('Rating2') or c.get('PressureRating2')
            or c.get('PressureRating[2]') or c.get('Rating1')
            or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
        ),
    },
    # Paddle = ASME B16.48 line blank / paddle blind / spectacle blind —
    # a flat plate sandwiched between two flange faces. Geometry-wise it
    # behaves like a flat disc (no bend, no second port — the run line
    # reuses NPD[1] for both sides since the blind sits between mating
    # flanges of identical NPD/rating). The plate thickness is governed
    # by ASME B16.48 Tables 2-7 as a function of (NPD, PressureRating);
    # SP3D's `PaddleSpacer,Ingr.SP3D.Content.Piping.PaddleSpacer` symbol
    # resolves the actual thickness, mass, CoG, surface area, and
    # displaced-water volume from those inputs at bulkload.
    # ScheduleThickness[1] sentinel '0' lets SP3D B16.48 lookup take over;
    # extractor + WorkbookCellOverride still win on any explicit value.
    'Paddle': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':            '0',
        'BendRadiusMultiplier':  '0',
        'PartDataBasis':         '15',   # LS1E paddle-blind family
        'PipingNote1':           'Line blank / paddle blind per ASME B16.48',
        'ScheduleThickness[1]':  lambda c: (
            c.get('ScheduleThickness1') or c.get('ScheduleThickness[1]')
            or c.get('Schedule1') or c.get('Schedule') or c.get('Schedule[1]')
            or '0'
        ),
    },
    # Coupling = ASME B16.11 forged socket-weld / threaded coupling
    # (full coupling, half coupling, or reducing coupling). Two ports
    # share the same body bore on a full coupling; on a reducing coupling
    # port-2 has a smaller bore. SP3D's
    # `Coupling,Ingr.SP3D.Content.Piping.Coupling` symbol resolves the
    # full B16.11 dimensional table (body length, socket depth, wall
    # thickness, mass, CoG) from (Npd[1], Npd[2], PressureRating[1]).
    # PartDataBasis '20' matches the builder default at L1505 — both
    # paths agree.
    # Id[2] / ScheduleThickness[2] lambdas mirror Id[1]/ScheduleThickness[1]
    # for full couplings (where bores are identical) and gracefully fall
    # back to '0' for reducing couplings (so SP3D B16.11 lookup wins).
    'Coupling': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'PartDataBasis':        '20',   # ASME B16.11 forged coupling (LS1E ref)
        'PipingNote1':          'Forged coupling per ASME B16.11 (socket-weld / threaded)',
        'Id[2]':                '0',
        'ScheduleThickness[2]': lambda c: (
            c.get('ScheduleThickness2') or c.get('ScheduleThickness[2]')
            or c.get('Schedule2') or c.get('ScheduleThickness1')
            or c.get('ScheduleThickness[1]') or c.get('Schedule1')
            or c.get('Schedule') or c.get('Schedule[1]') or '0'
        ),
        'PressureRating[2]':    lambda c: (
            c.get('Rating2') or c.get('PressureRating2')
            or c.get('PressureRating[2]') or c.get('Rating1')
            or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
        ),
    },
    # Nipple = short cut-to-length seamless pipe (ASME B36.10 carbon /
    # B36.19 stainless) with both ends prepared per the parent piping
    # spec (PE / TBE / TOE / SE). Two ports share **identical** NPD,
    # schedule, and rating since the nipple is just a piece of pipe.
    # SP3D's `Nipple,Ingr.SP3D.Content.Piping.Nipple` symbol resolves
    # mass / CoG / surface / displaced-water from
    # (Npd[1], ScheduleThickness[1], length) at bulkload — never
    # hardcode here, otherwise non-standard cut lengths would race the
    # 3D-model engine.
    # ManufacturingMethod = '5' (seamless) overrides the forged default
    # '1' from the shared base — already aligned with the existing
    # builder entry at L1436.
    'Nipple': {
        **_FORGED_FITTING_PASSTHROUGH_BASE,
        'BendRadius':           '0',
        'BendRadiusMultiplier': '0',
        'ManufacturingMethod':  '5',    # Seamless per ASME B36.10 (overrides base '1')
        'PartDataBasis':        '20',   # ASME B36.10 pipe nipple (LS1E ref)
        'PipingNote1':          'Seamless pipe nipple per ASME B36.10 / B36.19 (length per spec)',
        'Id[2]':                '0',
        'PressureRating[2]':    lambda c: (
            c.get('Rating2') or c.get('PressureRating2')
            or c.get('PressureRating[2]') or c.get('Rating1')
            or c.get('PressureRating') or c.get('PressureRating[1]') or '0'
        ),
    },
    # ──────────────────────────────────────────────────────────────────────
    # GasketPartData = the SP3D Reference-Data sheet that carries the
    # PHYSICAL gasket properties used by the flange-leakage / joint-
    # tightness analyser (NOT the lookup criteria — that is on the
    # GasketSelectionFilter sheet, already configured above).
    #
    # Source authority chain for every cell below (highest → lowest):
    #   1. ASME B16.20 (spiral-wound metal-jacketed) / B16.21 (non-metallic)
    #      — geometry comes straight from the standard once (NPD, Class)
    #      are known, so dimension cells use SP3D-recognised '0' sentinels
    #      that the bulkload engine fills from the matched gasket symbol.
    #   2. ASME BPVC Section VIII Div 1, Mandatory Appendix 2, Table 2-5.1
    #      — provides the m (gasket factor) and y (minimum design seating
    #      stress, psi) for every ASME-recognised gasket type. These are
    #      hard ENGINEERING values that SP3D reads directly from this
    #      sheet — leaving them blank forces SP3D to fall back to its
    #      generic carbon-steel default which under-predicts seating
    #      stress for graphite-filled service. Populated explicitly.
    #   3. PVRC ROTT (Pressure Vessel Research Council Room-Temperature
    #      Operational Tightness Test) — the modern leakage model. Each
    #      gasket vendor publishes Gb / a / Gs / Tp_min / Tp_max from
    #      their own ROTT test. Project-level constants below default to
    #      sentinel '0' (= "use ASME m/y instead") because ROTT data is
    #      vendor-specific and over-claiming a value would silently bias
    #      the leakage analysis. Override per project when a specific
    #      gasket vendor (Flexitallic, Garlock, Lamons, …) is fixed.
    #   4. SP3D codelist — FlangeFacing '21' (Raised Face) and
    #      GasketIndustryStandard '600' (ASME B16.20) match the values
    #      already wired into GasketSelectionFilter.EndPreparation /
    #      EndStandard so the two sheets stay aligned across rows.
    #
    # Default profile chosen: SPIRAL-WOUND, FLEXIBLE-GRAPHITE FILLER, with
    # inner & outer rings (B16.20 Style "CGI"). This is the LS1E-A3
    # reference's predominant gasket and the default for every Class
    # 150–2500 ASME B16.5 / B16.47 flange make-up in process service.
    # ──────────────────────────────────────────────────────────────────────
    'GasketPartData': {
        # — Standard / identity ————————————————————————————————————————
        'GasketIndustryStandard':                       '600',     # ASME B16.20 (codelist)
        'RingNumber':                                   'N/A',     # sized at install per B16.20 Table 5
        'StyleNumber':                                  'CGI',     # B16.20: spiral-wound w/ Centring + Graphite + Inner ring
        # — Geometry (SP3D resolves from B16.20 table at bulkload) ————————
        'GasketOutsideDiameter':                        '0',
        'GasketInsideDiameter':                         '0',
        'GasketOutsideDiameterBasis':                   '0',       # 0 = inherited from gasket catalog
        'GasketInsideDiameterBasis':                    '0',
        # — Flange insulation kit (not applicable to standard service) ——
        'FlangeInsulationKitType':                      '0',       # 0 = no kit
        'InsulatingWasherThickness':                    '0',
        'MetallicElectroPlatedWasherThk':               '0',
        # — ASME BPVC Sec VIII Div 1, Appx 2, Table 2-5.1 ——————————————
        # Spiral-wound, flexible-graphite filler, Type 304/316 windings.
        # m  = MaintenanceFactor (gasket factor, dimensionless)
        # y  = StressAtWhichSealIsInitiated (min design seating stress, psi)
        'MaintenanceFactor':                            '3.0',     # ASME m for SW + flex-graphite
        'StressAtWhichSealIsInitiated':                 '10000',   # ASME y, psi
        # — PVRC ROTT (sentinel '0' = use ASME m/y above) ——————————————
        # If a project fixes the gasket vendor, replace these with the
        # vendor's ROTT data sheet values: Gb (TightnessCurveSlope intercept,
        # psi), a (slope, dimensionless), Gs (IntersectionOfUnloadCurve…,
        # psi), and the Tp envelope (MinimumTightnessParameter /
        # MaximumTightnessParameter). Soft-coded — no engine logic depends
        # on these being non-zero unless the analyser is run in PVRC mode.
        'TightnessCurveSlope':                          '0',
        'IntersectionOfUnloadCurveWithVerticalAxis':    '0',
        'MinimumTightnessParameter':                    '0',
        'MaximumTightnessParameter':                    '0',
        # — Service-limit envelope —————————————————————————————————————
        # MaximumTemperature aligned with GasketSelectionFilter (same row
        # joins both sheets in SP3D). Pressure left as sentinel '0' so
        # SP3D resolves the limit from the joined PressureRating column
        # at bulkload — prevents drift between the two sheets.
        'FlangePressure':                               '0',
        'MaximumPressure':                              '0',
        'MaximumTemperature':                           '650F',    # ASTM A516-70 / Flexitallic CG max
        # — Mating-flange face / facing (matches GasketSelectionFilter) ——
        'FlangeFacing':                                 '21',      # 21 = Raised Face (SP3D codelist)
    },
    # ──────────────────────────────────────────────────────────────────────
    # BoltPartData = SP3D Reference-Data sheet for the PHYSICAL bolt /
    # stud properties (separate from BoltSelectionFilter which is the
    # lookup-criteria sheet, already configured above).
    #
    # The LS1E-A3 reference template populates every dimensional and
    # coding column from `_build_bolt_part_rows` at L3170+ — geometry,
    # GeometricIndustryStandard, ContractorCommodityCode, etc. are all
    # produced by the builder. The single column the LS1E reference
    # leaves blank in passthrough rows is `CoatingType`.
    #
    # SP3D codelist for CoatingType (LS1E reference):
    #   0 = No coating (bare metal)            ← matches builder default
    #                                            at L3189 and the LS1E
    #                                            reference dominant value.
    #   1 = PTFE / xylan                       (project add-on)
    #   2 = Cadmium plating                    (legacy / superseded)
    #   3 = Zinc plating                       (galvanised studs)
    #   4 = Hot-dip galvanised                 (heavy-duty external)
    #
    # Default '0' is correct for ASTM A193 B7 + A194 2H assemblies
    # supplied with anti-seize lubrication (see
    # SPEC_DEFAULTS['bolt_lubrication_requirements']) — the lubricant
    # provides the corrosion barrier, not a factory coating. Override
    # per project (e.g. offshore / atmospheric service) by editing the
    # value below or via WorkbookCellOverride at row level.
    # ──────────────────────────────────────────────────────────────────────
    'BoltPartData': {
        'CoatingType': '0',   # No coating — matches builder L3189 + LS1E reference
    },
})


def apply_passthrough_defaults(sheet_name: str, cells: dict) -> None:
    """Mutate `cells` in-place, filling blanks with soft-coded defaults.
    Safe no-op when sheet has no entry or autofill is disabled."""
    if not TEMPLATE_PASSTHROUGH_AUTOFILL_ENABLED:
        return
    defaults = TEMPLATE_PASSTHROUGH_FIELD_DEFAULTS.get(sheet_name)
    if not defaults:
        return
    for field, default in defaults.items():
        current = cells.get(field, '')
        if current not in (None, '', 'None'):
            continue
        try:
            value = default(cells) if callable(default) else default
        except Exception:
            value = ''
        if value not in (None, ''):
            cells[field] = str(value)

# Output filename templates (formatted with job_id).
SPEC_OUTPUT_FILENAME_TPL = 'spec_customisation_{job_id}_SPEC.xlsx'
CAT_OUTPUT_FILENAME_TPL  = 'spec_customisation_{job_id}_CAT.xlsx'


# ─────────────────────────────────────────────────────────────────────────────
# 2. STANDARD NPD LADDER (inches, ASME B36.10)
# ─────────────────────────────────────────────────────────────────────────────
STD_NPDS_INCHES = [
    0.125, 0.25, 0.375, 0.5, 0.75,
    1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12,
    14, 16, 18, 20, 24, 26, 28, 30, 32, 34, 36, 42, 48,
]

# Default S3D piping enumeration codes (kept here so they remain a single,
# audited source of truth — see SP3D Reference Data Guide).
S3D_DEFAULTS = {
    'NpdUnitType':            'in',
    'PipingPointBasis':       '15',     # NPD-only routing
    'EndStandard':            '5',      # ASME / ANSI (project default)
    'FlowDirection_Bidir':    '3',
    'FlowDirection_OutOnly':  '4',
    'EndPrep_BW':             '301',    # Butt Weld
    'EndPrep_RF':             '21',     # Raised Face flange
    'EndPrep_SW':             '441',    # Socket Weld
    'EndPrep_THR':            '331',    # Threaded
    'GraphicalRep':           '15',
}

# Normalisation map for the AI-extracted `end_connection` token.
_END_PREP_LOOKUP = {
    'bw':  S3D_DEFAULTS['EndPrep_BW'],   'butt weld': S3D_DEFAULTS['EndPrep_BW'],
    'rf':  S3D_DEFAULTS['EndPrep_RF'],   'raised face': S3D_DEFAULTS['EndPrep_RF'],
    'sw':  S3D_DEFAULTS['EndPrep_SW'],   'socket weld': S3D_DEFAULTS['EndPrep_SW'],
    'thr': S3D_DEFAULTS['EndPrep_THR'],  'threaded':    S3D_DEFAULTS['EndPrep_THR'],
    'npt': S3D_DEFAULTS['EndPrep_THR'],
}


# ─────────────────────────────────────────────────────────────────────────────
# 3. VALUE NORMALISERS
# ─────────────────────────────────────────────────────────────────────────────
_NPD_FRACTION_MAP = {
    '1/8': 0.125, '1/4': 0.25, '3/8': 0.375,
    '1/2': 0.5,   '3/4': 0.75,
    '1-1/4': 1.25, '1 1/4': 1.25,
    '1-1/2': 1.5,  '1 1/2': 1.5,
    '2-1/2': 2.5,  '2 1/2': 2.5,
}


# Characters that may follow an inch number across various source notations.
_INCH_TOKENS = ('"', '“', '”', '″', '′', "''", "'", 'IN', 'in', 'In', 'inch', 'INCH', 'Inch')


def _to_float_npd(size) -> float | None:
    """Parse an NPD size string ('1/2"', '2”', '2-1/2"', '1 1/2″') → float inches.
    Returns None when un-parseable."""
    if size in (None, ''):
        return None
    s = str(size)
    for tok in _INCH_TOKENS:
        s = s.replace(tok, '')
    s = s.replace('-', ' ').strip()
    if not s:
        return None
    if s in _NPD_FRACTION_MAP:
        return _NPD_FRACTION_MAP[s]
    # Mixed fraction: '1 1/2', '2 1/4'
    if ' ' in s and '/' in s:
        try:
            whole, frac = s.split(' ', 1)
            num, den = frac.split('/', 1)
            return float(whole) + float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass
    # Pure fraction: '1/2', '3/4'
    if '/' in s:
        try:
            num, den = s.split('/', 1)
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_npd(size) -> str:
    """Stringify an NPD value in S3D format (integer when whole, else decimal)."""
    v = _to_float_npd(size)
    if v is None:
        return str(size or '').strip()
    return str(int(v)) if float(v).is_integer() else str(v)


def _class_npd_range(cls) -> tuple[float | None, float | None]:
    """Return (min_npd, max_npd) in inches derived from the class's
    component size_from/size_to columns. Returns (None, None) when no
    parseable sizes are present so the caller can fall back to soft-coded
    project defaults.
    """
    npds: list[float] = []
    for c in cls.components.all():
        for sz in (c.size_from, c.size_to):
            v = _to_float_npd(sz)
            if v is not None:
                npds.append(v)
    if not npds:
        return (None, None)
    return (min(npds), max(npds))


# ── Soft-coded MaterialsCategory mapping ───────────────────────────────────────
# Maps a material-grade prefix (case-insensitive) to its SP3D codelist value
# for the CorrosionAllowance / Materials* sheets. First matching prefix wins,
# so order longest → shortest. Extend by editing this dict only — no code
# changes elsewhere required.
MATERIALS_CATEGORY_MAP: list[tuple[str, str]] = [
    # (grade prefix, SP3D MaterialsCategory code)
    ('SS316L', '16'),    # Stainless Steel 316L
    ('SS316',  '16'),    # Stainless Steel 316
    ('SS304L', '16'),
    ('SS304',  '16'),
    ('SS',     '16'),    # Generic stainless
    ('DSS',    '17'),    # Duplex Stainless
    ('AS',     '20'),    # Alloy Steel
    ('LTCS',   '15'),    # Low-Temp Carbon Steel
    ('CS',     '15'),    # Carbon Steel
    ('GRP',    '50'),    # Glass-Reinforced Plastic
    ('PVC',    '60'),    # PVC
]


def _materials_category(cls) -> str:
    """Resolve the SP3D MaterialsCategory code for a piping class via the
    soft-coded MATERIALS_CATEGORY_MAP. Falls back to the project default
    when no prefix matches (or material_grade is blank)."""
    grade = (cls.material_grade or '').strip().upper()
    for prefix, code in MATERIALS_CATEGORY_MAP:
        if grade.startswith(prefix.upper()):
            return code
    return SPEC_DEFAULTS['corrosion_allowance_materials_category_default']


def _npd_token(npd: float) -> str:
    """Build a deterministic, filesystem-safe token for an NPD (used in ICC codes)."""
    if float(npd).is_integer():
        return f'N{int(npd):04d}'
    return f'N{int(round(npd * 100)):04d}'   # 0.5 → N0050, 1.5 → N0150


def _enumerate_npds(size_from, size_to) -> list[float]:
    """Return the standard NPDs lying within [size_from, size_to].
    If only one bound is given, returns [that value]; if neither, returns []."""
    a = _to_float_npd(size_from)
    b = _to_float_npd(size_to)
    if a is None and b is None:
        return []
    if a is not None and b is None:
        return [a]
    if a is None and b is not None:
        return [b]
    lo, hi = min(a, b), max(a, b)
    out = [n for n in STD_NPDS_INCHES if lo <= n <= hi]
    if a not in out:
        out.append(a)
    if b not in out:
        out.append(b)
    return sorted(set(out))


def _fmt_temperature(t):
    if t in (None, ''):
        return ''
    try:
        return f'{float(t):g}C'
    except (TypeError, ValueError):
        return str(t)


def _fmt_pressure(p):
    if p in (None, ''):
        return ''
    try:
        return f'{float(p):g}barg'
    except (TypeError, ValueError):
        return str(p)


def _spec_name(cls) -> str:
    """Stable SpecName for a PipingClass (matches SmartPlant SpecName key)."""
    return (cls.class_full_code or cls.class_code or 'SPEC').strip()


def _safe_class_token(cls) -> str:
    """Filesystem-safe token derived from class code (for ICC generation)."""
    raw = (cls.class_code or cls.class_full_code or 'X').strip()
    return re.sub(r'[^A-Za-z0-9]+', '', raw).upper() or 'X'


def _normalize_end_prep(text: str | None) -> str:
    """Map end-connection text → S3D EndPreparation enum (returns enum or text)."""
    if not text:
        return ''
    key = str(text).strip().lower()
    if key in _END_PREP_LOOKUP:
        return _END_PREP_LOOKUP[key]
    first = key.split()[0] if key.split() else ''
    return _END_PREP_LOOKUP.get(first, text)


def _normalize_pressure_class(rating: str | None) -> str:
    """Convert '150#', 'Class 300', 'ASME 900#' → numeric flange class string."""
    if not rating:
        return ''
    m = re.search(r'(\d+)', str(rating))
    return m.group(1) if m else str(rating)


# ─────────────────────────────────────────────────────────────────────────────
# 4. CAT SHEET DEFAULTS  (one entry per CAT data sheet)
#
# Keys per sheet:
#   commodity_type  — S3D CommodityType token (PIPE, FWN, FBLD, E90, T, ...)
#   geometry_type   — S3D GeometryType code
#   symbol_def      — Hexagon SP3D content path used by the symbol library
#   icc_prefix      — Industry-Commodity-Code prefix used to name parts
#   ports           — number of piping points (1, 2 or 3)
#   bend_angle      — Optional, used for elbow sheets
#   primary_label   — '' or ':Primary' suffix for Npd column name
# ─────────────────────────────────────────────────────────────────────────────
#
# `primary_label` / `secondary_label` — optional ':Primary' / ':Secondary'
# suffixes appended to `Npd[N]` column-name variants so the writer can match
# whichever header the LS1E-A3 reference template ships (some sheets use the
# bare `Npd[1]` form, others use `Npd[1]:Primary`/`Npd[2]:Secondary`).
# Soft-coded so the suffix scheme is auditable against the reference workbook.
CAT_SHEET_DEFAULTS = {
    'PipeStock':         dict(commodity_type='PIPE',  geometry_type=None,  symbol_def=None,
                              icc_prefix='RAD_PIP', ports=2,
                              primary_label=':Primary', secondary_label=':Secondary'),
    'WeldNeckFlange':    dict(commodity_type='FWN',   geometry_type='15',  symbol_def='Flange,Ingr.SP3D.Content.Piping.Flange',
                              icc_prefix='RAD_FWN', ports=2),
    'BlindFlange':       dict(commodity_type='FBLD',  geometry_type='220', symbol_def='BlindFlange,Ingr.SP3D.Content.Piping.BlindFlange',
                              icc_prefix='RAD_FBL', ports=1),
    '90DegElbow':        dict(commodity_type='E90',   geometry_type='20',  symbol_def='90DegreeElbow,Ingr.SP3D.Content.Piping.Elbow90Degree',
                              icc_prefix='RAD_E90', ports=2, bend_angle='90deg'),
    '90DegLRElbow':      dict(commodity_type='E90LR', geometry_type='20',  symbol_def='90DegreeElbow,Ingr.SP3D.Content.Piping.Elbow90Degree',
                              icc_prefix='RAD_E9L', ports=2, bend_angle='90deg'),
    '45DegElbow':        dict(commodity_type='E45',   geometry_type='20',  symbol_def='45DegreeElbow,Ingr.SP3D.Content.Piping.Elbow45Deg',
                              icc_prefix='RAD_E45', ports=2, bend_angle='45deg'),
    '45DegLRElbow':      dict(commodity_type='E45LR', geometry_type='20',  symbol_def='45DegreeElbow,Ingr.SP3D.Content.Piping.Elbow45Deg',
                              icc_prefix='RAD_E4L', ports=2, bend_angle='45deg'),
    'Tee':               dict(commodity_type='T',     geometry_type='75',  symbol_def='Tee,Ingr.SP3D.Content.Piping.Tee',
                              icc_prefix='RAD_TEE', ports=3,
                              primary_label=':Primary', secondary_label=':Primary'),
    'ReducingTee':       dict(commodity_type='TR',    geometry_type='75',  symbol_def='Tee,Ingr.SP3D.Content.Piping.Tee',
                              icc_prefix='RAD_TER', ports=3,
                              primary_label=':Primary', secondary_label=':Primary'),
    'ConcentricSwage':   dict(commodity_type='SWGC',  geometry_type='70',  symbol_def='ConcentricReducer,Ingr.SP3D.Content.Piping.Concentric',
                              icc_prefix='RAD_SWG', ports=2),
    'ConcentricReducer': dict(commodity_type='REDC',  geometry_type='70',  symbol_def='ConcentricReducer,Ingr.SP3D.Content.Piping.Concentric',
                              icc_prefix='RAD_REC', ports=2),
    'EccentricReducer':  dict(commodity_type='REDE',  geometry_type='65',  symbol_def='EccentricReducer,Ingr.SP3D.Content.Piping.Eccentric',
                              icc_prefix='RAD_REE', ports=2),
    'Cap':               dict(commodity_type='CAP',   geometry_type='220', symbol_def='Cap,Ingr.SP3D.Content.Piping.Cap',
                              icc_prefix='RAD_CAP', ports=1),
    'Weldolet':          dict(commodity_type='WOL',   geometry_type='15',  symbol_def='Weldolet,Ingr.SP3D.Content.Piping.Olet',
                              icc_prefix='RAD_WOL', ports=2),
    'GateValve':         dict(commodity_type='GAT',   geometry_type='15',  symbol_def='GateValve,Ingr.SP3D.Content.Piping.GateValve',
                              icc_prefix='RAD_GAT', ports=2),
    'GlobeValve':        dict(commodity_type='GLO',   geometry_type='15',  symbol_def='GlobeValve,Ingr.SP3D.Content.Piping.GlobeValveF',
                              icc_prefix='RAD_GLO', ports=2),
    'CheckValve':        dict(commodity_type='CHK',   geometry_type='15',  symbol_def='CheckValve,Ingr.SP3D.Content.Piping.CheckValve',
                              icc_prefix='RAD_CHK', ports=2),
    'Paddle':            dict(commodity_type='BLSPA', geometry_type='15',  symbol_def='PaddleSpacer,Ingr.SP3D.Content.Piping.PaddleSpacer',
                              icc_prefix='RAD_PAD', ports=2),
    'Coupling':          dict(commodity_type='CPL',   geometry_type='15',  symbol_def='Coupling,Ingr.SP3D.Content.Piping.Coupling',
                              icc_prefix='RAD_CPL', ports=2),
    'Nipple':            dict(commodity_type='NIP',   geometry_type='15',  symbol_def='Nipple,Ingr.SP3D.Content.Piping.Nipple',
                              icc_prefix='RAD_NIP', ports=2),
    'GasketPartData':    dict(commodity_type=None,    geometry_type=None,  symbol_def=None,
                              icc_prefix='RAD_GSK', ports=0),
    'BoltPartData':      dict(commodity_type=None,    geometry_type=None,  symbol_def=None,
                              icc_prefix='RAD_BLT', ports=0),
}


# ─────────────────────────────────────────────────────────────────────────────
# 4b. CAT DIMENSIONAL / IDENTIFIER FIELDS  (soft-coded per sheet)
# ─────────────────────────────────────────────────────────────────────────────
# Values calibrated against the LS1E-A3 reference workbook
# (Documents/Digitization/Spec Customisation/LS1E-A3_CAT.xlsx). Each entry
# maps a CAT sheet → {column_name: value | callable(cls, comp) -> value}.
#
# Purpose: the base `_common_part_row` only fills generic identifiers + port
# fields. The reference workbook also carries sheet-specific *constants*
# (PartDataBasis, ManufacturingMethod, Density, valve trim/model numbers,
# symbol icons, dimensional sentinels). Adding them here — rather than
# inside the builder — keeps the builder small and lets us re-tune any
# value in one place without touching code paths.
#
# • Static values are written as-is.
# • Callables are invoked with (cls, comp) and may return '' to skip.
# • Empty strings / None are dropped by `_write_row` (single source of truth)
#   so columns with no project-wide value stay blank without polluting xlsx.
#
# Extending: add a new sheet entry OR a new column key to an existing entry.
# No core-logic changes required.
# ─────────────────────────────────────────────────────────────────────────────
CAT_DIMENSIONAL_FIELDS: dict[str, dict[str, object]] = {
    # ── Pipe stock ────────────────────────────────────────────────────────
    'PipeStock': {
        # Carbon-steel default density (LS1E reference: '490 lbm/ft^3'). For
        # alloy/SS classes the extractor's material_grade lookup should
        # override this via the callable form once a density resolver lands.
        'Density':             '490 lbm/ft^3',
        'ManufacturingMethod': '5',   # 5 = Seamless (LS1E dominant value)
        # ── Pipe-supply / dimensional fallbacks (LS1E reference leaves these
        # blank because SP3D's dimension tables resolve them at bulk-load
        # time. We fill SP3D-safe defaults so the canvas never shows empty
        # cells; the merge_fill_blank() helper guarantees real extracted
        # values keep winning. Override any value via WorkbookCellOverride
        # on the canvas, no code change needed.
        'PurchaseLength':      '6 m',     # ASME B36.10 random length (~6 m)
        'MinimumPipeLength':   '0.05 m',  # 50 mm trim-off cutoff
        'MaximumPipeLength':   '6 m',
        'SurfacePreparation':  '0',       # 0 = None / mill finish
        'LiningMaterial':      '0',       # 0 = None / unlined
        # Pipe stock has no end-cap weights — SP3D bulkload accepts 0.
        'DryWeightForEnd1':    '0',
        'DryWeightForEnd2':    '0',
        # SP3D dimension table resolves WeightPerUnitLength from
        # GeometricIndustryStandard + Schedule + NPD. Leave blank so the
        # bulk-loader computes it; extractor can override per row.
    },
    # ── Flanges ───────────────────────────────────────────────────────────
    'WeldNeckFlange': {
        'PartDataBasis':       '15',  # LS1E reference WNF rows = 15
        # ── SP3D-safe defaults for columns LS1E leaves blank ──────────────
        # All values are merged via merge_fill_blank() so any real extractor
        # value (e.g. comp.weight_kg → DryWeight) keeps winning.
        # Override any default via WorkbookCellOverride on the canvas — no
        # code change needed.
        'LiningMaterial':              '0',   # 0 = None / unlined
        'BendRadius':                  '0',   # N/A for flange
        'BendRadiusMultiplier':        '0',
        'MirrorBehaviorOption':        '0',   # 0 = no mirror (default)
        # Valve columns explicitly suppressed — WNF is not a valve.
        # (cat_sheet_constants() filters '' so these never appear, but
        # listing them documents intent and keeps the schema explicit.)
        'ValveManufacturer':           '',
        'ValveModelNumber':            '',
        'ValveTrim':                   '',
        # ASME B16.5 RF default flange-face finish enum (0 = mill finish /
        # SP3D resolves from MaterialGrade); refine via extractor per class.
        'FlangeFaceSurfaceFinish':     '0',
        'SurfacePreparation':          '0',
        'ManufacturingMethod':         '1',   # 1 = Forged (B16.5 default)
        'MiscRequisitionClassification': '0',
        # Id[1] / Id[2] are SP3D internal IDs — auto-generated at bulk-load,
        # left blank on purpose.
        # PipingNote1 is extractor-derived; default to blank.
        'PipingNote1':                 '',
        # Weight / centre-of-gravity / dimensional fields are computed by
        # the SP3D 3D-model engine from the symbol + NPD; default '0'
        # ensures the canvas never shows empty cells while still letting
        # real extractor values win via merge_fill_blank().
        'DryWeight':                   '0',
        'DryCogX':                     '0',
        'DryCogY':                     '0',
        'DryCogZ':                     '0',
        'WaterWeight':                 '0',
        'WaterCogX':                   '0',
        'WaterCogY':                   '0',
        'WaterCogZ':                   '0',
        'SurfaceArea':                 '0',
        'VolumetricCapacity':          '0',
        # FacetoFace is per-NPD per-pressure dimensional value from
        # ASME B16.5. Left for the extractor / dimensional resolver to
        # populate per row — adding a single static default would be wrong.
    },
    'BlindFlange': {
        # BlindFlange has no PartDataBasis in LS1E reference; intentionally
        # left empty so the column doesn't show a spurious value.
        # ── SP3D-safe defaults for columns LS1E leaves blank ──────────────
        # All values are merged via merge_fill_blank() so any real extractor
        # value (e.g. comp.weight_kg → DryWeight) keeps winning. Override
        # any default via WorkbookCellOverride on the canvas — no code change.
        'LiningMaterial':              '0',   # 0 = None / unlined
        'BendRadius':                  '0',   # N/A for flange
        'BendRadiusMultiplier':        '0',
        'MirrorBehaviorOption':        '0',   # 0 = no mirror (default)
        # Valve columns explicitly suppressed — BlindFlange is not a valve.
        'ValveManufacturer':           '',
        'ValveModelNumber':            '',
        'ValveTrim':                   '',
        # Flange-face / surface defaults (B16.5 RF, 0 = mill finish).
        'FlangeFaceSurfaceFinish':     '0',
        'SurfacePreparation':          '0',
        'ManufacturingMethod':         '1',   # 1 = Forged (B16.5 default)
        'MiscRequisitionClassification': '0',
        # PipingNote1 is extractor-derived; default to blank.
        'PipingNote1':                 '',
        # Weight / centre-of-gravity / dimensional fields are computed by
        # the SP3D 3D-model engine from the symbol + NPD; default '0' keeps
        # the canvas populated and merge_fill_blank() lets real values win.
        'DryWeight':                   '0',
        'DryCogX':                     '0',
        'DryCogY':                     '0',
        'DryCogZ':                     '0',
        'WaterWeight':                 '0',
        'WaterCogX':                   '0',
        'WaterCogY':                   '0',
        'WaterCogZ':                   '0',
        'SurfaceArea':                 '0',
        'VolumetricCapacity':          '0',
    },
    # ── Elbows ────────────────────────────────────────────────────────────
    '90DegElbow':   {
        # LS1E reference leaves all of the columns below blank for
        # 90DegElbow rows (PartDataBasis included). Sentinel defaults
        # below keep the canvas populated; merge_fill_blank() ensures any
        # real extractor / per-row computed value keeps winning. Override
        # any default per row via WorkbookCellOverride — no code change.
        # NOTE: BendRadius / BendRadiusMultiplier are computed per NPD by
        # the closure builders in _common_part_row(); the '0' defaults
        # here only fill rows where extraction yields nothing.
        'LiningMaterial':              '0',   # 0 = None / unlined
        'BendRadius':                  '0',
        'BendRadiusMultiplier':        '0',
        'MirrorBehaviorOption':        '0',   # 0 = no mirror
        # Valve columns explicitly suppressed — elbows are not valves.
        'ValveManufacturer':           '',
        'ValveModelNumber':            '',
        'ValveTrim':                   '',
        'FlangeFaceSurfaceFinish':     '0',
        'SurfacePreparation':          '0',
        'ManufacturingMethod':         '1',   # 1 = Forged (B16.9 default)
        'MiscRequisitionClassification': '0',
        'PipingNote1':                 '',
        # Weight / centre-of-gravity / dimensional columns — SP3D 3D-model
        # engine resolves these from the symbol + NPD at bulk-load time.
        'DryWeight':                   '0',
        'DryCogX':                     '0',
        'DryCogY':                     '0',
        'DryCogZ':                     '0',
        'WaterWeight':                 '0',
        'WaterCogX':                   '0',
        'WaterCogY':                   '0',
        'WaterCogZ':                   '0',
        'SurfaceArea':                 '0',
        'VolumetricCapacity':          '0',
    },
    '90DegLRElbow': {
        # LS1E reference leaves all of the columns below blank for
        # 90DegLRElbow rows (PartDataBasis included). Sentinel defaults
        # below keep the canvas populated; merge_fill_blank() ensures any
        # real extractor / per-row computed value keeps winning. Override
        # any default per row via WorkbookCellOverride — no code change.
        # NOTE: BendRadius / BendRadiusMultiplier are computed per NPD by
        # the closure builders in _common_part_row(); the '0' defaults
        # here only fill rows where extraction yields nothing.
        'BendRadius':                  '0',
        'BendRadiusMultiplier':        '0',
        'MirrorBehaviorOption':        '0',   # 0 = no mirror
        # Valve columns explicitly suppressed — elbows are not valves.
        'ValveManufacturer':           '',
        'ValveModelNumber':            '',
        'ValveTrim':                   '',
        'FlangeFaceSurfaceFinish':     '0',
        'SurfacePreparation':          '0',
        'ManufacturingMethod':         '1',   # 1 = Forged (B16.9 default)
        'MiscRequisitionClassification': '0',
        'PipingNote1':                 '',
        # Weight / centre-of-gravity / dimensional columns: SP3D 3D-model
        # engine resolves these from the symbol + NPD at bulk-load time;
        # '0' sentinel keeps the canvas populated.
        'DryWeight':                   '0',
        'DryCogX':                     '0',
        'DryCogY':                     '0',
        'DryCogZ':                     '0',
        'WaterWeight':                 '0',
        'WaterCogX':                   '0',
        'WaterCogY':                   '0',
        'WaterCogZ':                   '0',
        'SurfaceArea':                 '0',
        'VolumetricCapacity':          '0',
        # FacetoCenter is extractor-derived per NPD (e.g. '152mm', '610mm');
        # default '0' only fires when extraction yields nothing.
        'FacetoCenter':                '0',
    },
    '45DegElbow':   {
        # LS1E reference (sheet '45DegElbow', CommodityPart section, rows 9-12)
        # leaves every column below blank — same footprint as 90DegElbow.
        # Sentinel defaults keep the canvas populated; merge_fill_blank()
        # ensures extractor / per-row computed values always win. Override
        # per row via WorkbookCellOverride — no code change required.
        # BendRadius / BendRadiusMultiplier are computed per NPD by closure
        # builders in _common_part_row(); '0' here only fires on blank.
        # Standard-radius (1.0D) elbow — distinct from 45DegLRElbow.
        # PartDataBasis: same code as 45DegLRElbow per LS1E (forged 45° elbow
        # share same SP3D part-data category).
        'PartDataBasis':               '10',
        # GraphicalRepresentationOrNot: 0 = symbol-only (default for elbows).
        # GlobeValve uses '15' (its body is the only sheet LS1E populates
        # for this column); elbows render via the symbol_def alone.
        'GraphicalRepresentationOrNot': '0',
        'LiningMaterial':              '0',   # 0 = None / unlined
        'BendRadius':                  '0',
        'BendRadiusMultiplier':        '0',
        'MirrorBehaviorOption':        '0',
        'ValveManufacturer':           '',
        'ValveModelNumber':            '',
        'ValveTrim':                   '',
        'FlangeFaceSurfaceFinish':     '0',
        'SurfacePreparation':          '0',
        'ManufacturingMethod':         '1',   # 1 = Forged (B16.9 default)
        'MiscRequisitionClassification': '0',
        # Per-port routing identifiers (elbows are 2-port). '0' sentinel
        # keeps the canvas populated; extractor / per-row builder always
        # wins via merge_fill_blank(). Without a sentinel these keys are
        # filtered by cat_sheet_constants() and the cell renders blank.
        'Id[1]':                       '0',
        'PressureRating[1]':           '0',
        'Id[2]':                       '0',
        'PressureRating[2]':           '0',
        'PipingNote1':                 '',
        'DryWeight':                   '0',
        'DryCogX':                     '0',
        'DryCogY':                     '0',
        'DryCogZ':                     '0',
        'WaterWeight':                 '0',
        'WaterCogX':                   '0',
        'WaterCogY':                   '0',
        'WaterCogZ':                   '0',
        'SurfaceArea':                 '0',
        'VolumetricCapacity':          '0',
    },
    '45DegLRElbow': {
        # LS1E reference leaves all of the columns below blank for
        # 45DegLRElbow rows (PartDataBasis=10 is the only globally populated
        # column). Sentinel defaults below keep the canvas populated;
        # merge_fill_blank() ensures any real extractor / per-row computed
        # value keeps winning. Override per row via WorkbookCellOverride —
        # no code change required.
        # NOTE: BendRadius / BendRadiusMultiplier are computed per NPD by
        # the closure builders in _common_part_row(); the '0' defaults
        # here only fire when extraction yields nothing.
        # 45° long-radius elbow — schema is identical to 90DegLRElbow
        # (ASME B16.9 long-radius butt-weld fitting, 1.5D centreline radius).
        'PartDataBasis':                 '10',  # LS1E reference 45LR rows = 10
        'LiningMaterial':                '0',   # 0 = None / unlined
        'BendRadius':                    '0',
        'BendRadiusMultiplier':          '0',
        'MirrorBehaviorOption':          '0',   # 0 = no mirror
        # Valve columns explicitly suppressed — elbows are not valves.
        'ValveManufacturer':             '',
        'ValveModelNumber':              '',
        'ValveTrim':                     '',
        'FlangeFaceSurfaceFinish':       '0',
        'SurfacePreparation':            '0',
        'ManufacturingMethod':           '1',   # 1 = Forged (B16.9 default)
        'MiscRequisitionClassification': '0',
        'PipingNote1':                   '',
        # Weight / centre-of-gravity / dimensional columns — SP3D 3D-model
        # engine resolves these from the symbol + NPD at bulk-load time;
        # '0' sentinel keeps the canvas populated.
        'DryWeight':                     '0',
        'DryCogX':                       '0',
        'DryCogY':                       '0',
        'DryCogZ':                       '0',
        'WaterWeight':                   '0',
        'WaterCogX':                     '0',
        'WaterCogY':                     '0',
        'WaterCogZ':                     '0',
        'SurfaceArea':                   '0',
        'VolumetricCapacity':            '0',
        # FacetoCenter is extractor-derived per NPD (e.g. '38mm', '152mm');
        # default '0' only fires when extraction yields nothing.
        'FacetoCenter':                  '0',
    },
    # ── Tees ──────────────────────────────────────────────────────────────
    'Tee': {
        # LS1E reference (sheet 'Tee', CommodityPart section, row 7 = field
        # codes, 21 part rows). Only GeometricIndustryStandard=41 and
        # MaterialGrade=150 are populated globally. PressureRating[1..3]
        # show '3000' on a single socketweld row only — kept blank here
        # so the extractor always provides the real rating per part.
        #
        # IMPORTANT: PartDataBasis is BLANK across all 21 rows in LS1E for
        # equal-bore tees (SP3D auto-selects the part geometrically from the
        # symbol + NPD trio). Unlike ReducingTee (=860), no code is supplied
        # here — sentinel '' is filtered by cat_sheet_constants() so SP3D
        # never receives a bogus PartDataBasis on equal tees.
        #
        # Sentinel defaults populate the spec canvas; merge_fill_blank()
        # ensures any real extractor value always wins. No core logic touched.
        'GraphicalRepresentationOrNot':  '0',
        'LiningMaterial':                '0',
        'BendRadius':                    '0',
        'BendRadiusMultiplier':          '0',
        'MirrorBehaviorOption':          '0',
        'PartDataBasis':                 '',   # filtered — auto-selected by SP3D for equal tees
        'ValveManufacturer':             '',
        'ValveModelNumber':              '',
        'ValveTrim':                     '',
        'FlangeFaceSurfaceFinish':       '0',
        'SurfacePreparation':            '0',
        'ManufacturingMethod':           '1',  # 1 = Forged (B16.9 default for tee fittings)
        'MiscRequisitionClassification': '0',
        # Per-port routing identifiers — extractor-only (kept blank so the
        # cat_sheet_constants() filter drops them; SP3D never receives '0').
        'Id[1]':                         '',
        'PressureRating[1]':             '',
        'Id[2]':                         '',
        'PressureRating[2]':             '',
        'Id[3]':                         '',
        'PressureRating[3]':             '',
        'PipingNote1':                   '',
        'DryWeight':                     '0',
        'DryCogX':                       '0',
        'DryCogY':                       '0',
        'DryCogZ':                       '0',
        'WaterWeight':                   '0',
        'WaterCogX':                     '0',
        'WaterCogY':                     '0',
        'WaterCogZ':                     '0',
        'SurfaceArea':                   '0',
        'VolumetricCapacity':            '0',
    },
    'ReducingTee': {
        # LS1E reference (sheet 'ReducingTee', CommodityPart section,
        # row 7 = field codes, 84 part rows). Only PartDataBasis=860,
        # GeometricIndustryStandard=39 and MaterialGrade=264 are populated
        # — every target column listed below is blank in every row.
        # Sentinel defaults keep the spec canvas populated; merge_fill_blank()
        # ensures any real extractor value always wins. Override per row
        # via WorkbookCellOverride. No core logic touched.
        # BendRadius / BendRadiusMultiplier are computed per NPD by closure
        # builders in _common_part_row(); the '0' default only fires on blank.
        # Tees are 3-port (run × run × branch) — Id[n] / PressureRating[n]
        # are per-port routing values that MUST come from the extractor;
        # we keep them as '' so cat_sheet_constants() filters them out and
        # SP3D never receives a bogus NPD / rating placeholder.
        'PartDataBasis':                 '860',  # LS1E reference reducing-tee
        'GraphicalRepresentationOrNot':  '0',
        'LiningMaterial':                '0',   # 0 = None / unlined
        'BendRadius':                    '0',
        'BendRadiusMultiplier':          '0',
        'MirrorBehaviorOption':          '0',
        'ValveManufacturer':             '',
        'ValveModelNumber':              '',
        'ValveTrim':                     '',
        'FlangeFaceSurfaceFinish':       '0',
        'SurfacePreparation':            '0',
        'ManufacturingMethod':           '1',   # 1 = Forged (B16.9 default)
        'MiscRequisitionClassification': '0',
        # Per-port routing identifiers — extractor-driven. Populated as
        # '0' sentinel (instead of '' which cat_sheet_constants() would
        # filter) so the spec canvas always shows a value. The extractor
        # / row builder always overrides with the real per-port NPD /
        # ANSI pressure-class via merge_fill_blank() — the '0' fires
        # only when extraction yields nothing for a row.
        'Id[1]':                         '0',
        'PressureRating[1]':             '0',
        'Id[2]':                         '0',
        'PressureRating[2]':             '0',
        'Id[3]':                         '0',
        'PressureRating[3]':             '0',
        'PipingNote1':                   '',
        'DryWeight':                     '0',
        'DryCogX':                       '0',
        'DryCogY':                       '0',
        'DryCogZ':                       '0',
        'WaterWeight':                   '0',
        'WaterCogX':                     '0',
        'WaterCogY':                     '0',
        'WaterCogZ':                     '0',
        'SurfaceArea':                   '0',
        'VolumetricCapacity':            '0',
    },
    # ── Reducers / Swages ─────────────────────────────────────────────────
    'ConcentricSwage':   {},
    'ConcentricReducer': {},
    'EccentricReducer':  {},
    # ── Cap ───────────────────────────────────────────────────────────────
    'Cap': {},
    # ── Branch / Olets ────────────────────────────────────────────────────
    'Weldolet': {
        'PartDataBasis':       '3394',  # LS1E reference weldolet = 3394
    },
    # ── Valves (sheet-level catalogue identifiers) ────────────────────────
    # LS1E reference column-by-column inspection (CommodityPart section,
    # row 7 = field codes, rows 9+ = data):
    #   GateValve  → PartDataBasis=2258, ValveModelNumber=1070,
    #                ValveTrim=99, MiscReq=290, GIS=40, MaterialGrade=60
    #   GlobeValve → PartDataBasis=2200, ValveTrim=35, GIS=40,
    #                MaterialGrade=150, GraphicalRepresentationOrNot=15
    # Every other target attribute is left blank for every part-row in the
    # reference; the sentinels below match SP3D's "Not Specified / 0" enum
    # so the canvas never shows empty cells while merge_fill_blank() keeps
    # any real extractor value as the winner.
    # NOTE: ManufacturingMethod = '0' (Not Specified) for valves — unlike
    # forged fittings (B16.9 → '1'), valve bodies are cast/forged per model.
    'GateValve': {
        'PartDataBasis':                 '2258',
        'ValveModelNumber':              '1070',
        'ValveTrim':                     '99',
        'MiscRequisitionClassification': '290',
        'LiningMaterial':                '0',
        'BendRadius':                    '0',
        'BendRadiusMultiplier':          '0',
        'MirrorBehaviorOption':          '0',
        'ValveManufacturer':             '',
        'FlangeFaceSurfaceFinish':       '0',
        'SurfacePreparation':            '0',
        'ManufacturingMethod':           '0',
        'PipingNote1':                   '',
        'DryWeight':                     '0',
        'DryCogX':                       '0',
        'DryCogY':                       '0',
        'DryCogZ':                       '0',
        'WaterWeight':                   '0',
        'WaterCogX':                     '0',
        'WaterCogY':                     '0',
        'WaterCogZ':                     '0',
        'SurfaceArea':                   '0',
        'VolumetricCapacity':            '0',
    },
    'GlobeValve': {
        'PartDataBasis':                 '2200',
        'ValveTrim':                     '35',
        # LS1E reference is the only sheet that populates this column.
        'GraphicalRepresentationOrNot':  '15',
        'ValveModelNumber':              '',
        'MiscRequisitionClassification': '0',
        'LiningMaterial':                '0',
        'BendRadius':                    '0',
        'BendRadiusMultiplier':          '0',
        'MirrorBehaviorOption':          '0',
        'ValveManufacturer':             '',
        'FlangeFaceSurfaceFinish':       '0',
        'SurfacePreparation':            '0',
        'ManufacturingMethod':           '0',
        'PipingNote1':                   '',
        'DryWeight':                     '0',
        'DryCogX':                       '0',
        'DryCogY':                       '0',
        'DryCogZ':                       '0',
        'WaterWeight':                   '0',
        'WaterCogX':                     '0',
        'WaterCogY':                     '0',
        'WaterCogZ':                     '0',
        'SurfaceArea':                   '0',
        'VolumetricCapacity':            '0',
    },
    'CheckValve': {
        # ── SP3D-safe defaults for columns LS1E leaves blank ──────────────
        # Same merge contract as GateValve / GlobeValve: real extractor /
        # row-builder values always win via merge_fill_blank(); these are
        # last-resort sentinels so the canvas never renders empty cells.
        # Override any value on the canvas via WorkbookCellOverride —
        # no code change required.
        # ASME B16.34 / B16.10 cast or forged check-valve body.
        'PartDataBasis':                 '2267',
        'ValveTrim':                     '55',
        # GeometricIndustryStandard is resolved per-row by _resolve_gis_code()
        # against comp.material_standard; intentionally NOT defaulted here.
        'LiningMaterial':                '0',   # 0 = None / unlined
        'BendRadius':                    '0',   # N/A for valve body
        'BendRadiusMultiplier':          '0',
        'MirrorBehaviorOption':          '0',   # 0 = no mirror
        # Valve identity columns — extractor / catalogue resolver fills these
        # from the spec sheet. Blank sentinels keep cells visible without
        # injecting fake data.
        'ValveManufacturer':             '',
        'ValveModelNumber':              '',
        'FlangeFaceSurfaceFinish':       '0',
        'SurfacePreparation':            '0',
        # ManufacturingMethod = '0' (Not Specified) for valves — body may be
        # cast (B16.34) or forged depending on model; resolver may override.
        'ManufacturingMethod':           '0',
        'MiscRequisitionClassification': '0',
        'PipingNote1':                   '',
        # Weight / centre-of-gravity columns: SP3D 3D-model engine resolves
        # these from the symbol + NPD at bulk-load time. Sentinel '0' keeps
        # the canvas populated until extractor / catalogue feeds real values.
        'DryWeight':                     '0',
        'DryCogX':                       '0',
        'DryCogY':                       '0',
        'DryCogZ':                       '0',
        'WaterWeight':                   '0',
        'WaterCogX':                     '0',
        'WaterCogY':                     '0',
        'WaterCogZ':                     '0',
        'SurfaceArea':                   '0',
        'VolumetricCapacity':            '0',
        # FacetoFace is per-NPD per-pressure-class value from ASME B16.10
        # (e.g. 6"-150 SW = 165mm, 6"-150 FL = 451mm). Sentinel '0' fires
        # only when extractor yields nothing for the row.
        'FacetoFace':                    '0',
    },
    # ── Specialties ───────────────────────────────────────────────────────
    'Paddle': {
        # LS1E reference symbol icon for Paddle Blind / Spectacle Blind.
        'SymbolIcon':          'SymbolIcons\\SP3DPaddleBlind.gif',
    },
    'Coupling': {},
    'Nipple': {
        # Seamless nipples per ASME B36.10 (LS1E dominant value).
        'ManufacturingMethod': '5',
    },
    # ── Gasket / Bolt part data ───────────────────────────────────────────
    'GasketPartData': {
        # ASME B16.20 spiral-wound / RTJ thickness defaults. The reference
        # ships these as the only thickness columns SP3D bulkload needs.
        'ThicknessFor3DModel':  '1/8"',
        'ProcurementThickness': '1/8"',
    },
    'BoltPartData': {},
}


def cat_sheet_constants(sheet_name: str, cls=None, comp=None) -> dict:
    """Resolve `CAT_DIMENSIONAL_FIELDS[sheet_name]`, evaluating callables.

    Soft-coded helper used by both `_common_part_row` and the specialised
    gasket/bolt builders. Returns a fresh dict (no shared mutable state).
    Empty / None values are filtered so the writer's "skip on empty" rule
    behaves identically whether a column is unmapped or mapped-to-empty.
    """
    raw = CAT_DIMENSIONAL_FIELDS.get(sheet_name) or {}
    out: dict = {}
    for k, v in raw.items():
        try:
            value = v(cls, comp) if callable(v) else v
        except Exception:
            value = ''
        if value not in (None, ''):
            out[k] = value
    return out


def merge_fill_blank(row: dict, defaults: dict) -> dict:
    """Soft-coded fill-if-blank merge.

    Copies every key in `defaults` into `row` *only* when `row` is missing the
    key or holds an empty / None value. Extractor-derived values therefore
    always win over the sheet-level defaults in `CAT_DIMENSIONAL_FIELDS`
    — preserving real data while filling gaps the reference workbook expects.
    """
    for k, v in defaults.items():
        if row.get(k) in (None, ''):
            row[k] = v
    return row


# ─────────────────────────────────────────────────────────────────────────────
# 4c. GEOMETRIC INDUSTRY STANDARD — string → SP3D enumeration code map
# ─────────────────────────────────────────────────────────────────────────────
# SP3D bulk-load expects numeric codes for the GeometricIndustryStandard
# column, not human-readable strings ("ASME B36.10M" → code '100'). Codes
# are taken from the LS1E-A3 reference workbook and the SP3D Reference Data
# Guide. Add new entries here (key is lower-case, whitespace/dot stripped);
# the resolver `_resolve_gis_code()` normalises lookups.
# ─────────────────────────────────────────────────────────────────────────────
GEOMETRIC_INDUSTRY_STANDARD_CODES: dict[str, str] = {
    # Pipe schedules
    'asmeb3610':   '100',  # Carbon / alloy seamless & welded pipe
    'asmeb3610m':  '100',
    'asmeb3619':   '110',  # Stainless steel pipe
    'asmeb3619m':  '110',
    # Flanges (LS1E reference uses code '35' for ASME B16.5)
    'asmeb165':    '35',
    'asmeb1647':   '35',
    # Buttweld fittings
    'asmeb169':    '10',
    # Threaded / SW fittings
    'asmeb1611':   '20',
    # Gaskets
    'asmeb1620':   '600',
    'asmeb1621':   '601',
    # Bolts / studs
    'asmeb181':    '700',
    'asmeb1821':   '701',
}

# Per-sheet fallback when `comp.material_standard` is blank. Soft-coded so
# new sheets can be supported without touching `_common_part_row`.
GEOMETRIC_INDUSTRY_STANDARD_SHEET_DEFAULTS: dict[str, str] = {
    'PipeStock':         '100',  # LS1E reference value
    'WeldNeckFlange':    '35',   # LS1E reference value (ASME B16.5)
    'BlindFlange':       '35',
    '90DegElbow':        '10',
    '90DegLRElbow':      '10',
    '45DegElbow':        '10',
    '45DegLRElbow':      '10',
    'Tee':               '10',
    'ReducingTee':       '10',
    'ConcentricSwage':   '10',
    'ConcentricReducer': '10',
    'EccentricReducer':  '10',
    'Cap':               '10',
    'Weldolet':          '10',
    'GateValve':         '',    # SP3D resolves via ValveModelNumber
    'GlobeValve':        '',
    'CheckValve':        '',
    'Paddle':            '15',
    'Coupling':          '20',
    'Nipple':            '20',
    'GasketPartData':    '600',
    'BoltPartData':      '700',
}


def _resolve_gis_code(sheet_name: str, raw_value) -> str:
    """Normalise `comp.material_standard` to the SP3D enumeration code.

    Soft-coded lookup order:
      1. If `raw_value` already looks like a numeric SP3D code  → keep as-is.
      2. If a normalised key matches `GEOMETRIC_INDUSTRY_STANDARD_CODES`
         → return the mapped code.
      3. Else fall back to `GEOMETRIC_INDUSTRY_STANDARD_SHEET_DEFAULTS`.
      4. Else return the original string (or '' for unknown sheets).
    """
    if raw_value not in (None, ''):
        s = str(raw_value).strip()
        # Already a numeric SP3D code → keep
        if s.isdigit():
            return s
        # Normalise: strip whitespace, punctuation, lowercase
        key = ''.join(ch.lower() for ch in s if ch.isalnum())
        if key in GEOMETRIC_INDUSTRY_STANDARD_CODES:
            return GEOMETRIC_INDUSTRY_STANDARD_CODES[key]
        # Unknown human-readable string → keep it; SP3D ref-data guide may
        # accept the textual form on import.
        return s
    return GEOMETRIC_INDUSTRY_STANDARD_SHEET_DEFAULTS.get(sheet_name, '')


# ─────────────────────────────────────────────────────────────────────────────
# 5. SPEC SHEET BUILDERS
#
# Each builder is `(piping_class) → list[dict[column_name → value]]`.
# Unmapped column-name keys are silently dropped by the writer.
# ─────────────────────────────────────────────────────────────────────────────
# ── PMCD intelligent value resolvers (soft-coded) ────────────────────────────
# Each map is keyword-based and case-insensitive; the first matching keyword
# wins. Add new mappings at the top to override the generic fallbacks. These
# tables are deliberately kept module-level so they can be re-tuned without
# touching the builder logic, and so the spec-customization override UI can
# read them for inline editing in a later sprint.
_PMCD_LINING_KEYWORD_MAP = [
    # (lowercase keyword in fluid service / raw_notes, lining material value)
    ('hcl',            'Rubber Lined'),
    ('hydrochloric',   'Rubber Lined'),
    ('caustic',        'Rubber Lined'),
    ('slurry',         'Rubber Lined'),
    ('sulphuric',      'PTFE Lined'),
    ('sulfuric',       'PTFE Lined'),
    ('h2so4',          'PTFE Lined'),
    ('acid',           'PTFE Lined'),
    ('chlorine',       'PTFE Lined'),
    ('cement',         'Cement Lined'),
    ('potable',        'Cement Lined'),
    ('cooling water',  'Cement Lined'),
    ('produced water', 'Cement Lined'),
    ('sea water',      'Cement Lined'),
    ('raw water',      'Cement Lined'),
]

_PMCD_JACKET_KEYWORDS = (
    'jacket', 'jacketed', 'steam trace', 'steam-trace', 'steam tracing',
    'heat trace', 'heat-trace', 'heat tracing',
)

_PMCD_HEATING_FLUID_KEYWORDS = [
    # (lowercase keyword, JacketAndJumperFluidService value)
    ('hp steam',  'HP Steam'),
    ('mp steam',  'MP Steam'),
    ('lp steam',  'LP Steam'),
    ('steam',     'LP Steam'),
    ('hot oil',   'Hot Oil'),
    ('glycol',    'Glycol'),
    ('hot water', 'Hot Water'),
]


def _pmcd_class_text(cls) -> str:
    """Concatenated lower-cased haystack used by all PMCD keyword resolvers."""
    parts = [
        _spec_name(cls) or '',
        cls.material_grade or '',
        cls.raw_notes or '',
        '; '.join(cls.service_list or []),
    ]
    return ' '.join(parts).lower()


def _pmcd_lining_material(cls) -> str:
    haystack = _pmcd_class_text(cls)
    for needle, value in _PMCD_LINING_KEYWORD_MAP:
        if needle in haystack:
            return value
    return SPEC_DEFAULTS['pmcd_lining_material']


def _pmcd_is_jacketed(cls) -> bool:
    haystack = _pmcd_class_text(cls)
    if any(k in haystack for k in _PMCD_JACKET_KEYWORDS):
        return True
    # SP3D project convention: piping classes prefixed 'JK' are jacketed.
    sn = (_spec_name(cls) or '').strip().upper()
    return sn.startswith('JK')


def _pmcd_jacket_fluid(cls) -> str:
    if not _pmcd_is_jacketed(cls):
        return SPEC_DEFAULTS['pmcd_jacket_jumper_fluid']
    haystack = _pmcd_class_text(cls)
    for needle, value in _PMCD_HEATING_FLUID_KEYWORDS:
        if needle in haystack:
            return value
    return SPEC_DEFAULTS['pmcd_jacket_jumper_fluid_heated']


def _pmcd_jacket_moc(cls) -> str:
    if not _pmcd_is_jacketed(cls):
        return SPEC_DEFAULTS['pmcd_jacket_moc_class']
    # Mirror the class's own MOC — typical project convention for an
    # integral steam jacket is the same metallurgy as the process pipe.
    return (cls.material_grade or '').strip() or SPEC_DEFAULTS['pmcd_jacket_moc_class']


def _pmcd_jacket_description(cls) -> str:
    if not _pmcd_is_jacketed(cls):
        return SPEC_DEFAULTS['pmcd_jacket_description']
    moc = (cls.material_grade or '').strip()
    return f'Jacket: {moc}' if moc else SPEC_DEFAULTS['pmcd_jacket_description']


def _pmcd_jumper_moc(cls) -> str:
    # Jumpers share metallurgy with the jacket whenever the spec is jacketed.
    return _pmcd_jacket_moc(cls) if _pmcd_is_jacketed(cls) else SPEC_DEFAULTS['pmcd_jumper_moc_class']


def _pmcd_jumper_description(cls) -> str:
    if not _pmcd_is_jacketed(cls):
        return SPEC_DEFAULTS['pmcd_jumper_description']
    moc = (cls.material_grade or '').strip()
    return f'Jumper: {moc}' if moc else SPEC_DEFAULTS['pmcd_jumper_description']


def _pmcd_welding_procedure(cls) -> str:
    """Derive a deterministic WPS identifier from the class's MOC.

    Pattern: 'WPS-<sanitised material grade>' (alphanumerics + '-' only,
    upper-cased, capped at 30 chars to stay inside SP3D's varchar limit).
    Falls back to the soft-coded default when MOC is missing.
    """
    moc = (cls.material_grade or '').strip()
    if not moc:
        return SPEC_DEFAULTS['pmcd_welding_procedure_spec']
    token = re.sub(r'[^A-Za-z0-9]+', '-', moc).strip('-').upper()[:26]
    return f'WPS-{token}' if token else SPEC_DEFAULTS['pmcd_welding_procedure_spec']


def _rows_piping_materials_class_data(cls):
    # LastModifiedOn / ApprovalDate — derive from the class's audit
    # timestamp so the value is deterministic per export run.
    _ts = getattr(cls, 'created_at', None) or datetime.utcnow()
    _date_str = _ts.strftime(SPEC_DEFAULTS['pmcd_date_format'])
    return [{
        'SpecName':                       _spec_name(cls),
        'MaterialsOfConstructionClass':   cls.material_grade or '',
        'MaterialsDescription':           cls.material_grade or '',
        'FluidService':                   '; '.join(cls.service_list or []) or '',
        'DesignStandard':                 _normalize_pressure_class(cls.pressure_rating),
        'AutomatedFlangeSelectionOption': SPEC_DEFAULTS['pmcd_automated_flange_selection'],
        'PipingCommodityOverrideOption':  SPEC_DEFAULTS['pmcd_piping_commodity_override'],
        'WasherCreationOption':           SPEC_DEFAULTS['pmcd_washer_creation_option'],
        'GasketRequirementOverride':      '1',
        'LiningMaterial':                 _pmcd_lining_material(cls),
        'PipingNote1':                    (cls.raw_notes or '')[:255],
        'PipingSpecStatus':               SPEC_DEFAULTS['pmcd_piping_spec_status'],
        'Responsibility':                 SPEC_DEFAULTS['pmcd_responsibility'],
        'LastModifiedOn':                 _date_str,
        'Comments':                       (cls.raw_notes or '')[:255],
        'RevisionNumber':                 SPEC_DEFAULTS['pmcd_revision_number'],
        'ApprovedBy':                     SPEC_DEFAULTS['pmcd_approved_by'],
        'ApprovalDate':                   _date_str,
        'JacketMatOfConstructionClass':   _pmcd_jacket_moc(cls),
        'JumperMatOfConstructionClass':   _pmcd_jumper_moc(cls),
        'JacketMaterialsDescription':     _pmcd_jacket_description(cls),
        'JumperMaterialsDescription':     _pmcd_jumper_description(cls),
        'JacketAndJumperFluidService':    _pmcd_jacket_fluid(cls),
        'StressRelief':                   SPEC_DEFAULTS['pmcd_stress_relief'],
        'Examination':                    SPEC_DEFAULTS['pmcd_examination'],
        'HyperlinkToHumanSpec':           SPEC_DEFAULTS['pmcd_hyperlink_human_spec'],
        'StressReliefRequirement':        SPEC_DEFAULTS['pmcd_stress_relief_requirement'],
        'MaterialsGroup':                 SPEC_DEFAULTS['pmcd_materials_group'],
        'WeldingProcedureSpecification':  _pmcd_welding_procedure(cls),
        'MaterialsType':                  SPEC_DEFAULTS['pmcd_materials_type'],
    }]


def _rows_service_limits(cls):
    # Resolve the class-wide NPD envelope once (cheap O(n) over components)
    # so every P/T point inherits the same range — SP3D treats each row as
    # an independent service-limit envelope, but for a single piping class
    # the NPD range is invariant across temperature/pressure points.
    _npd_min, _npd_max = _class_npd_range(cls)
    npd_from = (
        _normalize_npd(_npd_min) if _npd_min is not None
        else SPEC_DEFAULTS['service_limits_npd_from_default']
    )
    npd_to = (
        _normalize_npd(_npd_max) if _npd_max is not None
        else SPEC_DEFAULTS['service_limits_npd_to_default']
    )
    npd_units = S3D_DEFAULTS['NpdUnitType']

    rows = []
    for pt in (cls.pt_rating_table or []):
        rows.append({
            'SpecName':                  _spec_name(cls),
            'Temperature':               _fmt_temperature(pt.get('temperature_c')),
            'NominalPipingDiameterFrom': npd_from,
            'NominalPipingDiameterTo':   npd_to,
            'NominalPipingDiameterUnits': npd_units,
            'Pressure':                  _fmt_pressure(pt.get('pressure_bar_g')),
        })
    return rows


def _rows_corrosion_allowance(cls):
    # Resolve once per class (soft-coded, no hardcoded magic values)
    materials_category = _materials_category(cls)
    corrosion = cls.corrosion_allowance or SPEC_DEFAULTS['corrosion_allowance_default']
    services = cls.service_list or []

    # SP3D expects one CorrosionAllowance record per (SpecName, FluidCode).
    # When the paper spec lists multiple services, emit one row each; when
    # the service list is empty we still emit a single row with blank FluidCode
    # so the corrosion value is bulkloaded.
    if not services:
        return [{
            'SpecName':           _spec_name(cls),
            'MaterialsCategory':  materials_category,
            'FluidCode':          '',
            'CorrosionAllowance': corrosion,
        }]
    return [{
        'SpecName':           _spec_name(cls),
        'MaterialsCategory':  materials_category,
        'FluidCode':          s,
        'CorrosionAllowance': corrosion,
    } for s in services]


def _rows_pipe_nominal_diameters(cls):
    """One row per *unique* pipe NPD encountered across the class's components."""
    seen: set[float] = set()
    for c in cls.components.all():
        for sz in (c.size_from, c.size_to):
            v = _to_float_npd(sz)
            if v is not None:
                seen.add(v)
    return [
        {'SpecName': _spec_name(cls),
         'Npd': _normalize_npd(v),
         'NpdUnitType': S3D_DEFAULTS['NpdUnitType']}
        for v in sorted(seen)
    ]


def _rows_piping_commodity_matl_control_data(cls):
    """One row per (component × NPD) — emits the full PipingCommodityMatlControlData
    schema (49 columns) so the SP3D bulkload sheet matches the LS1E-A3 reference
    exactly. Every column is soft-coded via SPEC_DEFAULTS['pcmd_*'] so values can
    be re-tuned per project without touching this builder.
    """
    rows = []
    cls_token = _safe_class_token(cls)
    # Pre-resolve constants once per class (microoptimisation; values are dict lookups).
    fab_type      = SPEC_DEFAULTS['pcmd_fabrication_type']
    supply_resp   = SPEC_DEFAULTS['pcmd_supply_responsibility']
    rpt_type      = SPEC_DEFAULTS['pcmd_reporting_type']
    qty_rpt       = SPEC_DEFAULTS['pcmd_quantity_reportable_parts']
    gasket_req    = SPEC_DEFAULTS['pcmd_gasket_requirements']
    bolt_req      = SPEC_DEFAULTS['pcmd_bolting_requirements']
    weld_req      = SPEC_DEFAULTS['pcmd_welding_requirement']

    for c in cls.components.all().order_by('display_order'):
        sheet = route_component_to_cat_sheet(c)
        prefix = (CAT_SHEET_DEFAULTS.get(sheet, {}) or {}).get('icc_prefix', 'RAD_CMP')
        npds = _enumerate_npds(c.size_from, c.size_to)
        if not npds:
            continue
        for npd in npds:
            icc = f'{prefix}_{cls_token}_{_npd_token(npd)}'
            first_size = _normalize_npd(npd)
            rows.append({
                # ── Identification ───────────────────────────────────────────────────
                'ContractorCommodityCode':        icc,
                # Reference uses identical first-size bounds for fixed-size parts;
                # reducers/multisize would populate SecondSize* via overrides.
                'FirstSizeFrom':                  first_size,
                'FirstSizeTo':                    first_size,
                'FirstSizeUnits':                 S3D_DEFAULTS['NpdUnitType'],
                'SecondSizeFrom':                 SPEC_DEFAULTS['pcmd_second_size_from'],
                'SecondSizeTo':                   SPEC_DEFAULTS['pcmd_second_size_to'],
                'SecondSizeUnits':                SPEC_DEFAULTS['pcmd_second_size_units'],
                'MultisizeOption':                SPEC_DEFAULTS['pcmd_multisize_option'],
                'IndustryCommodityCode':          icc,
                'ClientCommodityCode':            '',
                'CIMISCommodityCode':             '',
                'ShortMaterialDescription':       (c.description or c.sub_type or c.component_type)[:60],
                'LocalizedShortMaterialDesc':     '',
                'LongMaterialDescription':        (c.description or '')[:255],
                'Vendor':                         '',
                'Manufacturer':                   '',
                # ── Fabrication / supply (numeric SP3D codes — reference confirmed) ──
                'FabricationType':                fab_type,
                'SupplyResponsibility':           supply_resp,
                'ReportingType':                  rpt_type,
                'QuantityOfReportableParts':      qty_rpt,
                # ── Joint requirements ──────────────────────────────────────────────
                'GasketRequirements':             gasket_req,
                'BoltingRequirements':            bolt_req,
                'ClampRequirement':               SPEC_DEFAULTS['pcmd_clamp_requirement'],
                'WeldingRequirement':             weld_req,
                'LooseMaterialRequirements':      SPEC_DEFAULTS['pcmd_loose_material_requirements'],
                # ── Cap-screw substitution (only for flanged caps/blinds) ──────────────────
                'SubstCapScrewsQuantity':         SPEC_DEFAULTS['pcmd_subst_cap_screws_quantity'],
                'SubstCapScrewCntrCommodityCode': SPEC_DEFAULTS['pcmd_subst_cap_screw_cntr_code'],
                'SubstCapScrewDiameter':          SPEC_DEFAULTS['pcmd_subst_cap_screw_diameter'],
                'TappedHoleDepth':                SPEC_DEFAULTS['pcmd_tapped_hole_depth'],
                'TappedHoleDepth2':               SPEC_DEFAULTS['pcmd_tapped_hole_depth_2'],
                'CapScrewEngagementGap':          SPEC_DEFAULTS['pcmd_cap_screw_engagement_gap'],
                # ── Valve operator (only for valve commodities) ──────────────────────────
                'MultiportValveOpReq':            SPEC_DEFAULTS['pcmd_multiport_valve_op_req'],
                'ValveOperatorType':              SPEC_DEFAULTS['pcmd_valve_operator_type'],
                'ValveOperatorGeoIndStd':         SPEC_DEFAULTS['pcmd_valve_operator_geo_ind_std'],
                'ValveOperatorCatalogPartNumber': SPEC_DEFAULTS['pcmd_valve_operator_catalog_part'],
                # ── Reporting / procurement metadata ─────────────────────────────────
                # ReportableCommodityCode defaults to the contractor code so the
                # SP3D MTO references the same identifier; can be overridden in
                # future via a component-level field.
                'ReportableCommodityCode':        icc,
                'PartDataSource':                 SPEC_DEFAULTS['pcmd_part_data_source'],
                'AltOrientationCommodityCode':    SPEC_DEFAULTS['pcmd_alt_orientation_commodity'],
                'HyperlinkToElectronicVendor':    SPEC_DEFAULTS['pcmd_hyperlink_vendor'],
                'HyperlinkToElectronicManuals':   SPEC_DEFAULTS['pcmd_hyperlink_manuals'],
                'PipingNote1':                    (c.notes or '')[:255],
                'VendorPartNumber':               SPEC_DEFAULTS['pcmd_vendor_part_number'],
                'ManufacturerPartNumber':         SPEC_DEFAULTS['pcmd_manufacturer_part_number'],
                'AltReportableCommodityCode':     SPEC_DEFAULTS['pcmd_alt_reportable_commodity'],
                'QuantityOfAltReportableParts':   SPEC_DEFAULTS['pcmd_quantity_alt_reportable_parts'],
                'eClasseProcurementCode':         SPEC_DEFAULTS['pcmd_eclasse_procurement_code'],
                'UNSPSCeProcurementCode':         SPEC_DEFAULTS['pcmd_unspsc_procurement_code'],
                'LegacyCommodityCode':            SPEC_DEFAULTS['pcmd_legacy_commodity_code'],
            })
    return rows


def _rows_gasket_selection_filter(cls):
    rows = []
    spec_name = _spec_name(cls)
    fluid_code = _infer_fluid_from_spec_name(spec_name)
    for c in cls.components.filter(component_type='gasket'):
        contractor_code = (getattr(c, 'commodity_code', '') or '').strip()
        comments = (c.description or '').strip()[:128] or SPEC_DEFAULTS['gasket_comments_default']
        rows.append({
            'SpecName':                     spec_name,
            'NominalDiameterFrom':          _normalize_npd(c.size_from),
            'NominalDiameterTo':            _normalize_npd(c.size_to),
            'NpdUnitType':                  S3D_DEFAULTS['NpdUnitType'],
            'GasketOption':                 '1',
            'MaximumTemperature':           SPEC_DEFAULTS['gasket_max_temperature'],
            'MinimumTemperature':           SPEC_DEFAULTS['gasket_min_temperature'],
            'EndPreparation':               _normalize_end_prep(c.end_connection),
            'PressureRating':               _normalize_pressure_class(cls.pressure_rating),
            'EndStandard':                  S3D_DEFAULTS['EndStandard'],
            'AlternateEndPreparation':      SPEC_DEFAULTS['gasket_alt_end_preparation'],
            'AlternatePressureRating':      SPEC_DEFAULTS['gasket_alt_pressure_rating'],
            'AlternateEndStandard':         SPEC_DEFAULTS['gasket_alt_end_standard'],
            'FluidCode':                    fluid_code,
            'ScheduleThickness':            SPEC_DEFAULTS['gasket_schedule_thickness'],
            'ContractorCommodityCode':      contractor_code,
            'Priority':                     SPEC_DEFAULTS['gasket_priority'],
            'RingNumber':                   SPEC_DEFAULTS['gasket_ring_number'],
            'FabricationCategoryOverride':  SPEC_DEFAULTS['gasket_fabrication_category_override'],
            'SupplyResponsibilityOverride': SPEC_DEFAULTS['gasket_supply_responsibility_override'],
            'Comments':                     comments,
            'QuantityOfAltReportableParts': SPEC_DEFAULTS['gasket_qty_alt_reportable_parts'],
            'AltReportableCommodityCode':   SPEC_DEFAULTS['gasket_alt_reportable_code'],
            'QuantityOfReportableParts':    SPEC_DEFAULTS['gasket_qty_reportable_parts'],
            'ReportableCommodityCode':      contractor_code,
            'PipingNote1':                  SPEC_DEFAULTS['gasket_piping_note_1'],
        })
    return rows


def _rows_bolt_selection_filter(cls):
    """Emit every column SP3D evaluates for BoltSelectionFilter.

    Soft-coded defaults via SPEC_DEFAULTS so admins can retune without
    touching code. Same two-tier model as gasket selection — builder runs
    when the user's spec has 'bolt' components; otherwise the template
    passthrough enrichment (TEMPLATE_PASSTHROUGH_FIELD_DEFAULTS) fills the
    LS1E reference rows so SP3D never sees a blank required field.
    """
    rows = []
    spec_name = _spec_name(cls)
    for c in cls.components.filter(component_type='bolt'):
        commodity_code = getattr(c, 'commodity_code', '') or ''
        description = (c.description or '')[:128] or SPEC_DEFAULTS['bolt_comments_default']
        rows.append({
            'SpecName':                    spec_name,
            'NominalDiameterFrom':         _normalize_npd(c.size_from),
            'NominalDiameterTo':           _normalize_npd(c.size_to),
            'NpdUnitType':                 S3D_DEFAULTS['NpdUnitType'],
            'BoltOption':                  '1',
            'MaximumTemperature':          SPEC_DEFAULTS['nut_max_temperature'],
            'EndPreparation':              _normalize_end_prep(c.end_connection) or S3D_DEFAULTS['EndPrep_RF'],
            'PressureRating':              _normalize_pressure_class(cls.pressure_rating),
            'EndStandard':                 S3D_DEFAULTS['EndStandard'],
            'AlternateEndPreparation':     SPEC_DEFAULTS['bolt_alt_end_preparation'],
            'AlternatePressureRating':     SPEC_DEFAULTS['bolt_alt_pressure_rating'],
            'AlternateEndStandard':        SPEC_DEFAULTS['bolt_alt_end_standard'],
            'ContractorCommodityCode':     commodity_code,
            'Priority':                    SPEC_DEFAULTS['bolt_priority'],
            'BoltExtensionOption':         SPEC_DEFAULTS['bolt_bolt_extension_option'],
            'FabricationCategoryOverride': SPEC_DEFAULTS['bolt_fabrication_category_override'],
            'SupplyResponsibilityOverride':SPEC_DEFAULTS['bolt_supply_responsibility_override'],
            'Comments':                    description,
            'PipingNote1':                 SPEC_DEFAULTS['bolt_piping_note_1'],
            'LubricationRequirements':     SPEC_DEFAULTS['bolt_lubrication_requirements'],
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 5b. SPEC DEFAULTS  (one-stop tunable defaults for the auxiliary SPEC sheets)
#
# Used by the secondary builders below so a single config edit retunes the
# entire bulkload — no rebuild logic to change.  All values are strings to
# match what SmartPlant 3D's Reference Data Generator expects.
# ─────────────────────────────────────────────────────────────────────────────
SPEC_DEFAULTS = {
    'standard_note_name':              'Project Note',
    'standard_note_purpose':           'General',
    'short_code_hierarchy_type':       'Standard',
    'short_code':                      'STD',
    'pipe_branch_angle_low':           '90',
    'pipe_branch_angle_high':          '90',
    'pipe_branch_short_code':          'TEE',
    # SP3D PipeBranch fallback chain — used when the primary ShortCode is not
    # available for a given (HeaderSize, BranchSize) pair. Standard piping
    # practice (ASME B16.9): Tee → Weldolet (unequal branches on large headers)
    # → Sockolet (small branches on large headers, socket-weld systems).
    'pipe_branch_secondary_short_code':'Weldolet',
    'pipe_branch_tertiary_short_code': 'Sockolet',
    'permissible_tap_number':          '1',
    'permissible_tap_is_preferred':    '1',
    'commodity_option_main':           '1',           # main-line commodity option
    'joint_quality_factor':            '1.0',
    'min_thickness':                   '',            # left blank → template fallback
    'retirement_thickness':            '',
    'thread_thickness':                '1mm',         # SP3D threaded-end relief deduction
    'preferred_schedule':              'S-80',        # PreferredSchedule1 default (LS1E reference)
    # SP3D ThicknessDataRule fallback chain — used when PreferredSchedule1 is
    # not available at a given NPD. Priority chain for a carbon-steel /
    # 600# class spec with S-80 primary; tune per project via env or admin.
    'preferred_schedule_2':            'S-160',
    'preferred_schedule_3':            'S-XXS',
    'preferred_schedule_4':            'S-XS',
    'preferred_schedule_5':            'S-40',
    'preferred_schedule_6':            'S-STD',
    'nut_option':                      '1',
    'nut_bolt_type':                   '5',           # SP3D BoltType=5 → ASTM A193 B7 stud (LS1E ref)
    # NutSelectionFilter — SP3D defaults for every column SP3D evaluates per
    # (spec, bolt-diameter) pair. Values match the LS1E-A3 reference where
    # present and use ASME/ASTM-valid SP3D codes for columns the reference
    # leaves blank. All tunable via DB / env without code change.
    'nut_max_temperature':             '650F',        # ASTM A194 Gr 2H max service temp
    'nut_contractor_commodity_code_prefix': 'NUT',    # Yields NUT001, NUT002, … per diameter
    'nut_supplementary_nut_option':    '0',           # 0 = no supplementary nut required
    'nut_suppl_cntr_commodity_code':   '',            # blank when supplementary option = 0
    'nut_fabrication_category_override':'0',          # 0 = no override (inherit from spec)
    'nut_supply_responsibility_override':'0',         # 0 = no override (inherit from spec)
    'nut_comments':                    'Heavy hex nut per ASME B18.2.2, ASTM A194 Gr 2H',
    'nut_piping_note_1':               'N/A',         # placeholder; replace with project piping note ref
    # GasketSelectionFilter — SP3D defaults for every column SP3D evaluates per
    # (spec, gasket-NPD) pair. Values are SP3D-valid for ASME B16.20 spiral-wound
    # / B16.21 non-metallic gaskets. Tune per project without code change.
    'gasket_max_temperature':          '650F',        # ASTM A516-70 / Flexitallic CG max service
    'gasket_min_temperature':          '-29C',        # ASME B31.3 minimum without impact test
    'gasket_alt_end_preparation':      '',            # blank = no alternate end-prep
    'gasket_alt_pressure_rating':      '',            # blank = no alternate rating
    'gasket_alt_end_standard':         '',            # blank = no alternate end-standard
    'gasket_schedule_thickness':       '',            # gaskets have no pipe schedule
    'gasket_priority':                 '1',           # single-priority selection
    'gasket_ring_number':              '',            # blank = inherited from gasket catalogue
    'gasket_comments_default':         'Gasket per ASME B16.20 / B16.21',
    'gasket_fabrication_category_override': '7',     # LS1E reference value
    'gasket_supply_responsibility_override': '10',   # LS1E reference value
    'gasket_qty_reportable_parts':     '1',
    'gasket_qty_alt_reportable_parts': '0',
    'gasket_alt_reportable_code':      '',
    'gasket_piping_note_1':            'N/A',         # placeholder; replace with project piping note ref
    # BoltSelectionFilter — SP3D defaults for every column SP3D evaluates per
    # (spec, bolt-NPD) pair. Values are SP3D-valid for ASTM A193 B7 stud bolts
    # + A194 2H heavy hex nuts (ASME B16.5 flange make-up). Tune per project
    # without code change.
    # Alt-end defaults derived from LS1E reference template's dominant
    # non-blank values (see audit in _diag_bolt_template.py). Override per
    # project by editing these constants -- no code change required.
    'bolt_alt_end_preparation':        '121',         # LS1E template: 21/33 rows
    'bolt_alt_pressure_rating':        '900',         # LS1E template: 29/33 rows
    'bolt_alt_end_standard':           '5',           # LS1E template: 33/33 rows
    'bolt_priority':                   '1',           # single-priority selection
    'bolt_bolt_extension_option':      '1',           # LS1E reference: BoltExtensionOption=1
    'bolt_fabrication_category_override': '7',        # LS1E reference value
    'bolt_supply_responsibility_override': '10',      # LS1E reference value
    'bolt_comments_default':           'Stud bolt per ASME B16.5 / ASTM A193 B7 with ASTM A194 2H nuts',
    'bolt_piping_note_1':              'N/A',         # placeholder; replace with project piping note ref
    'bolt_lubrication_requirements':   'Molykote 1000 anti-seize (or equivalent) applied to threads and nut bearing faces',
    # AllowablePipingMaterialsClass — ensure FluidCode is NEVER blank.
    # When a class has no extracted service list, infer the fluid from the
    # spec_name prefix (soft-coded SPEC_NAME_FLUID_PREFIX_MAP below), then
    # fall back to this generic default. Both tunable without code change.
    'default_fluid_code':              'Process Fluid',
    # ── Numeric SP3D FluidCode (used by InsideSurfaceTreatment and any
    # other sheet that requires a numeric FluidCode ID instead of a fluid
    # name).  Matches LS1E reference template (FluidCode=521 across all
    # 7 IST rows).  Override per-project via SPEC_NAME_NUMERIC_FLUID_MAP
    # below; falls back to this default for unmapped spec-name prefixes.
    'numeric_fluid_code':              '521',
    'bend_angles':                     ('90', '45'),
    'inside_surface_treatment':        '0',           # 0 = none
    'outside_surface_treatment':       '0',
    'coating_type':                    '0',
    'environmental_zone':              'Indoor',
    'min_pipe_length':                 '0.5',         # metres
    'preferred_min_pipe_length':       '1.0',
    'purchase_length':                 '6.0',
    'takedown_short_code':             'FLG',
    'weld_short_code':                 'BW',
    'is_pair_required':                '1',
    'is_weld':                         '0',
    'port_method_of_trimming':         '1',
    'port_alignment_tolerance':        '0.001',
    'weld_class':                      '1',
    'weld_clearance_radius_increase':  '0.025',
    'weld_clearance_length':           '0.050',

    # ── PipingMaterialsClassData (PMCD) defaults ──────────────────────────
    # Values calibrated against the SP3D LS1E-A3 reference workbook.
    # All keys are soft-coded so they can be overridden via the workbook
    # cell override UI (apps.spec_customization.models.WorkbookCellOverride)
    # without touching code.
    'pmcd_automated_flange_selection':    '1',     # 1 = enabled
    'pmcd_piping_commodity_override':     '1',     # 1 = allow overrides
    'pmcd_washer_creation_option':        '0',     # 0 = no auto-washer
    # ── Lining / Jacket / Welding intelligent defaults ───────────────────
    # SP3D enum literal for "not applicable" in these slots is the string
    # 'None' (not the Python None / blank). When the class metadata gives
    # no hint of a lining/jacket requirement we still populate the cell so
    # SP3D bulkload sees an explicit "no-jacket / no-lining" declaration
    # instead of a missing record.
    'pmcd_lining_material':               'None',   # SP3D sentinel for unlined
    'pmcd_piping_spec_status':            'Issued',
    'pmcd_responsibility':                'Engineering',
    'pmcd_approved_by':                   'Engineering Manager',
    'pmcd_revision_number':               '0',     # leading revision
    'pmcd_jacket_moc_class':              'None',   # SP3D sentinel for not-jacketed
    'pmcd_jumper_moc_class':              'None',
    'pmcd_jacket_description':            'Not Applicable',
    'pmcd_jumper_description':            'Not Applicable',
    'pmcd_jacket_jumper_fluid':           'None',
    'pmcd_stress_relief':                 '0',     # 0 = not required (ASME B31.3 §331)
    'pmcd_examination':                   '1',     # 1 = Normal (ASME B31.3 §341.4)
    'pmcd_hyperlink_human_spec':          '',
    'pmcd_stress_relief_requirement':     '15',    # SP3D codelist (LS1E-A3)
    'pmcd_materials_group':               '15',    # SP3D codelist (LS1E-A3)
    # WeldingProcedureSpecification default — used when material grade is
    # unknown. Builder will override with WPS-<sanitised-MOC> when grade
    # is available (e.g. material_grade='A106 Gr B' → 'WPS-A106-GR-B').
    'pmcd_welding_procedure_spec':        'WPS-GEN-001',
    'pmcd_materials_type':                '545',   # SP3D codelist (LS1E-A3)
    # Heating-medium default for jacketed-spec rows (overridden when the
    # class's raw_notes name a specific fluid like 'LP Steam').
    'pmcd_jacket_jumper_fluid_heated':    'LP Steam',
    # Date format for LastModifiedOn / ApprovalDate (SP3D accepts ISO).
    'pmcd_date_format':                   '%Y-%m-%d',

    # ── ServiceLimits NPD-range fallback ─────────────────────────────────
    # Used when a piping class has no parseable component sizes (e.g.
    # services where the paper spec only lists P/T limits, not pipe sizes).
    # Values are in inches; SP3D NpdUnitType is taken from S3D_DEFAULTS.
    'service_limits_npd_from_default':    '0.5',     # 1/2" — typical project minimum
    'service_limits_npd_to_default':      '24',      # 24"  — typical process header maximum

    # ── CorrosionAllowance ────────────────────────────────────────────────────
    # Default MaterialsCategory used when MATERIALS_CATEGORY_MAP does not
    # resolve the class's material_grade (e.g. blank/unknown grade).
    # '15' = Carbon Steel per LS1E-A3 reference — the safest neutral default.
    'corrosion_allowance_materials_category_default': '15',
    # Default corrosion allowance emitted when the class has none extracted.
    'corrosion_allowance_default':        '1.6mm',

    # ── PipingCommodityMatlControlData (PCMD) row-level codes ──────────────────
    # All values are SP3D codelist tokens taken directly from the LS1E-A3
    # reference workbook. Override per project by setting environment-driven
    # values in a future config layer — do NOT inline new magic numbers.
    'pcmd_fabrication_type':              '15',   # Shop fabricated
    'pcmd_supply_responsibility':         '2',    # Contractor
    'pcmd_reporting_type':                '5',    # Each
    'pcmd_quantity_reportable_parts':     '1',
    'pcmd_gasket_requirements':           '20',   # Per LS1E-A3 reference
    'pcmd_bolting_requirements':          '35',   # Per LS1E-A3 reference
    'pcmd_welding_requirement':           '5',    # Per LS1E-A3 reference
    # Multisize / second-size dimensions — blank by default; populated only
    # for reducers/multisize fittings (handled per-component in future).
    'pcmd_second_size_from':              '',
    'pcmd_second_size_to':                '',
    'pcmd_second_size_units':             '',
    'pcmd_multisize_option':              '1',    # 1 = single size (SP3D default)
    # Bolt-up / cap-screw substitution — not used for plain piping commodities.
    'pcmd_clamp_requirement':             '0',
    'pcmd_loose_material_requirements':   '0',
    'pcmd_subst_cap_screws_quantity':     '0',
    'pcmd_subst_cap_screw_cntr_code':     '',
    'pcmd_subst_cap_screw_diameter':      '',
    'pcmd_tapped_hole_depth':             '',
    'pcmd_tapped_hole_depth_2':           '',
    'pcmd_cap_screw_engagement_gap':      '',
    # Valve operator block — only applicable to valve commodities.
    'pcmd_multiport_valve_op_req':        '0',
    'pcmd_valve_operator_type':           '0',
    'pcmd_valve_operator_geo_ind_std':    '',
    'pcmd_valve_operator_catalog_part':   '',
    # Reporting / procurement metadata — blank in reference, runtime-tunable.
    'pcmd_part_data_source':              '1',    # 1 = Intergraph standard catalog
    'pcmd_alt_orientation_commodity':     '',
    'pcmd_hyperlink_vendor':              '',
    'pcmd_hyperlink_manuals':             '',
    'pcmd_vendor_part_number':            '',
    'pcmd_manufacturer_part_number':      '',
    'pcmd_alt_reportable_commodity':      '',
    'pcmd_quantity_alt_reportable_parts': '0',
    'pcmd_eclasse_procurement_code':      '',
    'pcmd_unspsc_procurement_code':       '',
    'pcmd_legacy_commodity_code':         '',

    # ── MaterialsData (ASME B31.3 Table A-1 row defaults) ──────────────────────
    # WallThickness band used as the validity envelope for the (grade, temperature,
    # allowable stress) row. SP3D rejects rows where the design wall thickness
    # falls outside [From, To]. Use a broad band so any commercially sized pipe
    # is covered; project-specific tuning can narrow it later.
    'materials_data_wall_thickness_from': '0in',
    'materials_data_wall_thickness_to':   '10in',
    # CoefficientY = ASME B31.3 Y-factor for Barlow formula. 0.4 is the
    # reference value used in LS1E-A3 for ferritic/austenitic steels below 900°F.
    'materials_data_coefficient_y':       '0.4',
    # Fallback allowable stress when pt_rating_table row lacks a value.
    # Format mirrors the LS1E-A3 reference ("15000psig").
    'materials_data_allowable_stress_default': '15000psig',
    # MillTolerance: absolute tolerance (kept blank by the reference — SP3D uses
    # MillTolerancePercentage by default; provide a default for explicit override).
    'materials_data_mill_tolerance':      '',
}


# ─────────────────────────────────────────────────────────────────────────────
# 5c. AUXILIARY SPEC BUILDERS  (one row per class / per class×NPD)
#
# Each builder is intentionally narrow: it emits sensible defaults so every
# SmartPlant 3D SPEC sheet visible in the LS1E-A3 reference template is
# populated for downstream bulkload.  User edits via the canvas
# (`WorkbookCellOverride`) override these values on export.
# ─────────────────────────────────────────────────────────────────────────────
def _rows_standard_notes_data(cls):
    return [{
        'Name':        f'{_spec_name(cls)}-NOTE',
        'Purpose':     SPEC_DEFAULTS['standard_note_purpose'],
        'Description': (cls.raw_notes or SPEC_DEFAULTS['standard_note_name'])[:255],
    }]


def _rows_short_code_hierarchy_rule(cls):
    return [{
        'ShortCodeHierarchyType': SPEC_DEFAULTS['short_code_hierarchy_type'],
        'ShortCode':              SPEC_DEFAULTS['short_code'],
    }]


def _rows_materials_data(cls):
    """One row per unique PT point, anchored to the class's material grade.

    Emits the full MaterialsData schema (10 columns) per the LS1E-A3 reference:
    DesignStandard, MaterialsGrade, Temperature, WallThicknessFrom/To,
    CoefficientY, AllowableStress, MillTolerancePercentage, MillTolerance.
    Every non-derived value comes from SPEC_DEFAULTS — no inline magic numbers.
    """
    rows = []
    grade = cls.material_grade or ''
    design_std    = _normalize_pressure_class(cls.pressure_rating)
    wt_from       = SPEC_DEFAULTS['materials_data_wall_thickness_from']
    wt_to         = SPEC_DEFAULTS['materials_data_wall_thickness_to']
    coef_y        = SPEC_DEFAULTS['materials_data_coefficient_y']
    stress_dflt   = SPEC_DEFAULTS['materials_data_allowable_stress_default']
    mill_tol_pct  = '12.5'   # ASME B36.10 manufacturing tolerance (constant)
    mill_tol      = SPEC_DEFAULTS['materials_data_mill_tolerance']
    for pt in (cls.pt_rating_table or []):
        # Preserve PT-row stress when extractor populated it; otherwise fall back
        # to the soft-coded default so the column is never blank.
        stress_val = pt.get('allowable_stress')
        stress_str = str(stress_val) if stress_val not in (None, '') else stress_dflt
        rows.append({
            'DesignStandard':          design_std,
            'MaterialsGrade':          grade,
            'Temperature':             _fmt_temperature(pt.get('temperature_c')),
            'WallThicknessFrom':       wt_from,
            'WallThicknessTo':         wt_to,
            'CoefficientY':            coef_y,
            'AllowableStress':         stress_str,
            'MillTolerancePercentage': mill_tol_pct,
            'MillTolerance':           mill_tol,
        })
    return rows


def _rows_pipe_branch(cls):
    """One row per (header_npd, branch_npd) pair when fittings include tees."""
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    if not npds:
        return []
    rows = []
    for h in npds:
        for b in npds:
            if b > h:
                continue
            rows.append({
                'SpecName':           _spec_name(cls),
                'HeaderSize':         _normalize_npd(h),
                'BranchSize':         _normalize_npd(b),
                'AngleLow':           SPEC_DEFAULTS['pipe_branch_angle_low'],
                'AngleHigh':          SPEC_DEFAULTS['pipe_branch_angle_high'],
                'HdrSizeNPDUnitType': S3D_DEFAULTS['NpdUnitType'],
                'BrSizeNPDUnitType':  S3D_DEFAULTS['NpdUnitType'],
                'ShortCode':          SPEC_DEFAULTS['pipe_branch_short_code'],
                'SecondaryShortCode': SPEC_DEFAULTS['pipe_branch_secondary_short_code'],
                'TertiaryShortCode':  SPEC_DEFAULTS['pipe_branch_tertiary_short_code'],
            })
    return rows


def _rows_permissible_taps(cls):
    return [{
        'SpecName':             _spec_name(cls),
        'PermissibleTapNumber': SPEC_DEFAULTS['permissible_tap_number'],
        'IsPreferredTap':       SPEC_DEFAULTS['permissible_tap_is_preferred'],
    }]


def _rows_joint_quality_factor(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    if not npds:
        return []
    return [{
        'SpecName':            _spec_name(cls),
        'NominalDiameterFrom': _normalize_npd(min(npds)),
        'NominalDiameterTo':   _normalize_npd(max(npds)),
        'NpdUnitType':         S3D_DEFAULTS['NpdUnitType'],
        'CommodityOption':     SPEC_DEFAULTS['commodity_option_main'],
        'JointQualityFactor':  SPEC_DEFAULTS['joint_quality_factor'],
    }]


def _rows_thickness_data_rule(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    return [
        _thickness_row_for_npd(cls, n)
        for n in npds
    ]


# Soft-coded ASME B36.10 / B31.3 wall-thickness lookup for the LS1E-class spec.
# Values are reference-confirmed from LS1E-A3_SPEC.xlsx (mm).
# Key: nominal pipe diameter (inches, float). Value: dict with
#   'min'      → MinimumThickness (calculated minimum wall, post corrosion allowance)
#   'retire'   → RetirementThickness (corrosion allowance + safety margin)
#   'sched1'   → PreferredSchedule1 (primary pipe schedule at this NPD)
# Override per-project via DB or admin; never hardcode in builders.
THICKNESS_DATA_BY_NPD = {
    0.5:  {'min': '3.73mm', 'retire': '1mm',   'sched1': 'S-80'},
    0.75: {'min': '3.91mm', 'retire': '1mm',   'sched1': 'S-80'},
    1.0:  {'min': '4.55mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    1.5:  {'min': '5.08mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    2.0:  {'min': '5.54mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    3.0:  {'min': '5.49mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    4.0:  {'min': '6.02mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    6.0:  {'min': '7.11mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    8.0:  {'min': '6.35mm', 'retire': '1.5mm', 'sched1': 'S-80'},
    10.0: {'min': '6.35mm', 'retire': '2.3mm', 'sched1': 'S-80'},
    12.0: {'min': '6.35mm', 'retire': '2.8mm', 'sched1': 'S-80'},
    14.0: {'min': '6.35mm', 'retire': '2.8mm', 'sched1': 'S-80'},
    16.0: {'min': '6.35mm', 'retire': '3.1mm', 'sched1': 'S-80'},
    18.0: {'min': '6.35mm', 'retire': '3.1mm', 'sched1': 'S-80'},
    20.0: {'min': '6.35mm', 'retire': '3.1mm', 'sched1': 'S-80'},
    24.0: {'min': '9.53mm', 'retire': '3.1mm', 'sched1': 'S-80'},
    # Above 24" SP3D uses explicit wall-thickness schedules instead of S-series
    26.0: {'min': '7.92mm', 'retire': '3.8mm', 'sched1': '26.0 mm'},
    28.0: {'min': '7.92mm', 'retire': '3.8mm', 'sched1': '26.0 mm'},
    30.0: {'min': '7.92mm', 'retire': '3.8mm', 'sched1': '26.0 mm'},
    32.0: {'min': '7.92mm', 'retire': '3.8mm', 'sched1': '26.0 mm'},
}


def _thickness_row_for_npd(cls, n):
    """Build one ThicknessDataRule row, looking up NPD-specific thickness
    values from THICKNESS_DATA_BY_NPD with safe fallback to SPEC_DEFAULTS."""
    try:
        key = float(n)
    except (TypeError, ValueError):
        key = None
    entry = THICKNESS_DATA_BY_NPD.get(key, {}) if key is not None else {}
    return {
        'SpecName':                   _spec_name(cls),
        'NominalPipingDiameter':      _normalize_npd(n),
        'NominalPipingDiameterUnits': S3D_DEFAULTS['NpdUnitType'],
        'MinimumThickness':           entry.get('min',    SPEC_DEFAULTS['min_thickness']),
        'RetirementThickness':        entry.get('retire', SPEC_DEFAULTS['retirement_thickness']),
        'ThreadThickness':            SPEC_DEFAULTS['thread_thickness'],
        'PreferredSchedule1':         entry.get('sched1', SPEC_DEFAULTS['preferred_schedule']),
        'PreferredSchedule2':         SPEC_DEFAULTS['preferred_schedule_2'],
        'PreferredSchedule3':         SPEC_DEFAULTS['preferred_schedule_3'],
        'PreferredSchedule4':         SPEC_DEFAULTS['preferred_schedule_4'],
        'PreferredSchedule5':         SPEC_DEFAULTS['preferred_schedule_5'],
        'PreferredSchedule6':         SPEC_DEFAULTS['preferred_schedule_6'],
    }


# Soft-coded SP3D bolt-diameter ladder per ASME B16.5 / B18.2.1 — 0.25" to 4"
# in 0.125" increments. Matches the LS1E-A3 NutSelectionFilter reference
# (31 rows). Override per-project by editing this constant or by injecting
# project-specific diameters via env / admin in future.
BOLT_DIAMETERS_INCH = [
    '0.25in',  '0.375in', '0.5in',   '0.625in', '0.75in',  '0.875in', '1in',
    '1.125in', '1.25in',  '1.375in', '1.5in',   '1.625in', '1.75in',  '1.875in',
    '2in',     '2.125in', '2.25in',  '2.375in', '2.5in',   '2.625in', '2.75in',
    '2.875in', '3in',     '3.125in', '3.25in',  '3.375in', '3.5in',   '3.625in',
    '3.75in',  '3.875in', '4in',
]


def _rows_nut_selection_filter(cls):
    """One row per bolt diameter, populating every SP3D NutSelectionFilter
    column with soft-coded defaults. Reference (LS1E-A3) emits 31 rows from
    0.25in to 4in; ContractorCommodityCode is sequential (NUT001…NUT031)."""
    spec_name = _spec_name(cls)
    pr        = _normalize_pressure_class(cls.pressure_rating)
    prefix    = SPEC_DEFAULTS['nut_contractor_commodity_code_prefix']
    rows = []
    for idx, diam in enumerate(BOLT_DIAMETERS_INCH, start=1):
        rows.append({
            'SpecName':                     spec_name,
            'NutOption':                    SPEC_DEFAULTS['nut_option'],
            'MaximumTemperature':           SPEC_DEFAULTS['nut_max_temperature'],
            'BoltType':                     SPEC_DEFAULTS['nut_bolt_type'],
            'BoltDiameter':                 diam,
            'PressureRating':               pr,
            'ContractorCommodityCode':      f'{prefix}{idx:03d}',
            'SupplementaryNutOption':       SPEC_DEFAULTS['nut_supplementary_nut_option'],
            'SupplNutCntrCommodityCode':    SPEC_DEFAULTS['nut_suppl_cntr_commodity_code'],
            'FabricationCategoryOverride':  SPEC_DEFAULTS['nut_fabrication_category_override'],
            'SupplyResponsibilityOverride': SPEC_DEFAULTS['nut_supply_responsibility_override'],
            'Comments':                     SPEC_DEFAULTS['nut_comments'],
            'PipingNote1':                  SPEC_DEFAULTS['nut_piping_note_1'],
        })
    return rows


def _infer_fluid_from_spec_name(spec_name: str) -> str:
    """Soft-coded prefix→fluid lookup. Returns SPEC_DEFAULTS['default_fluid_code']
    if no prefix matches. Longest-prefix-wins so 'AN3' beats 'A'."""
    if not spec_name:
        return SPEC_DEFAULTS['default_fluid_code']
    sn = spec_name.upper().strip()
    # Strip 'PIPING SPEC: ' prefix if present
    if ':' in sn:
        sn = sn.split(':', 1)[1].strip()
    # Longest-prefix-wins
    for prefix in sorted(SPEC_NAME_FLUID_PREFIX_MAP.keys(), key=len, reverse=True):
        if sn.startswith(prefix):
            return SPEC_NAME_FLUID_PREFIX_MAP[prefix]
    return SPEC_DEFAULTS['default_fluid_code']


# Soft-coded prefix→fluid map for AllowablePipingMaterialsClass FluidCode
# inference when a piping class has no extracted service_list. Project-tunable
# without code change. Codes match common Rejlers/ADNOC service naming.
SPEC_NAME_FLUID_PREFIX_MAP = {
    # Air services
    'AA':  'Instrument Air',
    'AC':  'Compressed Air',
    'AF':  'Filtered Air',
    'AG':  'Plant Air',
    'AJ':  'Jacket Air',
    'AS':  'Service Air',
    # Nitrogen services
    'AN':  'Nitrogen',
    'N':   'Nitrogen',
    # Water services
    'CW':  'Cooling Water',
    'PW':  'Potable Water',
    'FW':  'Fire Water',
    'BFW': 'Boiler Feed Water',
    'DW':  'Demineralised Water',
    'RW':  'Raw Water',
    # Steam
    'HPS': 'High Pressure Steam',
    'MPS': 'Medium Pressure Steam',
    'LPS': 'Low Pressure Steam',
    'ST':  'Steam',
    # Hydrocarbons
    'HC':  'Hydrocarbon',
    'NG':  'Natural Gas',
    'FG':  'Fuel Gas',
    'LPG': 'LPG',
    'CO':  'Crude Oil',
    'DO':  'Diesel Oil',
    # Flare / vent
    'FL':  'Flare',
    'VT':  'Vent',
    # Generic process / utility (LS1E project codes start with 'A' → utility-air family)
    'A':   'Utility Air',
    'B':   'Process Fluid',
    'C':   'Chemical',
    'D':   'Drain',
}


# ── Soft-coded prefix → numeric SP3D FluidCode ID map ───────────────────
# SP3D bulkload sheets like InsideSurfaceTreatment require a NUMERIC
# FluidCode (e.g. 521 in LS1E reference) rather than a fluid name.
# These IDs come from the project's SP3D FluidCode catalog. Keep this
# map aligned with the customer's master list; add new prefixes as
# additional specs are imported.  Longest-prefix-wins (same as the name
# map above).  Anything unmatched falls back to
# SPEC_DEFAULTS['numeric_fluid_code'].
SPEC_NAME_NUMERIC_FLUID_MAP = {
    # LS-prefixed specs (low-pressure service) — LS1E reference uses 521
    'LS':  '521',
    # Common Rejlers/ADNOC numeric IDs (placeholders aligned to LS1E
    # family; override per project via SP3D FluidCode master).
    'HS':  '521',
    'MS':  '521',
    # Hydrocarbon / process families default to the same generic ID until
    # the customer provides an authoritative FluidCode map.
    'HC':  '521',
    'NG':  '521',
    'FG':  '521',
}


def _infer_numeric_fluid_from_spec_name(spec_name: str) -> str:
    """Soft-coded prefix → numeric FluidCode ID.  Used by sheets whose
    SP3D bulkload column expects a numeric FluidCode (e.g.
    InsideSurfaceTreatment in the LS1E reference uses 521).  Falls back to
    SPEC_DEFAULTS['numeric_fluid_code'] for unmapped prefixes."""
    default = SPEC_DEFAULTS['numeric_fluid_code']
    if not spec_name:
        return default
    sn = str(spec_name).upper().strip()
    if ':' in sn:
        sn = sn.split(':', 1)[1].strip()
    for prefix in sorted(SPEC_NAME_NUMERIC_FLUID_MAP.keys(), key=len, reverse=True):
        if sn.startswith(prefix):
            return SPEC_NAME_NUMERIC_FLUID_MAP[prefix]
    return default


def _rows_allowable_piping_materials_class(cls):
    """Emit one FluidCode row per service. Smart fallback chain ensures the
    column is NEVER blank: explicit service_list → inferred-from-spec-name →
    SPEC_DEFAULTS['default_fluid_code']. Deduplicates within a single class."""
    spec_name = _spec_name(cls)
    services  = [s for s in (cls.service_list or []) if s and str(s).strip()]
    if not services:
        services = [_infer_fluid_from_spec_name(spec_name)]
    # Dedup while preserving order
    seen, ordered = set(), []
    for s in services:
        key = str(s).strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            ordered.append(key)
    return [{'SpecName': spec_name, 'FluidCode': s} for s in ordered]


def _rows_bend_angles(cls):
    """Emit BendAngles only when the class actually has elbow fittings."""
    has_elbow = any(
        route_component_to_cat_sheet(c) in ('90DegElbow', '90DegLRElbow',
                                            '45DegElbow', '45DegLRElbow')
        for c in cls.components.all()
    )
    if not has_elbow:
        return []
    npds = sorted({n for c in cls.components.all()
                   if route_component_to_cat_sheet(c) and
                      'Elbow' in route_component_to_cat_sheet(c)
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    rows = []
    for n in npds:
        for a in SPEC_DEFAULTS['bend_angles']:
            rows.append({
                'SpecName':    _spec_name(cls),
                'Npd':         _normalize_npd(n),
                'NpdUnitType': S3D_DEFAULTS['NpdUnitType'],
                'BendAngle':   a,
            })
    return rows


def _rows_inside_surface_treatment(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    if not npds:
        return []
    spec_name = _spec_name(cls)
    # FluidCode: numeric SP3D ID resolved from spec_name prefix
    # (SPEC_NAME_NUMERIC_FLUID_MAP). LS1E reference template populates
    # this column for every IST row — never blank.
    fluid_code = _infer_numeric_fluid_from_spec_name(spec_name)
    return [{
        'SpecName':                       spec_name,
        'NominalPipingDiameterFrom':      _normalize_npd(min(npds)),
        'NominalPipingDiameterTo':        _normalize_npd(max(npds)),
        'NominalPipingDiameterUnits':     S3D_DEFAULTS['NpdUnitType'],
        'FluidCode':                      fluid_code,
        'CoatingType':                    SPEC_DEFAULTS['coating_type'],
        'InsideSurfaceTreatment':         SPEC_DEFAULTS['inside_surface_treatment'],
    }]


def _rows_outside_surface_treatment(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    if not npds:
        return []
    return [{
        'SpecName':                       _spec_name(cls),
        'NominalPipingDiameterFrom':      _normalize_npd(min(npds)),
        'NominalPipingDiameterTo':        _normalize_npd(max(npds)),
        'NominalPipingDiameterUnits':     S3D_DEFAULTS['NpdUnitType'],
        'EnvironmentalZone':              SPEC_DEFAULTS['environmental_zone'],
        'CoatingType':                    SPEC_DEFAULTS['coating_type'],
        'OutsideSurfaceTreatment':        SPEC_DEFAULTS['outside_surface_treatment'],
    }]


def _rows_minimum_pipe_length_rule_per_spec(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    return [
        {
            'SpecName':                  _spec_name(cls),
            'Npd':                       _normalize_npd(n),
            'NpdUnitType':               S3D_DEFAULTS['NpdUnitType'],
            'MinimumPipeLength':         SPEC_DEFAULTS['min_pipe_length'],
            'PreferredMinimumPipeLength':SPEC_DEFAULTS['preferred_min_pipe_length'],
        }
        for n in npds
    ]


def _rows_min_pipe_length_purchase_per_spec(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    return [
        {
            'SpecName':                   _spec_name(cls),
            'NominalPipingDiameter':      _normalize_npd(n),
            'NominalPipingDiameterUnits': S3D_DEFAULTS['NpdUnitType'],
            'PurchaseLength':             SPEC_DEFAULTS['purchase_length'],
            'MinimumPipeLength':          SPEC_DEFAULTS['min_pipe_length'],
            'PreferredMinimumPipeLength': SPEC_DEFAULTS['preferred_min_pipe_length'],
        }
        for n in npds
    ]


def _rows_pipe_takedown_parts(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    return [
        {
            'SpecName':         _spec_name(cls),
            'TakeDownShortCode':SPEC_DEFAULTS['takedown_short_code'],
            'WeldShortCode':    SPEC_DEFAULTS['weld_short_code'],
            'IsPairRequired':   SPEC_DEFAULTS['is_pair_required'],
            'Npd':              _normalize_npd(n),
            'NpdUnitType':      S3D_DEFAULTS['NpdUnitType'],
            'IsWeld':           SPEC_DEFAULTS['is_weld'],
        }
        for n in npds
    ]


def _rows_port_alignment_per_spec(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    if not npds:
        return []
    return [{
        'SpecName':                       _spec_name(cls),
        'NominalPipingDiameterFrom':      _normalize_npd(min(npds)),
        'NominalPipingDiameterTo':        _normalize_npd(max(npds)),
        'NominalPipingDiameterUnits':     S3D_DEFAULTS['NpdUnitType'],
        'EndPreparation':                 S3D_DEFAULTS['EndPrep_BW'],
        'MethodOfTrimming':               SPEC_DEFAULTS['port_method_of_trimming'],
        'AcceptableAlignmentTolerance':   SPEC_DEFAULTS['port_alignment_tolerance'],
    }]


def _rows_weld_clearance_rule(cls):
    npds = sorted({n for c in cls.components.all()
                   for n in _enumerate_npds(c.size_from, c.size_to)})
    if not npds:
        return []
    return [{
        'SpecName':                       _spec_name(cls),
        'NominalPipingDiameterFrom':      _normalize_npd(min(npds)),
        'NominalPipingDiameterTo':        _normalize_npd(max(npds)),
        'NominalPipingDiameterUnits':     S3D_DEFAULTS['NpdUnitType'],
        'WeldClass':                      SPEC_DEFAULTS['weld_class'],
        'WeldClearanceRadiusIncrease':    SPEC_DEFAULTS['weld_clearance_radius_increase'],
        'WeldClearanceLength':            SPEC_DEFAULTS['weld_clearance_length'],
    }]


# ─────────────────────────────────────────────────────────────────────────────
# PipingCommodityFilter (PCF) — canonical SPEC↔CAT join sheet
# ─────────────────────────────────────────────────────────────────────────────
# PCF is the master commodity index referenced by every other SP3D bulkload
# sheet. The reference LS1E-A3 workbook carries 769 rows here (the largest
# SPEC sheet) and the join key — `CommodityCode` — is also embedded in the
# CAT workbook's Definition rows. Without a builder, PCF showed up in the
# Spec Customization canvas only as the LS1E template scaffold, so the
# extracted classes were invisible. This builder emits one row per
# (component × NPD) using the SAME `icc` recipe as PCMD, which guarantees
# the SPEC↔CAT cross-link is consistent across both sheets.
#
# Side columns (FluidCode, BendRadius, MaximumTemperature, etc.) are filled
# automatically by the existing PIPING_COMMODITY_FILTER_DEFAULTS autofill
# pass — this builder only owns the identification + sizing + materials
# fields that come straight from the extracted spec.
# ─────────────────────────────────────────────────────────────────────────────

# Soft-coded resolver: (component_type, sub_type keyword) → SP3D ShortCode.
# Calibrated against the LS1E-A3 reference (PCF.ShortCode column). Keep
# narrow keywords first; first match wins. Extend by adding a tuple — no
# core-logic changes required.
PCF_SHORT_CODE_RULES = [
    # (component_type, sub_type/description keyword (lowercase), ShortCode)
    ('pipe',    '',                'Piping'),
    ('flange',  'blind',           'Blind Flange'),
    ('flange',  'weld neck',       'Weld Neck Flange'),
    ('flange',  'weldneck',        'Weld Neck Flange'),
    ('flange',  'wn',              'Weld Neck Flange'),
    ('flange',  '',                'Weld Neck Flange'),
    ('fitting', '90 lr',           '90 Deg LR Elbow'),
    ('fitting', '90 long',         '90 Deg LR Elbow'),
    ('fitting', '45 lr',           '45 Deg LR Elbow'),
    ('fitting', '45 long',         '45 Deg LR Elbow'),
    ('fitting', '90',              '90 Deg Elbow'),
    ('fitting', '45',              '45 Deg Elbow'),
    ('fitting', 'elbow',           '90 Deg Elbow'),
    ('fitting', 'reducing tee',    'Reducing Tee'),
    ('fitting', 'red tee',         'Reducing Tee'),
    ('fitting', 'tee',             'Tee'),
    ('fitting', 'eccentric',       'Eccentric Reducer'),
    ('fitting', 'concentric',      'Concentric Reducer'),
    ('fitting', 'reducer',         'Concentric Reducer'),
    ('fitting', 'swage',           'Concentric Swage'),
    ('fitting', 'cap',             'Cap'),
    ('fitting', 'weldolet',        'Weldolet'),
    ('fitting', 'sockolet',        'Weldolet'),
    ('fitting', 'thredolet',       'Weldolet'),
    ('fitting', 'olet',            'Weldolet'),
    ('fitting', 'coupling',        'Coupling'),
    ('fitting', 'nipple',          'Nipple'),
    ('fitting', 'paddle',          'Paddle'),
    ('fitting', 'spec blind',      'Paddle'),
    ('fitting', 'spectacle',       'Paddle'),
    ('fitting', '',                'Pipe Fitting'),
    ('valve',   'gate',            'Gate Valve'),
    ('valve',   'globe',           'Globe Valve'),
    ('valve',   'needle',          'Globe Valve'),
    ('valve',   'check',           'Check Valve'),
    ('valve',   'nrv',             'Check Valve'),
    ('valve',   '',                'Valve'),
    ('gasket',  '',                'Gasket'),
    ('bolt',    '',                'Stud Bolt'),
]


def _pcf_short_code_for_component(c) -> str:
    """Soft-coded ShortCode resolver — SPEC↔CAT class-level join key.

    Closes the audit gap where SPEC.ShortCode ('Gate Valve') had to map to
    CAT.<sheet> ('GateValve'). Both sides now derive from the same rule
    set, eliminating the whitespace/casing drift.
    """
    ctype = (c.component_type or '').strip().lower()
    blob  = f'{c.sub_type or ""} {c.description or ""}'.lower()
    for rule_type, kw, short_code in PCF_SHORT_CODE_RULES:
        if rule_type != ctype:
            continue
        if not kw or kw in blob:
            return short_code
    return 'Pipe Fitting'  # final fallback — never blank


def _rows_piping_commodity_filter(cls):
    """Emit one PCF row per (component × NPD).

    Identification + sizing + materials are taken from the extracted spec;
    the 25+ side columns (FluidCode, MaxTemp, BendRadius, etc.) are filled
    automatically by `PIPING_COMMODITY_FILTER_DEFAULTS` so this builder
    stays small and the side defaults remain editable from a single place.
    """
    rows = []
    spec_name = _spec_name(cls)
    cls_token = _safe_class_token(cls)
    pressure  = _normalize_pressure_class(cls.pressure_rating)
    fluid_code = _infer_fluid_from_spec_name(spec_name) if '_infer_fluid_from_spec_name' in globals() else ''

    for c in cls.components.all().order_by('display_order'):
        sheet = route_component_to_cat_sheet(c)
        prefix = (CAT_SHEET_DEFAULTS.get(sheet, {}) or {}).get('icc_prefix', 'RAD_CMP')
        npds = _enumerate_npds(c.size_from, c.size_to)
        if not npds:
            continue
        short_code = _pcf_short_code_for_component(c)
        end_prep   = _normalize_end_prep(c.end_connection) or S3D_DEFAULTS['EndPrep_BW']
        sched      = c.schedule_or_rating or ''
        # Optional commodity_code attribute (set at runtime by extractor on some
        # components) — fall back to the deterministic ICC so the column is
        # never blank and the SPEC↔CAT cross-link stays intact.
        commodity_code_override = (getattr(c, 'commodity_code', '') or '').strip()

        for npd in npds:
            icc = f'{prefix}_{cls_token}_{_npd_token(npd)}'
            first_size = _normalize_npd(npd)
            rows.append({
                # ── Identification (canonical SPEC↔CAT join key) ──────────
                'SpecName':            spec_name,
                'CommodityCode':       commodity_code_override or icc,
                'ShortCode':           short_code,
                # ── First-size range (always same value for fixed-size parts;
                # size-reducing components are flagged by autofill via
                # _pcf_short_code_is_size_reducer to populate SecondSize*).
                'FirstSizeFrom':       first_size,
                'FirstSizeTo':         first_size,
                'FirstSizeUnits':      S3D_DEFAULTS['NpdUnitType'],
                # ── End preparation + rating (the per-row spec rule) ──────
                'EndPreparation':      end_prep,
                'EndStandard':         S3D_DEFAULTS['EndStandard'],
                'Rating1':             pressure,
                'PressureRating':      pressure,
                'ScheduleThickness':   sched,
                # ── Materials ─────────────────────────────────────────────
                'MaterialsGrade':      cls.material_grade or '',
                'MaterialGrade':       cls.material_grade or '',
                'GeometricIndustryStandard': c.material_standard or '',
                # ── Fluid (mirrors GasketSelectionFilter inference) ───────
                'FluidCode':           fluid_code or 'Process Fluid',
            })
    return rows


# Sheet → list of builders. Each builder yields zero-or-more rows per class.
# Order here also defines the order sheets appear in the workbook canvas.
SPEC_SHEET_BUILDERS = {
    # Core (data driven from the AI extractor) ─────────────────────────────
    'PipingCommodityFilter':           [_rows_piping_commodity_filter],
    'PipingMaterialsClassData':        [_rows_piping_materials_class_data],
    'ServiceLimits':                   [_rows_service_limits],
    'CorrosionAllowance':              [_rows_corrosion_allowance],
    'PipeNominalDiameters':            [_rows_pipe_nominal_diameters],
    'PipingCommodityMatlControlData':  [_rows_piping_commodity_matl_control_data],
    'GasketSelectionFilter':           [_rows_gasket_selection_filter],
    'BoltSelectionFilter':             [_rows_bolt_selection_filter],
    # Auxiliary (project defaults — soft-coded via SPEC_DEFAULTS) ──────────
    'StandardNotesData':               [_rows_standard_notes_data],
    'ShortCodeHierarchyRule':          [_rows_short_code_hierarchy_rule],
    'MaterialsData':                   [_rows_materials_data],
    'PipeBranch':                      [_rows_pipe_branch],
    'PermissibleTaps':                 [_rows_permissible_taps],
    'JointQualityFactor':              [_rows_joint_quality_factor],
    'ThicknessDataRule':               [_rows_thickness_data_rule],
    'NutSelectionFilter':              [_rows_nut_selection_filter],
    'AllowablePipingMaterialsClass':   [_rows_allowable_piping_materials_class],
    'BendAngles':                      [_rows_bend_angles],
    'InsideSurfaceTreatment':          [_rows_inside_surface_treatment],
    'OutsideSurfaceTreatment':         [_rows_outside_surface_treatment],
    'MinimumPipeLengthRulePerSpec':    [_rows_minimum_pipe_length_rule_per_spec],
    'MinPipeLengthPurchasePerSpec':    [_rows_min_pipe_length_purchase_per_spec],
    'PipeTakedownParts':               [_rows_pipe_takedown_parts],
    'PortAlignmentPerSpec':            [_rows_port_alignment_per_spec],
    'WeldClearanceRule':               [_rows_weld_clearance_rule],
}


# ─────────────────────────────────────────────────────────────────────────────
# 6. CAT — component routing
#
# Each rule: (component_type, regex_against_sub_type+description, target_sheet)
# First matching rule wins.  Matching is case-insensitive.
# Ordered narrow→generic (LR before generic, blind before weld-neck,
# reducing-tee before tee, ecc/conc reducers before swage).
# ─────────────────────────────────────────────────────────────────────────────
_CAT_ROUTING_RULES = [
    # Pipe ────────────────────────────────────────────────────────────────
    ('pipe',    r'.*',                                                            'PipeStock'),

    # Flange ──────────────────────────────────────────────────────────────
    ('flange',  r'(?i)\b(blind|bld|blnd)\b',                                      'BlindFlange'),
    ('flange',  r'(?i)\b(wn|weld[\s\-]?neck|weldneck)\b',                         'WeldNeckFlange'),
    ('flange',  r'.*',                                                            'WeldNeckFlange'),

    # Fitting ─────────────────────────────────────────────────────────────
    ('fitting', r'(?i)\b90.*long.*radius\b|\b90.*\bl[\s\.]*r\.?',                  '90DegLRElbow'),
    ('fitting', r'(?i)\b45.*long.*radius\b|\b45.*\bl[\s\.]*r\.?',                  '45DegLRElbow'),
    ('fitting', r'(?i)(elbow|ell)\W*90\b|\b90\W*deg.*(elbow|ell)\b|\b90\b.*elbow', '90DegElbow'),
    ('fitting', r'(?i)(elbow|ell)\W*45\b|\b45\W*deg.*(elbow|ell)\b|\b45\b.*elbow', '45DegElbow'),
    ('fitting', r'(?i)reducing\s*tee|red\.?\s*tee|red\b.*tee\b',                  'ReducingTee'),
    ('fitting', r'(?i)\b(tee|t-piece)\b',                                         'Tee'),
    ('fitting', r'(?i)concentric\s*reducer|conc\.?\s*red|conc\b.*reducer',        'ConcentricReducer'),
    ('fitting', r'(?i)eccentric\s*reducer|ecc\.?\s*red|ecc\b.*reducer',           'EccentricReducer'),
    ('fitting', r'(?i)\bswage|swg\b',                                             'ConcentricSwage'),
    ('fitting', r'(?i)\bcap\b',                                                   'Cap'),
    ('fitting', r'(?i)weldolet|wol\b|sockolet|sol\b|thredolet|tol\b|olet',        'Weldolet'),
    ('fitting', r'(?i)coupling',                                                  'Coupling'),
    ('fitting', r'(?i)nipple',                                                    'Nipple'),
    ('fitting', r'(?i)paddle|spec\s*blind|spectacle|spacer\s*ring',               'Paddle'),
    # No fitting fallback — leave un-routable fittings in the unrouted log.

    # Valve ───────────────────────────────────────────────────────────────
    ('valve',   r'(?i)\bgate\b',                                                  'GateValve'),
    ('valve',   r'(?i)\b(globe|needle)\b',                                        'GlobeValve'),
    ('valve',   r'(?i)\bcheck\b|nrv|non[\s\-]?return',                            'CheckValve'),
    # No valve fallback.

    # Gasket / Bolt ───────────────────────────────────────────────────────
    ('gasket',  r'.*',                                                            'GasketPartData'),
    ('bolt',    r'.*',                                                            'BoltPartData'),
]


def _bucket_component_type(component) -> str:
    """Collapse free-text `component_type` (e.g. 'elbow 90 deg lr',
    'slip on flange', 'flanges blind') into a canonical bucket so routing rules
    can match.  Order matters: more specific keywords first."""
    blob = f'{(component.component_type or "")} {(component.sub_type or "")} {(component.description or "")}'.lower()
    # gasket / bolt first (avoid 'bolt-on flange' confusing flange match)
    if any(k in blob for k in ('gasket', 'gskt')):
        return 'gasket'
    if any(k in blob for k in ('bolt', 'stud bolt', 'machine bolt')) and 'bolted' not in blob:
        return 'bolt'
    if any(k in blob for k in ('valve', 'nrv', 'non-return', 'non return')):
        return 'valve'
    if any(k in blob for k in ('flange', 'flng')):
        return 'flange'
    if any(k in blob for k in ('elbow', 'ell ', ' tee', 'reducer', 'reducing', 'cap', 'olet',
                                'coupling', 'nipple', 'swage', 'paddle', 'spec blind',
                                'spectacle', 'spacer', 'fitting', 'union')):
        return 'fitting'
    if any(k in blob for k in ('pipe', 'tube')):
        return 'pipe'
    return (component.component_type or '').strip().lower()


def route_component_to_cat_sheet(component) -> str | None:
    """Return the CAT sheet name for a component, or None if no rule matches."""
    bucket = _bucket_component_type(component)
    sub_text = f'{component.component_type or ""} {component.sub_type or ""} {component.description or ""}'
    for ctype, pattern, sheet in _CAT_ROUTING_RULES:
        if bucket != ctype:
            continue
        if re.search(pattern, sub_text):
            return sheet
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 7. CAT BUILDERS — per-component, multi-NPD row generators
#
#  Each builder returns `list[dict]` (one dict per generated row) so the
#  exporter can emit 1..N rows per component (one per NPD in range).
# ─────────────────────────────────────────────────────────────────────────────
def _common_part_row(cls, comp, sheet_name, npd1, npd2=None, npd3=None) -> dict:
    """Shared field set for all CAT 'commodity part' sheets.

    Includes BOTH bracketed (`Npd[1]`) and bracket+suffixed (`Npd[1]:Primary`)
    column-name variants — only whichever matches the template's actual header
    is written; the rest are silently dropped by the writer."""
    d = CAT_SHEET_DEFAULTS.get(sheet_name, {}) or {}
    cls_token = _safe_class_token(cls)
    suffix_parts = [_npd_token(n) for n in (npd1, npd2, npd3) if n is not None]
    icc = f'{d.get("icc_prefix", "RAD_CMP")}_{cls_token}_{"_".join(suffix_parts)}'
    end_prep = _normalize_end_prep(comp.end_connection) or S3D_DEFAULTS['EndPrep_BW']
    sched = comp.schedule_or_rating or 'STD'
    pressure = _normalize_pressure_class(cls.pressure_rating)
    primary_lbl   = d.get('primary_label', '')
    secondary_lbl = d.get('secondary_label', '')

    row: dict = {
        'IndustryCommodityCode':         icc,
        'CommodityType':                 d.get('commodity_type') or '',
        'GeometryType':                  d.get('geometry_type') or '',
        'GraphicalRepresentationOrNot':  S3D_DEFAULTS['GraphicalRep'],
        'SymbolDefinition':              d.get('symbol_def') or '',
        'MaterialGrade':                 cls.material_grade or '',
        'GeometricIndustryStandard':     _resolve_gis_code(sheet_name, comp.material_standard),
        # Port 1 (always present)
        'PipingPointBasis[1]':           S3D_DEFAULTS['PipingPointBasis'],
        'PressureRating[1]':             pressure,
        'EndPreparation[1]':             end_prep,
        'EndStandard[1]':                S3D_DEFAULTS['EndStandard'],
        'ScheduleThickness[1]':          sched,
        'FlowDirection[1]':              S3D_DEFAULTS['FlowDirection_Bidir'],
        'Npd[1]':                        _normalize_npd(npd1),
        f'Npd[1]{primary_lbl}':          _normalize_npd(npd1),
        'NpdUnitType[1]':                S3D_DEFAULTS['NpdUnitType'],
        'PipingNote1':                   (comp.notes or '')[:255],
    }
    if d.get('bend_angle'):
        row['BendAngle'] = d['bend_angle']

    # Port 2 (if applicable)
    if npd2 is not None and d.get('ports', 1) >= 2:
        row.update({
            'PipingPointBasis[2]':       S3D_DEFAULTS['PipingPointBasis'],
            'PressureRating[2]':         pressure,
            'EndPreparation[2]':         end_prep,
            'EndStandard[2]':            S3D_DEFAULTS['EndStandard'],
            'ScheduleThickness[2]':      sched,
            'FlowDirection[2]':          S3D_DEFAULTS['FlowDirection_Bidir'],
            'Npd[2]':                    _normalize_npd(npd2),
            # LS1E reference: PipeStock uses ':Secondary' on Npd[2], Tee uses
            # ':Primary' on Npd[2]. Emit both bare + suffixed; writer keeps
            # whichever header the template actually carries.
            f'Npd[2]{secondary_lbl}':    _normalize_npd(npd2),
            'NpdUnitType[2]':            S3D_DEFAULTS['NpdUnitType'],
        })

    # Port 3 (Tee / ReducingTee — secondary branch)
    if npd3 is not None and d.get('ports', 1) >= 3:
        row.update({
            'PipingPointBasis[3]':       S3D_DEFAULTS['PipingPointBasis'],
            'PressureRating[3]':         pressure,
            'EndPreparation[3]':         end_prep,
            'EndStandard[3]':            S3D_DEFAULTS['EndStandard'],
            'ScheduleThickness[3]':      sched,
            'FlowDirection[3]':          S3D_DEFAULTS['FlowDirection_Bidir'],
            'Npd[3]':                    _normalize_npd(npd3),
            'Npd[3]:Secondary':          _normalize_npd(npd3),
            'NpdUnitType[3]':            S3D_DEFAULTS['NpdUnitType'],
        })

    # Single-port shapes (BlindFlange, Cap) → unidirectional flow.
    if d.get('ports', 1) == 1:
        row['FlowDirection[1]'] = S3D_DEFAULTS['FlowDirection_OutOnly']

    # Sheet-specific constants (PartDataBasis, ManufacturingMethod, valve
    # trim/model identifiers, SymbolIcon, etc.) — soft-coded per LS1E-A3
    # reference. Merged as fill-if-blank so extractor-derived values keep
    # winning over the static defaults.
    merge_fill_blank(row, cat_sheet_constants(sheet_name, cls, comp))

    return row


def _build_cat_rows(cls, comp, sheet_name) -> list[dict]:
    """Multi-NPD row generator used by every commodity-part CAT sheet."""
    d = CAT_SHEET_DEFAULTS.get(sheet_name, {}) or {}
    ports = d.get('ports', 1)
    npds = _enumerate_npds(comp.size_from, comp.size_to)
    if not npds:
        return []
    rows: list[dict] = []
    for npd in npds:
        if ports == 1:
            rows.append(_common_part_row(cls, comp, sheet_name, npd))
        elif ports == 2:
            rows.append(_common_part_row(cls, comp, sheet_name, npd, npd))
        elif ports == 3:
            # Tee: run × run × branch.  When branch size is not separately
            # captured, default branch = run (gets refined by extractor later).
            rows.append(_common_part_row(cls, comp, sheet_name, npd, npd, npd))
    return rows


def _build_gasket_part_rows(cls, comp) -> list[dict]:
    d = CAT_SHEET_DEFAULTS['GasketPartData']
    cls_token = _safe_class_token(cls)
    npds = _enumerate_npds(comp.size_from, comp.size_to)
    if not npds:
        return []
    rows = []
    sheet_constants = cat_sheet_constants('GasketPartData', cls, comp)
    for npd in npds:
        icc = f'{d["icc_prefix"]}_{cls_token}_{_npd_token(npd)}'
        row = {
            'IndustryCommodityCode':  icc,
            'NominalDiameterFrom':    _normalize_npd(npd),
            'NominalDiameterTo':      _normalize_npd(npd),
            'NominalDiameter':        _normalize_npd(npd),
            'NpdUnitType':            S3D_DEFAULTS['NpdUnitType'],
            'GasketType':             comp.sub_type or 'SpiralWound',
            'GasketIndustryStandard': comp.material_standard or 'ASME B16.20',
            'MaterialsGrade':         cls.material_grade or '',
            'FlangeFacing':           cls.flange_facing or 'RF',
            'MaximumPressure':        _normalize_pressure_class(cls.pressure_rating),
        }
        # Sheet-level constants (ThicknessFor3DModel, ProcurementThickness,
        # etc.) — soft-coded in CAT_DIMENSIONAL_FIELDS, per LS1E-A3 reference.
        merge_fill_blank(row, sheet_constants)
        rows.append(row)
    return rows


def _build_bolt_part_rows(cls, comp) -> list[dict]:
    d = CAT_SHEET_DEFAULTS['BoltPartData']
    cls_token = _safe_class_token(cls)
    npds = _enumerate_npds(comp.size_from, comp.size_to)
    if not npds:
        return []
    rows = []
    sheet_constants = cat_sheet_constants('BoltPartData', cls, comp)
    for npd in npds:
        icc = f'{d["icc_prefix"]}_{cls_token}_{_npd_token(npd)}'
        row = {
            'IndustryCommodityCode':     icc,
            'BoltType':                  comp.sub_type or 'Stud',
            'GeometricIndustryStandard': _resolve_gis_code('BoltPartData', comp.material_standard),
            'MaterialsGrade':            cls.material_grade or '',
            'CoatingType':               '0',
        }
        # Sheet-level constants merged from CAT_DIMENSIONAL_FIELDS.
        merge_fill_blank(row, sheet_constants)
        rows.append(row)
    return rows


# Sheet → builder.  All commodity-part sheets share the same multi-NPD builder;
# gasket and bolt sheets have specialised builders.
def _make_cat_builder(sheet_name):
    return lambda cls, comp: _build_cat_rows(cls, comp, sheet_name)


CAT_SHEET_BUILDERS = {sn: _make_cat_builder(sn) for sn in CAT_SHEET_DEFAULTS
                      if sn not in ('GasketPartData', 'BoltPartData')}
CAT_SHEET_BUILDERS['GasketPartData'] = _build_gasket_part_rows
CAT_SHEET_BUILDERS['BoltPartData']   = _build_bolt_part_rows
