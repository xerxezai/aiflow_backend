"""
Django settings for AIFlow project.
Smart configuration using environment variables for security and flexibility.
Aligned with centralized environment configuration (9-3-26 commit).
"""

import os
from pathlib import Path
from decouple import config
import dj_database_url

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================
# SOFT-CODED CENTRALIZED CONFIGURATION
# ============================================
# Import centralized environment configuration for alignment
try:
    from config.environment_config import (
        config as env_config,
        get_environment,
        get_cors_origins,
        get_csrf_origins,
        get_database_url as get_centralized_db_url,
    )
    CENTRALIZED_CONFIG_AVAILABLE = True
    print(f"[CONFIG] ✅ Centralized configuration loaded")
except ImportError as e:
    print(f"[CONFIG] ⚠️  Centralized configuration not available: {e}")
    CENTRALIZED_CONFIG_AVAILABLE = False
    env_config = None
    get_environment = lambda: 'local'
    get_cors_origins = lambda: []
    get_csrf_origins = lambda: []
    get_centralized_db_url = lambda: None
# ============================================

# Helper function to safely cast config values, handling empty strings
def safe_cast_int(value, default):
    """Safely cast to int, returning default if value is empty or invalid"""
    if not value or value == '':
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def safe_cast_bool(value, default):
    """Safely cast to bool, returning default if value is empty or invalid"""
    if not value or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'on')

def app_exists(app_path):
    """
    Check if a Django app exists before loading it.
    Prevents ModuleNotFoundError during deployment.
    
    Args:
        app_path (str): Django app path (e.g., 'apps.ml_detection')
    
    Returns:
        bool: True if app directory exists with __init__.py
    """
    try:
        # Convert app path to file system path
        # e.g., 'apps.ml_detection' -> BASE_DIR/apps/ml_detection
        parts = app_path.split('.')
        app_dir = BASE_DIR.joinpath(*parts)
        
        # Check if directory exists and has __init__.py
        return app_dir.is_dir() and app_dir.joinpath('__init__.py').exists()
    except Exception as e:
        print(f"[WARNING] Could not check app existence for '{app_path}': {e}")
        return False

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = safe_cast_bool(config('DEBUG', default='False'), False)

# Validate SECRET_KEY in production (after DEBUG is defined)
if not DEBUG and SECRET_KEY == 'django-insecure-change-this-in-production':
    print("\n" + "="*70)
    print("🚨 SECURITY WARNING: Using default SECRET_KEY in production!")
    print("="*70)
    print("Set SECRET_KEY environment variable in Railway:")
    print("  1. Go to Railway → aiflowbackend-production → Variables")
    print("  2. Add: SECRET_KEY = <random-50-character-string>")
    print("")
    print("Generate a secure key with:")
    print("  python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"")
    print("="*70 + "\n")

# ================================================================
# USER MANAGEMENT SECURITY SETTINGS
# ================================================================
# Default password for admin-initiated password resets
# This password should be changed by users on first login
DEFAULT_USER_PASSWORD = config('DEFAULT_USER_PASSWORD', default='Rejlers@123')

# Railway-friendly ALLOWED_HOSTS configuration (SOFT-CODED)
try:
    # SOFT-CODED: Try to use centralized configuration first
    if CENTRALIZED_CONFIG_AVAILABLE:
        backend_config = env_config.get_backend_config()
        ALLOWED_HOSTS = backend_config.get('allowed_hosts', ['*'])
        print(f"[DJANGO] ✅ Using ALLOWED_HOSTS from centralized config: {ALLOWED_HOSTS}")
    else:
        # Fallback to environment variable
        ALLOWED_HOSTS_ENV = config('ALLOWED_HOSTS', default='*')  # Allow all by default for Railway
        if ALLOWED_HOSTS_ENV == '*':
            ALLOWED_HOSTS = ['*']
        else:
            ALLOWED_HOSTS = [s.strip() for s in ALLOWED_HOSTS_ENV.split(',')]

        # Add Railway domain automatically
        RAILWAY_STATIC_URL = config('RAILWAY_STATIC_URL', default='')
        if RAILWAY_STATIC_URL:
            railway_domain = RAILWAY_STATIC_URL.replace('https://', '').replace('http://', '')
            if railway_domain and railway_domain not in ALLOWED_HOSTS and '*' not in ALLOWED_HOSTS:
                ALLOWED_HOSTS.append(railway_domain)

        # Add .railway.app domains if not using wildcard
        if '*' not in ALLOWED_HOSTS and not any(host.endswith('.railway.app') for host in ALLOWED_HOSTS):
            ALLOWED_HOSTS.append('.railway.app')
        
        print(f"[DJANGO] ALLOWED_HOSTS: {ALLOWED_HOSTS}")
except Exception as e:
    print(f"[ERROR] ALLOWED_HOSTS configuration failed: {e}")
    # Fallback to allow all hosts to prevent 500 error
    ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'corsheaders',
    'drf_spectacular',  # API documentation
    'channels',  # Django Channels for WebSocket support
    
    # Local apps - Core
    'apps.core',
    'apps.users',
    'apps.api',
    'apps.rbac',
    'apps.hr_core',  # HR Core - Unified Employee Master System (Phase 1: Parallel to existing tables)
    
    # Local apps - Features (Plugin Architecture)
    'apps.pid_analysis',
    'apps.pfd',  # PFD Project Management - Reference Documents & Verification
    'apps.pfd_converter',
    'apps.crs',
    'apps.finance',  # Finance Invoice Automation
    'apps.sales',  # Sales Management - CRM, Pipeline, AI-Powered Insights
    'apps.designiq',  # DesignIQ - AI-Powered Engineering Design Intelligence
    'apps.procurement',  # Procurement Management - Vendor & PO Tracking
    'apps.notifications',  # Notification System - Multi-Channel Alerts & Email
    'apps.process_datasheet',  # Process Datasheet - AI-Powered Equipment Datasheet Generation
    # SOFT-CODED: Electrical Datasheet - RE-ENABLED
    'apps.electrical_datasheet',  # Electrical Datasheet - Transformer & Switchgear Technical Data Sheets
    'apps.usage_tracking',  # Usage Tracking & Metering - Internal Analytics Dashboard
    'apps.wrench_integration',  # Wrench Project Platform Integration
    'apps.data_mining',  # Data Mining Platform - AI-Powered Data Integration & Transformation (Tableau Prep-style)
    'apps.pid_verification',   # P&ID Quality Checker — deterministic rule engine
    'apps.sld_verification',   # SLD Quality Checker — electrical single line diagram verification
    'apps.pfd_quality',          # PFD Quality Checker — deterministic rule engine
    'apps.cross_recommendation', # Cross PID/PFD recommendation bridge
    'apps.non_teff_metadata',     # Non-TEFF Metadata Extractor — multi-format document metadata extraction
    'apps.instrument_tools',     # Instrument Tools — IO List / Cable Block Diagram / Cable Schedule (Generator + QC)
    'apps.instrument_io_workflow',  # Instrument IO List Workflow — CRS-style multi-revision IO List doc handling
    'apps.spec_customization',   # Spec Customization — Paper Spec PDF extraction (Piping Classes)
    'apps.marketing_analytics',  # Marketing Analytics — Google Analytics (GA4) real-time dashboard widget
    'apps.timesheet',            # Time Sheet Analytics — SQL Server attendance integration
    'apps.project_control',      # Project Management — phased cost dashboards, estimates, documents, AI take-off (stubbed)
    'apps.invoice_tracker',      # Invoice Tracker — Accounts Receivable register (external + internal) + Excel import + S3 attachments
    'apps.payroll',              # Payroll Intelligence Platform — validation, audit alerts, project costing, AI insights, chatbot
    'apps.payroll_engine',       # Payroll Engine — fresh monthly payroll automation (Draft → HR → Finance → Released)
    'apps.onboarding',           # Onboarding & Offboarding — employee lifecycle management (joining, exit, equipment, documents)
    'apps.site_visits',          # Site Visit Tracking — GPS-based attendance for off-site engineers
    'apps.dashboard',             # Personal Dashboard — role-scoped data bundles + AI insights
]

