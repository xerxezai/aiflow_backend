"""
Percentage-Based Salary Deduction Configuration
SOFT-CODED deduction rules for allowance-based payroll adjustments
Used by: DeductionModal, salary_views.apply_deduction
"""
from decimal import Decimal

# ═══════════════════════════════════════════════════════════════════════════
# DEDUCTIBLE ALLOWANCE COMPONENTS (SOFT-CODED)
# ═══════════════════════════════════════════════════════════════════════════
# Only these allowance components can be reduced via percentage deduction
# Maps to keys in SalarySlip.allowances_breakdown JSON field
DEDUCTIBLE_ALLOWANCE_COMPONENTS = [
    {
        'key': 'housing_allowance',
        'label': 'Housing Allowance',
        'priority': 1,  # Deduction priority (1 = first)
        'icon': '🏠',
    },
    {
        'key': 'transportation_allowance',
        'label': 'Transportation',
        'priority': 2,
        'icon': '🚗',
    },
    {
        'key': 'home_leave_allowance',
        'label': 'Home Leave Allowance',
        'priority': 3,
        'icon': '✈️',
    },
    {
        'key': 'other_allowance',
        'label': 'Other Allowance',
        'priority': 4,
        'icon': '💰',
    },
    {
        'key': 'others_allowance',
        'label': 'Other Pay',
        'priority': 5,
        'icon': '💵',
    },
]

# Helper: Extract just the keys for quick lookup
DEDUCTIBLE_KEYS = [comp['key'] for comp in DEDUCTIBLE_ALLOWANCE_COMPONENTS]


# ═══════════════════════════════════════════════════════════════════════════
# DEDUCTION PERCENTAGE RULES (SOFT-CODED)
# ═══════════════════════════════════════════════════════════════════════════

# Preset percentage options shown in modal (quick selection)
DEDUCTION_PERCENTAGE_PRESETS = [5, 10, 15, 20, 25, 30, 50, 75, 100]

# Validation limits
MIN_DEDUCTION_PERCENTAGE = Decimal('0.00')
MAX_DEDUCTION_PERCENTAGE = Decimal('100.00')

# Rounding precision (2 decimals for AED)
DEDUCTION_AMOUNT_PRECISION = 2


# ═══════════════════════════════════════════════════════════════════════════
# AI-DRIVEN DEDUCTION RECOMMENDATIONS (SOFT-CODED)
# ═══════════════════════════════════════════════════════════════════════════
# Intelligent percentage recommendations based on net salary ranges
# Format: (min_net_salary, max_net_salary, recommended_percentage, reason)

DEDUCTION_RECOMMENDATIONS = [
    {
        'min_salary': Decimal('0.00'),
        'max_salary': Decimal('5000.00'),
        'percentage': 5,
        'reason': 'Low salary range - minimal deduction recommended to maintain livable income',
        'severity': 'info',
    },
    {
        'min_salary': Decimal('5000.01'),
        'max_salary': Decimal('10000.00'),
        'percentage': 10,
        'reason': 'Entry-level salary - moderate deduction to balance impact',
        'severity': 'info',
    },
    {
        'min_salary': Decimal('10000.01'),
        'max_salary': Decimal('20000.00'),
        'percentage': 15,
        'reason': 'Mid-range salary - standard deduction percentage',
        'severity': 'warning',
    },
    {
        'min_salary': Decimal('20000.01'),
        'max_salary': Decimal('50000.00'),
        'percentage': 20,
        'reason': 'Higher salary - increased deduction capacity',
        'severity': 'warning',
    },
    {
        'min_salary': Decimal('50000.01'),
        'max_salary': Decimal('999999.99'),
        'percentage': 25,
        'reason': 'Executive salary - higher deduction threshold acceptable',
        'severity': 'error',
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# DEDUCTION STATUS RESTRICTIONS (SOFT-CODED)
# ═══════════════════════════════════════════════════════════════════════════
# Only allow deductions on slips in these statuses
DEDUCTION_ALLOWED_STATUSES = ['draft', 'pending_review']

# Deduction metadata key stored in deductions_breakdown
DEDUCTION_METADATA_KEY = 'percentage_deduction_metadata'

# Key in deductions_breakdown for total percentage deduction
DEDUCTION_AMOUNT_KEY = 'allowance_percentage_deduction'


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_ai_recommendation(net_salary):
    """
    AI-driven percentage recommendation based on salary range
    Returns: {'percentage': int, 'reason': str, 'severity': str}
    """
    net_salary = Decimal(str(net_salary))
    
    for rec in DEDUCTION_RECOMMENDATIONS:
        if rec['min_salary'] <= net_salary <= rec['max_salary']:
            return {
                'percentage': rec['percentage'],
                'reason': rec['reason'],
                'severity': rec['severity'],
            }
    
    # Fallback if no match (shouldn't happen with proper config)
    return {
        'percentage': 10,
        'reason': 'Standard deduction percentage',
        'severity': 'info',
    }


def calculate_deduction_breakdown(allowances_breakdown, percentage):
    """
    Calculate deduction amounts from each deductible allowance component
    
    Args:
        allowances_breakdown (dict): Current allowances breakdown
        percentage (Decimal): Deduction percentage (0-100)
    
    Returns:
        dict: {
            'component_deductions': {component_key: amount, ...},
            'total_deduction': Decimal,
            'affected_components': [component_key, ...],
        }
    """
    percentage = Decimal(str(percentage)) / Decimal('100')
    component_deductions = {}
    total_deduction = Decimal('0.00')
    affected_components = []
    
    for component in DEDUCTIBLE_ALLOWANCE_COMPONENTS:
        key = component['key']
        current_amount = Decimal(str(allowances_breakdown.get(key, 0)))
        
        if current_amount > 0:
            deduction_amount = (current_amount * percentage).quantize(
                Decimal('0.01')  # Round to 2 decimals
            )
            component_deductions[key] = float(deduction_amount)
            total_deduction += deduction_amount
            affected_components.append(key)
    
    return {
        'component_deductions': component_deductions,
        'total_deduction': float(total_deduction),
        'affected_components': affected_components,
    }


def validate_deduction_request(slip, percentage):
    """
    Validate if deduction can be applied to this slip
    
    Returns:
        dict: {'valid': bool, 'error': str or None}
    """
    # Check status
    if slip.status not in DEDUCTION_ALLOWED_STATUSES:
        return {
            'valid': False,
            'error': f'Deductions can only be applied to slips in {", ".join(DEDUCTION_ALLOWED_STATUSES)} status. Current status: {slip.status}'
        }
    
    # Check percentage range
    percentage = Decimal(str(percentage))
    if not (MIN_DEDUCTION_PERCENTAGE <= percentage <= MAX_DEDUCTION_PERCENTAGE):
        return {
            'valid': False,
            'error': f'Deduction percentage must be between {MIN_DEDUCTION_PERCENTAGE}% and {MAX_DEDUCTION_PERCENTAGE}%'
        }
    
    # Check if there are any deductible allowances
    allowances = slip.allowances_breakdown or {}
    has_deductible = any(
        Decimal(str(allowances.get(comp['key'], 0))) > 0
        for comp in DEDUCTIBLE_ALLOWANCE_COMPONENTS
    )
    
    if not has_deductible:
        return {
            'valid': False,
            'error': 'No deductible allowances found in this salary slip'
        }
    
    return {'valid': True, 'error': None}
