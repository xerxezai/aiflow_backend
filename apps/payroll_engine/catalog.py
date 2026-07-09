"""Payroll Engine catalog — soft-coded dictionaries for components,
statuses, payment modes, and Excel column mappings.

Treat this file as the single source of truth. Views, services, and
the frontend ``payrollEngine.config.js`` mirror these definitions.
Never hard-code a label or code outside this module.
"""
from __future__ import annotations
from typing import Dict, List

# ── Workflow ────────────────────────────────────────────────────────
class Status:
    """Run + slip lifecycle. Backed by the WORKFLOW_STATUSES below."""
    DRAFT = 'draft'
    HR_APPROVED = 'hr_approved'
    FINANCE_APPROVED = 'finance_approved'
    RELEASED = 'released'


WORKFLOW_STATUSES: List[Dict] = [
    {'code': Status.DRAFT,           'label': 'Draft',            'tone': 'gray',   'order': 0},
    {'code': Status.HR_APPROVED,     'label': 'HR Approved',      'tone': 'blue',   'order': 1},
    {'code': Status.FINANCE_APPROVED,'label': 'Finance Approved', 'tone': 'amber',  'order': 2},
    {'code': Status.RELEASED,        'label': 'Released',         'tone': 'green',  'order': 3},
]

# from_status → list of allowed next states
WORKFLOW_TRANSITIONS: Dict[str, List[str]] = {
    Status.DRAFT:            [Status.HR_APPROVED],
    Status.HR_APPROVED:      [Status.FINANCE_APPROVED, Status.DRAFT],
    Status.FINANCE_APPROVED: [Status.RELEASED, Status.HR_APPROVED],
    Status.RELEASED:         [],
}

# Role gating per transition (intersect with RBAC; empty = any authenticated)
WORKFLOW_ROLES: Dict[str, List[str]] = {
    Status.HR_APPROVED:      ['HR Manager', 'HR', 'Admin'],
    Status.FINANCE_APPROVED: ['Finance Manager', 'Finance', 'Admin'],
    Status.RELEASED:         ['Finance Manager', 'Finance', 'Admin'],
    Status.DRAFT:            ['HR Manager', 'HR', 'Admin', 'Finance', 'Finance Manager'],
}


# ── Payment modes ───────────────────────────────────────────────────
PAYMENT_MODES: List[Dict] = [
    {'code': 'WPS',            'label': 'WPS'},
    {'code': 'WPS-RSI',        'label': 'WPS – RSI'},
    {'code': 'BANK_TRANSFER',  'label': 'Bank Transfer'},
    {'code': 'BANK_TRANSFER_RSI', 'label': 'Bank Transfer – RSI'},
    {'code': 'CHEQUE',         'label': 'Cheque'},
    {'code': 'CASH',           'label': 'Cash'},
]
DEFAULT_PAYMENT_MODE = 'WPS'


# ── Fixed earning columns (stored directly on Payslip) ──────────────
# These are the 4 standard earnings every payslip has, modelled as
# columns on the Payslip table for fast aggregation.
FIXED_EARNINGS: List[Dict] = [
    {'code': 'basic',      'label': 'Basic Salary',           'field': 'basic'},
    {'code': 'housing',    'label': 'Housing Allowance',      'field': 'housing'},
    {'code': 'transport',  'label': 'Transportation Allowance','field': 'transport'},
    {'code': 'home_leave', 'label': 'Home Leave Allowance',   'field': 'home_leave'},
]


# ── Free-form earning components (PayslipLineItem rows) ─────────────
EARNING_COMPONENTS: List[Dict] = [
    {'code': 'leave_payout',     'label': 'Leave Payout',          'taxable': False},
    {'code': 'overtime',         'label': 'Overtime',              'taxable': False},
    {'code': 'bonus',            'label': 'Bonus',                 'taxable': False},
    {'code': 'commission',       'label': 'Commission',            'taxable': False},
    {'code': 'reimbursement',    'label': 'Reimbursement',         'taxable': False},
    {'code': 'gratuity',         'label': 'Gratuity',              'taxable': False},
    {'code': 'eosb',             'label': 'End of Service Benefit','taxable': False},
    {'code': 'other_earning',    'label': 'Other',                 'taxable': False},
]