# ✨ SMART APP LOADING - Only load apps that exist (prevents deployment crashes)
OPTIONAL_APPS = [
    'apps.qhse',  # QHSE Management - Quality, Health, Safety, Environment
    'apps.ml_detection',  # ML Detection & Real-time Alerts
    'apps.activity',  # Real-time Activity Tracking
]

for app in OPTIONAL_APPS:
    if app_exists(app):
        INSTALLED_APPS.append(app)
        print(f"[OK] Loaded optional app: {app}")
    else:
        print(f"[WARNING] Skipped missing app: {app}")

# Add remaining apps
INSTALLED_APPS.extend([
    # WARNING CRITICAL: MLflow MUST STAY DISABLED for Railway
    # Enabling this will cause startup hangs (MLflow server not available)
    # 'apps.mlflow_integration',  # DO NOT UNCOMMENT
    
    # AWS S3 Storage (always include - it's in requirements.txt)
    'storages',
    # Add new features here - no core changes needed!
])

MIDDLEWARE = [
    # Must be first: normalise RFC-invalid Docker hostnames (underscores) before
    # CommonMiddleware's host-header validation runs. No-op in production (DEBUG=False).
    'apps.core.middleware.NormaliseDockerHostMiddleware',
    # CORS MUST be before SecurityMiddleware so CORS headers are added even when
    # SecurityMiddleware short-circuits the request (e.g. HTTPS enforcer redirects).
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.rbac.middleware.LoginTrackingMiddleware',
    'apps.users.middleware.PasswordExpiryMiddleware',  # Password expiry checking
    'apps.rbac.middleware.RBACMiddleware',
    'apps.activity.tracker.ActivityMiddleware',  # Activity tracking middleware
    'apps.usage_tracking.middleware.UsageTrackingMiddleware',  # Usage metering - Internal Analytics
    # AI Champion telemetry — captures every authenticated API request as an
    # ActivityEvent so the leaderboard / cost dashboard at /admin/ai-champion
    # receives LIVE data. Soft-coded URL→application/feature mapping; never
    # raises (telemetry failures are logged but do not break the request).
    'apps.rbac.ai_champion_middleware.AIChampionTelemetryMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ============================================================================== 
# AI CHAMPION TELEMETRY (SOFT-CODED)
# ============================================================================== 
# Runtime-tunable telemetry controls for platform-wide activity tracking.
# These values are read by apps.rbac.ai_champion_middleware.AIChampionTelemetryMiddleware
# and can be adjusted without touching business logic.

AI_CHAMPION_TELEMETRY_ENABLED = config('AI_CHAMPION_TELEMETRY_ENABLED', default=True, cast=bool)
AI_CHAMPION_API_PREFIX = config('AI_CHAMPION_API_PREFIX', default='/api/v1/')

# Comma-separated env override supported, e.g.
# AI_CHAMPION_TELEMETRY_EXCLUDE_PREFIXES="health/,auth/,rbac/users/me/"
AI_CHAMPION_TELEMETRY_EXCLUDE_PREFIXES = [
    p.strip() for p in config(
        'AI_CHAMPION_TELEMETRY_EXCLUDE_PREFIXES',
        default='rbac/ai-champion/,rbac/analytics/,auth/,health/,cors/,rbac/users/me/,notifications/poll'
    ).split(',') if p.strip()
]

# Ordered URL substring map: (needle, application_code, module_code)
# First match wins. Keep broad matches last.
AI_CHAMPION_TELEMETRY_APPLICATION_MAP = [
    ('pid-verification', 'pid-verification', 'process'),
    ('pid_verification', 'pid-verification', 'process'),
    ('pfd_quality', 'pfd-quality', 'process'),
    ('pfd_converter', 'pfd-converter', 'process'),
    ('pfd', 'pfd', 'process'),
    ('process_datasheet', 'process-datasheet', 'process'),
    ('equipment', 'equipment-list', 'process'),
    ('line', 'line-list', 'piping'),
    ('electrical', 'electrical', 'electrical'),
    ('instrument', 'instrument', 'instrument'),
    ('mechanical', 'mechanical', 'mechanical'),
    ('piping', 'piping', 'piping'),
    ('qhse', 'qhse', 'qhse'),
    ('crs', 'crs', 'documents'),
    ('designiq', 'designiq', 'designiq'),
    ('finance', 'finance', 'finance'),
    ('procurement', 'procurement', 'procurement'),
    ('sales', 'sales', 'sales'),
    ('wrench', 'wrench-integration', 'integrations'),
    ('rbac/users', 'user-management', 'admin'),
    ('rbac/roles', 'rbac-roles', 'admin'),
    ('rbac/audit', 'audit', 'admin'),
    ('rbac', 'rbac', 'admin'),
    ('activity', 'activity-tracking', 'admin'),
    ('usage_tracking', 'usage-tracking', 'admin'),
    ('notifications', 'notifications', 'platform'),
]

ROOT_URLCONF = 'config.urls'

# ==============================================================================
# HOST HEADER HANDLING (Fix for Docker internal hostnames in API responses)
# ==============================================================================
# When running in Docker, the request.get_host() may return 'backend:8000'
# which is not accessible from the browser. These settings ensure proper hostname.
USE_X_FORWARDED_HOST = True  # Use X-Forwarded-Host header if present
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # For HTTPS detection
# ==============================================================================

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ==============================================================================
# CHANNEL LAYERS CONFIGURATION (WebSocket support)
# ==============================================================================
# Redis-backed channel layer for Django Channels (WebSocket real-time features)
# Railway: Will use REDIS_URL if available, otherwise falls back to in-memory
# Docker: Uses redis:6379
# Fallback: In-memory channel layer (single-server only, no WebSocket persistence)

# Parse Redis configuration for Channel Layers
REDIS_URL_FOR_CHANNELS = config('REDIS_URL', default=None)

if REDIS_URL_FOR_CHANNELS:
    # Extract host and port from Redis URL for channels_redis
    # channels_redis expects (host, port) tuple, not full URL
    import re
    redis_match = re.match(r'redis://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)', REDIS_URL_FOR_CHANNELS)
    if redis_match:
        redis_host = redis_match.group(3)
        redis_port = int(redis_match.group(4))
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels_redis.core.RedisChannelLayer',
                'CONFIG': {
                    'hosts': [(redis_host, redis_port)],
                    'capacity': 1500,
                    'expiry': 10,
                },
            },
        }
        print(f"[CHANNELS] OK Channel layer configured (URL-based): {redis_host}:{redis_port}")
    else:
        # Could not parse URL - use in-memory fallback
        print(f"[CHANNELS] WARNING  Could not parse REDIS_URL")
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels.layers.InMemoryChannelLayer',
            },
        }
        print(f"[CHANNELS] WARNING Using in-memory channels (single-server only)")
else:
    # Check if REDIS_HOST is configured
    REDIS_HOST_FOR_CHANNELS = config('REDIS_HOST', default=None)
    if REDIS_HOST_FOR_CHANNELS and REDIS_HOST_FOR_CHANNELS != 'None':
        # Docker Compose: host/port configuration
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels_redis.core.RedisChannelLayer',
                'CONFIG': {
                    'hosts': [(REDIS_HOST_FOR_CHANNELS, config('REDIS_PORT', default=6379, cast=int))],
                    'capacity': 1500,
                    'expiry': 10,
                },
            },
        }
        print(f"[CHANNELS] OK Channel layer configured (host-based): {REDIS_HOST_FOR_CHANNELS}:{config('REDIS_PORT', default=6379)}")
    else:
        # No Redis available - use in-memory channel layer
        CHANNEL_LAYERS = {
            'default': {
                'BACKEND': 'channels.layers.InMemoryChannelLayer',
            },
        }
        print(f"[CHANNELS] WARNING Using in-memory channels (Redis not configured)")
        print(f"[CHANNELS] Note: WebSockets limited to single server. Set REDIS_URL for multi-server support.")

