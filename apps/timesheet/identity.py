"""
Employee Identity Normalization — Canonical lookup utilities
============================================================
All tables that store employee identity fields (employee_code, employee_name,
employee_email) MUST use these helpers at every write boundary:
  • Model.save() overrides
  • Ingest views (mirror_views.py)
  • Management commands that import data

This ensures that biometric device variants ('22393', '22393 ', ' 22393') and
email case variants ('john@x.com', 'JOHN@X.COM') all resolve to the same
canonical record, eliminating duplicate rows across every report and export.

Soft-coded via environment variables:
  TIMESHEET_CODE_NORM   'strip' (default) | 'strip_upper' | 'strip_zfill_6'
  TIMESHEET_EMAIL_NORM  'strip_lower' (default) | 'strip'
  TIMESHEET_NAME_NORM   'strip_collapse' (default) | 'strip'
"""
from __future__ import annotations

import re
from decouple import config as _env


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded normalization strategies (overridable per environment)
# ─────────────────────────────────────────────────────────────────────────────
# CODE_NORM: how to normalise biometric employee_code values.
#   'strip'        — strip leading/trailing whitespace only (default)
#   'strip_upper'  — strip + uppercase (for alpha codes like 'abc-001')
#   'strip_zfill_6'— strip + zero-pad to 6 digits (for systems using '00822' etc.)
CODE_NORM = _env('TIMESHEET_CODE_NORM', default='strip').lower().strip()

# EMAIL_NORM: how to normalise email values.
#   'strip_lower'  — strip + lowercase (default, RFC-compliant)
#   'strip'        — strip only (use when downstream is case-sensitive)
EMAIL_NORM = _env('TIMESHEET_EMAIL_NORM', default='strip_lower').lower().strip()

# NAME_NORM: how to normalise employee name strings.
#   'strip_collapse' — strip + collapse internal whitespace (default)
#   'strip'          — strip only
NAME_NORM = _env('TIMESHEET_NAME_NORM', default='strip_collapse').lower().strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public normalization functions
# ─────────────────────────────────────────────────────────────────────────────

def norm_code(code: str | None) -> str:
    """Return a canonically-normalised employee_code.

    Examples (default 'strip'):
      '22393'   → '22393'
      ' 22393'  → '22393'
      '22393 '  → '22393'
      None      → ''
    """
    s = str(code or '').strip()
    if CODE_NORM == 'strip_upper':
        return s.upper()
    if CODE_NORM == 'strip_zfill_6':
        return s.zfill(6) if s.isdigit() else s
    return s  # default: strip only


def norm_email(email: str | None) -> str:
    """Return a canonically-normalised email address.

    Examples (default 'strip_lower'):
      'John@Example.COM '  → 'john@example.com'
      None                 → ''
    """
    s = str(email or '').strip()
    if EMAIL_NORM in ('strip_lower', 'lower'):
        return s.lower()
    return s  # 'strip' only


def norm_name(name: str | None) -> str:
    """Return a canonically-normalised employee name.

    Examples (default 'strip_collapse'):
      '  John   Smith  '  → 'John Smith'
      None                → ''
    """
    s = str(name or '').strip()
    if NAME_NORM in ('strip_collapse', 'collapse'):
        return re.sub(r'\s+', ' ', s)
    return s  # strip only


def norm_identity(
    code: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> dict:
    """Normalise all three identity fields at once.

    Returns a dict with keys: code, email, name.
    Use as a one-liner at ingest/import boundaries::

        ident = norm_identity(ev['employee_code'], ev['employee_email'], ev['employee_name'])
        ev['employee_code']  = ident['code']
        ev['employee_email'] = ident['email']
        ev['employee_name']  = ident['name']
    """
    return {
        'code':  norm_code(code),
        'email': norm_email(email),
        'name':  norm_name(name),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lookup key helpers — used by resolvers so the comparison side is also
# always normalised (prevents "resolved on ingest but not on lookup" bugs).
# ─────────────────────────────────────────────────────────────────────────────

def lookup_code(code: str | None) -> str:
    """Same as norm_code — alias kept for semantic clarity at lookup sites."""
    return norm_code(code)


def lookup_email(email: str | None) -> str:
    """Same as norm_email — alias kept for semantic clarity at lookup sites."""
    return norm_email(email)


def lookup_name(name: str | None) -> str:
    """Normalise a name for fuzzy lookup (lowercase + collapse whitespace).
    Lower-cased intentionally so 'John Smith' == 'john smith' in lookups.
    """
    return re.sub(r'\s+', ' ', str(name or '').strip().lower())