# ── Deduction components (PayslipLineItem rows) ─────────────────────
DEDUCTION_COMPONENTS: List[Dict] = [
    {'code': 'absent',                'label': 'Absent'},
    {'code': 'sick_leave',            'label': 'Sick Leave'},
    {'code': 'housing_advance',       'label': 'Housing Allowance Advance'},
    {'code': 'salary_advance',        'label': 'Salary Advance'},
    {'code': 'telephone',             'label': 'Telephone'},
    {'code': 'car_benefit',           'label': 'Car Benefit'},
    {'code': 'housing_benefit',       'label': 'Housing Benefit'},
    {'code': 'dependant_insurance',   'label': 'Dependant Insurance'},
    {'code': 'utility_bill',          'label': 'Utility Bill'},
    {'code': 'fine',                  'label': 'Fine / Penalty'},
    {'code': 'loan_repayment',        'label': 'Loan Repayment'},
    {'code': 'other_deduction',       'label': 'Other'},
]


# ── Line item kinds ────────────────────────────────────────────────
class LineItemKind:
    EARNING = 'earning'
    DEDUCTION = 'deduction'


LINE_ITEM_KINDS: List[Dict] = [
    {'code': LineItemKind.EARNING,   'label': 'Earning'},
    {'code': LineItemKind.DEDUCTION, 'label': 'Deduction'},
]


# ── Line item sources (provenance) ──────────────────────────────────
class LineItemSource:
    AUTO = 'auto'              # created by run_generator
    MANUAL = 'manual'          # added/edited in UI
    EXCEL = 'excel'            # uploaded via Excel import
    ADJUSTMENT = 'adjustment'  # materialised from PayrollAdjustment


LINE_ITEM_SOURCES: List[Dict] = [
    {'code': LineItemSource.AUTO,       'label': 'Auto-generated'},
    {'code': LineItemSource.MANUAL,     'label': 'Manual entry'},
    {'code': LineItemSource.EXCEL,      'label': 'Excel upload'},
    {'code': LineItemSource.ADJUSTMENT, 'label': 'Adjustment'},
]


# ── Adjustment statuses ─────────────────────────────────────────────
class AdjustmentStatus:
    PENDING = 'pending'
    APPLIED = 'applied'
    CANCELLED = 'cancelled'


ADJUSTMENT_STATUSES: List[Dict] = [
    {'code': AdjustmentStatus.PENDING,   'label': 'Pending', 'tone': 'amber'},
    {'code': AdjustmentStatus.APPLIED,   'label': 'Applied', 'tone': 'green'},
    {'code': AdjustmentStatus.CANCELLED, 'label': 'Cancelled','tone': 'gray'},
]


# ── Reference lists ─────────────────────────────────────────────────
GRADE_OPTIONS: List[str] = [
    'Senior Assistant',
    'Assistant',
    'Senior Engineer',
    'Engineer',
    'Junior Engineer',
    'Manager',
    'Senior Manager',
    'Director',
    'VP',
    'GM',
    'Other',
]

NATIONALITY_GROUPS: List[Dict] = [
    {'code': 'expats',   'label': 'Expats'},
    {'code': 'locals',   'label': 'UAE Nationals'},
    {'code': 'gcc',      'label': 'GCC Nationals'},
]


# ── Excel I/O — Master roster sheet ────────────────────────────────
# Maps spreadsheet column index (1-based, matching openpyxl) to the
# canonical PayrollEmployee field. The header row is row 4 in the
# April 2026 master sheet; data starts at row 5.
EXCEL_MASTER_HEADER_ROW = 4
EXCEL_MASTER_DATA_START_ROW = 5
EXCEL_MASTER_TOTAL_LABEL = 'TOTAL'  # row that signals end of data

EXCEL_MASTER_COLUMN_MAP: Dict[int, str] = {
    1:  'sr_no',
    2:  'full_name',
    3:  'mol_no',
    4:  'routing_code',
    5:  'iban',
    6:  'bank_name',
    7:  'employee_no',
    8:  'joining_date',
    9:  'department',
    10: 'discipline',
    11: 'designation',
    12: 'grade',
    13: 'nationality_group',
    14: 'basic',
    15: 'housing',
    16: 'transport',
    17: 'home_leave',
    18: 'other_allowance',          # rarely used; treated as earning line
    19: 'other_pay_amount',         # "Other Pay" → free-form earning line
    20: 'other_pay_details',        # description for other_pay_amount
    21: 'deduction_amount',         # free-form deduction
    22: 'deduction_details',        # description for deduction_amount
    23: 'final_remuneration',       # net (computed; used for validation)
    24: 'payment_mode',
    25: 'hours',                    # appended — monthly working hours (live biometric)
}