# ==============================================================================
# End of Channel Layers Configuration
# ==============================================================================

# ============================================
# SMART DATABASE CONFIGURATION - SOFT CODED
# ============================================
# Environment-aware database configuration
# Supports: production, staging, development, testing
# SOFT-CODED: add new environments by editing _ENV_DB_MAP only
#
# SECURITY: All database credentials come from environment variables (.env file
# locally, Railway/Docker env vars in production). NEVER hardcode credentials
# here — settings.py is committed to git.
#
# Required env vars (set in backend/.env for local dev):
#   ENVIRONMENT             — one of: development | staging | preprod | production
#   LOCAL_DATABASE_URL      — your local pgAdmin DB (e.g. postgres://user:pw@localhost:5432/dbname)
#   TEST_DATABASE_URL       — Railway test/staging DB URL
#   PRODUCTION_DATABASE_URL — Railway production DB URL (production only; never set locally)
#
# Override precedence: DATABASE_URL > _ENV_DB_MAP[ENVIRONMENT] > LOCAL_DATABASE_URL
# Railway sets DATABASE_URL automatically — no extra config needed in production.

# Get environment type — override via ENVIRONMENT env var or .env file
ENVIRONMENT = config('ENVIRONMENT', default='development')

# ── Named database URL constants (loaded from env, never hardcoded) ──────────
LOCAL_DATABASE_URL      = config('LOCAL_DATABASE_URL',      default='')
TEST_DATABASE_URL       = config('TEST_DATABASE_URL',       default='')
PRODUCTION_DATABASE_URL = config('PRODUCTION_DATABASE_URL', default='')
PREPROD_DATABASE_URL    = TEST_DATABASE_URL  # backward-compat alias

# ── Soft-coded environment → database routing map ────────────────────────────
# To add a new environment: just add a key here; no other changes needed.
_ENV_DB_MAP = {
    'production':  PRODUCTION_DATABASE_URL,
    'prod':        PRODUCTION_DATABASE_URL,
    'staging':     TEST_DATABASE_URL,
    'preprod':     TEST_DATABASE_URL,
    # Local dev uses local pgAdmin DB by default; falls back to Railway test if not set
    'development': LOCAL_DATABASE_URL or TEST_DATABASE_URL,
    'dev':         LOCAL_DATABASE_URL or TEST_DATABASE_URL,
    'local':       LOCAL_DATABASE_URL or TEST_DATABASE_URL,
    'testing':     TEST_DATABASE_URL,
    'test':        TEST_DATABASE_URL,
}

_default_db_url = _ENV_DB_MAP.get(ENVIRONMENT.lower(), '')
DATABASE_URL = config('DATABASE_URL', default=_default_db_url)

# ── Soft-coded list of management commands that don't need a real database ───
# (collectstatic, makemessages, etc. are run during Docker build before any
# DB is attached — falling back to an in-memory sqlite avoids spurious build
# failures while still raising loudly for any DB-touching command at runtime.)
import sys as _sys
_DB_OPTIONAL_COMMANDS = {
    'collectstatic', 'compilemessages', 'makemessages',
    'check', 'help', 'version', '--version',
}
_running_db_optional = any(arg in _DB_OPTIONAL_COMMANDS for arg in _sys.argv)

if not DATABASE_URL:
    if _running_db_optional or config('DJANGO_SKIP_DB_CHECK', default=False, cast=bool):
        # Stub DB — never used for queries, just keeps Django settings importable
        # so build-time tasks (e.g. collectstatic) don't fail.
        DATABASE_URL = 'sqlite:///:memory:'
        print(f"[DJANGO] ⚠️  No DATABASE_URL configured — using in-memory sqlite stub "
              f"(safe for build-time '{' '.join(_sys.argv[1:2]) or 'startup'}' only)")
    else:
        raise RuntimeError(
            f"No database URL configured for ENVIRONMENT='{ENVIRONMENT}'. "
            f"Set DATABASE_URL or LOCAL_DATABASE_URL/TEST_DATABASE_URL/PRODUCTION_DATABASE_URL "
            f"in backend/.env. See backend/.env.example for template."
        )

_env_labels = {
    'production': '🏭 PRODUCTION',
    'prod':       '🏭 PRODUCTION',
    'staging':    '🚀 STAGING',
    'preprod':    '🚀 PREPROD',
    'development':'🔧 DEVELOPMENT (local)',
    'dev':        '🔧 DEVELOPMENT (local)',
    'local':      '🔧 LOCAL',
    'testing':    '🧪 TESTING',
    'test':       '🧪 TESTING',
}
print(f"[DJANGO] {_env_labels.get(ENVIRONMENT.lower(), f'🔧 {ENVIRONMENT.upper()}')} Environment")
print(f"[DJANGO] 🗄️  DB host: {DATABASE_URL.split('@')[-1]}")

# Parse database configuration
db_config = dj_database_url.parse(DATABASE_URL)

# Extended timeout configuration for Railway database
db_config['CONN_MAX_AGE'] = 600  # Keep connection alive for 10 minutes
db_config['OPTIONS'] = {
    'connect_timeout': 60,  # 60 seconds for initial connection
    'options': '-c statement_timeout=120000',  # 120 seconds for queries
    'keepalives': 1,
    'keepalives_idle': 60,
    'keepalives_interval': 10,
    'keepalives_count': 10
}

# Dedicated test database name so `manage.py test` / pytest never touch the main DB
if ENVIRONMENT.lower() not in ('production', 'prod'):
    db_config['TEST'] = {'NAME': 'test_aiflow'}

DATABASES = {'default': db_config}

print(f"[DJANGO] DB: {db_config.get('HOST')}:{db_config.get('PORT')}")
print(f"[DJANGO] Timeouts: connect=60s, query=120s, keepalive=60s")

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
# Soft-coded: Application timezone for display (default UTC for safety)
# Set to 'Asia/Dubai' for UAE (UTC+4) or your office timezone
TIME_ZONE = config('DJANGO_TIME_ZONE', default='UTC')
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
# Note: STATIC_URL, STATIC_ROOT, MEDIA_URL, MEDIA_ROOT are configured
# in the S3 section below based on USE_S3 setting
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'users.User'

# Authentication Backends
# Use custom backend for case-insensitive email authentication
AUTHENTICATION_BACKENDS = [
    'apps.users.auth_backend.CaseInsensitiveEmailBackend',  # Custom case-insensitive email auth
    'django.contrib.auth.backends.ModelBackend',  # Default Django auth (fallback)
]

# REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,  # Increased from 10 to show more items per page
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'rest_framework.views.exception_handler',
}

# ==============================================================================
# JWT CONFIGURATION (Simple JWT)
# ==============================================================================
from datetime import timedelta

SIMPLE_JWT = {
    # Token Lifetimes
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),  # Access token valid for 24 hours
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),   # Refresh token valid for 7 days
    'ROTATE_REFRESH_TOKENS': True,                  # Rotate refresh token on use
    'BLACKLIST_AFTER_ROTATION': False,              # Keep old refresh tokens valid
    
    # Token Types
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    
    # Token Classes
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    
    # Signing
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    
    # Blacklisting (optional - can be enabled later)
    'JTI_CLAIM': 'jti',
    
    # Sliding tokens (disabled)
    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(hours=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}

# print(f"[JWT] ====== Configuration Loaded ======")
# print(f"[JWT] Access Token Lifetime: {SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']}")
# print(f"[JWT] Refresh Token Lifetime: {SIMPLE_JWT['REFRESH_TOKEN_LIFETIME']}")
# print(f"[JWT] Rotate Refresh Tokens: {SIMPLE_JWT['ROTATE_REFRESH_TOKENS']}")
# print(f"[JWT] ===================================")

