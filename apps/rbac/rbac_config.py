"""
RBAC Configuration
Centralized configuration for Role-Based Access Control system.
All RBAC settings live here — edit this file to change roles, modules, and policies.
Follows soft-coding principles: no role/module names are hardcoded in views or logic.

Cross-verified against:
  - user_management/  (standalone RBAC microservice package)
  - data-management/  (document/dataset microservice with append-only audit)
"""

# ─────────────────────────────────────────────────────────────────────────────
# COMPLETE MODULE CATALOGUE
# Single source of truth — seed_rbac.py reads this list.
# Each entry maps to Module.code in the DB.
# ─────────────────────────────────────────────────────────────────────────────
ALL_MODULES_CATALOGUE = [
    # ── Core Engineering ──────────────────────────────────────────────────
    {'code': 'pid_analysis',           'name': 'P&ID Analysis',               'icon': 'FileText',    'order': 1,  'description': 'P&ID document analysis and processing'},
    {'code': 'pfd_to_pid',             'name': 'PFD to P&ID Converter',        'icon': 'RefreshCw',   'order': 2,  'description': 'AI-powered conversion of PFD to P&ID drawings'},
    {'code': 'pfd_quality',            'name': 'PFD Quality Check',            'icon': 'CheckSquare', 'order': 3,  'description': 'AI-powered quality verification of PFD documents'},
    {'code': 'crs_documents',          'name': 'CRS Document Management',      'icon': 'FolderOpen',  'order': 4,  'description': 'Upload and manage CRS documents with AI analysis'},
    {'code': 'designiq',               'name': 'DesignIQ - AI Design Intelligence', 'icon': 'Cpu',    'order': 5,  'description': 'AI-powered engineering design optimization and analysis'},
    {'code': 'data_mining',            'name': 'Data Mining Platform',         'icon': 'TableCells',  'order': 6,  'description': 'Tableau Prep-style data transformation and master file generation'},
    {'code': 'qhse',                   'name': 'QHSE Overview',                'icon': 'Shield',      'order': 7,  'description': 'QHSE project quality overview dashboard'},
    # ── QHSE Sub-Modules (each sidebar item has its own module code) ─────
    {'code': 'qhse_detailed',          'name': 'QHSE Project Details',         'icon': 'TableCells',  'order': 71, 'description': 'Detailed project quality view and drill-down'},
    {'code': 'qhse_quality',           'name': 'Quality Management',           'icon': 'ChartBar',    'order': 72, 'description': 'Quality metrics, audits and non-conformance tracking'},
    {'code': 'qhse_health_safety',     'name': 'Health & Safety',              'icon': 'Shield',      'order': 73, 'description': 'Health and safety incident management'},
    {'code': 'qhse_environmental',     'name': 'Environmental',                'icon': 'DocumentText','order': 74, 'description': 'Environmental compliance and impact management'},
    {'code': 'qhse_energy',            'name': 'Energy Management',            'icon': 'ChartBar',    'order': 75, 'description': 'Energy consumption tracking and efficiency reporting'},
    # ── Discipline Datasheets ─────────────────────────────────────────────
    {'code': 'process_datasheet',      'name': 'Process Datasheet',            'icon': 'FileText',    'order': 10, 'description': 'Process equipment datasheets — MOV, SDV, pumps, pressure instruments'},
    {'code': 'electrical_datasheet',   'name': 'Electrical Datasheet',         'icon': 'Zap',         'order': 11, 'description': 'Electrical equipment and SLD-based datasheet generation'},
    {'code': 'electrical_sld',         'name': 'Electrical SLD',               'icon': 'Zap',         'order': 12, 'description': 'Single Line Diagram analysis and tagging'},
    {'code': 'instrument_datasheet',   'name': 'Instrument Datasheet',         'icon': 'Activity',    'order': 13, 'description': 'Instrument equipment datasheets and tag lists'},
    {'code': 'instrument_index',       'name': 'Instrument Index',             'icon': 'List',        'order': 14, 'description': 'AI extraction of instrument index from P&ID drawings'},
    {'code': 'mechanical_datasheet',   'name': 'Mechanical Datasheet',         'icon': 'Tool',        'order': 15, 'description': 'Mechanical equipment datasheets and inspection records'},
    {'code': 'civil_datasheet',        'name': 'Civil Datasheet',              'icon': 'Home',        'order': 16, 'description': 'Civil and structural engineering datasheets'},
    {'code': 'piping_datasheet',       'name': 'Piping Datasheet',             'icon': 'GitBranch',   'order': 17, 'description': 'Piping material specifications and critical line list'},
    {'code': 'piping_pms',             'name': 'Piping Material Specification', 'icon': 'Database',   'order': 18, 'description': 'Piping material specification management'},
    {'code': 'digitization_datasheet', 'name': 'Digitization Datasheet',       'icon': 'Scan',        'order': 19, 'description': 'AI-powered digitization of legacy datasheets'},
    {'code': 'spec_customization',     'name': 'Spec Customization',           'icon': 'Settings',    'order': 20, 'description': 'Engineering specification customization tools'},
    {'code': 'non_teff_metadata',      'name': 'Non-TEFF Metadata Extractor',  'icon': 'Search',      'order': 21, 'description': 'Extract metadata from Non-TEFF documents (PDF, Excel, Word, AutoCAD)'},
    # ── Process sub-module codes (granular per sidebar item) ───────────────────
    {'code': 'pid_line_list',          'name': 'Line List',                    'icon': 'TableCells',  'order': 22, 'description': 'Extract base line list columns from P&ID drawings'},
    {'code': 'pid_equipment_list',     'name': 'Equipment List',               'icon': 'TableCells',  'order': 23, 'description': 'Extract equipment tags and type classification from P&ID'},
    # ── Piping sub-module code (moved away from shared designiq code) ───────────
    {'code': 'piping_critical_line_list', 'name': 'Critical Line List',        'icon': 'GitBranch',   'order': 24, 'description': '5-document critical line list with full 35-column enrichment'},
    # ── Instrument sub-module code (split from shared instrument_datasheet code) ──
    {'code': 'instrument_io_list',     'name': 'Instrument IO List',           'icon': 'CircleStack', 'order': 25, 'description': 'Generate or QC an Input/Output list from the instrument register'},
    # ── Admin / Platform ─────────────────────────────────────────────────
    {'code': 'user_mgmt',              'name': 'User Management',              'icon': 'Users',       'order': 50, 'description': 'Manage users, roles, and permissions'},
    {'code': 'org_settings',           'name': 'Organization Settings',        'icon': 'Settings',    'order': 51, 'description': 'Configure organization settings and preferences'},
    {'code': 'audit_logs',             'name': 'Audit Logs',                   'icon': 'FileSearch',  'order': 52, 'description': 'View system audit logs and activity (append-only per data-management spec)'},
    {'code': 'file_storage',           'name': 'File Storage',                 'icon': 'Database',    'order': 53, 'description': 'Manage files and documents in S3'},
    {'code': 'reports',                'name': 'Reports & Analytics',          'icon': 'BarChart',    'order': 54, 'description': 'Generate reports and view analytics'},
    {'code': 'api_access',             'name': 'API Access',                   'icon': 'Code',        'order': 55, 'description': 'Access REST APIs programmatically'},
    # ── HR & Payroll (Sensitive — Super Admin grant only) ─────────────────
    {'code': 'hr_management',          'name': 'Human Resources',              'icon': 'Users',       'order': 70, 'description': 'HR management — employee records, leave, and workforce planning'},
    {'code': 'payroll',                'name': 'Payroll Engine',               'icon': 'DollarSign',  'order': 71, 'description': 'Payroll processing, salary slips, and compensation management'},
    {'code': 'timesheet',              'name': 'Timesheet & Attendance',       'icon': 'Clock',       'order': 72, 'description': 'Employee timesheet tracking and biometric attendance reports'},
    {'code': 'hr_self_service',        'name': 'HR Self-Service',              'icon': 'User',        'order': 73, 'description': 'Personal leave requests, attendance records and payslip access'},
    {'code': 'hr_onboarding',          'name': 'Onboarding | Offboarding',     'icon': 'UserPlus',    'order': 74, 'description': 'Employee lifecycle management — onboarding pipeline and offboarding exits'},
    # ── Business Modules ──────────────────────────────────────────────────
    {'code': 'finance',                'name': 'Finance',                      'icon': 'CreditCard',  'order': 80, 'description': 'Invoice tracking, billing and financial management'},
    {'code': 'sales',                  'name': 'Sales',                        'icon': 'TrendingUp',  'order': 81, 'description': 'Internal sales pipeline and business development'},
    {'code': 'project_control',        'name': 'Project Control',              'icon': 'Briefcase',   'order': 82, 'description': 'Project planning, tracking and schedule control'},
    {'code': 'procurement',              'name': 'Procurement',                'icon': 'ShoppingCart','order': 83, 'description': 'Procurement overview and dashboard'},
    {'code': 'procurement_vendors',      'name': 'Vendor Management',          'icon': 'Users',       'order': 84, 'description': 'Manage vendors and supplier records'},
    {'code': 'procurement_requisitions', 'name': 'Purchase Requisitions',      'icon': 'DocumentText','order': 85, 'description': 'Purchase recommendations and requisitions'},
    {'code': 'procurement_orders',       'name': 'Purchase Orders',            'icon': 'DocumentPlus','order': 86, 'description': 'Create and manage purchase orders'},
    {'code': 'procurement_receipts',     'name': 'Goods Receipt',              'icon': 'Folder',      'order': 87, 'description': 'Goods receipt and delivery confirmation'},
]

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM ROLES CONFIGURATION
# All system roles with their display metadata.
# Seed and migrations read this — never hardcode role names elsewhere.
#
# level hierarchy (from user_management package spec):
#   1 = Super Admin  |  2 = Admin  |  3 = Manager
#   4 = Engineer     |  5 = Reviewer  |  6 = Viewer
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_ROLES_CONFIG = [
    {
        'code': 'super_admin',
        'name': 'Super Administrator',
        'level': 1,
        'description': 'Full system access — manages all organizations and users. Bypasses all module checks.',
        'is_system_role': True,
        'badge_color': 'red',
    },
    {
        'code': 'admin',
        'name': 'Administrator',
        'level': 2,
        'description': 'Organization administrator — manages users, roles, modules, and settings.',
        'is_system_role': True,
        'badge_color': 'orange',
    },
    {
        'code': 'process_engineer',
        'name': 'Process Engineer',
        'level': 4,
        'description': 'Process discipline engineer — access to process datasheets, P&ID, PFD tools.',
        'is_system_role': True,
        'badge_color': 'blue',
    },
    {
        'code': 'electrical_engineer',
        'name': 'Electrical Engineer',
        'level': 4,
        'description': 'Electrical discipline engineer — access to electrical datasheets and SLD analysis.',
        'is_system_role': True,
        'badge_color': 'yellow',
    },
    {
        'code': 'instrument_engineer',
        'name': 'Instrument Engineer',
        'level': 4,
        'description': 'Instrument discipline engineer — access to instrument datasheets and index.',
        'is_system_role': True,
        'badge_color': 'purple',
    },
    {
        'code': 'mechanical_engineer',
        'name': 'Mechanical Engineer',
        'level': 4,
        'description': 'Mechanical discipline engineer — access to mechanical datasheets.',
        'is_system_role': True,
        'badge_color': 'gray',
    },
    {
        'code': 'civil_engineer',
        'name': 'Civil Engineer',
        'level': 4,
        'description': 'Civil/structural discipline engineer — access to civil datasheets.',
        'is_system_role': True,
        'badge_color': 'green',
    },
    {
        'code': 'piping_engineer',
        'name': 'Piping Engineer',
        'level': 4,
        'description': 'Piping discipline engineer — access to piping datasheets and PMS.',
        'is_system_role': True,
        'badge_color': 'indigo',
    },
    {
        'code': 'qhse_engineer',
        'name': 'QHSE Engineer',
        'level': 4,
        'description': 'Quality, Health, Safety and Environment engineer.',
        'is_system_role': True,
        'badge_color': 'teal',
    },
    {
        'code': 'design_engineer',
        'name': 'Design Engineer',
        'level': 4,
        'description': 'Design/digital twin engineer — DesignIQ, PFD to P&ID, P&ID analysis.',
        'is_system_role': True,
        'badge_color': 'cyan',
    },
    {
        'code': 'project_manager',
        'name': 'Project Manager',
        'level': 3,
        'description': 'Cross-discipline project manager — read access across engineering modules.',
        'is_system_role': True,
        'badge_color': 'pink',
    },
    {
        'code': 'viewer',
        'name': 'Viewer',
        'level': 6,
        'description': 'Read-only access. No module access unless explicitly assigned.',
        'is_system_role': True,
        'badge_color': 'slate',
    },
    {
        # SOFT-CODED: Default role — auto-assigned to every new user and to any
        # existing user who has no other active role.  Module list is defined
        # in ROLE_MODULE_POLICY['default'] below.  Change the module list there
        # (not here) to update what Default users can access.
        'code': 'default',
        'name': 'Default',
        'level': 4,
        'description': 'Default access for all users — standard engineering modules plus HR self-service.',
        'is_system_role': True,
        'badge_color': 'green',
    },
    {
        'code': 'hr_admin',
        'name': 'HR & Payroll Administrator',
        'level': 2,
        'description': 'Full access to HR, Payroll, and Timesheet data. Sensitive role — grant only via Super Administrator.',
        'is_system_role': True,
        'badge_color': 'rose',
        'sensitive': True,
        'sensitive_modules': ['hr_management', 'payroll', 'timesheet', 'hr_onboarding'],
    },
]