# Header label for the Hours column (kept here so header row + map stay in sync).
EXCEL_MASTER_HOURS_HEADER = 'Hours'

# ── Working-days policy ─────────────────────────────────────────────
# Default number of working days assumed per calendar month (UAE standard).
# HR can override this at the run level when generating a payroll run.
# Must stay in sync with LEAVE_ENCASHMENT_WORKING_DAYS in
# frontend/src/config/hrLeave.config.js and ENCASHMENT_WORKING_DAYS in
# backend/apps/payroll/services/leave_encashment.py.
DEFAULT_WORKING_DAYS_PER_MONTH: int = 22

# ── Public-holiday region filter ────────────────────────────────────────────
# Region codes to count when computing public_holidays_in_month at run
# generation. Must match PublicHoliday.region choices in apps.payroll.models.
# 'AE' = UAE national holidays; 'COMPANY' = company-wide days off.
# Change here — nowhere else in the codebase uses this filter directly.
DEFAULT_PH_REGIONS: list = ['AE', 'COMPANY']

# ── Leave categories surfaced in the payslip table ───────────────────────
# Maps Payslip field name → LeaveType.category value (from apps.payroll).
# Adding a new leave type here automatically surfaces it in run generation
# and the serializer — also add the DB field + migration + config column.
LEAVE_CATEGORIES_FOR_PAYROLL: dict = {
    'annual_leave_days': 'annual',
    'unpaid_leave_days': 'unpaid',
}

# ── External import field map ───────────────────────────────────────
# Maps (file_type, comparison_field) → Payslip field to update.
# Add a new entry here when a file profile introduces a new field.
# Must stay in sync with EXTERNAL_UPLOAD_FILE_TYPES in payrollEngine.config.js.
EXTERNAL_IMPORT_FIELD_MAP: dict = {
    'valueframe': {
        'hours':      'hours',            # VF Total Hours → payslip.hours
        'leave_days': 'annual_leave_days', # VF Annual Vacation → payslip.annual_leave_days
    },
    'sympa': {
        'basic':     'basic',
        'housing':   'housing',
        'transport': 'transport',
        'home_leave': 'home_leave',
    },
    'generic': {},  # user must configure column mapping (future)
}


# ── Excel I/O — Per-employee payslip sheet ──────────────────────────
# Cell coordinates ARE the layout (matches Excel template exactly).
EXCEL_PAYSLIP_LAYOUT: Dict[str, tuple] = {
    'title_row':            (5, 1),   # "Payslip for the month of"
    'period_label':         (5, 2),   # "APRIL 2026"

    'name_label':           (8, 1),
    'name_value':           (8, 2),
    'joining_date_label':   (8, 3),
    'joining_date_value':   (8, 4),

    'employee_no_label':    (9, 1),
    'employee_no_value':    (9, 2),

    'title_label':          (10, 1),
    'title_value':          (10, 2),

    # Hours / Month — single-cell block above the SALARY header so the
    # biometric figure is visible at the top of every payslip.
    'hours_label':          (11, 1),
    'hours_value':          (11, 2),

    'salary_header':        (13, 1),  # "SALARY"
    'deductions_header':    (13, 3),  # "DEDUCTIONS"

    'col_headers_row':      14,

    'basic_label':          (15, 1),
    'basic_value':          (15, 2),
    'housing_label':        (16, 1),
    'housing_value':        (16, 2),
    'transport_label':      (17, 1),
    'transport_value':      (17, 2),
    'home_leave_label':     (18, 1),
    'home_leave_value':     (18, 2),

    'others_earning_label': (19, 1),  # "Others:"
    'others_earning_value': (19, 2),
    'others_earning_detail_row': 20,  # description row beneath "Others:"

    # Deduction column starts at column 3 (same rows 15..)
    'deduction_start_row':  15,
    'deduction_label_col':  3,
    'deduction_detail_col': 4,
    'deduction_value_col':  5,

    'total_earnings_label': (21, 1),
    'total_earnings_value': (21, 2),
    'total_deductions_label': (21, 3),
    'total_deductions_value': (21, 5),

    'net_label':            (23, 1),  # "NET PAYABLE Amount"
    'net_value':            (23, 3),

    'payment_mode_label':   (25, 1),
    'payment_mode_value':   (25, 2),
}


# ── Lookup helpers ─────────────────────────────────────────────────
def status_meta(code: str) -> Dict:
    return next((s for s in WORKFLOW_STATUSES if s['code'] == code), {})