# ==============================================================================
# End of JWT Configuration
# ==============================================================================

# ==============================================================================
# CORS CONFIGURATION - RAILWAY PRODUCTION READY (SOFT-CODED)
# ==============================================================================
# This configuration now uses centralized environment configuration
# from config/environments.json for proper alignment across all services

# SOFT-CODED: Sanitize any URL coming from env vars before it enters CORS_ALLOWED_ORIGINS.
# Fixes two common Railway misconfiguration patterns:
#   1. Missing scheme  → aiflowbackend.up.railway.app  becomes  https://aiflowbackend.up.railway.app
#   2. Trailing slash  → https://example.com/           becomes  https://example.com
# Returns None (skipped) for empty strings.
def sanitize_cors_origin(url: str) -> str | None:
    if not url or not url.strip():
        return None
    url = url.strip().rstrip('/')
    if url and '://' not in url:
        url = 'https://' + url
    return url or None

# PRODUCTION URLS (sanitized so Railway env values can't break CORS validation)
PRODUCTION_FRONTEND = sanitize_cors_origin(
    config('FRONTEND_URL', default='https://airflow-frontend.vercel.app')
) or 'https://airflow-frontend.vercel.app'
PRODUCTION_BACKEND = sanitize_cors_origin(
    config('BACKEND_URL', default='https://aiflowbackend-production.up.railway.app')
) or 'https://aiflowbackend-production.up.railway.app'
FRONTEND_URL = sanitize_cors_origin(
    config('FRONTEND_URL', default='http://localhost:5173')
) or 'http://localhost:5173'  # For email links

# ==============================================================================
# RAILWAY PRODUCTION SAFETY CHECK
# ==============================================================================
# CRITICAL: Detect if we're on Railway production and ensure core domains are set
IS_RAILWAY = config('RAILWAY_ENVIRONMENT', default='') != ''
RAILWAY_ENV_NAME = config('RAILWAY_ENVIRONMENT', default='local')

if IS_RAILWAY:
    print(f"\n[RAILWAY] 🚂 Detected Railway environment: {RAILWAY_ENV_NAME}")
    print(f"[RAILWAY] Frontend URL: {PRODUCTION_FRONTEND}")
    print(f"[RAILWAY] Backend URL: {PRODUCTION_BACKEND}")
    
    # Ensure production URLs are set correctly for Railway
    if 'radai.ae' not in PRODUCTION_FRONTEND:
        print("[RAILWAY] ⚠️  WARNING: FRONTEND_URL does not contain radai.ae")
        print("[RAILWAY] ⚠️  Using fallback: https://www.radai.ae")
        PRODUCTION_FRONTEND = 'https://www.radai.ae'
    
    if 'railway.app' not in PRODUCTION_BACKEND:
        print("[RAILWAY] ⚠️  WARNING: BACKEND_URL does not contain railway.app")
        print("[RAILWAY] ⚠️  Using fallback: https://aiflowbackend-production.up.railway.app")
        PRODUCTION_BACKEND = 'https://aiflowbackend-production.up.railway.app'
    
    print(f"[RAILWAY] ✅ Production URLs validated\n")

# ==============================================================================
# CORS CONFIGURATION (SOFT-CODED with Railway Safety)
# Setting this to True will break JWT authentication with credentials
# Railway Env Var: CORS_ALLOW_ALL_ORIGINS=False (or omit to use default)
CORS_ALLOW_ALL_ORIGINS = safe_cast_bool(config('CORS_ALLOW_ALL_ORIGINS', default='False'), False)

if CORS_ALLOW_ALL_ORIGINS:
    # If allowing all origins, disable credentials for security
    CORS_ALLOW_CREDENTIALS = False
    CORS_ALLOWED_ORIGINS = []  # Not used when allow all is True
    print("[CORS] WARNING  WARNING: CORS_ALLOW_ALL_ORIGINS is True - ALL origins allowed!")
else:
    # SOFT-CODED: Use centralized configuration if available
    if CENTRALIZED_CONFIG_AVAILABLE:
        print("[CORS] ✅ Using centralized environment configuration")
        CORS_ALLOWED_ORIGINS = get_cors_origins()
        print(f"[CORS] Current environment: {get_environment()}")
    else:
        # Fallback to environment variable or defaults
        CORS_ORIGINS_ENV = config('CORS_ALLOWED_ORIGINS', default='')
        if CORS_ORIGINS_ENV:
            # If env var is set, use it (comma-separated list) — sanitize each entry
            CORS_ALLOWED_ORIGINS = [
                sanitized for origin in CORS_ORIGINS_ENV.split(',')
                if (sanitized := sanitize_cors_origin(origin))
            ]
        else:
            # Use default list
            CORS_ALLOWED_ORIGINS = [
                # Production - Custom Domain
                'https://radai.ae',
                'https://www.radai.ae',
                'http://radai.ae',  # Include HTTP for redirects
                'http://www.radai.ae',
                # Production - Vercel
                PRODUCTION_FRONTEND,
                PRODUCTION_BACKEND,
                # Development
                'http://localhost:3000',
                'http://localhost:5173',
                'http://localhost:5175',
                'http://127.0.0.1:3000',
                'http://127.0.0.1:5173',
                'http://127.0.0.1:5175',
            ]
    
    # CRITICAL: Always add production domains regardless of config source
    # This ensures Railway production always accepts requests from www.radai.ae
    production_domains = [
        'https://radai.ae',
        'https://www.radai.ae',
        'http://radai.ae',
        'http://www.radai.ae',
        'https://aiflowbackend-production.up.railway.app',
    ]
    for domain in production_domains:
        if domain not in CORS_ALLOWED_ORIGINS:
            CORS_ALLOWED_ORIGINS.append(domain)
    
    # Always add backend URL to allowed origins (for Railway health checks)
    BACKEND_URL_CORS = sanitize_cors_origin(config('BACKEND_URL', default=''))
    if BACKEND_URL_CORS and BACKEND_URL_CORS not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(BACKEND_URL_CORS)
    
    # Always add frontend URL to allowed origins
    FRONTEND_URL_CORS = sanitize_cors_origin(config('FRONTEND_URL', default=''))
    if FRONTEND_URL_CORS and FRONTEND_URL_CORS not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(FRONTEND_URL_CORS)
    
    # Allow credentials (REQUIRED for JWT tokens in Authorization header)
    CORS_ALLOW_CREDENTIALS = True

# Allow all standard methods
CORS_ALLOW_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']

# Allow all necessary headers
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'cache-control',
    'pragma',
]

# Expose headers for downloads
CORS_EXPOSE_HEADERS = ['content-disposition', 'content-type', 'cache-control']

# Cache preflight for 1 hour
CORS_PREFLIGHT_MAX_AGE = safe_cast_int(config('CORS_PREFLIGHT_MAX_AGE', default='3600'), 3600)

# Allow regex patterns for Vercel previews and localhost (only if not allowing all)
if not CORS_ALLOW_ALL_ORIGINS:
    CORS_ALLOWED_ORIGIN_REGEXES = [
        r'^https://.*\.vercel\.app$',
        r'^http://localhost:\d+$',
        r'^http://127\.0\.0\.1:\d+$',
    ]
else:
    CORS_ALLOWED_ORIGIN_REGEXES = []

# Additional CORS settings for preflight
CORS_ALLOW_PRIVATE_NETWORK = True

print("\n" + "="*70)
print("[CORS] ====== CORS CONFIGURATION ======")
if IS_RAILWAY:
    print(f"[CORS] 🚂 Railway Environment: {RAILWAY_ENV_NAME}")