# Module Assignment Strategy
# SECURITY: We enforce strictly role-based access. Module assignment happens
# through Roles only; direct per-user module assignment is disabled by
# default. Flip `create_custom_roles` to True (and the matching frontend flag
# ALLOW_PER_USER_MODULE_ASSIGNMENT in rbacAccess.config.js) to re-enable the
# legacy per-user "custom_<email>" role hack.
MODULE_ASSIGNMENT_CONFIG = {
    'strategy': 'role_based',  # 'role_based' or 'direct'
    'create_custom_roles': False,  # Legacy behaviour; disabled for role-only access.
    'custom_role_prefix': 'custom_',
    'custom_role_level': 10,  # Level for custom roles
    'clear_existing_on_update': True,  # Clear existing module assignments when updating
    'assign_permissions_automatically': True,  # Auto-assign all module permissions
    'fallback_to_default_role': True,  # Assign default role if no roles specified
}

# Default Role Settings
# SOFT-CODED: change 'code' here to swap which role is auto-assigned to new users.
DEFAULT_ROLE_CONFIG = {
    'code': 'default',          # was 'user' — now points to the Default system role
    'name': 'Default',
    'level': 4,
    'auto_assign_on_creation': True,
}

# Admin Role Detection
ADMIN_ROLE_CODES = ['super_admin', 'admin', 'administrator']
SUPERADMIN_ROLE_CODES = ['super_admin', 'superadmin']

