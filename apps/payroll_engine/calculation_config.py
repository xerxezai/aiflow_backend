"""Soft-coded Payroll Calculation Rules — Business Logic Configuration

All payroll calculation formulas, thresholds, and rules live here as constants.
HR and Finance can modify these values without touching calculation code.

Last Updated: 2026-07-08
"""
from decimal import Decimal
import os


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


# ═══════════════════════════════════════════════════════════════════════════
# WORKING HOURS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Standard working hours per day for different employee categories
# Emirates employees (with only BASIC salary component) work 8 hours/day
# Other nationalities work 9 hours/day
EMIRATES_HOURS_PER_DAY = _env_decimal('PAYROLL_EMIRATES_HOURS_PER_DAY', '8.0')
OTHER_HOURS_PER_DAY = _env_decimal('PAYROLL_OTHER_HOURS_PER_DAY', '9.0')

# Threshold to detect Emirates employees: if housing + transport + home_leave = 0,
# employee is classified as Emirates national with 8-hour day
EMIRATES_DETECTION_THRESHOLD = Decimal('0.01')  # anything below this is considered zero


def get_employee_hours_per_day(basic: Decimal, housing: Decimal, 
                                 transport: Decimal, home_leave: Decimal) -> Decimal:
    """Determine working hours per day based on salary components.
    
    Logic:
    - If employee has ONLY basic salary (no housing/transport/home_leave),
      they are Emirates national → 8 hours/day
    - Otherwise → 9 hours/day (standard for expatriates)
    
    Args:
        basic: Basic salary component
        housing: Housing allowance
        transport: Transport allowance
        home_leave: Home leave allowance
        
    Returns:
        Decimal hours per working day (8.0 or 9.0)
    """
    total_allowances = housing + transport + home_leave
    
    if total_allowances < EMIRATES_DETECTION_THRESHOLD:
        # Emirates national with only BASIC salary
        return EMIRATES_HOURS_PER_DAY
    else:
        # Expatriate with full package
        return OTHER_HOURS_PER_DAY


# ═══════════════════════════════════════════════════════════════════════════
# SALARY CALCULATION FORMULAS
# ═══════════════════════════════════════════════════════════════════════════

# GROSS SALARY = Basic + Housing + Transport + Home Leave + Other Earnings
# This is calculated from fixed components plus any additional earnings (bonus, overtime, etc.)

# DEDUCTIONS CONFIGURATION
# Unpaid leave is deducted from salary based on daily rate
STANDARD_WORKDAYS_PER_MONTH = _env_int('PAYROLL_STANDARD_WORKDAYS', 26)

def calculate_daily_rate(monthly_gross: Decimal) -> Decimal:
    """Calculate daily salary rate for unpaid leave deductions.
    
    Formula: Daily Rate = Monthly Gross Salary ÷ Standard Workdays per Month
    Default: 26 working days per month (UAE labor law standard)
    """
    if STANDARD_WORKDAYS_PER_MONTH <= 0:
        return Decimal('0.00')
    return monthly_gross / Decimal(STANDARD_WORKDAYS_PER_MONTH)


def calculate_unpaid_leave_deduction(unpaid_days: Decimal, monthly_gross: Decimal) -> Decimal:
    """Calculate deduction amount for unpaid leave days.
    
    Formula: Deduction = Unpaid Leave Days × Daily Rate
    
    Args:
        unpaid_days: Number of unpaid leave days taken
        monthly_gross: Employee's monthly gross salary
        
    Returns:
        Decimal deduction amount
    """
    if unpaid_days <= 0:
        return Decimal('0.00')
    
    daily_rate = calculate_daily_rate(monthly_gross)
    return unpaid_days * daily_rate


# ═══════════════════════════════════════════════════════════════════════════
# TOTAL WORKED DAYS CALCULATION
# ═══════════════════════════════════════════════════════════════════════════

def calculate_total_worked_days(
    hours_present: Decimal,
    hours_per_day: Decimal,
    public_holiday_days: Decimal,
    annual_leave_days: Decimal
) -> Decimal:
    """Calculate total worked days for payroll.
    
    Formula: Total Worked Days = Days from Hours + Public Holidays + Annual Leave
    
    Where:
    - Days from Hours = Total Hours ÷ Hours per Working Day (8 or 9 based on nationality)
    - Public Holidays = Official holidays in the month (counted as worked days)
    - Annual Leave = Approved paid annual leave (counted as worked days)
    - Unpaid Leave = NOT counted (already excluded from hours)
    
    This represents the total "credited" days for salary calculation purposes.
    
    Args:
        hours_present: Total biometric hours recorded (or ValueFrame hours)
        hours_per_day: Employee's hours per working day (8 or 9)
        public_holiday_days: Public holidays in the payroll month
        annual_leave_days: Approved annual leave days taken
        
    Returns:
        Decimal total worked days (includes actual work + paid absences)
    """
    if hours_per_day <= 0:
        days_from_hours = Decimal('0.00')
    else:
        days_from_hours = hours_present / hours_per_day
    
    total_worked = days_from_hours + public_holiday_days + annual_leave_days
    
    return total_worked.quantize(Decimal('0.01'))