print("="*70)
print(f"[CORS] Allow All Origins: {CORS_ALLOW_ALL_ORIGINS}")
if not CORS_ALLOW_ALL_ORIGINS:
    print(f"[CORS] Allowed Origins Count: {len(CORS_ALLOWED_ORIGINS)}")
    print(f"[CORS] Allowed Origins:")
    for origin in CORS_ALLOWED_ORIGINS:
        # Highlight production domains
        if 'radai.ae' in origin or 'railway.app' in origin:
            print(f"  ✅ {origin}  <-- PRODUCTION")
        else:
            print(f"  - {origin}")
print(f"[CORS] Allow Credentials: {CORS_ALLOW_CREDENTIALS}")
print(f"[CORS] Preflight Max Age: {CORS_PREFLIGHT_MAX_AGE}s")
print(f"[CORS] Frontend URL: {PRODUCTION_FRONTEND}")
print(f"[CORS] Backend URL: {PRODUCTION_BACKEND}")
if IS_RAILWAY:
    print(f"[CORS] Railway Health Check: {PRODUCTION_BACKEND}/api/v1/health/")
print("="*70 + "\n")

# ==============================================================================
# CSRF CONFIGURATION (SOFT-CODED)
# ==============================================================================

# SOFT-CODED: Use centralized configuration if available
if CENTRALIZED_CONFIG_AVAILABLE:
    print("[CSRF] ✅ Using centralized environment configuration")
    CSRF_TRUSTED_ORIGINS = get_csrf_origins()
else:
    # Fallback: Build CSRF trusted origins from CORS origins
    CSRF_TRUSTED_ORIGINS = [
        'https://radai.ae',
        'https://www.radai.ae',
        PRODUCTION_FRONTEND,
        PRODUCTION_BACKEND,
        'http://localhost:3000',
        'http://localhost:5173',
    ]

    # Add any additional origins from environment
    if not CORS_ALLOW_ALL_ORIGINS and CORS_ALLOWED_ORIGINS:
        for origin in CORS_ALLOWED_ORIGINS:
            if origin not in CSRF_TRUSTED_ORIGINS and origin.startswith('https'):
                CSRF_TRUSTED_ORIGINS.append(origin)

# CRITICAL: Always add production domains to CSRF regardless of config source
production_csrf_domains = [
    'https://radai.ae',
    'https://www.radai.ae',
    'https://aiflowbackend-production.up.railway.app',
]
for domain in production_csrf_domains:
    if domain not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(domain)

# CSRF settings - Important for API endpoints
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = 'None' if not DEBUG else 'Lax'
CSRF_USE_SESSIONS = False
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript to read for API calls

print(f"[CSRF] Trusted Origins: {len(CSRF_TRUSTED_ORIGINS)} domains")
for origin in CSRF_TRUSTED_ORIGINS:
    print(f"  - {origin}")

# ==============================================================================
# End of CORS/CSRF Configuration
# ==============================================================================

# ==============================================================================
# CACHE CONFIGURATION (Redis)
# ==============================================================================
# Cache backend for session storage, task progress tracking, and performance optimization
# Railway: Set REDIS_URL environment variable (e.g., redis://default:password@host:port)
# Docker: Uses redis:6379 by default
# Fallback: Uses in-memory cache if Redis not available (Railway without Redis plugin)

REDIS_URL = config('REDIS_URL', default=None)
REDIS_HOST = config('REDIS_HOST', default=None)

if REDIS_URL:
    # Railway or external Redis (URL-based configuration)
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'SOCKET_CONNECT_TIMEOUT': 5,  # seconds
                'SOCKET_TIMEOUT': 5,  # seconds
                'RETRY_ON_TIMEOUT': True,
                'MAX_CONNECTIONS': 50,
                'CONNECTION_POOL_KWARGS': {
                    'max_connections': 50,
                    'retry_on_timeout': True,
                },
            },
            'KEY_PREFIX': 'radai',
            'TIMEOUT': 300,  # 5 minutes default
        }
    }
    print(f"[CACHE] OK Redis cache configured (URL-based)")
    print(f"[CACHE] URL: {REDIS_URL.split('@')[0]}@***")  # Hide credentials
elif REDIS_HOST and REDIS_HOST != 'None':
    # Docker Compose: host/port configuration
    REDIS_PORT = config('REDIS_PORT', default=6379, cast=int)
    REDIS_PASSWORD = config('REDIS_PASSWORD', default=None)
    
    redis_location = f"redis://{':' + REDIS_PASSWORD + '@' if REDIS_PASSWORD else ''}{REDIS_HOST}:{REDIS_PORT}/1"
    
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': redis_location,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'SOCKET_CONNECT_TIMEOUT': 5,
                'SOCKET_TIMEOUT': 5,
                'RETRY_ON_TIMEOUT': True,
                'MAX_CONNECTIONS': 50,
            },
            'KEY_PREFIX': 'radai',
            'TIMEOUT': 300,
        }
    }
    print(f"[CACHE] OK Redis cache configured (host-based)")
    print(f"[CACHE] Host: {REDIS_HOST}:{REDIS_PORT}")
else:
    # Fallback: In-memory cache (Railway without Redis plugin)
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'radai-cache',
            'TIMEOUT': 300,
            'OPTIONS': {
                'MAX_ENTRIES': 1000,
            }
        }
    }
    print(f"[CACHE] WARNING Using in-memory cache (Redis not configured)")
    print(f"[CACHE] Note: Cache will be lost on restart. Set REDIS_URL for persistent cache.")

# ==============================================================================
# End of Cache Configuration
# ==============================================================================

# ==============================================================================
# CELERY CONFIGURATION (Task Queue)
# ==============================================================================
# Celery broker and result backend - uses same Redis configuration as cache
# Railway: Set REDIS_URL or CELERY_BROKER_URL environment variable
# Docker: Uses redis:6379 by default
# Fallback: Celery disabled if Redis not available

if REDIS_URL:
    # Use the same Redis URL for Celery (different database number for separation)
    CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=REDIS_URL.replace('/1', '/0'))
    CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default=REDIS_URL.replace('/1', '/0'))
    print(f"[CELERY] OK Broker configured (URL-based)")
elif REDIS_HOST and REDIS_HOST != 'None':
    # Fallback to host/port configuration
    REDIS_PORT = config('REDIS_PORT', default=6379, cast=int)
    CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=f'redis://{REDIS_HOST}:{REDIS_PORT}/0')
    CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default=f'redis://{REDIS_HOST}:{REDIS_PORT}/0')
    print(f"[CELERY] OK Broker configured (host-based): {REDIS_HOST}:{REDIS_PORT}")
else:
    # No Redis available - disable Celery (tasks will run synchronously)
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_BROKER_URL = None
    CELERY_RESULT_BACKEND = None  # No backend needed for EAGER mode
    print(f"[CELERY] WARNING Running in EAGER mode (Redis not configured)")
    print(f"[CELERY] Note: Tasks run synchronously. Set REDIS_URL for async tasks.")

# Check if EAGER mode is explicitly requested via environment variable.
# IMPORTANT: preserve True set by the no-Redis branch above; only override if explicitly
# provided in the environment so we never clobber the safe synchronous fallback.
_eager_already_set = globals().get('CELERY_TASK_ALWAYS_EAGER', False)
CELERY_TASK_ALWAYS_EAGER = config(
    'CELERY_TASK_ALWAYS_EAGER',
    default=_eager_already_set,
    cast=bool,
)
CELERY_TASK_EAGER_PROPAGATES = config(
    'CELERY_TASK_EAGER_PROPAGATES',
    default=CELERY_TASK_ALWAYS_EAGER,
    cast=bool,
)

if CELERY_TASK_ALWAYS_EAGER:
    print(f"[CELERY] ⚡ EAGER mode enabled via environment variable")
    print(f"[CELERY] ⚡ Tasks will execute synchronously (immediate results)")

