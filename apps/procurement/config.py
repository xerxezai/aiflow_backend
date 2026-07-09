"""
Professional Procurement System Configuration - Master Config File
═══════════════════════════════════════════════════════════════════════

Soft-coded configuration for project-based procurement with master database.
All business rules, thresholds, categories, and UI settings centralized here.

Usage:
  - Backend: Import constants for business logic
  - Frontend: Mirror as JavaScript config (procurement.config.js)
  - Future: Load from database for runtime configuration
"""

# ══════════════════════════════════════════════════════════════════════════════
# PROJECT MASTER CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

PROJECT_TYPES = {
    'engineering': {
        'code': 'engineering',
        'name': 'Engineering Services',
        'icon': 'CogIcon',
        'color': 'indigo',
        'default_budget_categories': ['engineering', 'software', 'consultancy']
    },
    'construction': {
        'code': 'construction',
        'name': 'Construction',
        'icon': 'WrenchIcon',
        'color': 'orange',
        'default_budget_categories': ['construction', 'equipment', 'manpower']
    },
    'maintenance': {
        'code': 'maintenance',
        'name': 'Maintenance & Operations',
        'icon': 'Cog6ToothIcon',
        'color': 'emerald',
        'default_budget_categories': ['maintenance', 'spare_parts', 'manpower']
    },
    'pmc': {
        'code': 'pmc',
        'name': 'Project Management Consultancy',
        'icon': 'BriefcaseIcon',
        'color': 'purple',
        'default_budget_categories': ['consultancy', 'engineering', 'software']
    },
    'feasibility': {
        'code': 'feasibility',
        'name': 'Feasibility Study',
        'icon': 'MagnifyingGlassIcon',
        'color': 'blue',
        'default_budget_categories': ['engineering', 'consultancy']
    },
    'feed': {
        'code': 'feed',
        'name': 'Front-End Engineering Design (FEED)',
        'icon': 'DocumentTextIcon',
        'color': 'cyan',
        'default_budget_categories': ['engineering', 'software']
    },
    'detailed_design': {
        'code': 'detailed_design',
        'name': 'Detailed Engineering',
        'icon': 'PencilSquareIcon',
        'color': 'teal',
        'default_budget_categories': ['engineering', 'software', 'procurement']
    },
    'commissioning': {
        'code': 'commissioning',
        'name': 'Commissioning & Startup',
        'icon': 'BoltIcon',
        'color': 'yellow',
        'default_budget_categories': ['testing', 'manpower', 'equipment']
    },
    'shutdown': {
        'code': 'shutdown',
        'name': 'Shutdown & Turnaround',
        'icon': 'StopCircleIcon',
        'color': 'red',
        'default_budget_categories': ['manpower', 'procurement', 'equipment']
    },
    'brownfield': {
        'code': 'brownfield',
        'name': 'Brownfield Modification',
        'icon': 'ArrowPathIcon',
        'color': 'amber',
        'default_budget_categories': ['engineering', 'construction', 'procurement']
    },
    'greenfield': {
        'code': 'greenfield',
        'name': 'Greenfield Development',
        'icon': 'MapIcon',
        'color': 'lime',
        'default_budget_categories': ['engineering', 'construction', 'equipment', 'procurement']
    },
    'internal': {
        'code': 'internal',
        'name': 'Internal Project',
        'icon': 'BuildingOfficeIcon',
        'color': 'gray',
        'default_budget_categories': ['overhead', 'software', 'training']
    },
}

PROJECT_STATUS_CONFIG = {
    'planning': {
        'code': 'planning',
        'label': 'Planning',
        'color': 'gray',
        'icon': 'ClipboardDocumentListIcon',
        'description': 'Project in planning phase',
        'next_states': ['active', 'cancelled']
    },
    'active': {
        'code': 'active',
        'label': 'Active',
        'color': 'green',
        'icon': 'PlayCircleIcon',
        'description': 'Project actively running',
        'next_states': ['on_hold', 'completed', 'cancelled']
    },
    'on_hold': {
        'code': 'on_hold',
        'label': 'On Hold',
        'color': 'yellow',
        'icon': 'PauseCircleIcon',
        'description': 'Project temporarily paused',
        'next_states': ['active', 'cancelled']
    },
    'completed': {
        'code': 'completed',
        'label': 'Completed',
        'color': 'blue',
        'icon': 'CheckCircleIcon',
        'description': 'Project successfully completed',
        'next_states': ['archived']
    },
    'cancelled': {
        'code': 'cancelled',
        'label': 'Cancelled',
        'color': 'red',
        'icon': 'XCircleIcon',
        'description': 'Project cancelled',
        'next_states': ['archived']
    },
    'archived': {
        'code': 'archived',
        'label': 'Archived',
        'color': 'slate',
        'icon': 'ArchiveBoxIcon',
        'description': 'Project archived',
        'next_states': []
    },
}

