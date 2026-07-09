"""
Soft-Coded Discipline & Module Access Configuration
Decouples discipline/department assignments from code
Enables flexible, runtime-configurable access control for 300+ users
"""
import logging
import json
import re
from functools import lru_cache
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class DisciplineAccessConfig:
    """
    Soft-coded configuration mapping disciplines to accessible modules
    Built for scalability: supports 300+ concurrent users
    
    Configuration can be:
    1. Loaded from environment (for Railway production)
    2. Loaded from database (for dynamic runtime changes)
    3. Cached for performance
    """
    
    # Cache timeout (1 hour) - config changes propagate within 1 hour
    CACHE_TIMEOUT = 3600
    CACHE_KEY = 'discipline_module_access_config'

    # Alias mapping for real-world department values from HR/user onboarding.
    DISCIPLINE_ALIASES = {
        'process engineer': 'process_engineering',
        'process_engineer': 'process_engineering',
        'process engineers': 'process_engineering',
        'process_engineers': 'process_engineering',
        'process engineering': 'process_engineering',
        'process-engineering': 'process_engineering',
        'process_eng': 'process_engineering',
        'process': 'process_engineering',
        'proc eng': 'process_engineering',
    }
    
    # Default configuration - used as fallback
    DEFAULT_DISCIPLINE_MODULES = {
        # P&ID Verification module
        'pid_verification': {
            'code': 'pid_verification',
            'name': 'P&ID Quality Verification',
            'description': 'Process and Instrumentation Diagram quality checks',
            'accessible_by_disciplines': [
                'engineering',
                'process_engineering',
                'mechanical_engineering',
                'electrical_engineering',
                'instruments_control',
                'qa_qc',  # QA/QC can verify all
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer', 'reviewer'],
            'required_permission': 'pid_verification.upload'
        },
        
        # PFD Quality module
        'pfd_quality': {
            'code': 'pfd_quality',
            'name': 'PFD Quality Verification',
            'description': 'Process Flow Diagram quality and compliance checks',
            # Soft-coded global toggle: when True, all authenticated users can access
            # this module without discipline/role mapping maintenance.
            'allow_all_authenticated': True,
            'accessible_by_disciplines': [
                'process_engineering',
                'qa_qc',  # QA/QC can verify PFD
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer', 'reviewer'],
            'required_permission': 'pfd_quality.upload'
        },

        # Process Datasheet module
        'process_datasheet': {
            'code': 'process_datasheet',
            'name': 'Process Datasheet',
            'description': 'Process engineering datasheet tools and forms',
            # Soft-coded global toggle: expose Datasheet to all authenticated users.
            'allow_all_authenticated': True,
            'accessible_by_disciplines': [
                'process_engineering',
                'engineering',
                'qa_qc',
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer', 'reviewer'],
            'required_permission': 'process_datasheet.use'
        },
        
        # Cross-feature recommendations
        'cross_recommendation': {
            'code': 'cross_recommendation',
            'name': 'Cross-Feature Recommendations',
            'description': 'Link related P&ID and PFD documents',
            'accessible_by_disciplines': [
                'process_engineering',
                'engineering',
                'qa_qc',
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer', 'reviewer'],
            'required_permission': 'cross_recommendation.link'
        },
        
        # Design IQ module
        'design_iq': {
            'code': 'design_iq',
            'name': 'Design Intelligence & Extraction',
            'description': 'Extract and analyze design documents',
            'accessible_by_disciplines': [
                'engineering',
                'process_engineering',
                'mechanical_engineering',
                'electrical_engineering',
                'qa_qc',
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer', 'reviewer'],
            'required_permission': 'design_iq.upload'
        },
        
        # QHSE module
        'qhse': {
            'code': 'qhse',
            'name': 'Quality, Health, Safety & Environment',
            'description': 'QHSE document management and compliance',
            'accessible_by_disciplines': [
                'qa_qc',
                'qhse',
                'health_safety',
                'environmental',
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'reviewer'],
            'required_permission': 'qhse.upload'
        },
        
        # ML Detection module
        'ml_detection': {
            'code': 'ml_detection',
            'name': 'ML-Based Detection & Analysis',
            'description': 'Machine learning powered document analysis',
            'accessible_by_disciplines': [
                'engineering',
                'process_engineering',
                'qa_qc',
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer'],
            'required_permission': 'ml_detection.use'
        },
        
        # Data Mining module
        'data_mining': {
            'code': 'data_mining',
            'name': 'Data Mining Platform',
            'description': 'Tableau Prep-style data transformation and master file generation',
            # Soft-coded global toggle: expose Data Mining to all authenticated users.
            'allow_all_authenticated': True,
            'accessible_by_disciplines': [
                'engineering',
                'process_engineering',
                'mechanical_engineering',
                'electrical_engineering',
                'instruments_control',
                'qa_qc',
                'admin',
                'super_admin',
            ],
            'accessible_by_roles': ['super_admin', 'admin', 'manager', 'engineer', 'reviewer'],
            'required_permission': 'data_mining.use'
        },
    }
    
    # Discipline definitions
    DEFAULT_DISCIPLINES = {
        'engineering': {
            'code': 'engineering',
            'name': 'Engineering (General)',
            'order': 10
        },
        'process_engineering': {
            'code': 'process_engineering',
            'name': 'Process Engineering',
            'order': 20
        },
        'mechanical_engineering': {
            'code': 'mechanical_engineering',
            'name': 'Mechanical Engineering',
            'order': 25
        },
        'electrical_engineering': {
            'code': 'electrical_engineering',
            'name': 'Electrical Engineering',
            'order': 26
        },
        'instruments_control': {
            'code': 'instruments_control',
            'name': 'Instruments & Control',
            'order': 27
        },
        'qa_qc': {
            'code': 'qa_qc',
            'name': 'Quality Assurance / Quality Control',
            'order': 30
        },
        'qhse': {
            'code': 'qhse',
            'name': 'QHSE (Quality, Health, Safety, Environment)',
            'order': 40
        },
        'health_safety': {
            'code': 'health_safety',
            'name': 'Health & Safety',
            'order': 41
        },
        'environmental': {
            'code': 'environmental',
            'name': 'Environmental',
            'order': 42
        },
        'admin': {
            'code': 'admin',
            'name': 'Administration',
            'order': 100
        },
        'super_admin': {
            'code': 'super_admin',
            'name': 'Super Administrator',
            'order': 1
        },
    }
    
    @classmethod
    @lru_cache(maxsize=1)
    def _load_from_environment(cls):
        """Load configuration from environment variables"""
        env_config = settings.DISCIPLINE_MODULE_ACCESS
        if env_config:
            logger.info("[DisciplineAccess] Loaded configuration from Django settings")
            return env_config
        return None
    
    @classmethod
    def get_module_config(cls, module_code):
        """
        Get configuration for a specific module
        
        Args:
            module_code: Module identifier (e.g., 'pid_verification', 'pfd_quality')
            
        Returns:
            Dict with module configuration or None if not found
        """
        config = cls.get_all_modules()
        return config.get(module_code)
    
    @classmethod
    def get_all_modules(cls):
        """Get all module configurations with caching"""
        # Try cache first
        cached = cache.get(cls.CACHE_KEY)
        if cached:
            logger.debug("[DisciplineAccess] Using cached module configuration")
            return cached
        
        # Try environment
        env_config = cls._load_from_environment()
        if env_config:
            config = env_config
        else:
            config = cls.DEFAULT_DISCIPLINE_MODULES
        
        # Cache for performance
        cache.set(cls.CACHE_KEY, config, timeout=cls.CACHE_TIMEOUT)
        return config
    
    @classmethod
    def user_has_module_access(cls, user_profile, module_code):
        """
        Check if user has access to a module based on discipline
        
        Args:
            user_profile: UserProfile instance
            module_code: Module identifier
            
        Returns:
            True if user can access module, False otherwise
        """
        # Super admin has all access
        if user_profile.roles.filter(code='super_admin', is_active=True).exists():
            logger.debug(f"[DisciplineAccess] Super admin has access to {module_code}")
            return True
        
        # Get module config
        module_config = cls.get_module_config(module_code)
        if not module_config:
            logger.warning(f"[DisciplineAccess] Module config not found: {module_code}")
            return False

        # Soft-coded global access mode for a module.
        if module_config.get('allow_all_authenticated'):
            logger.info("[DisciplineAccess] Module '%s' allows all authenticated users", module_code)
            return True
        
        # Check discipline access (normalized + alias-aware)
        user_discipline_raw = user_profile.department or ''
        user_discipline = cls._normalize_discipline(user_discipline_raw)
        accessible_disciplines = {
            cls._normalize_discipline(d)
            for d in module_config.get('accessible_by_disciplines', [])
            if d
        }

        if user_discipline in accessible_disciplines:
            logger.info(f"[DisciplineAccess] User discipline '{user_discipline}' has access to {module_code}")
            return True
        
        # Check role-based fallback
        user_roles = user_profile.roles.filter(is_active=True).values_list('code', flat=True)
        accessible_roles = module_config.get('accessible_by_roles', [])
        
        for role in user_roles:
            if role in accessible_roles:
                logger.info(f"[DisciplineAccess] User role '{role}' has access to {module_code}")
                return True
        
        logger.warning(
            f"[DisciplineAccess] User '{user_profile.user.email}' (discipline: {user_discipline_raw} -> {user_discipline}) "
            f"NOT granted access to {module_code}"
        )
        return False

    @classmethod
    def _normalize_discipline(cls, value: str) -> str:
        """Normalize free-text department names to canonical discipline codes."""
        if not value:
            return ''
        normalized = re.sub(r'[^a-z0-9]+', '_', value.strip().lower()).strip('_')
        return cls.DISCIPLINE_ALIASES.get(normalized, normalized)
    
    @classmethod
    def get_user_accessible_modules(cls, user_profile):
        """
        Get list of all modules accessible to user
        
        Args:
            user_profile: UserProfile instance
            
        Returns:
            List of accessible module codes
        """
        accessible = []
        
        for module_code in cls.get_all_modules().keys():
            if cls.user_has_module_access(user_profile, module_code):
                accessible.append(module_code)
        
        logger.info(f"[DisciplineAccess] User accessible modules: {accessible}")
        return accessible

    @classmethod
    def get_globally_enabled_module_codes(cls):
        """Return module codes soft-coded to be available to all authenticated users."""
        codes = []
        for module_code, cfg in cls.get_all_modules().items():
            if cfg.get('allow_all_authenticated'):
                codes.append(module_code)
        return codes
    
    @classmethod
    def invalidate_cache(cls):
        """Clear cached configuration (call after updating config)"""
        cache.delete(cls.CACHE_KEY)
        cls._load_from_environment.cache_clear()
        logger.info("[DisciplineAccess] Configuration cache cleared")
