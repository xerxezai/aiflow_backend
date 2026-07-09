"""
Soft-coded mapping from Matrix biometric `department` labels → RBAC role codes.

Design principles:
  - Only map when we are confident. Ambiguous departments fall back to
    ``DEFAULT_FALLBACK_ROLE`` (a blanket engineering-access role) so the
    person still keeps working access without accidentally being granted a
    specific engineer role they don't hold.
  - External / non-Rejlers labels (Visitor, Service Provider) map to
    ``SKIP`` so the alignment tool won't touch those accounts.
  - The mapping is intentionally case-preserving because it mirrors what
    the Matrix DB literally emits (typos included — e.g. "Eletrical").

Edit this file to change how departments translate to roles. The runtime
tools re-read it on every invocation.
"""

# Sentinel: skip this user entirely (do not modify their roles)
SKIP = "__SKIP__"

# Fallback for real Rejlers people whose department doesn't map cleanly to a
# discipline. Keeps them productive without over-privileging.
DEFAULT_FALLBACK_ROLE = "engineering_common_access"

# ── Department → RBAC role code ──────────────────────────────────────────────
DEPARTMENT_TO_ROLE = {
    # Core engineering disciplines
    "Process Engineering":                             "process_engineer",
    "Civil and Structural Engineering":                "civil_engineer",
    "Eletrical Engineering":                           "electrical_engineer",   # sic — Matrix has the typo
    "Electrical Engineering":                          "electrical_engineer",
    "Instrumentation, Automation, Telecom":            "instrument_engineer",
    "Piping, Layout, Mechanical":                      "piping_engineer",
    "PDDS":                                            "design_engineer",       # Piping Design/Drafting/Documents
    "QHSE":                                            "qhse_engineer",

    # Management / cross-functional
    "Project Management":                              "project_manager",
    "Engineering Management":                          "project_manager",
    "Operation Management":                            "admin",
    "Digitalization":                                  "admin",
    "Human Resource":                                  "hr_admin",

    # Project teams (mixed disciplines — safest to give common access)
    "H2 Extraction - 10th Floor":                      DEFAULT_FALLBACK_ROLE,
    "PE4 & PE5 REVAMP - 8th Floor":                    DEFAULT_FALLBACK_ROLE,
    "ADOC - 14th Floor":                               DEFAULT_FALLBACK_ROLE,
    "Ruwais Train 1&2 Facilities Fire & Gas- 14 Fr":   DEFAULT_FALLBACK_ROLE,
    "EPCM":                                            DEFAULT_FALLBACK_ROLE,

    # Departments without a matching RBAC role — keep common access until
    # you seed Finance / Sales / Procurement roles (see notes below).
    "Finance & ICT":                                   DEFAULT_FALLBACK_ROLE,
    "Sales":                                           DEFAULT_FALLBACK_ROLE,
    "Procurement":                                     DEFAULT_FALLBACK_ROLE,

    # Delivery bureau — map on discipline suffix
    "Delivery, Process Design":                        "process_engineer",
    "Delivery, Civil & Structural":                    "civil_engineer",
    "Delivery, Electrical":                            "electrical_engineer",
    "Delivery, Instrumentation & Automation":          "instrument_engineer",
    "Delivery, Project Management":                    "project_manager",
    "Delivery, Digital Solutions":                     "admin",

    # Non-Rejlers or should-not-have-RADAI-account
    "Visitor":                                         SKIP,
    "3W Networks":                                     SKIP,
    "Service Provider - LEEDS":                        SKIP,
    "Service Provider - Maintenance":                  SKIP,
    "Rejlers Sweden":                                  SKIP,
    "EDC":                                             SKIP,
    "Residue":                                         SKIP,
}


def resolve_role_for_department(dept_label):
    """
    Return (role_code, confidence) for a raw department label.
      - ('some_role', 'high')   → explicit match
      - ('fallback',  'medium') → mapped to DEFAULT_FALLBACK_ROLE (review recommended)
      - (SKIP,        'skip')   → do not touch this user
      - (None,        'none')   → no mapping — user needs manual assignment
    """
    if not dept_label:
        return None, "none"
    key = dept_label.strip()
    if key in DEPARTMENT_TO_ROLE:
        role = DEPARTMENT_TO_ROLE[key]
        if role == SKIP:
            return SKIP, "skip"
        confidence = "medium" if role == DEFAULT_FALLBACK_ROLE else "high"
        return role, confidence
    return None, "none"