# Sensitive roles — only Super Admin may grant these
SENSITIVE_ROLE_CODES = ['hr_admin']

# Sensitive module codes — restricted to hr_admin and super_admin
SENSITIVE_MODULE_CODES = ['hr_management', 'payroll', 'timesheet', 'hr_onboarding']

# Module Access Rules
MODULE_ACCESS_RULES = {
    'check_role_first': True,  # Check role-based access first
    'check_direct_assignment': True,  # Then check direct module assignment
    'admin_has_all_access': True,  # Admins bypass module checks
    'superadmin_has_all_access': True,  # Super admins bypass all checks
}

# Audit Logging
AUDIT_CONFIG = {
    'log_role_assignments': True,
    'log_module_assignments': True,
    'log_permission_changes': True,
    'log_access_denials': True,
    'log_module_access_checks': True,  # Detailed logging for debugging
}

# User Profile Settings
USER_PROFILE_CONFIG = {
    'require_organization': False,  # Organization is optional
    'auto_create_profile': True,  # Auto-create profile for existing users
    'default_status': 'active',
    'require_email_verification': False,  # Email verification optional
}

# Module Categories (for UI grouping)
MODULE_CATEGORIES = {
    'core': {
        'name': 'Core Modules',
        'description': 'Essential system modules',
        'icon': '🔧',
        'order': 1
    },
    'engineering': {
        'name': 'Engineering',
        'description': 'Engineering and design modules',
        'icon': '⚙️',
        'order': 2
    },
    'business': {
        'name': 'Business Operations',
        'description': 'Finance, procurement, and business modules',
        'icon': '💼',
        'order': 3
    },
    'compliance': {
        'name': 'QHSE & Compliance',
        'description': 'Quality, health, safety, and environment',
        'icon': '🛡️',
        'order': 4
    },
    'admin': {
        'name': 'Administration',
        'description': 'System administration modules',
        'icon': '👨‍💼',
        'order': 5
    }
}