# SOFT-CODED: opt-in flag for the base_extraction endpoint to prefer Celery
# over thread-based extraction.  False by default (safe for all environments).
# Set CELERY_BASE_EXTRACTION_PREFER_CELERY=true in Railway env vars only when
# a live Redis broker is confirmed to be available.
CELERY_BASE_EXTRACTION_PREFER_CELERY = config(
    'CELERY_BASE_EXTRACTION_PREFER_CELERY', default=False, cast=bool
)
print(f"[CELERY] base_extraction prefer_celery: {CELERY_BASE_EXTRACTION_PREFER_CELERY}")

CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# SOFT-CODED: Broker transport options — ensures .delay() fails fast when
# the Redis broker is unreachable (slow timeout vs immediate ERRNO 111).
# Values come from environment variables with safe defaults.
# CELERY_BROKER_CONNECTION_TIMEOUT: how long (seconds) to wait for broker TCP connect
CELERY_BROKER_CONNECTION_TIMEOUT = config('CELERY_BROKER_CONNECTION_TIMEOUT', default=5, cast=float)
CELERY_BROKER_TRANSPORT_OPTIONS = {
    # Redis socket timeouts — fail fast if broker is unreachable
    'socket_connect_timeout': CELERY_BROKER_CONNECTION_TIMEOUT,
    'socket_timeout':         CELERY_BROKER_CONNECTION_TIMEOUT * 2,  # read/write ops
    # Retry once only so .delay() raises quickly instead of blocking
    'max_retries':     1,
    'interval_start':  0,
    'interval_step':   0.2,
    'interval_max':    0.5,
    # Long visibility timeout so heavy OCR tasks are not re-queued mid-flight
    'visibility_timeout': 43200,  # 12 hours
}
print(f"[CELERY] Broker connect timeout: {CELERY_BROKER_CONNECTION_TIMEOUT}s")

# Safe printing of broker URLs (handle None case)
if CELERY_BROKER_URL:
    print(f"[CELERY] Broker: {CELERY_BROKER_URL.split('@')[0] if '@' in CELERY_BROKER_URL else CELERY_BROKER_URL}")
else:
    print(f"[CELERY] Broker: None (EAGER mode)")
if CELERY_RESULT_BACKEND:
    print(f"[CELERY] Result Backend: {CELERY_RESULT_BACKEND.split('@')[0] if '@' in CELERY_RESULT_BACKEND else CELERY_RESULT_BACKEND}")
else:
    print(f"[CELERY] Result Backend: None")

# Celery Beat — scheduled tasks
# Run daily at 02:00 server time; the task self-skips unless PayrollSchedule.enabled
# and the calendar day matches the configured day_of_month / days_after_month_end.
from celery.schedules import crontab  # noqa: E402
CELERY_BEAT_SCHEDULE = {
    'auto-generate-monthly-payroll': {
        'task': 'apps.finance.tasks.auto_generate_monthly_payroll',
        'schedule': crontab(hour=2, minute=0),
    },
}

# ==============================================================================
# End of Celery Configuration
# ==============================================================================

# Process Datasheet - Dynamic Retry Configuration
DATASHEET_MAX_RETRIES = config('DATASHEET_MAX_RETRIES', default=5, cast=int)
DATASHEET_TASK_TIMEOUT = config('DATASHEET_TASK_TIMEOUT', default=600, cast=int)  # 10 minutes
DATASHEET_RETRY_BACKOFF = config('DATASHEET_RETRY_BACKOFF', default=2, cast=int)  # Exponential backoff multiplier

# API Documentation
SPECTACULAR_SETTINGS = {
    'TITLE': 'RADAI API',
    'DESCRIPTION': 'Smart API for RADAI application',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ==============================================================================
# AWS S3 CONFIGURATION (SECURE)
# ==============================================================================

# Enable S3 storage (set to True to use S3, False to use local storage)
# IMPORTANT: Requires S3_READY=True to prevent deployment failures with invalid credentials
USE_S3_CONFIG = safe_cast_bool(config('USE_S3', default='False'), False)
S3_READY = safe_cast_bool(config('S3_READY', default='False'), False)

# Smart S3 validation: Require explicit S3_READY flag to prevent credential errors
if USE_S3_CONFIG and not S3_READY:
    print("WARNING  [S3] USE_S3=True but S3_READY=False. Using local storage for safety.")
    print("    Set S3_READY=True on Railway after updating AWS credentials.")
    USE_S3 = False
elif USE_S3_CONFIG and S3_READY:
    # Double-check credentials are present
    aws_access_key = config('AWS_ACCESS_KEY_ID', default='')
    aws_secret_key = config('AWS_SECRET_ACCESS_KEY', default='')
    aws_bucket_name = config('AWS_STORAGE_BUCKET_NAME', default='')
    
    # Validate that all required S3 configuration is present
    if not aws_access_key or not aws_secret_key or not aws_bucket_name:
        print("WARNING  [S3] Credentials incomplete despite S3_READY=True. Falling back to local storage.")
        print(f"    - AWS_ACCESS_KEY_ID: {'OK Set' if aws_access_key else '✗ Missing'}")
        print(f"    - AWS_SECRET_ACCESS_KEY: {'OK Set' if aws_secret_key else '✗ Missing'}")
        print(f"    - AWS_STORAGE_BUCKET_NAME: {'OK Set' if aws_bucket_name else '✗ Missing'}")
        USE_S3 = False  # Disable S3 if credentials are incomplete
    else:
        print(f"OK [S3] Enabled with bucket: {aws_bucket_name}")
        USE_S3 = True
else:
    USE_S3 = False

if USE_S3:
    # AWS Credentials - LOADED FROM ENVIRONMENT (NEVER HARDCODE)
    # Boto3 automatically checks:
    # 1. Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    # 2. IAM Role (EC2, ECS, Lambda) - PREFERRED for production
    # 3. AWS credentials file (~/.aws/credentials)
    
    # Expose credentials as settings for explicit boto3 usage (soft-coded)
    # These are read from environment variables, NEVER hardcoded
    AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default='')
    AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='')
    
    # WARNING CRITICAL: S3 bucket must exist before deployment
    # Railway Env Var: AWS_STORAGE_BUCKET_NAME=user-management-rejlers (production bucket)
    # Only configure S3 if bucket name is set (prevents startup errors)
    AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME', default='')
    
    if AWS_STORAGE_BUCKET_NAME:
        # S3 Configuration
        AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='us-east-1')
        
        # Security: Use AWS Signature Version 4 (required for some regions)
        AWS_S3_SIGNATURE_VERSION = 's3v4'
        
        # Force region-specific endpoint — presigned URLs for opt-in regions
        # (e.g. me-central-1 / UAE) break if boto3 uses the global s3.amazonaws.com
        # endpoint because S3 redirects invalidate the Signature=host header.
        AWS_S3_ENDPOINT_URL = f'https://s3.{AWS_S3_REGION_NAME}.amazonaws.com'
        
        # Security: Enable encryption at rest
        AWS_S3_ENCRYPTION = True
        
        # Security: All files are private by default
        AWS_DEFAULT_ACL = 'private'
        
        # Security: Use presigned URLs instead of public URLs
        AWS_S3_CUSTOM_DOMAIN = None
        AWS_QUERYSTRING_AUTH = True
        
        # URL expiration for presigned URLs (1 hour)
        AWS_QUERYSTRING_EXPIRE = 3600

        # Per-feature presigned URL expiry (soft-coded, env-overridable)
        IO_LIST_PDF_PRESIGN_EXPIRY = int(config('IO_LIST_PDF_PRESIGN_EXPIRY', default='3600'))
        
        # Performance: Connection settings
        AWS_S3_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB
        AWS_S3_FILE_OVERWRITE = False  # Don't overwrite files
        
        # Storage backends
        DEFAULT_FILE_STORAGE = 'apps.core.storage_backends.MediaStorage'
        # Keep static files local, only use S3 for media/documents
        STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
        
        # Media files (uploaded by users) - use S3
        MEDIA_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/media/'
        
        # Static files (CSS/JS) - use local storage
        STATIC_ROOT = BASE_DIR / 'staticfiles'
        STATIC_URL = '/static/'
    else:
        # S3 enabled but bucket not configured - use local storage
        print("WARNING  USE_S3=True but AWS_STORAGE_BUCKET_NAME not set. Using local storage.")
        MEDIA_ROOT = BASE_DIR / 'media'
        MEDIA_URL = '/media/'
        STATIC_ROOT = BASE_DIR / 'staticfiles'
        STATIC_URL = '/static/'
        STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