PROJECT_HEALTH_CONFIG = {
    'green': {
        'code': 'green',
        'label': 'On Track',
        'color': 'emerald',
        'icon': 'CheckCircleIcon',
        'threshold_min': 90,  # Budget utilization % range
        'threshold_max': 100,
    },
    'yellow': {
        'code': 'yellow',
        'label': 'At Risk',
        'color': 'amber',
        'icon': 'ExclamationTriangleIcon',
        'threshold_min': 80,
        'threshold_max': 89,
    },
    'red': {
        'code': 'red',
        'label': 'Critical',
        'color': 'red',
        'icon': 'ExclamationCircleIcon',
        'threshold_min': 0,
        'threshold_max': 79,
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# BUDGET CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

BUDGET_CATEGORIES = {
    'engineering': {
        'code': 'engineering',
        'name': 'Engineering Services',
        'icon': 'CpuChipIcon',
        'color': 'blue',
        'typical_percentage': 25,  # Typical % of total project cost
    },
    'procurement': {
        'code': 'procurement',
        'name': 'Procurement & Materials',
        'icon': 'ShoppingCartIcon',
        'color': 'purple',
        'typical_percentage': 30,
    },
    'equipment': {
        'code': 'equipment',
        'name': 'Equipment & Machinery',
        'icon': 'CogIcon',
        'color': 'orange',
        'typical_percentage': 20,
    },
    'construction': {
        'code': 'construction',
        'name': 'Construction & Installation',
        'icon': 'WrenchScrewdriverIcon',
        'color': 'amber',
        'typical_percentage': 15,
    },
    'manpower': {
        'code': 'manpower',
        'name': 'Manpower & Labor',
        'icon': 'UserGroupIcon',
        'color': 'teal',
        'typical_percentage': 10,
    },
    'travel': {
        'code': 'travel',
        'name': 'Travel & Accommodation',
        'icon': 'GlobeAltIcon',
        'color': 'cyan',
        'typical_percentage': 2,
    },
    'testing': {
        'code': 'testing',
        'name': 'Testing & Commissioning',
        'icon': 'BeakerIcon',
        'color': 'emerald',
        'typical_percentage': 5,
    },
    'certification': {
        'code': 'certification',
        'name': 'Certification & Inspection',
        'icon': 'ShieldCheckIcon',
        'color': 'indigo',
        'typical_percentage': 3,
    },
    'software': {
        'code': 'software',
        'name': 'Software & Licenses',
        'icon': 'ComputerDesktopIcon',
        'color': 'violet',
        'typical_percentage': 2,
    },
    'training': {
        'code': 'training',
        'name': 'Training & Development',
        'icon': 'AcademicCapIcon',
        'color': 'pink',
        'typical_percentage': 1,
    },
    'consultancy': {
        'code': 'consultancy',
        'name': 'Consultancy Services',
        'icon': 'BriefcaseIcon',
        'color': 'fuchsia',
        'typical_percentage': 8,
    },
    'contingency': {
        'code': 'contingency',
        'name': 'Contingency Reserve',
        'icon': 'ShieldExclamationIcon',
        'color': 'slate',
        'typical_percentage': 10,
    },
    'overhead': {
        'code': 'overhead',
        'name': 'Overhead & Admin',
        'icon': 'BuildingOffice2Icon',
        'color': 'gray',
        'typical_percentage': 5,
    },
    'other': {
        'code': 'other',
        'name': 'Other Expenses',
        'icon': 'EllipsisHorizontalCircleIcon',
        'color': 'neutral',
        'typical_percentage': 4,
    },
}

# Budget alert thresholds (soft-coded)
BUDGET_ALERT_THRESHOLDS = {
    'warning': 80,      # Warn when budget utilization reaches 80%
    'critical': 95,     # Critical alert at 95%
    'overspend': 100,   # Over-budget alert at 100%
}

# ══════════════════════════════════════════════════════════════════════════════
# INVOICE TRACKING CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

INVOICE_STATUS_CONFIG = {
    'not_invoiced': {
        'code': 'not_invoiced',
        'label': 'Not Invoiced',
        'color': 'gray',
        'icon': 'ClockIcon',
        'description': 'No invoices received yet',
    },
    'partially_invoiced': {
        'code': 'partially_invoiced',
        'label': 'Partially Invoiced',
        'color': 'yellow',
        'icon': 'BanknotesIcon',
        'description': 'Some invoices received, amount < PO total',
    },
    'fully_invoiced': {
        'code': 'fully_invoiced',
        'label': 'Fully Invoiced',
        'color': 'green',
        'icon': 'CheckCircleIcon',
        'description': 'Invoiced amount matches PO total',
    },
    'over_invoiced': {
        'code': 'over_invoiced',
        'label': 'Over Invoiced',
        'color': 'red',
        'icon': 'ExclamationTriangleIcon',
        'description': 'Invoiced amount exceeds PO total - investigate',
    },
}

# 3-way matching configuration (soft-coded)
THREE_WAY_MATCHING_CONFIG = {
    'enabled': True,
    'tolerance_percentage': 5,  # Allow 5% variance in PO ↔ Invoice ↔ Receipt matching
    'require_receipt': True,    # Must have goods receipt before invoice approval
    'auto_match': True,         # Auto-match invoices to POs by vendor + amount
}

# ══════════════════════════════════════════════════════════════════════════════
# COST CENTER CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

COST_CENTER_DEPARTMENTS = [
    {'code': 'process', 'name': 'Process Engineering', 'color': 'blue'},
    {'code': 'piping', 'name': 'Piping Engineering', 'color': 'green'},
    {'code': 'mechanical', 'name': 'Mechanical Engineering', 'color': 'orange'},
    {'code': 'electrical', 'name': 'Electrical Engineering', 'color': 'yellow'},
    {'code': 'instrument', 'name': 'Instrumentation & Control', 'color': 'purple'},
    {'code': 'civil', 'name': 'Civil & Structural', 'color': 'gray'},
    {'code': 'architecture', 'name': 'Architecture', 'color': 'pink'},
    {'code': 'qhse', 'name': 'QHSE', 'color': 'red'},
    {'code': 'procurement', 'name': 'Procurement', 'color': 'indigo'},
    {'code': 'finance', 'name': 'Finance', 'color': 'emerald'},
    {'code': 'it', 'name': 'IT & Systems', 'color': 'cyan'},
    {'code': 'hr', 'name': 'Human Resources', 'color': 'rose'},
    {'code': 'admin', 'name': 'Administration', 'color': 'slate'},
]

# ══════════════════════════════════════════════════════════════════════════════
# FRONTEND UI CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Project dashboard widgets configuration
DASHBOARD_WIDGETS = {
    'active_projects': {'enabled': True, 'order': 1, 'size': 'large'},
    'budget_summary': {'enabled': True, 'order': 2, 'size': 'medium'},
    'po_by_project': {'enabled': True, 'order': 3, 'size': 'medium'},
    'invoice_reconciliation': {'enabled': True, 'order': 4, 'size': 'large'},
    'vendor_performance': {'enabled': True, 'order': 5, 'size': 'small'},
    'cost_center_spend': {'enabled': True, 'order': 6, 'size': 'medium'},
}

# Table pagination (soft-coded)
DEFAULT_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = [10, 25, 50, 100, 250]

# Auto-refresh intervals (seconds)
AUTO_REFRESH_INTERVALS = {
    'dashboard': 60,
    'project_list': 120,
    'po_list': 90,
    'invoice_tracker': 60,
}

# Export formats
EXPORT_FORMATS = ['excel', 'csv', 'pdf']

# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# Link to invoice tracker (accounts receivable)
INVOICE_TRACKER_INTEGRATION = {
    'enabled': True,
    'auto_link_by_project': True,  # Auto-link customer invoices to projects
    'show_in_dashboard': True,
}

# Link to finance module (accounts payable)
FINANCE_INTEGRATION = {
    'enabled': True,
    'auto_link_invoices_to_pos': True,  # Auto-match vendor invoices to POs
    'show_invoice_status_in_po_list': True,
}

# Link to timesheet module
TIMESHEET_INTEGRATION = {
    'enabled': True,
    'auto_link_time_to_projects': True,  # Link timesheet hours to projects
    'show_project_hours_in_dashboard': True,
}