# Error Messages
ERROR_MESSAGES = {
    'no_roles': 'User has no roles assigned. Please assign at least one role.',
    'no_modules': 'User has no accessible modules. Please assign modules or roles.',
    'module_not_found': 'Requested module not found or inactive.',
    'access_denied': 'You do not have access to this module.',
    'invalid_role': 'Invalid or inactive role specified.',
    'role_level_insufficient': 'Your role level is insufficient for this action.',
}

# Success Messages
SUCCESS_MESSAGES = {
    'role_assigned': 'Role successfully assigned to user.',
    'module_assigned': 'Module access granted successfully.',
    'permission_granted': 'Permission granted successfully.',
    'user_created': 'User created successfully with assigned roles and modules.',
}

# ─────────────────────────────────────────────────────────────────────────────
# ENGINEERING SECTION MODULE CATALOGUE
# Single source of truth for what constitutes "full Engineering section" access.
# All module codes here correspond to the 1. Engineering section in the frontend.
# Edit this list (not views/commands) when adding/removing engineering modules.
# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED: Modules granted to the Default role.
# Edit this list to change what every ordinary user can access out of the box.
# Does NOT include QHSE, Finance, Procurement, Sales, Project Control, or
# HR sub-modules other than hr_self_service.
DEFAULT_ROLE_MODULES = [
    # ── Process Engineering ───────────────────────────────────────────
    'pid_analysis',
    'pfd_quality',
    'process_datasheet',
    'pid_line_list',
    'pid_equipment_list',
    # ── Piping Engineering ────────────────────────────────────────────
    'piping_critical_line_list',
    'piping_pms',
    'piping_datasheet',
    # ── Electrical Engineering ────────────────────────────────────────
    'electrical_sld',
    'electrical_datasheet',
    # ── Civil Engineering ─────────────────────────────────────────────
    'civil_datasheet',
    # ── Mechanical Engineering ────────────────────────────────────────
    'mechanical_datasheet',
    # ── Digital Transformation ────────────────────────────────────────
    'spec_customization',
    'non_teff_metadata',
    # ── Common & Integration ──────────────────────────────────────────
    'crs_documents',
    'pfd_to_pid',
    'designiq',
    'data_mining',
    # ── HR Self-Service ONLY ──────────────────────────────────────────
    'hr_self_service',
]