else:
    # Local storage configuration (development/production without S3)
    MEDIA_ROOT = BASE_DIR / 'media'
    MEDIA_URL = '/media/'
    STATIC_ROOT = BASE_DIR / 'staticfiles'
    STATIC_URL = '/static/'
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ─────────────────────────────────────────────────────────────────────
# Large-file upload tuning (soft-coded via env vars)
# ─────────────────────────────────────────────────────────────────────
# Engineering source documents (P&IDs, spec books, scanned catalogues) can
# legitimately reach ~1 GB. The defaults below match the frontend cap
# (VITE_SPEC_MAX_FILE_MB = 1024) and the Gunicorn worker timeout.
#
# Override per env without code deploy:
#   DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE  → total non-file request body cap (bytes)
#   DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE  → in-memory file threshold; above this
#                                         Django spools to disk via TemporaryFileUploadHandler
#   DJANGO_DATA_UPLOAD_MAX_NUMBER_FIELDS → multipart field count cap
# ─────────────────────────────────────────────────────────────────────
_GB = 1024 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    config('DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE', default=str(2 * _GB))
)  # 2 GB headroom for ~1 GB files + form fields
FILE_UPLOAD_MAX_MEMORY_SIZE = int(
    config('DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE', default=str(5 * 1024 * 1024))
)  # 5 MB — anything larger streams to a temp file on disk (no RAM blow-up)
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(
    config('DJANGO_DATA_UPLOAD_MAX_NUMBER_FIELDS', default='10000')
)
FILE_UPLOAD_HANDLERS = [
    'django.core.files.uploadhandler.TemporaryFileUploadHandler',
]

# ─────────────────────────────────────────────────────────────────────
# Wrench → S3 Export — dedicated bucket (soft-coded via env var)
# Set `WRENCH_S3_BUCKET` in .env / Railway. Falls back to 'wrench-radai'.
# Used by apps.wrench_integration.s3_service._get_bucket()
# ─────────────────────────────────────────────────────────────────────
WRENCH_S3_BUCKET = config('WRENCH_S3_BUCKET', default='wrench-radai')

# ─────────────────────────────────────────────────────────────────────
# Payroll Intelligence — S3 prefix config (reuses AWS_STORAGE_BUCKET_NAME)
# Files land in payroll/slips/, payroll/documents/, payroll/exports/
# ─────────────────────────────────────────────────────────────────────
PAYROLL_S3_PREFIX          = config('PAYROLL_S3_PREFIX',         default='payroll/slips')
PAYROLL_DOCS_S3_PREFIX     = config('PAYROLL_DOCS_S3_PREFIX',    default='payroll/documents')
PAYROLL_EXPORTS_S3_PREFIX  = config('PAYROLL_EXPORTS_S3_PREFIX', default='payroll/exports')

# Mirror mode for project-document exports to S3 (soft-coded, opt-in).
#   'flat'      → wrench/projects/{order_no}/documents/{doc_no}{ext}   (default)
#   'genealogy' → wrench/projects/{order_no}/{folder1}/{folder2}/.../{doc_no}{ext}
# 'genealogy' rebuilds the original R:\Projects folder hierarchy in S3 by
# honouring each Wrench document's GENEALOGY_STRING. See
# apps.wrench_integration.s3_documents_service for details.
WRENCH_S3_MIRROR_MODE = config('WRENCH_S3_MIRROR_MODE', default='flat')

# OpenAI Configuration (existing)
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
OPENAI_MODEL = config('OPENAI_MODEL', default='gpt-4o')

# ==============================================================================
# PROCUREMENT DOCUMENT EXTRACTION CONFIGURATION (SOFT-CODED)
# ==============================================================================
# Extraction method for PO/PR documents:
#   'tesseract' - Free OCR with regex pattern matching (recommended for cost)
#   'openai'    - GPT-4o structured extraction (better accuracy, costs $)
# Override via PROCUREMENT_EXTRACTION_METHOD env var
PROCUREMENT_EXTRACTION_METHOD = config(
    'PROCUREMENT_EXTRACTION_METHOD',
    default='tesseract',
).lower()

# Tesseract OCR language (for multi-language support)
PROCUREMENT_OCR_LANG = config('PROCUREMENT_OCR_LANG', default='eng')

# Maximum PDF file size for extraction (bytes) - soft-coded for easy tuning
PROCUREMENT_MAX_PDF_SIZE = int(config(
    'PROCUREMENT_MAX_PDF_SIZE',
    default=10 * 1024 * 1024,  # 10MB default
))

# Confidence threshold for auto-mapping vendors (0.0 - 1.0)
# Lower = more lenient matching, Higher = stricter matching
PROCUREMENT_VENDOR_MATCH_THRESHOLD = float(config(
    'PROCUREMENT_VENDOR_MATCH_THRESHOLD',
    default=0.7,  # 70% similarity required
))

# ==============================================================================
# PAYROLL WORKFLOW CONFIGURATION (SOFT-CODED)
# ==============================================================================
# Super-admin email: this user can unfreeze any master payroll file.
# Override via PAYROLL_WORKFLOW_SUPERADMIN_EMAIL env var.
PAYROLL_WORKFLOW_SUPERADMIN_EMAIL = config(
    'PAYROLL_WORKFLOW_SUPERADMIN_EMAIL',
    default='tanzeem.agra@rejlers.ae',
).lower()

# Comma-separated list of HR Manager emails that receive an in-app + email
# notification whenever a master payroll file is frozen.
# Add more recipients by updating this env var (no code change needed).
PAYROLL_FREEZE_NOTIFY_EMAILS = [
    e.strip().lower()
    for e in config(
        'PAYROLL_FREEZE_NOTIFY_EMAILS',
        default='sanglin.samuel@rejlers.ae',
    ).split(',')
    if e.strip()
]

# SLA thresholds (working days) per workflow stage.
# Approval Tracker highlights files exceeding these limits.
# Override any value via environment without a code change.
PAYROLL_TRACKER_SLA_DAYS = {
    'draft':            int(config('PAYROLL_SLA_DRAFT',            default=3)),
    'frozen':           int(config('PAYROLL_SLA_FROZEN',           default=2)),
    'hr_approved':      int(config('PAYROLL_SLA_HR_APPROVED',      default=3)),
    'finance_review':   int(config('PAYROLL_SLA_FINANCE_REVIEW',   default=3)),
    'finance_approved': int(config('PAYROLL_SLA_FINANCE_APPROVED', default=2)),
    'released':         None,   # terminal stage — no SLA
}

# ==============================================================================
# REPORT GENERATION CONFIGURATION (SOFT-CODED)
# ==============================================================================

# Company Branding for Reports
REPORT_COMPANY_NAME = config('REPORT_COMPANY_NAME', default='REJLERS ABU DHABI')
REPORT_COMPANY_SUBTITLE = config('REPORT_COMPANY_SUBTITLE', default='Engineering & Design Consultancy')
REPORT_COMPANY_WEBSITE = config('REPORT_COMPANY_WEBSITE', default='www.rejlers.com/ae')