def next_statuses(current: str) -> List[str]:
    return WORKFLOW_TRANSITIONS.get(current, [])


def is_terminal(code: str) -> bool:
    return len(next_statuses(code)) == 0


def fixed_earning_fields() -> List[str]:
    return [e['field'] for e in FIXED_EARNINGS]


def earning_codes() -> List[str]:
    return [c['code'] for c in EARNING_COMPONENTS]


def deduction_codes() -> List[str]:
    return [c['code'] for c in DEDUCTION_COMPONENTS]


def payment_mode_codes() -> List[str]:
    return [p['code'] for p in PAYMENT_MODES]


def normalise_payment_mode(raw) -> str:
    """Map any Excel string to a known code. Defaults to DEFAULT_PAYMENT_MODE."""
    if not raw:
        return DEFAULT_PAYMENT_MODE
    s = str(raw).strip().upper().replace(' ', '').replace('–', '-').replace('—', '-')
    aliases = {
        'WPS': 'WPS',
        'WPS-RSI': 'WPS-RSI',
        'WPSRSI': 'WPS-RSI',
        'BANKTRANSFER': 'BANK_TRANSFER',
        'BANKTRANSFER-RSI': 'BANK_TRANSFER_RSI',
        'CHQ': 'CHEQUE',
        'CHEQUE': 'CHEQUE',
        'CASH': 'CASH',
    }
    return aliases.get(s, DEFAULT_PAYMENT_MODE)


# ── Bulk percentage deduction ───────────────────────────────────────
# Single source of truth for the HR "Apply % deduction" tool.
# - PROTECTED_FIELDS can never be picked as a calculation base.
# - ALLOWED_FIELDS are the choices HR sees in the modal.
# - DEFAULT_FIELDS are pre-checked when the modal opens.
BULK_DEDUCTION_PROTECTED_FIELDS: List[str] = ['basic']
BULK_DEDUCTION_ALLOWED_FIELDS:   List[str] = [
    'housing', 'transport', 'home_leave', 'other_earnings',
]
BULK_DEDUCTION_DEFAULT_FIELDS:   List[str] = list(BULK_DEDUCTION_ALLOWED_FIELDS)

# The component code that tags the generated PayslipLineItem. Using a unique
# code lets us upsert/replace previous bulk runs idempotently.
BULK_DEDUCTION_COMPONENT_CODE = 'bulk_pct_deduction'
BULK_DEDUCTION_DEFAULT_LABEL  = 'Bulk Salary Deduction'

# Soft limits (kept as strings so we can convert to Decimal without import here).
BULK_DEDUCTION_MIN_PCT = '0.01'
BULK_DEDUCTION_MAX_PCT = '100.00'


# ── Comparison module — external file profiles ─────────────────────
# Each profile drives how an uploaded vendor file is parsed:
#   * header_row     : 1-based row where canonical headers live (None ⇒ auto-scan)
#   * header_scan_max: how far down to look when header_row is None
#   * field_aliases  : per-canonical-field list of header strings the parser
#                      will match (case-insensitive, whitespace-collapsed).
#                      The first non-empty match wins.
#   * compose        : optional rules that combine multiple cells into one
#                      canonical field, e.g. SYMPA splits the name across
#                      "Surname" + "Preferred given name".
# Add a new vendor by appending another dict — no service edits needed.
class ComparisonStatus:
    MATCH = 'match'
    VARIANCE = 'variance'
    EXTERNAL_ONLY = 'external_only'   # row in external file, not in our payroll
    PAYROLL_ONLY = 'payroll_only'     # employee on our payroll, missing from file


COMPARISON_STATUS_LABELS = {
    ComparisonStatus.MATCH:         {'label': 'Match',                 'tone': 'green'},
    ComparisonStatus.VARIANCE:      {'label': 'Variance',              'tone': 'amber'},
    ComparisonStatus.EXTERNAL_ONLY: {'label': 'External-only',         'tone': 'sky'},
    ComparisonStatus.PAYROLL_ONLY:  {'label': 'Missing from external', 'tone': 'rose'},
}