ENGINEERING_SECTION_MODULES = [
    # ── Core P&ID / Process ───────────────────────────────────────────
    'pid_analysis',
    'pfd_to_pid',
    'pfd_quality',
    'pid_line_list',
    'pid_equipment_list',
    'crs_documents',
    'designiq',
    'data_mining',
    'qhse',
    'qhse_detailed',
    'qhse_quality',
    'qhse_health_safety',
    'qhse_environmental',
    'qhse_energy',
    # ── Discipline Datasheets ─────────────────────────────────────────
    'process_datasheet',
    'electrical_datasheet',
    'electrical_sld',
    'instrument_datasheet',
    'instrument_index',
    'instrument_io_list',
    'mechanical_datasheet',
    'civil_datasheet',
    'piping_datasheet',
    'piping_pms',
    'piping_critical_line_list',
    # ── Digitization ─────────────────────────────────────────────────
    'digitization_datasheet',
    'spec_customization',
    'non_teff_metadata',
]

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED ROLE → MODULE POLICY
# Maps role codes to the module codes that role should always have access to.
# Used by: management commands (apply_role_module_policy, seed_rbac)
# When a user is assigned a role, they automatically receive all modules in that
# role's policy list.  This is the SINGLE source of truth for role-based module
# access — edit here to change what any role can see.
# ─────────────────────────────────────────────────────────────────────────────
ROLE_MODULE_POLICY = {
    # ── DEFAULT ENGINEERING ACCESS POLICY ────────────────────────────────
    # All roles get the full Engineering section (1.1–1.7) by default.
    # ENGINEERING_SECTION_MODULES is the single source of truth for what
    # constitutes the Engineering section.  To add/remove a module from the
    # default grant, edit ENGINEERING_SECTION_MODULES above — not here.
    # ─────────────────────────────────────────────────────────────────────

    # Process-focused engineers: full Engineering section
    'process_engineer': ENGINEERING_SECTION_MODULES,

    # Electrical discipline: full Engineering section
    'electrical_engineer': ENGINEERING_SECTION_MODULES,

    # Instrument discipline: full Engineering section
    'instrument_engineer': ENGINEERING_SECTION_MODULES,

    # Mechanical discipline: full Engineering section
    'mechanical_engineer': ENGINEERING_SECTION_MODULES,

    # Civil / structural discipline: full Engineering section
    'civil_engineer': ENGINEERING_SECTION_MODULES,

    # Piping discipline: full Engineering section
    'piping_engineer': ENGINEERING_SECTION_MODULES,

    # QHSE discipline: full Engineering section
    'qhse_engineer': ENGINEERING_SECTION_MODULES,

    # DesignIQ / digital twin roles: full Engineering section
    'design_engineer': ENGINEERING_SECTION_MODULES,

    # Project managers: full Engineering section + reports
    'project_manager': ENGINEERING_SECTION_MODULES + ['reports'],

    # Viewer: full Engineering section (read-only enforced by UI/view guards)
    'viewer': ENGINEERING_SECTION_MODULES,

    # Admin: full Engineering section + all admin/platform + business modules
    'admin': ENGINEERING_SECTION_MODULES + [
        'user_mgmt',
        'org_settings',
        'audit_logs',
        'reports',
        'api_access',
        'finance',
        'sales',
        'project_control',
        'procurement',
        'procurement_vendors',
        'procurement_requisitions',
        'procurement_orders',
        'procurement_receipts',
    ],

    # Default: standard engineering + common + hr_self_service (see DEFAULT_ROLE_MODULES)
    'default': DEFAULT_ROLE_MODULES,

    # Super-admins bypass module checks in the app, but listed for completeness
    'super_admin': [],

    # ── Organisation-level custom roles (production DB) ───────────────────
    # These are non-system roles created manually in the production database
    # to grant "1. Engineering + 2. COMMON" access bundles.
    # SOFT-CODED: add new org-role codes here to grant full engineering access.
    # All entries here map to ENGINEERING_SECTION_MODULES so that every feature
    # under "1. Engineering" (1.1–1.7) and "2. COMMON" is accessible.
    'engineering_common_access': ENGINEERING_SECTION_MODULES,
}

