"""
Transformer Datasheet Schema — Soft-Coded Section/Field Definitions

Mirrors the structure of the ADNOC / Borouge "Technical Data Sheet for
Transformer (Power and Distribution)" template (DS-13-574-EP-00001.xlsm).

The schema covers BOTH variants:
    • Distribution Transformer  (e.g. 1250 kVA, 11/0.433 kV)
    • Power Transformer         (e.g. 25 MVA,  33/11.5 kV)

Sections that exist only on the power-transformer variant are marked with
``variants={"power"}``.  Sections that exist only on the distribution variant
are marked with ``variants={"distribution"}``.  Items without ``variants``
are common to both.

Columns (matching the xlsm layout):
    sr_no | description | unit | required_data (SPECIFIED DESIGN DATA) |
    vendor_data (filled by vendor / extracted from upload) | rev (L/N/P)

Document-level constants (header block) and sheet metadata are also exported
so the Excel renderer can reproduce the original cover-style layout.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
#  Document header constants
# ─────────────────────────────────────────────────────────────────────────────
DOC_HEADER = {
    "company_name":        "Abu Dhabi Polymers Company Ltd. (Borouge)",
    "location":            "RUWAIS - U.A.E.",
    "project_title":       "EPC FOR BOROUGE EU3 H2 EXTRACTION UNIT PROJECT AGREEMENT NO. 4700002115",
    "document_title":      "DOCUMENT TITLE: TECHNICAL DATA SHEET FOR TRANSFORMER (POWER AND DISTRIBUTION)",
    "company_doc_label":   "COMPANY DOCUMENT NUMBER",
    "company_doc_default": "DS-13-574-EP-00001",
    "contractor_label":    "CONTRACTOR DRAWING NUMBER",
    "contractor_default":  "PA-31011-DS-13-574-EP-00001",
    "rejlers_label":       "REJLERS DRAWING NUMBER",
    "rejlers_default":     "5900863-EL-DAT-0001",
}

# Column headers for the body table
TABLE_HEADERS = ["Sl. No.", "DESCRIPTION", "UNIT", "SPECIFIED DESIGN DATA", "VENDOR DATA", "Rev"]
TABLE_COL_WIDTHS = [9, 56, 11, 38, 30, 7]

# Rev codes used in the template (Legacy / New / Project-specific / Hold)
REV_CODES = {"L": "Legacy", "N": "New", "P": "Project", "H": "Hold"}

# Variant tags
VARIANT_POWER = "power"           # 25 MVA, 33/11.5 kV
VARIANT_DISTRIBUTION = "distribution"  # 1250 kVA, 11/0.433 kV

# Default project info per variant (matches the supplied xlsm samples)
VARIANT_DEFAULTS = {
    VARIANT_POWER: {
        "title_line":       "25MVA, 33/11.5 kV POWER TRANSFORMER",
        "title_field":      "33/11.5 kV POWER TRANSFORMER",
        "rating":           "25",
        "rating_unit":      "MVA",
        "primary_voltage":  "33",
        "secondary_voltage_no_load": "11.5",
        "secondary_voltage_full_load": "11",
        "vector_group":     "Dyn11",
        "criticality":      "1",
        "inspection_class": "1",
        "tank_type":        "WITH CONSERVATOR",
        "earthing_system":  "RESISTIVE",
        "neutral_in_box":   "YES",
        "tolerance_imp":    "-7.5%",
        "primary_sc_rating":"40",
        "primary_pfwv":     "70",
        "secondary_pfwv":   "28",
        "primary_iwv":      "170",
        "secondary_iwv":    "75",
        "secondary_earthing":" NER - 400A, 10 sec, 15.88 ohm",
    },
    VARIANT_DISTRIBUTION: {
        "title_line":       "1250kVA, 11/0.433 kV DISTRIBUTION TRANSFORMER",
        "title_field":      "11/0.433 kV DISTRIBUTION TRANSFORMER",
        "rating":           "1250",
        "rating_unit":      "kVA",
        "primary_voltage":  "11",
        "secondary_voltage_no_load": "0.433",
        "secondary_voltage_full_load": "0.415",
        "vector_group":     "Dyn11",
        "criticality":      "3",
        "inspection_class": "2",
        "tank_type":        "LIQUID IMMERSED HERMETICALLY SEALED",
        "earthing_system":  "SOLID",
        "neutral_in_box":   "BUS",
        "tolerance_imp":    "+/- 10 %",
        "primary_sc_rating":"25",
        "primary_pfwv":     "28",
        "secondary_pfwv":   "3",
        "primary_iwv":      "75",
        "secondary_iwv":    "12",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  Auxiliary sub-sheet content (COVERSHEET / REVISION / HOLD / INDEX / NOTES)
#  All values are soft-coded — override via project_info on export.
# ─────────────────────────────────────────────────────────────────────────────
SHEET_TITLES = {
    "cover":    "COVERSHEET",
    "revision": "REVISION",
    "hold":     "HOLD",
    "index":    "INDEX",
    "data":     "DATASHEET",
    "notes":    "NOTES",
}

# Default 11-page pagination — overridable via project_info["pagination"]
DEFAULT_PAGINATION = {
    "cover":    "01/11",
    "revision": "02/11",
    "hold":     "03/11",
    "index":    "04/11",
    "data":     "05/10",   # spans 05–10
    "notes":    "11/11",
}

REVISION_HISTORY = [
    # (rev, date, section, description)
    ("L", "10.Jan.25", "-", "ISSUED FOR COMPANY REVIEW"),
    ("N", "18.Jun.25", "-", "APPROVED FOR ENGINEERING"),
    ("P", "09.Jul.25", "-", "APPROVED FOR PURCHASE"),
]

REVISION_FOOTER_NOTES = [
    "This page records the revision status of the document.",
    "All previous issues are hereby superseded.",
    "Revisions after first issues are denoted by a triangular flag with revision number adjacent to revised area.",
    "Document changes shall be made in track changes mode and reviewed before issue.",
]

HOLD_ENTRIES = [
    # (rev, description, section)
    ("1", "NIL", ""),
]

INDEX_ENTRIES = [
    # (sr, description, sheet)
    ("1", "COVER SHEET",            "1"),
    ("2", "REVISION HISTORY",       "2"),
    ("3", "HOLD",                   "3"),
    ("4", "TABLE OF CONTENTS",      "4"),
    ("5", "TRANSFORMER DATA SHEET", "5-10"),
    ("6", "GENERAL NOTES",          "11"),
]

ABBREVIATIONS = [
    ("***", "VENDOR TO SPECIFY"),
    ("CT",  "CURRENT TRANSFORMER"),
    ("kA",  "KILO AMPERE"),
    ("kV",  "KILO VOLT"),
    ("kVA", "KILO VOLT AMPERE"),
    ("NER", "NEUTRAL EARTHING RESISTOR"),
    ("NA",  "NOT APPLICABLE"),
    ("VT",  "VOLTAGE TRANSFORMER"),
    ("W",   "WATT"),
]

GENERAL_NOTES = [
    # (label, text)
    ("1",  "TRANSFORMER DESIGN SHALL BE SUITABLE FOR PRIMARY INPUT VOLTAGE OF +/-10% AND FREQUENCY VARIATION OF +/- 2%."),
    ("2",  "THE FOLLOWING FILTERING VALVES WITH BLANKING PLATES SHALL BE INCLUDED:"),
    ("a",  "TANK TOP AND BOTTOM"),
    ("b",  "RADIATOR TOP AND BOTTOM"),
    ("c",  "DRAIN VALVES"),
    ("3",  "ALL TRANSFORMER PROTECTION DEVICES SHALL BE PROVIDED WITH 2 SETS OF TRIP ALARM CONTACTS, ONE SET FOR SWITCHGEAR TRIP & ALARM AND ONE SET FOR SCMS."),
    ("4",  "VENDOR TO PROVIDE CALCULATION FOR PRESSURE WITHSTAND CAPABILITY OF TERMINAL BOXES UNDER MAXIMUM FAULT CONDITION AND ARCING FAULT WITH FAULT CURRENT EQUAL TO RATED SHORT CIRCUIT CURRENT. TERMINAL BOXES SHALL BE CAPABLE OF WITHSTANDING PRESSURE UNDER WORST FAULT CONDITION."),
    ("5",  "SPECIAL AND ROUTINE TESTS SHALL BE FULLY WITNESSED BY CLIENT'S REPRESENTATIVE AND TO BE CONSIDERED AS HOLD POINT."),
    ("6",  "THERMAL IMAGE WINDOWS SHALL BE PROVIDED AT CABLE BOXES. THE WINDOWS SHALL WITHSTAND THE PRESSURE CREATED DUE TO SHORT CIRCUIT INSIDE THE CABLE BOX. IT SHALL BE TESTED WITH TRANSFORMER CABLE BOXES."),
    ("7",  "IN ADDITION TO APPENDIX-3 OF BGS-EE-003, \"MAGNETIC BALANCE TEST\" SHALL BE PERFORMED ON EACH TRANSFORMER."),
    ("8",  "CERTIFICATION SHALL BE REQUIRED FOR TRANSFORMER OF IDENTICAL DESIGN AND RATING. OTHERWISE TESTING ONE UNIT OF AN IDENTICAL BATCH IS REQUIRED."),
    ("9",  "\"LEAK TEST\" SPECIFIED IN CL. 3-g OF APPENDIX-3 OF BGE-EE-003 SHALL BE PERFORMED ON EACH TRANSFORMER & BE WITNESSED."),
    ("10", "ONLY MOMENTARY PARALLELING DURING SCHEDULED CHANGE OVER OR RESTORATION OF NORMAL SUPPLY."),
    ("11", "TRANSFORMER SHALL BE CAPABLE TO WITHSTAND SHORT TIME OVERLOADING AS PER IEC-60076-7 TABLE 3."),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Helper builders
# ─────────────────────────────────────────────────────────────────────────────
def _row(sr, desc, unit="", spec="", rev="L", variants=None):
    """Standard data row."""
    return {
        "sr_no": sr,
        "description": desc,
        "unit": unit,
        "required_data": spec,
        "vendor_data": "",
        "rev": rev,
        "is_section": False,
        "variants": variants,
    }


def _section(letter, title, variants=None):
    """Section-header row (e.g. 'A   GENERAL DATA')."""
    return {
        "sr_no": letter,
        "description": title,
        "unit": "",
        "required_data": "",
        "vendor_data": "",
        "rev": "L",
        "is_section": True,
        "variants": variants,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Schema — ordered list of rows.  Soft-coded; mirrors the xlsm 1:1.
# ─────────────────────────────────────────────────────────────────────────────
def build_schema(variant: str = VARIANT_POWER) -> list[dict]:
    """
    Return the ordered list of rows for the given variant.

    Variant must be ``VARIANT_POWER`` or ``VARIANT_DISTRIBUTION``.
    Items whose ``variants`` set excludes the requested variant are filtered.
    """
    v = VARIANT_DEFAULTS.get(variant, VARIANT_DEFAULTS[VARIANT_POWER])
    rows: list[dict] = []

    # ── A. GENERAL DATA ────────────────────────────────────────────────────
    rows += [
        _section("A", "GENERAL DATA"),
        _row("1",  "TAG NO.",                            "-",   ""),
        _row("2",  "TITLE",                              "-",   v["title_field"]),
        _row("3",  "MANUFACTURER / COUNTRY OF ORIGIN",   "-",   "*** (AS PER COMPANY APPROVED LIST )"),
        _row("4",  "YEAR OF MANUFACTURE",                "-",   "***"),
        _row("5",  "QUANTITY",                           "No.", "2"),
        _row("6",  "RATING",                             v["rating_unit"], v["rating"], rev="N"),
        _row("7",  "PROJECT SPECIFICATION",              "-",   "BGS-EE-003"),
        _row("8",  "STANDARDS",                          "-",   "IEC 60076"),
        _row("9",  "DESIGN LIFE",                        "-",   "25 YEARS"),
        _row("10", "CRITICALITY RATING",                 "-",   v["criticality"]),
        _row("11", "INSPECTION CLASS",                   "-",   v["inspection_class"]),
        _row("12", "MATERIAL CERTIFICATION",             "-",   "3.1", rev="P"),
    ]

    # ── B. ENVIRONMENTAL CONDITIONS ────────────────────────────────────────
    rows += [
        _section("B", "ENVIRONMENTAL CONDITIONS"),
        _row("1", "TYPE OF INSTALLATION",            "",        "OUTDOOR IN SHADED AREA"),
        _row("2", "ATMOSPHERE",                      "",        "SALTY, SULFUROUS AND DUSTY WITH HIGH CONCENTRATION OF WINDBORNE SAND"),
        _row("3", "ALTITUDE",                        "M",       "LESS THAN 1000m AMSL"),
        _row("4", "MAX AMBIENT TEMPERATURE",         "°C",      "54"),
        _row("5", "MINIMUM AMBIENT TEMPERATURE",     "°C",      "5"),
        _row("6", "MAXIMUM RELATIVE HUMIDITY",       "at 43°C", "0.95"),
        _row("7", "AVERAGE RELATIVE HUMIDITY",       "at 54°C", "0.6"),
        _row("8", "DEGREE OF PROTECTION (IP)",       "-",       "IP55"),
        _row("9", "SPECIAL CONDITIONS",              "-",       "TROPICALIZED"),
    ]

    # ── C. GENERAL TECHNICAL CHARACTERISTICS ───────────────────────────────
    rows += [
        _section("C", "GENERAL TECHNICAL CHARACTERISTICS"),
        _row("1", "RATED PRIMARY VOLTAGE",                          "kV", v["primary_voltage"]),
        _row("2", "RATED SECONDARY VOLTAGE AT NO LOAD",             "kV", v["secondary_voltage_no_load"]),
        _row("3", "SECONDARY VOLTAGE AT RATED POWER AND P.F 0.8",   "kV", v["secondary_voltage_full_load"], rev="P"),
        _row("4", "RATED FREQUENCY",                                "Hz", "50"),
        _row("5", "NO. OF PHASES",                                  "-",  "3"),
        _row("6", "CONNECTION SYMBOL AND VECTOR GROUP",             "-",  v["vector_group"]),
        _row("7", "MAXIMUM FLUX DENSITY",                           "T",  "***"),
    ]
    if variant == VARIANT_POWER:
        rows += [
            _row("8",  "AIR CORE REACTANCE",            "",  "***", rev="N", variants={VARIANT_POWER}),
            _row("9",  "WITH SEPARATE WINDINGS",        "-", "CORE TYPE", variants={VARIANT_POWER}),
            _row("10", "SECONDARY SYSTEM EARTHING",     "-", v["secondary_earthing"], variants={VARIANT_POWER}),
        ]
    else:
        rows += [
            _row("8", "WITH SEPARATE WINDINGS",         "-", "CORE TYPE", variants={VARIANT_DISTRIBUTION}),
        ]

    # ── D. INSULATION SYSTEMS ──────────────────────────────────────────────
    rows += [
        _section("D", "INSULATION SYSTEMS"),
        _row("1",   "INSULATION CLASS",                  "-",  "A"),
        _row("2",   "UNIFORM INSULATION",                "-",  "YES"),
        _section("3", "POWER FREQUENCY WITHSTAND VOLTAGE:"),
        _row("3.1", " - PRIMARY",                        "kV", v["primary_pfwv"]),
        _row("3.2", " - SECONDARY",                      "kV", v["secondary_pfwv"]),
        _section("4", "IMPULSE WITHSTAND VOLTAGE:"),
        _row("4.1", " - PRIMARY",                        "kV", v["primary_iwv"]),
        _row("4.2", " - SECONDARY",                      "kV", v["secondary_iwv"]),
        _row("5",   "OIL IMMERSED TRANSFORMER",          "-",  "YES"),
        _row("6",   "OIL TYPE",                          "-",  "AS PER BGS-EE-003"),
        _row("7",   "TRANSFORMER TANK CONSTRUCTION",     "-",  v["tank_type"]),
        _row("8",   "COMPLETELY FILLED",                 "-",  "***"),
        _row("9",   "WITH GAS CUSHION",                  "-",  "***"),
    ]
    if variant == VARIANT_POWER:
        rows += [
            _row("10", "BREATHING TYPE",         "-", "YES", variants={VARIANT_POWER}),
            _row("11", "WITH CONSERVATOR",       "-", "YES", variants={VARIANT_POWER}),
            _row("12", "DIAPHRAGM TYPE",         "-", "YES", variants={VARIANT_POWER}),
            _row("13", "IMPREGNATED TYPE",       "-", "YES", variants={VARIANT_POWER}),
            _row("14", "CAST-RESIN (ENCAPSULATED) TYPE", "-", "YES", variants={VARIANT_POWER}),
            _row("15", "TYPE OF COOLING (ONAN/ONAF)",    "-", "ONAN", rev="N", variants={VARIANT_POWER}),
        ]
    else:
        rows += [
            _row("10", "TYPE OF COOLING (ONAN/ONAF)",    "-", "ONAN", variants={VARIANT_DISTRIBUTION}),
        ]

    # ── E. MODE OF OPERATION ───────────────────────────────────────────────
    rows += [
        _section("E", "MODE OF OPERATION"),
        _row("1", "INDIVIDUAL / PARALLEL", "-", "PARALLEL (REFER NOTE 10)"),
    ]

    # ── F. PRIMARY WINDING ─────────────────────────────────────────────────
    rows += [
        _section("F", "PRIMARY WINDING"),
        _row("1", "HIGH VOLTAGE ( UM )",                         "kV", "12" if variant == VARIANT_DISTRIBUTION else "***"),
        _row("2", "COPPER",                                      "-",  "YES"),
        _row("3", "MAXIMUM CURRENT DENSITY IN THE WINDING",      "-",  "***"),
        _row("4", "RATED PRIMARY CURRENT",                       "A",  "***"),
    ]

    # ── G. SECONDARY WINDING ───────────────────────────────────────────────
    rows += [
        _section("G", "SECONDARY WINDING"),
        _row("1", "HIGH VOLTAGE ( UM )",                          "kV", "0.6" if variant == VARIANT_DISTRIBUTION else "***"),
        _row("2", "COPPER",                                       "-",  "YES"),
        _row("3", "ADDITIONAL NEUTRAL BROUGHT IN A SEPARATED BOX","-",  v["neutral_in_box"]),
        _row("4", "EARTHING SYSTEM",                              "-",  v["earthing_system"]),
        _row("5", "MAXIMUM CURRENT DENSITY IN THE WINDING",       "-",  "***"),
        _row("6", "RATED PRIMARY CURRENT",                        "-",  "***"),
    ]

    # ── H. ELECTRICAL AND MECHANICAL CHARACTERISTICS ───────────────────────
    rows += [
        _section("H", "ELECTRICAL AND MECHANICAL CHARACTERISTICS"),
        _row("1",   "NO-LOAD CURRENT (PRIMARY)",                 "-", "***"),
        _row("2",   "MAGNETIZING INRUSH CURRENT AND DURATION",   "-", "***"),
        _row("3",   "SHORT CIRCUIT IMPEDANCE AT 75°C",           "%" if variant == VARIANT_DISTRIBUTION else "-",
             "6" if variant == VARIANT_DISTRIBUTION else "0.07", rev="P"),
        _row("3.1", "TRANSFORMER IMPEDANCE AT PRINCIPLE TAP",    "",  "***"),
        _row("3.2", "TRANSFORMER IMPEDANCE AT MINIMUM TAP",      "",  "***"),
        _row("3.3", "TRANSFORMER IMPEDANCE AT MAXIMUM TAP",      "",  "***"),
        _row("4",   "TOLERANCE ON SHORT CIRCUIT IMPEDANCE",      "-", v["tolerance_imp"]),
        _row("5",   "ZERO SEQUENCE IMPEDANCE",                   "-", "***"),
        _row("6",   "POSITIVE SEQUENCE X/R RATIO",               "-", "***"),
        _row("7",   "ZERO SEQUENCE X/R RATIO",                   "-", "***"),
    ]
    if variant == VARIANT_DISTRIBUTION:
        rows += [
            _row("8",  "PRIMARY SIDE VOLTAGE LEVEL AND VARIATION", "kV",  "11kV ± 10%", variants={VARIANT_DISTRIBUTION}),
            _row("9",  "FREQUENCY AND VARIATION",                  "Hz",  "50 Hz ± 2%", variants={VARIANT_DISTRIBUTION}),
            _row("10", "PRIMARY 11KV SYSTEM APPARENT SHORT CIRCUIT RATING", "kA", v["primary_sc_rating"], rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("11", "MAX. SHORT CIRCUIT DURATION",              "Sec", "1", variants={VARIANT_DISTRIBUTION}),
            _row("12", "SECONDARY SIDE APPARENT SHORT CIRCUIT RATING", "kA", "65", rev="P", variants={VARIANT_DISTRIBUTION}),
            _row("13", "MAX. SHORT CIRCUIT DURATION",              "Sec", "1", variants={VARIANT_DISTRIBUTION}),
            _row("14", "TOP OIL TEMPERATURE RISE (AS PER IEC - 60076-2, TABLE I,II & III)",     "°C", "45", rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("15", "AVERAGE WINDING TEMPERATURE RISE (AS PER IEC - 60076-2, TABLE I,II & III)", "°C", "50", rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("16", "HOT SPOT TEMPERATURE (AS PER IEC - 60076-2, TABLE I,II & III)",         "°C", "63", rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("17", "IRON LOSSES (NO LOAD)",      "-", "***", variants={VARIANT_DISTRIBUTION}),
            _row("18", "COPPER LOSSES (FULL LOAD)",  "-", "***", variants={VARIANT_DISTRIBUTION}),
            _row("19", "TOTAL LOSSES",               "-", "***", variants={VARIANT_DISTRIBUTION}),
            _section("20", "EFFICIENCY AT 0.8 POWER FACTOR"),
            _row("20.1", "50% LOAD",                 "-", "***", variants={VARIANT_DISTRIBUTION}),
            _row("20.2", "75% LOAD",                 "-", "***", variants={VARIANT_DISTRIBUTION}),
            _row("20.3", "100% LOAD",                "-", "***", variants={VARIANT_DISTRIBUTION}),
            _section("20.4", "EFFICIENCY AT POWER FACTOR 1"),
            _row("20.5", "50% LOAD",                 "-", "***", rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("20.6", "75% LOAD",                 "-", "***", rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("20.7", "100% LOAD",                "-", "***", rev="N", variants={VARIANT_DISTRIBUTION}),
            _row("20.8", "MAX. EFFICIENCY AT % LOAD","-", "***", rev="N", variants={VARIANT_DISTRIBUTION}),
            _section("21", "VOLTAGE REGULATION"),
            _row("21.1", "AT UNITY POWER FACTOR",    "-", "***", variants={VARIANT_DISTRIBUTION}),
            _row("21.2", "AT 0.8 POWER FACTOR",      "-", "***", variants={VARIANT_DISTRIBUTION}),
            _row("22",   "SATURATION VOLTAGE",       "-", "***", rev="N", variants={VARIANT_DISTRIBUTION}),
        ]
    else:
        rows += [
            _row("8",  "PRIMARY SYSTEM APPARENT SHORT CIRCUIT RATING", "kA", v["primary_sc_rating"], variants={VARIANT_POWER}),
            _row("9",  "MAX. SHORT CIRCUIT DURATION", "Sec", "1", variants={VARIANT_POWER}),
            _row("10", "TOP OIL TEMPERATURE RISE",    "°C",  "45", variants={VARIANT_POWER}),
            _row("11", "AVERAGE WINDING TEMPERATURE RISE", "°C", "50", variants={VARIANT_POWER}),
            _row("12", "HOT SPOT TEMPERATURE",        "°C",  "63", variants={VARIANT_POWER}),
            _row("13", "IRON LOSSES (NO LOAD)",       "-",   "***", rev="N", variants={VARIANT_POWER}),
            _row("14", "COPPER LOSSES (FULL LOAD)",   "-",   "***", rev="N", variants={VARIANT_POWER}),
            _row("15", "TOTAL LOSSES",                "-",   "***", variants={VARIANT_POWER}),
            _section("16", "EFFICIENCY AT 0.8 POWER FACTOR"),
            _row("16.1", "50% LOAD",                  "-", "***", variants={VARIANT_POWER}),
            _row("16.2", "75% LOAD",                  "-", "***", variants={VARIANT_POWER}),
            _row("16.3", "100% LOAD",                 "-", "***", variants={VARIANT_POWER}),
            _section("16.4", "EFFICIENCY AT POWER FACTOR 1"),
            _row("16.5", "50% LOAD",                  "-", "***", rev="N", variants={VARIANT_POWER}),
            _row("16.6", "75% LOAD",                  "-", "***", rev="N", variants={VARIANT_POWER}),
            _row("16.7", "100% LOAD",                 "-", "***", rev="N", variants={VARIANT_POWER}),
            _row("16.8", "MAX. EFFICIENCY AT % LOAD", "-", "***", rev="N", variants={VARIANT_POWER}),
            _section("17", "VOLTAGE REGULATION"),
            _row("17.1", "AT UNITY POWER FACTOR",     "-", "***", variants={VARIANT_POWER}),
            _row("17.2", "AT 0.8 POWER FACTOR",       "-", "***", variants={VARIANT_POWER}),
            _row("18",   "SATURATION VOLTAGE",        "-", "***", rev="N", variants={VARIANT_POWER}),
        ]

    # ── I. TAP CHANGERS ────────────────────────────────────────────────────
    rows += [
        _section("I", "TAP CHANGERS"),
        _row("1", "OFF-CIRCUIT (Y/N)", "-",   "YES"),
    ]
    if variant == VARIANT_POWER:
        rows += [_row("2", "ON-LOAD (Y/N)", "-", "NO", variants={VARIANT_POWER})]
        nxt = 3
    else:
        nxt = 2
    rows += [
        _row(str(nxt),     "NO. OF TAPPINGS",                              "No.", "5"),
        _row(str(nxt + 1), "TAPPING STEP",                                 "%",   "0.025"),
        _row(str(nxt + 2), "TAPPING RANGE",                                "%",   "± 5% (IN STEP OF 2.5%)"),
        _row(str(nxt + 3), "VOLTAGE REGULATOR & PARALLEL CONTROL SYSTEM",  "-",   "N/A"),
    ]

    # ── J. TANK ────────────────────────────────────────────────────────────
    rows += [
        _section("J", "TANK"),
        _row("1",   "TANK MATERIAL",                          "-",  "***"),
        _row("2",   "FABRICATED UNDER BASE",                  "MM", "YES (THICKNESS MIN. 10 MM)"),
        _row("3",   "THICKNESS OF TANK",                      "-",  "6 MM MIN."),
        _row("3.1", "• SIDES",                                "MM", "***"),
        _row("3.2", "• BOTTOM",                               "MM", "***"),
        _row("3.3", "• TOP",                                  "MM", "***"),
        _row("4",   "TYPE OF TANK (SEALED / CONSERVATOR)",    "-",
             "HERMETICALLY SEALED" if variant == VARIANT_DISTRIBUTION else "CONSERVATOR"),
        _row("5",   "RADIATOR",                               "-",  "DETACHABLE"),
        _row("6",   "NUMBER OF RADIATORS",                    "-",  "***"),
        _row("7",   "TRANSFORMER MOUNTING",                   "-",  "BI-DIRECTIONAL ROLLERS"),
    ]

    # ── K. TANK COVER TYPE ─────────────────────────────────────────────────
    rows += [
        _section("K", "TANK COVER TYPE"),
        _row("1", "BOLTED",     "-",  "YES"),
        _row("2", "WELDED",     "-",  "NA"),
        _row("3", "BELL TYPE",  "-",  "NA"),
        _row("4", "THICKNESS",  "MM", "AS PER BGS-EE-003"),
    ]

    # ── L. DIMENSIONS ──────────────────────────────────────────────────────
    rows += [
        _section("L", "DIMENSIONS"),
        _row("1", "OVERALL WITH ACCESSORIES (LENGTH / WIDTH / HEIGHT)", "MM", "***"),
        _row("2", "BETWEEN ROLLER AXIS",                                "MM", "***"),
    ]

    # ── M. WEIGHTS ─────────────────────────────────────────────────────────
    rows += [
        _section("M", "WEIGHTS"),
        _row("1", "TOTAL",            "KG",    "***"),
        _row("2", "OIL",              "LITER", "***"),
        _row("3", "CORE AND WINDING", "KG",    "***"),
        _row("4", "TANK AND FITTING", "KG",    "***"),
        _row("5", "VOLUME OF OIL",    "LITER", "***"),
        _row("6", "MAKE OF OIL",      "-",     "***"),
    ]

    # ── N. NOISE LEVEL ─────────────────────────────────────────────────────
    rows += [
        _section("N", "NOISE LEVEL"),
        _row("1", "WITHOUT COOLING", "dB", "*** (AS PER IEC 60076-10)", rev="N"),
        _row("2", "WITH COOLING",    "-",  "NA"),
    ]

    # ── O. CONNECTIONS ─────────────────────────────────────────────────────
    rows += [
        _section("O", "CONNECTIONS"),
        _section("1", "PRIMARY VOLTAGE SIDE"),
        _row("1.1",   "CABLE CONNECTION",                                  "-", "YES"),
        _row("1.2",   "CABLE TYPE AND SIZE",                               "-", "3C x 95 Sqmm", rev="P"),
        _row("1.3",   "TYPE OF BUSHING AND RATING",                        "-", "AS PER SPEC. BGS-EE-003"),
        _row("1.3.1", "QUANTITY OF BUSHING",                               "-", "***", rev="N"),
        _row("1.4",   "SPACE FOR CURRENT TRANSFORMER",                     "-", "***"),
        _row("1.5",   "PLUG IN TERMINAL",                                  "-", "***"),
        _row("1.6",   "CABLE BOX WITH OIL",                                "-", "NO"),
        _row("1.7",   "SF6 CONNECTION",                                    "-", "NO"),
        _row("1.8",   "PROTECTIVE ENCLOSURE",                              "-", "AIR INSULATED CABLE BOX"),
        _row("1.9",   "THERMAL IMAGE WINDOW FOR CABLE INVESTIGATION",      "-", "REQUIRED"),
        _row("1.10",  "PRESSURE RELIEF DIAPHRAGM",                         "-", "REQUIRED"),
        _row("1.11",  "DISCONNECTING CHAMBERS / LINKS",                    "-", "REQUIRED"),
        _section("2", "SECONDARY VOLTAGE SIDE"),
        _row("2.1",   "CABLE CONNECTION",      "-", "NO", rev="N"),
        _row("2.2",   "CABLE SIZE",            "-", "NA", rev="N"),
        _row("2.3",   "BUS DUCT",              "-", "YES", rev="N"),
        _row("2.5",   "BUS DUCT TYPE",         "-", "PHASE INSULATED AS PER BGS-EE-006", rev="N"),
        _row("2.6",   "BUS DUCT TERMINATIONS", "-", "AS PER BGS-EE-003", rev="N"),
        _section("3", "NEUTRAL SIDE"),
        _row("3.1",   "NEUTRAL TERMINAL IN A SEPARATE NEUTRAL TERMINAL BOX", "-", "YES"),
        _row("3.2",   "THERMAL IMAGE WINDOW FOR CABLE INVESTIGATION",         "-", "YES"),
        _row("3.3",   "PRESSURE RELIEF DIAPHRAGM",                            "-", "YES"),
    ]

    # ── P. CONTROL AND PROTECTION DEVICES ──────────────────────────────────
    rows += [
        _section("P", "CONTROL AND PROTECTION DEVICES"),
        _row("1",  "PRESSURE RELIEF DEVICE WITH TWO TRIP CONTACT FORM \"C\"",  "-", "YES"),
        _row("2",  "BUCHHOLZ RELAY WITH TWO ALARM AND TWO TRIP CONTACTS",      "-",
             "NA" if variant == VARIANT_DISTRIBUTION else "YES"),
        _row("3",  "THERMAL IMAGE TYPE WINDING TEMPERATURE WITH CONTACTS\n(TWO ALARM / TWO TRIP)", "-", "YES"),
        _row("4",  "OIL TEMP. INDICATOR WITH CONTACTS (TWO ALARM & TWO TRIP)", "-", "YES"),
        _row("5",  "WINDING TEMP. INDICATOR WITH CONTACTS (TWO ALARM & TWO TRIP)", "-", "YES"),
        _row("6",  "THERMO METER POCKETS",                                     "-", "YES"),
        _row("7",  "THERMOWELL",                                               "-", "YES"),
        _row("8",  "THERMOSTAT",                                               "-", "***"),
        _row("9",  "LIQUID LEVEL GUAGE WITH 2 CONTACTS (ALARM / TRIP)",        "-", "YES"),
        _row("10", "PRESSURE RELIEF VALVE WITH OPERATING 2 SINGLE CONTACTS",   "-", "YES"),
        _row("11", "MAGNETIC OIL LEVEL GUAGE WITH TWO CONTACTS (ALARM)",       "-", "YES"),
        _row("12", "PRESSURE VACCUM GAUGE WITH OPERATING 4 CONTACTS (ALARM / TRIP)", "-", "YES"),
        _row("13", "NEUTRAL CT IN SEPARATE NEUTRAL TERMINAL BOX (Y/N)",        "-", "YES (***)"),
        _row("14", "CURRENT TRANSFORMER FOR STANDBY E/F PROTECTION",           "-", "2000/1A, 5P20, 15VA", rev="N"),
    ]

    # ── Q. ACCESSORIES ─────────────────────────────────────────────────────
    rows += [
        _section("Q", "ACCESSORIES"),
        _row("1",  "PRIMARY SURGE ARRESTOR",                              "-", "NA"),
        _row("2",  "AIR DRYER",                                           "-", "NA"),
        _row("3",  "LIFTING EYES & JACKING LUGS",                         "-", "YES"),
        _row("4",  "PULLING EYES FOR MOVING TRANSFORMER IN ALL DIRECTIONS","-", "YES"),
        _row("5",  "SAFETY VALVE ON TANK AND RADIATORS",                  "-", "YES\nTOP AND BOTTOM"),
        _row("6",  "FILLING VALVE ON TANK AND RADIATORS",                 "-", "YES\nTOP AND BOTTOM"),
        _row("7",  "SAMPLING VALVE / DRAIN VALVE ON TANK AND RADIATORS",  "-", "YES\nTOP AND BOTTOM"),
        _row("8",  "FILLER PLUG / FILTER VALVES",                         "-", "YES"),
        _row("9",  "CASTERS (FIXED / ORIENTABLE)",                        "-", "YES, ORIENTABLE"),
        _row("10", "EARTH TERMINAL",                                      "-", "YES, 2"),
        _row("11", "MARSHALLING BOX",                                     "-", "YES"),
        _row("12", "JACKING PADS",                                        "-", "YES"),
        _row("13", "THERMOMETERS",                                        "-", "YES", rev="N"),
        _row("14", "LUGS/HOOKS",                                          "-", "YES", rev="N"),
        _row("15", "TERMINAL BOX FOR AUXILIARIES",                        "-", "YES", rev="N"),
    ]

    # ── R. INSPECTION & TESTING ────────────────────────────────────────────
    rows += [
        _section("R", "INSPECTION & TESTING"),
        _row("1", "INSPECTIONS & TESTS",                "-", "AS PER SPEC. BGS-EE-003"),
        _row("2", "ROUTINE TESTS",                      "-", "AS PER APPENDIX-3 OF BGS-EE-003"),
        _row("3", "TYPE TESTS & ACOUSTIC SOUND TESTS",  "-", "AS PER APPENDIX-3 OF BGS-EE-003"),
        _row("4", "SPECIAL TESTS",                      "-", "AS PER APPENDIX-3 OF BGS-EE-003"),
    ]

    # ── S. PAINTING ────────────────────────────────────────────────────────
    rows += [
        _section("S", "PAINTING"),
        _row("1", "COLOUR",                       "-", "RAL 6011 AS PER BGS-MX-001"),
        _row("2", "PAINTING THICKNESS TANK",      "-", "***"),
        _row("3", "PAINTING THICKNESS RADIATOR",  "-", "***"),
        _row("4", "GALVANISATION THICKNESS TANK", "-", "***"),
        _row("5", "GALVANISATION THICKNESS RADIATOR", "-", "***"),
    ]

    # ── T. LOSS EVALUATION ─────────────────────────────────────────────────
    rows += [
        _section("T", "LOSS EVALUATION"),
        _row("1", "ENERGY COST",   "$", "0.095 USD", rev="P"),
        _row("2", "INTEREST RATE", "-", "0.08",      rev="P"),
        _row("3", "LOADING FACTOR","-", "0.5"),
    ]

    # ── U. EXTERNAL POWER SUPPLY REQUIREMENT ──────────────────────────────
    rows += [
        _section("U", "EXTERNAL POWER SUPPLY REQUIREMENT"),
        _row("1", "EXTERNAL POWER SUPPLY FOR AUXILIARY POWER", "-", "240V AC, 50 Hz, 1-Ph FOR SPACE HEATER SUPPLY."),
        _row("2", "AUXILIARY LOAD DETAILS",                    "W", "***"),
    ]

    # Filter by variant (defensive — most rows already use _row defaults)
    out = []
    for r in rows:
        v_set = r.get("variants")
        if v_set and variant not in v_set:
            continue
        # Strip helper-only keys so the schema is JSON-clean
        out.append({
            "sr_no":         r["sr_no"],
            "description":   r["description"],
            "unit":          r["unit"],
            "required_data": r["required_data"],
            "vendor_data":   r["vendor_data"],
            "rev":           r["rev"],
            "is_section":    r.get("is_section", False),
        })
    return out


def detect_variant_from_text(text: str) -> str:
    """Heuristic: pick variant based on rating cues in the document."""
    if not text:
        return VARIANT_POWER
    t = text.upper()
    # Power transformer cues
    power_cues = ["MVA", "33 KV", "33KV", "33/11", "POWER TRANSFORMER"]
    # Distribution cues
    dist_cues  = ["KVA", "11/0.4", "11/0.43", "0.433", "DISTRIBUTION TRANSFORMER", "1250"]
    p = sum(1 for c in power_cues if c in t)
    d = sum(1 for c in dist_cues  if c in t)
    return VARIANT_DISTRIBUTION if d > p else VARIANT_POWER