# Canonical fields we know how to compare. Order = display order.
COMPARISON_FIELDS: List[Dict] = [
    {'field': 'hours',            'label': 'Hours',            'kind': 'hours'},
    {'field': 'basic',            'label': 'Basic',            'kind': 'currency'},
    {'field': 'housing',          'label': 'Housing',          'kind': 'currency'},
    {'field': 'transport',        'label': 'Transport',        'kind': 'currency'},
    {'field': 'home_leave',       'label': 'Home Leave',       'kind': 'currency'},
    {'field': 'other_earnings',   'label': 'Other Earnings',   'kind': 'currency'},
    {'field': 'total_deductions', 'label': 'Total Deductions', 'kind': 'currency'},
    {'field': 'gross_earnings',   'label': 'Gross Earnings',   'kind': 'currency'},
    {'field': 'net_payable',      'label': 'Net Payable',      'kind': 'currency'},
    {'field': 'leave_days',       'label': 'Leave Days',       'kind': 'days'},
]


# Universal header aliases — checked by every profile (case + whitespace
# insensitive). Profile-specific aliases extend these.
COMPARISON_FIELD_ALIASES: Dict[str, List[str]] = {
    'employee_no':      ['employee number', 'employee no', 'employee #', 'emp no',
                         'emp #', 'staff id', 'staff no', 'staff #', 'badge', 'badge no',
                         'employee id'],
    'full_name':        ['employee name', 'full name', 'name', 'staff name'],
    'hours':            ['total hours', 'working hours', 'invoicing hours',
                         'hours worked', 'normal', 'attended hours'],
    'basic':            ['basic', 'basic salary', 'base salary',
                         'currently valid monthly base salary', 'monthly base salary'],
    'housing':          ['housing', 'housing allowance',
                         'currently valid housing allowance'],
    'transport':        ['transport', 'transpo', 'transportation',
                         'transport allowance', 'transportation allowance',
                         'currently valid transportation allowance'],
    'home_leave':       ['home leave', 'home leave allowance',
                         'currently valid home leave allowance'],
    'other_earnings':   ['other allowance', 'other pay', 'others',
                         'other earnings', 'overtime', 'bonus'],
    'total_deductions': ['deduction', 'deductions', 'total deductions',
                         'salary deduction', 'absence deduction'],
    'gross_earnings':   ['gross', 'gross salary', 'monthly gross salary',
                         'gross earnings', 'total earnings'],
    'net_payable':      ['net', 'net payable', 'net salary', 'take home',
                         'final remuneration'],
    'leave_days':       ['annual vacation', 'leave', 'leave days', 'sick leave'],
}


# Per-vendor profiles. 'auto' = use generic aliases + header auto-scan.
COMPARISON_PROFILES: Dict[str, Dict] = {
    'auto': {
        'label': 'Auto-detect (smart fuzzy match)',
        'header_row': None,
        'header_scan_max': 20,
        'field_aliases': {},
        'compose': {},
    },
    'valueframe': {
        'label': 'ValueFrame — Wage Type Report (hours)',
        'header_row': None,        # ValueFrame puts the table at row 12 but row may shift
        'header_scan_max': 20,     # so scan
        'field_aliases': {
            'employee_no':  ['employee number'],
            'full_name':    ['employee name'],
            'hours':        ['total hours'],
            'leave_days':   ['annual vacation', 'sick leave, medical certificate',
                             'paternity leave, paid', 'other paid leave'],
        },
        'compose': {},
    },
    'sympa': {
        'label': 'Sympa — Salary Master (basic + allowances)',
        'header_row': 1,
        'header_scan_max': 1,
        'field_aliases': {
            'basic':         ['currently valid monthly base salary'],
            'housing':       ['currently valid housing allowance'],
            'transport':     ['currently valid transportation allowance'],
            'home_leave':    ['currently valid home leave allowance'],
            'gross_earnings':['monthly gross salary'],
        },
        # SYMPA has no single full-name column; compose from 2 columns.
        'compose': {
            'full_name': {
                'parts':     ['preferred given name', 'surname'],
                'separator': ' ',
            },
        },
    },
    'generic': {
        'label': 'Generic XLSX/CSV (header in row 1)',
        'header_row': 1,
        'header_scan_max': 1,
        'field_aliases': {},
        'compose': {},
    },
}


def comparison_profile(code: str) -> Dict:
    """Return profile dict by code; falls back to 'auto'."""
    return COMPARISON_PROFILES.get(code) or COMPARISON_PROFILES['auto']


def comparison_field_codes() -> List[str]:
    return [f['field'] for f in COMPARISON_FIELDS]


def comparison_field_meta(field: str) -> Dict:
    return next((f for f in COMPARISON_FIELDS if f['field'] == field), {})