# Which module codes map to which discipline (used for diagnostics)
MODULE_DISCIPLINE_MAP = {
    'process_datasheet': 'Process',
    'pid_analysis':      'Process / P&ID',
    'pfd_to_pid':        'Process',
    'pfd_quality':       'Process',
    'designiq':          'DesignIQ',
    'electrical_datasheet': 'Electrical',
    'electrical_sld':    'Electrical',
    'instrument_datasheet': 'Instrument',
    'instrument_index':  'Instrument',
    'mechanical_datasheet': 'Mechanical',
    'civil_datasheet':   'Civil',
    'piping_datasheet':  'Piping',
    'piping_pms':        'Piping',
    'digitization_datasheet': 'Digitization',
    'spec_customization': 'Digitization',
    'non_teff_metadata': 'Digitization',
    'qhse':              'QHSE',
    'crs_documents':     'CRS',
    'user_mgmt':         'Admin',
    'org_settings':      'Admin',
    'audit_logs':        'Admin',
    'reports':           'Admin',
    'api_access':        'Admin',
}


def get_custom_role_code(email):
    """Generate custom role code from email"""
    username = email.split('@')[0]
    return f"{MODULE_ASSIGNMENT_CONFIG['custom_role_prefix']}{username}"

def get_custom_role_name(first_name, last_name):
    """Generate custom role name from user details"""
    full_name = f"{first_name} {last_name}".strip()
    return f"Custom Role - {full_name}" if full_name else "Custom Role"

def should_create_custom_role():
    """Check if custom roles should be created for module assignments"""
    return MODULE_ASSIGNMENT_CONFIG['create_custom_roles']

def is_admin_role(role_code):
    """Check if a role code represents an admin role"""
    return role_code.lower() in ADMIN_ROLE_CODES

def is_superadmin_role(role_code):
    """Check if a role code represents a super admin role"""
    return role_code.lower() in SUPERADMIN_ROLE_CODES