# Report Colors (Hex values without #)
REPORT_PRIMARY_COLOR = config('REPORT_PRIMARY_COLOR', default='003366')  # Dark blue
REPORT_SECONDARY_COLOR = config('REPORT_SECONDARY_COLOR', default='FFA500')  # Orange
REPORT_TEXT_COLOR = config('REPORT_TEXT_COLOR', default='333333')
REPORT_BORDER_COLOR = config('REPORT_BORDER_COLOR', default='CCCCCC')

# Report Template Settings
REPORT_TITLE = config('REPORT_TITLE', default='P&ID DESIGN VERIFICATION REPORT')
REPORT_FOOTER_TEXT = config('REPORT_FOOTER_TEXT', default='CONFIDENTIAL ENGINEERING DOCUMENT')
REPORT_FOOTER_NOTE = config('REPORT_FOOTER_NOTE', default='This document is the property of {company}. Unauthorized distribution is prohibited.')

# Format footer note with company name
REPORT_FOOTER_NOTE_FORMATTED = REPORT_FOOTER_NOTE.format(company=REPORT_COMPANY_NAME)
# ==============================================================================
# EMAIL CONFIGURATION (AWS SES SMTP)
# ==============================================================================

# Email backend configuration
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')

# AWS SES SMTP Configuration
# Note: ME-CENTRAL-1 region doesn't support SES SMTP, use US-EAST-1
EMAIL_HOST = config('EMAIL_HOST', default='email-smtp.us-east-1.amazonaws.com')
EMAIL_PORT = safe_cast_int(config('EMAIL_PORT', default='587'), 587)
EMAIL_USE_TLS = safe_cast_bool(config('EMAIL_USE_TLS', default='True'), True)
EMAIL_USE_SSL = safe_cast_bool(config('EMAIL_USE_SSL', default='False'), False)

# SMTP Credentials (from AWS SES - rejlers-radai IAM user - Production)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')  # SMTP Username - MUST be set via env var
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')  # SMTP Password - MUST be set via env var

# From Email (using verified email temporarily)
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='tanzeem.agra@rejlers.ae')
SERVER_EMAIL = config('SERVER_EMAIL', default='tanzeem.agra@rejlers.ae')

# Email settings
EMAIL_TIMEOUT = 10  # Timeout in seconds
EMAIL_SUBJECT_PREFIX = config('EMAIL_SUBJECT_PREFIX', default='[RADAI] ')

# Email Verification Settings
EMAIL_VERIFICATION_REQUIRED = safe_cast_bool(config('EMAIL_VERIFICATION_REQUIRED', default='True'), True)
EMAIL_VERIFICATION_TOKEN_EXPIRY = safe_cast_int(config('EMAIL_VERIFICATION_TOKEN_EXPIRY', default='86400'), 86400)  # 24 hours

print("\n" + "=" * 60)
# print("EMAIL CONFIGURATION")
print("=" * 60)
print(f"Email Backend: {EMAIL_BACKEND}")
print(f"Email Host: {EMAIL_HOST}")
print(f"Email Port: {EMAIL_PORT}")
print(f"Use TLS: {EMAIL_USE_TLS}")
print(f"SMTP User Configured: {'Yes' if EMAIL_HOST_USER else 'No'}")
print(f"Default From Email: {DEFAULT_FROM_EMAIL}")
print("=" * 60 + "\n")

# ========================================================================
# Finance Module - Approval Team Email Addresses
# ========================================================================
FINANCE_EMAIL = config('FINANCE_EMAIL', default='khanabdullahomar886@gmail.com')
FINANCE_RICHA_EMAIL = config('FINANCE_RICHA_EMAIL', default='test.user1@rejlers.ae')
RICHA_EMAIL = FINANCE_RICHA_EMAIL  # Alias for backward compatibility
JAMAL_EMAIL = config('FINANCE_JAMAL_EMAIL', default='test.user2@rejlers.ae')
RAFAT_EMAIL = config('FINANCE_RAFAT_EMAIL', default='test.user3@rejlers.ae')
MOE_EMAIL = config('FINANCE_MOE_EMAIL', default='test.user4@rejlers.ae')
JARMO_EMAIL = config('FINANCE_JARMO_EMAIL', default='test.user5@rejlers.ae')
ANEEF_EMAIL = config('FINANCE_ANEEF_EMAIL', default='test.user6@rejlers.ae')
ALEKSI_EMAIL = config('FINANCE_ALEKSI_EMAIL', default='test.user7@rejlers.ae')
SHERWIN_EMAIL = config('FINANCE_SHERWIN_EMAIL', default='test.user8@rejlers.ae')
NIJUM_EMAIL = config('FINANCE_NIJUM_EMAIL', default='test.user9@rejlers.ae')
HR_ADMIN_EMAIL = config('FINANCE_HR_ADMIN_EMAIL', default='test.user10@rejlers.ae')

# ========================================================================
# Usage Tracking Configuration
# ========================================================================
# Enable/disable usage tracking globally
ENABLE_USAGE_TRACKING = safe_cast_bool(config('ENABLE_USAGE_TRACKING', default='True'), True)

# Data retention (days)
USAGE_LOG_RETENTION_DAYS = safe_cast_int(config('USAGE_LOG_RETENTION_DAYS', default='90'), 90)

# Cache TTL for summary data (seconds)
USAGE_CACHE_TTL = safe_cast_int(config('USAGE_CACHE_TTL', default='300'), 300)

print("\n" + "=" * 60)
print("USAGE TRACKING CONFIGURATION")
print("=" * 60)
print(f"Enabled: {ENABLE_USAGE_TRACKING}")
print(f"Log Retention: {USAGE_LOG_RETENTION_DAYS} days")
print(f"Cache TTL: {USAGE_CACHE_TTL} seconds")
print("=" * 60 + "\n")

# ========================================================================
# DISCIPLINE-BASED MODULE ACCESS CONFIGURATION (Soft-Coded RBAC)
# ========================================================================
# Maps user disciplines/departments to module access for 300+ concurrent users
# No code changes needed - update via Django admin or environment config
# Supports unlimited disciplines and modules through DisciplineAccessConfig

from apps.rbac.discipline_config import DisciplineAccessConfig

# Default soft-coded configuration (can be overridden via environment)
DISCIPLINE_MODULE_ACCESS = DisciplineAccessConfig.DEFAULT_DISCIPLINE_MODULES

print("\n" + "=" * 60)
print("DISCIPLINE-BASED ACCESS CONTROL (SOFT-CODED RBAC)")
print("=" * 60)
for module_code, config_dict in DISCIPLINE_MODULE_ACCESS.items():
    accessible_depts = config_dict.get('accessible_by_disciplines', [])
    print(f"  {module_code:25} → {len(accessible_depts)} department(s)")
print("=" * 60 + "\n")

# ========================================================================
# ROBUST QUEUE SERVICE CONFIGURATION
# ========================================================================
# Circuit breaker settings for Celery queue reliability
# Auto-fallback to sync processing if queue unavailable (300+ user support)

QUEUE_CIRCUIT_BREAKER_MAX_FAILURES = safe_cast_int(
    config('QUEUE_CIRCUIT_BREAKER_MAX_FAILURES', default='5'), 5
)
QUEUE_CIRCUIT_BREAKER_TIMEOUT = safe_cast_int(
    config('QUEUE_CIRCUIT_BREAKER_TIMEOUT', default='300'), 300
)
QUEUE_MAX_RETRIES = safe_cast_int(
    config('QUEUE_MAX_RETRIES', default='3'), 3
)

print("\n" + "=" * 60)
print("ROBUST QUEUE SERVICE (FALLBACK)")
print("=" * 60)
print(f"Circuit Breaker Max Failures: {QUEUE_CIRCUIT_BREAKER_MAX_FAILURES}")
print(f"Circuit Breaker Timeout: {QUEUE_CIRCUIT_BREAKER_TIMEOUT}s")
print(f"Max Retries: {QUEUE_MAX_RETRIES}")
print("=" * 60 + "\n")