# ═══════════════════════════════════════════════════════════════════════════
# NET PAYABLE CALCULATION
# ═══════════════════════════════════════════════════════════════════════════

def calculate_net_payable(
    gross_earnings: Decimal,
    unpaid_leave_deduction: Decimal,
    other_deductions: Decimal
) -> Decimal:
    """Calculate final net payable amount.
    
    Formula: Net Payable = Gross Earnings - Total Deductions
    
    Where:
    - Gross Earnings = Basic + Housing + Transport + Home Leave + Other Earnings
    - Total Deductions = Unpaid Leave Deduction + Other Deductions
    
    Args:
        gross_earnings: Total gross salary
        unpaid_leave_deduction: Calculated unpaid leave penalty
        other_deductions: All other deductions (loans, advances, etc.)
        
    Returns:
        Decimal net amount to pay
    """
    total_deductions = unpaid_leave_deduction + other_deductions
    net = gross_earnings - total_deductions
    
    return net.quantize(Decimal('0.01'))


# ═══════════════════════════════════════════════════════════════════════════
# VALUEFRAME EXCEL INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

# Column mapping for ValueFrame Excel upload
# HR can upload ValueFrame reports with "Total Hours" column
VALUEFRAME_COLUMN_MAPPING = {
    'employee_code': ['Employee Code', 'Emp Code', 'Code', 'Employee No', 'Emp No'],
    'employee_name': ['Employee Name', 'Name', 'Full Name'],
    'total_hours': ['Total Hours', 'Hours', 'Total', 'Working Hours'],
    'annual_leave': ['Annual Leave', 'AL', 'Paid Leave'],
    'unpaid_leave': ['Unpaid Leave', 'UL', 'LWP', 'Leave Without Pay'],
}

# Fuzzy matching threshold for column name detection (0-1, higher = stricter)
VALUEFRAME_FUZZY_THRESHOLD = 0.8


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION RULES
# ═══════════════════════════════════════════════════════════════════════════

# Maximum reasonable hours per month (to catch data entry errors)
MAX_HOURS_PER_MONTH = _env_decimal('PAYROLL_MAX_HOURS_PER_MONTH', '250.0')

# Minimum hours threshold (below this triggers warning)
MIN_HOURS_WARNING = _env_decimal('PAYROLL_MIN_HOURS_WARNING', '100.0')

# Maximum unpaid leave days per month (to catch errors)
MAX_UNPAID_LEAVE_DAYS = _env_decimal('PAYROLL_MAX_UNPAID_DAYS', '10.0')


def validate_hours(hours: Decimal) -> tuple[bool, str]:
    """Validate monthly hours are within reasonable range.
    
    Returns:
        (is_valid, error_message)
    """
    if hours < 0:
        return False, "Hours cannot be negative"
    
    if hours > MAX_HOURS_PER_MONTH:
        return False, f"Hours exceed maximum {MAX_HOURS_PER_MONTH} (possible data error)"
    
    if hours < MIN_HOURS_WARNING:
        return True, f"Warning: Hours below {MIN_HOURS_WARNING} (check if employee was absent)"
    
    return True, ""


def validate_leave_days(unpaid_days: Decimal) -> tuple[bool, str]:
    """Validate unpaid leave days are reasonable.
    
    Returns:
        (is_valid, error_message)
    """
    if unpaid_days < 0:
        return False, "Unpaid leave days cannot be negative"
    
    if unpaid_days > MAX_UNPAID_LEAVE_DAYS:
        return False, f"Unpaid leave exceeds maximum {MAX_UNPAID_LEAVE_DAYS} days (check data)"
    
    return True, ""


# ═══════════════════════════════════════════════════════════════════════════
# DOCUMENTATION
# ═══════════════════════════════════════════════════════════════════════════

CALCULATION_HELP_TEXT = {
    'total_worked_days': (
        'Total credited days for salary calculation. '
        'Formula: (Hours ÷ Hours per Day) + Public Holidays + Annual Leave. '
        'Excludes unpaid leave.'
    ),
    'hours_per_day': (
        'Working hours per day. Emirates nationals (basic only) = 8 hours. '
        'Expatriates (with allowances) = 9 hours.'
    ),
    'unpaid_leave_deduction': (
        'Salary deduction for unpaid leave. '
        'Formula: Unpaid Days × (Monthly Gross ÷ 26 working days).'
    ),
    'net_payable': (
        'Final amount to pay after all deductions. '
        'Formula: Gross Earnings - Unpaid Leave Deduction - Other Deductions.'
    ),
}
