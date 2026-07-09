"""Soft-coded runtime configuration for the Payroll Engine.

Every threshold, default, and tunable knob lives here so it can be
overridden via environment variables without code changes.
"""
import os
from decimal import Decimal, ROUND_HALF_UP


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.environ.get(name, default)
    try:
        return Decimal(str(raw))
    except Exception:
        return Decimal(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


# ── Money ───────────────────────────────────────────────────────────
CURRENCY = os.environ.get('PAYROLL_CURRENCY', 'AED')
CURRENCY_SYMBOL = os.environ.get('PAYROLL_CURRENCY_SYMBOL', 'AED')
DECIMAL_PLACES = _env_int('PAYROLL_DECIMAL_PLACES', 2)
ROUNDING = ROUND_HALF_UP
QUANTUM = Decimal(10) ** -DECIMAL_PLACES  # e.g. Decimal('0.01')

# ── Calendar ────────────────────────────────────────────────────────
STANDARD_WORKDAYS_PER_MONTH = _env_int('PAYROLL_STANDARD_WORKDAYS', 22)
STANDARD_HOURS_PER_DAY = _env_int('PAYROLL_STANDARD_HOURS_PER_DAY', 8)

# Business-defined "one work day equals N hours" used for converting the
# live biometric hours total on a Payslip / PayrollRun into days. Rejlers
# Abu Dhabi runs a 9-hour workday, so default = 9. Override per-environment
# via env without code changes.
HOURS_PER_WORKDAY = _env_decimal('PAYROLL_HOURS_PER_WORKDAY', '9')


def hours_to_days(hours) -> Decimal:
    """Convert any hours value (int / float / Decimal / str) into days.

    Days are quantised to two decimals (≈ 7-minute precision). Returns
    Decimal('0.00') for ``None`` or unparsable input, never raises.
    """
    if hours is None:
        return Decimal('0.00')
    if HOURS_PER_WORKDAY <= 0:
        return Decimal('0.00')
    try:
        h = hours if isinstance(hours, Decimal) else Decimal(str(hours))
    except Exception:
        return Decimal('0.00')
    return (h / HOURS_PER_WORKDAY).quantize(Decimal('0.01'), rounding=ROUNDING)

# Default contracted hours per month for a new employee. Defaults to
# workdays × hours/day. Override per environment with PAYROLL_DEFAULT_HOURS.
DEFAULT_EMPLOYEE_HOURS = _env_decimal(
    'PAYROLL_DEFAULT_HOURS',
    str(STANDARD_WORKDAYS_PER_MONTH * STANDARD_HOURS_PER_DAY),
)

# ── Hours sourcing (biometric / timesheet integration) ─────────────
# When True (default), the Payroll Engine populates Payslip.hours from
# the live Time Sheet Summary "Total" column at run-generation time and
# whenever HR calls the `refresh_hours_from_timesheet` action.
# Set to False to fall back to each employee's static `PayrollEmployee.hours`
# value (useful in environments where the biometric DB is unreachable).
HOURS_FROM_TIMESHEET = _env_bool('PAYROLL_HOURS_FROM_TIMESHEET', True)

# If the timesheet has no biometric data for an employee in a given month,
# True → use that employee's PayrollEmployee.hours (avoids zeroing them out);
# False → set hours to zero so HR notices missing punches.
HOURS_FALLBACK_TO_EMPLOYEE = _env_bool('PAYROLL_HOURS_FALLBACK_TO_EMPLOYEE', True)

# When `refresh_hours_from_timesheet` runs on an existing payroll run and an
# employee has no biometric rows / overrides for the month, set their hours
# to 0 (so the Payroll table mirrors the Attendance ▸ Summary "Total" column
# byte-for-byte). When False, the previous snapshot is kept — useful if you
# want remote / new-hire defaults preserved.
REFRESH_ZERO_MISSING_HOURS = _env_bool('PAYROLL_REFRESH_ZERO_MISSING', True)

# ── Workflow ────────────────────────────────────────────────────────
# Allow re-opening an approved run back to draft (HR can revoke).
ALLOW_REVERT_TO_DRAFT = _env_bool('PAYROLL_ALLOW_REVERT', True)

# ── Generation ──────────────────────────────────────────────────────
# When generating a new run, copy free-form line items from the prior run.
CARRY_FORWARD_LINE_ITEMS = _env_bool('PAYROLL_CARRY_FORWARD_LINE_ITEMS', False)

# ── Excel ───────────────────────────────────────────────────────────
EXCEL_MAX_UPLOAD_MB = _env_int('PAYROLL_EXCEL_MAX_MB', 25)
EXCEL_DATE_FORMAT = os.environ.get('PAYROLL_EXCEL_DATE_FORMAT', '%Y-%m-%d')

# ── Notifications ───────────────────────────────────────────────────
NOTIFY_ON_TRANSITION = _env_bool('PAYROLL_NOTIFY_TRANSITIONS', True)

# ── RBAC: who may edit / delete employee master records ────────────
# Comma-separated RBAC role codes (matched case-insensitively against
# user.roles[].code). Django superusers and is_staff users are always
# allowed regardless of this list. Override via env if you want HR
# managers to edit employees in addition to super-admins.
EMPLOYEE_WRITE_ROLE_CODES = [
    code.strip().lower()
    for code in os.environ.get(
        'PAYROLL_EMPLOYEE_WRITE_ROLES',
        'superadmin,super_admin,admin',
    ).split(',')
    if code.strip()
]

# ── RBAC: who may create / edit / cancel payroll adjustments ───────
# Defaults to a slightly broader list than employee writes, since HR
# Managers typically queue adjustments long before super-admins finalise
# the run. Override via env without code changes.
ADJUSTMENT_WRITE_ROLE_CODES = [
    code.strip().lower()
    for code in os.environ.get(
        'PAYROLL_ADJUSTMENT_WRITE_ROLES',
        'superadmin,super_admin,admin,hr_manager,senior_hr,hr',
    ).split(',')
    if code.strip()
]

# ── RBAC: who may FORCE-edit / FORCE-delete approved or released runs ──
# This is an emergency override (e.g. correcting an erroneous approval).
# Defaults to super-admins only. Django superusers are always allowed.
# Every force operation is recorded in PayrollWorkflowLog for audit.
RUN_FORCE_OVERRIDE_ROLE_CODES = [
    code.strip().lower()
    for code in os.environ.get(
        'PAYROLL_RUN_FORCE_OVERRIDE_ROLES',
        'superadmin,super_admin',
    ).split(',')
    if code.strip()
]

# ── Comparison module (ValueFrame / Sympa / external HR systems) ───
# Tolerances control when a field-level diff is flagged as a "variance"
# rather than treated as a match. Both absolute AND percentage thresholds
# must be exceeded for currency fields. Hours use a dedicated absolute
# threshold (since 2h ≈ a quarter day, not 2 AED).
COMPARISON_TOL_ABS = _env_decimal('PAYROLL_COMPARISON_TOL_ABS', '1.00')
COMPARISON_TOL_PCT = _env_decimal('PAYROLL_COMPARISON_TOL_PCT', '0.5')   # %
COMPARISON_HOURS_TOL_ABS = _env_decimal('PAYROLL_COMPARISON_HOURS_TOL', '2.00')

# Two-tier severity: variances above the warning threshold are flagged
# "warning"; above the critical threshold are flagged "critical" (red).
COMPARISON_SEVERITY_WARN_PCT = _env_decimal('PAYROLL_COMPARISON_WARN_PCT', '2.0')
COMPARISON_SEVERITY_CRIT_PCT = _env_decimal('PAYROLL_COMPARISON_CRIT_PCT', '10.0')

# Employee matching strategy when external file lacks employee_no.
# Try exact name first, then fuzzy if MATCH_FUZZY=true.
COMPARISON_MATCH_FUZZY = _env_bool('PAYROLL_COMPARISON_MATCH_FUZZY', True)
COMPARISON_MATCH_THRESHOLD = float(os.environ.get(
    'PAYROLL_COMPARISON_MATCH_THRESHOLD', '0.85'))

# Max comparison rows kept per upload (safety cap — typical run has ~300).
COMPARISON_MAX_ROWS = _env_int('PAYROLL_COMPARISON_MAX_ROWS', 5000)
