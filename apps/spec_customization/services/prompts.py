"""
Spec Customization — Soft-Coded AI Prompt Templates
====================================================

All prompts live here. Tweak in this file only.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are an expert piping engineer extracting structured data
from legacy Piping Material Specification (PMS) PDF pages used in Oil & Gas
plants (ADNOC, ARAMCO, Shell, BP style).

For every PIPING SPEC class you find, return a strict JSON object — no prose,
no markdown fences, no commentary. Be conservative: if a value is not clearly
visible on the page, return an empty string for that field, NOT a guess.
"""

EXTRACT_PIPING_CLASS_PROMPT = """Analyse the page(s) below and identify every
PIPING SPEC class (also known as: Piping Material Specification, PMS, Line Class,
Pipe Class, Material Class, Spec Code, or similar). The header may appear in any
of these forms — treat them all as piping class headers:
  • "PIPING SPEC: A"      • "PIPING SPECIFICATION: A1"
  • "PIPING CLASS A"      • "PIPE CLASS A"
  • "LINE CLASS: A1B"     • "MATERIAL CLASS A"
  • "SPEC CODE: A1A"      • "P.M.S. A"  / "PMS: A"
  • "CLASS 150-A"         • "PIPING MATERIAL SPECIFICATION A"
For each class produce one object in this exact schema:

{
  "class_code":           "A",                       # single letter or short code
  "class_full_code":      "PIPING SPEC: A",          # full header line
  "material_grade":       "CARBON STEEL",            # principal material
  "pressure_rating":      "CLASS 150",               # ANSI / ASME class
  "flange_facing":        "RF",                      # RF / FF / RTJ
  "corrosion_allowance":  "1.5 mm",
  "service_list": [
    "General Process", "Sweet Fuel Gas", "L.P. Steam"
  ],
  "pt_rating_table": [
    {"pressure_bar_g": 19.7, "temperature_c": 38},
    {"pressure_bar_g": 17.7, "temperature_c": 100}
  ],
  "components": [
    {
      "component_type":     "pipe",                  # pipe|valve|fitting|flange|gasket|bolt
      "sub_type":           "",                      # e.g. Gate, Globe, Elbow, Tee
      "size_from":          "1/2\"",
      "size_to":            "1-1/2\"",
      "description":        "SMLS, BE, PE",
      "schedule_or_rating": "SCH 80",
      "material_standard":  "ASTM A106 GR. B",
      "end_connection":     "BW",
      "notes":              ""
    }
  ],
  "raw_notes":            "",                        # any general notes block
  "confidence":           0.88                       # 0.0–1.0 self-assessed
}

Return a single JSON object: {"piping_classes": [ ... ]}

IMPORTANT:
- Return ONLY the JSON object. No markdown. No explanation.
- If the page contains no PIPING SPEC class, return {"piping_classes": []}.
- Group components by component_type as listed in the page tables.
- Preserve units exactly as printed (inches, bar-g, °C, etc.).
"""

# Concise prompt used when AI must re-process a chunk with smaller context.
COMPACT_EXTRACTION_PROMPT = """Extract piping classes from this page in JSON:
{"piping_classes":[{"class_code":"","class_full_code":"","material_grade":"",
"pressure_rating":"","flange_facing":"","corrosion_allowance":"",
"service_list":[],"pt_rating_table":[],"components":[],"raw_notes":"",
"confidence":0.0}]}.  Return ONLY the JSON object.
"""
