"""
Time Sheet Analytics — Soft-Coded Configuration
================================================
Every connection parameter, table name, column name, business rule and
report option is defined here and overridable via environment variables.
Add a new field by editing this file only.

ENV VAR PREFIX: TIMESHEET_*

Connection:
    TIMESHEET_HOST              default 192.168.99.52
    TIMESHEET_PORT              default 1433
    TIMESHEET_USER              (required for real data)
    TIMESHEET_PASSWORD          (required for real data) — never in code
    TIMESHEET_DATABASE          (set after running the discovery wizard)
    TIMESHEET_TIMEOUT           default 10 (seconds)
    TIMESHEET_DRIVER            'pymssql' | 'pyodbc'  (auto-detected)

Schema mapping (set via .env after discovery wizard tells you the names):
    TIMESHEET_TABLE             e.g. 'dbo.AttendanceLog' or 'dbo.vw_DailyAttendance'
    TIMESHEET_COL_EMPCODE       default 'EmpCode'
    TIMESHEET_COL_EMAIL         default 'EmpEmail'
    TIMESHEET_COL_NAME          default 'EmpName'
    TIMESHEET_COL_DEPT          default 'Department'
    TIMESHEET_COL_TIME          default 'PunchTime'           (single-event schema)
    TIMESHEET_COL_TYPE          default 'PunchType'           (single-event schema)
    TIMESHEET_IN_VALUE          default 'IN'
    TIMESHEET_OUT_VALUE         default 'OUT'
    TIMESHEET_COL_LOGIN         (optional) for two-column schema, e.g. 'FirstIn'
    TIMESHEET_COL_LOGOUT        (optional) for two-column schema, e.g. 'LastOut'
    TIMESHEET_COL_DATE          (optional) date column for two-column schema

Business rules:
    TIMESHEET_EXPECTED_LOGIN    default 9   (hour-of-day)
    TIMESHEET_EXPECTED_LOGOUT   default 18
    TIMESHEET_LATE_MIN          default 15  (minutes past expected login = "late")
    TIMESHEET_FULL_DAY          default 8.0 (hours that count as a full day)
    TIMESHEET_WORK_DAYS         default 'Mon,Tue,Wed,Thu,Fri'

UI:
    TIMESHEET_POLL_SEC          default 30  (frontend auto-refresh cadence)
"""
import os
from decouple import config

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _env_int(key, default):
    try:
        return int(config(key, default=str(default)))
    except (TypeError, ValueError):
        return int(default)


def _env_float(key, default):
    try:
        return float(config(key, default=str(default)))
    except (TypeError, ValueError):
        return float(default)


def _env_csv(key, default_list):
    raw = config(key, default=','.join(default_list))
    return [p.strip() for p in raw.split(',') if p.strip()]


def _env_first(keys, default=''):
    """Return the first env var in `keys` that has a non-empty value.
    Lets us accept multiple aliases for the same setting without breaking
    older .env files."""
    for k in keys:
        v = config(k, default='')
        if v:
            return v
    return default


def _env_first_int(keys, default):
    raw = _env_first(keys, default=str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _env_first_float(keys, default):
    raw = _env_first(keys, default=str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


# ─────────────────────────────────────────────────────────────────────────────
# Public soft-coded config
# ─────────────────────────────────────────────────────────────────────────────
SQLSERVER = {
    'host':     config('TIMESHEET_HOST',     default='192.168.99.52'),
    'port':     _env_int('TIMESHEET_PORT', 1433),
    'user':     config('TIMESHEET_USER',     default=''),
    'password': config('TIMESHEET_PASSWORD', default=''),
    'database': config('TIMESHEET_DATABASE', default=''),
    # Short default so cloud envs (Railway) that can't reach the on-prem
    # SQL Server (private LAN IP) fail in ~5s instead of hanging 110s.
    'timeout':  _env_int('TIMESHEET_TIMEOUT', 5),
    'driver':   config('TIMESHEET_DRIVER',   default='auto'),  # 'pymssql' | 'pyodbc' | 'auto'
}

# Soft-coded master switch. Set TIMESHEET_FEATURE_ENABLED=false on Railway/
# any environment that has no network path to the biometric SQL Server. The
# UI then renders the existing 'Not Configured' banner instead of erroring.
FEATURE_ENABLED = config('TIMESHEET_FEATURE_ENABLED', default='true').lower() in (
    '1', 'true', 'yes', 'on',
)

# Soft-coded data source selector.
#   'sqlserver' → query the on-prem Matrix SQL Server directly (local LAN only)
#   'mirror'    → query the Postgres TimesheetEvent table populated by the
#                 office-side sync agent (works anywhere, incl. Railway)
DATA_SOURCE = config('TIMESHEET_DATA_SOURCE', default='sqlserver').lower().strip()
if DATA_SOURCE not in ('sqlserver', 'mirror'):
    DATA_SOURCE = 'sqlserver'

# Shared secret for the office agent → Railway ingest endpoint. Generate a
# strong random string and set this identically on both sides. Empty value
# means ingest is disabled (returns 403).
MIRROR_API_KEY = config('TIMESHEET_MIRROR_API_KEY', default='')

# Soft cap on a single ingest batch — guards against runaway POST bodies.
MIRROR_INGEST_MAX_BATCH = _env_int('TIMESHEET_MIRROR_MAX_BATCH', 5000)

SCHEMA = {
    'table': _env_first(['TIMESHEET_TABLE'], default=''),
    'columns': {
        'employee_code':  _env_first(['TIMESHEET_COL_EMP_CODE',  'TIMESHEET_COL_EMPCODE'], default='EmpCode'),
        # Empty default = "no email column in this schema" (e.g. Matrix biometric).
        # services.py emits the email field conditionally — same pattern as 'department'.
        'employee_email': _env_first(['TIMESHEET_COL_EMP_EMAIL', 'TIMESHEET_COL_EMAIL'],   default=''),
        'employee_name':  _env_first(['TIMESHEET_COL_EMP_NAME',  'TIMESHEET_COL_NAME'],    default='EmpName'),
        'department':     _env_first(['TIMESHEET_COL_DEPARTMENT', 'TIMESHEET_COL_DEPT'],   default=''),
        # Schema variant A: one row per punch with a type column
        'punch_time':     _env_first(['TIMESHEET_COL_PUNCH_TIME', 'TIMESHEET_COL_TIME'],   default='PunchTime'),
        'punch_type':     _env_first(['TIMESHEET_COL_PUNCH_TYPE', 'TIMESHEET_COL_TYPE'],   default='PunchType'),
        'in_value':       _env_first(['TIMESHEET_COL_IN_VALUE',   'TIMESHEET_IN_VALUE'],   default='IN'),
        'out_value':      _env_first(['TIMESHEET_COL_OUT_VALUE',  'TIMESHEET_OUT_VALUE'],  default='OUT'),
        # Schema variant B: one row per (user, day) with separate in/out columns
        'login_time':     _env_first(['TIMESHEET_COL_LOGIN_TIME',  'TIMESHEET_COL_LOGIN'],  default=''),
        'logout_time':    _env_first(['TIMESHEET_COL_LOGOUT_TIME', 'TIMESHEET_COL_LOGOUT'], default=''),
        'date':           _env_first(['TIMESHEET_COL_DATE'],                                 default=''),
    },
}

RULES = {
    'expected_login_hour':  _env_first_int(['TIMESHEET_EXPECTED_LOGIN_HOUR',  'TIMESHEET_EXPECTED_LOGIN'],  9),
    'expected_logout_hour': _env_first_int(['TIMESHEET_EXPECTED_LOGOUT_HOUR', 'TIMESHEET_EXPECTED_LOGOUT'], 18),
    'late_threshold_min':   _env_first_int(['TIMESHEET_LATE_THRESHOLD_MIN',   'TIMESHEET_LATE_MIN'],        15),
    'full_day_hours':       _env_first_float(['TIMESHEET_FULL_DAY_HOURS',    'TIMESHEET_FULL_DAY'],        8.0),
    
    # ── Maximum daily hours enforcement (user-approved 2026-06-26) ──────────
    # Caps ALL daily hours calculations to this value. Hours exceeding this
    # limit are truncated to enforce company policy (standard: 9 hours max).
    # Override via env: TIMESHEET_MAX_DAILY_HOURS=9
    'max_daily_hours':      _env_first_float(['TIMESHEET_MAX_DAILY_HOURS'], 9.0),
    
    'working_days':         _env_csv('TIMESHEET_WORK_DAYS', ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']),
    # Soft-coded rolling time-window for the Live view (mirror mode).
    'live_lookback_hours':  _env_int('TIMESHEET_LIVE_LOOKBACK_HOURS', 20),

    # ── Hours calculation mode (mirror mode) ─────────────────────────────────
    # 'paired'  (default) — only count time between matched IN→OUT punch pairs.
    #   An employee who checks IN, wanders off without checking OUT, then checks
    #   IN again later only gets credit for completed IN→OUT segments.
    #   Eliminates inflated "hours" caused by employees forgetting/skipping
    #   the exit punch.
    # 'elapsed' (legacy)  — first_in to last_out regardless of interim punches.
    #   Use this only if your biometric system emits every IN/OUT correctly.
    # Override via env: TIMESHEET_HOURS_MODE=elapsed
    'hours_mode': config('TIMESHEET_HOURS_MODE', default='paired').lower().strip(),

    # Maximum hours credited for an open (unclosed) IN punch.
    # When an employee has punched IN but not yet OUT:
    #   0   = do NOT credit any hours until they punch out (default, strictest)
    #   N   = credit min(elapsed_since_in, N) hours (e.g. 10 = cap at 10 h)
    # Override via env: TIMESHEET_OPEN_SHIFT_MAX_HOURS=10
    'open_shift_max_hours': _env_first_float(
        ['TIMESHEET_OPEN_SHIFT_MAX_HOURS', 'TIMESHEET_OPEN_SHIFT_MAX'], 0.0
    ),
}

# UTC offset (hours) of naive datetime strings sent by the office sync agent.
# The Matrix biometric system records times in local office time (e.g. UAE = UTC+4).
# The sync agent sends them as naive ISO strings ("2026-06-17T08:31:42") —
# with no timezone indicator — so the ingest endpoint must know the offset
# to store the correct UTC value.
#
# Default 0 = treat incoming naive timestamps as already UTC (backward-
# compatible for agents that pre-convert, or where the SQL Server is UTC).
# Set to 4 for UAE/Abu Dhabi office (UTC+4), 3 for KSA (UTC+3), etc.
#
# IMPORTANT: changing this value only affects NEWLY ingested events.
# Re-run the sync agent with --full to back-fill existing records.
INGEST_TZ_OFFSET_HOURS = _env_int('TIMESHEET_INGEST_TZ_OFFSET', 0)

UI = {
    'polling_seconds': _env_int('TIMESHEET_POLL_SEC', 30),
}

# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE OPTIMIZATION — Intelligent Caching & Query Limits
# ─────────────────────────────────────────────────────────────────────────────
# Multi-tier caching dramatically reduces load on the SQL Server and speeds
# up live data synchronization. All settings are soft-coded via env vars.

PERFORMANCE = {
    # Query result limit (safety cap for memory/network)
    # Live view typically returns ~200-500 rows (one per active employee)
    # Daily typically returns ~200-500 rows (one per employee who punched today)
    # Monthly typically returns ~200-500 rows (one per employee with activity)
    # Set lower if your org has 5K+ employees and you see memory issues.
    'max_result_rows': _env_int('TIMESHEET_MAX_RESULT_ROWS', 10000),
    
    # SQL query timeout (seconds) — independent from connection timeout
    # Protects against slow queries when SQL Server is under load
    'query_timeout_sec': _env_int('TIMESHEET_QUERY_TIMEOUT', 30),
    
    # Enable query result pagination for large datasets
    # When True, queries use SQL Server's OFFSET/FETCH for memory efficiency
    'enable_pagination': config('TIMESHEET_ENABLE_PAGINATION', default='false', cast=bool),
    'page_size': _env_int('TIMESHEET_PAGE_SIZE', 1000),
    
    # Connection pool settings (for high-concurrency environments)
    'connection_pool_size': _env_int('TIMESHEET_POOL_SIZE', 5),
    'connection_pool_overflow': _env_int('TIMESHEET_POOL_OVERFLOW', 10),
    
    # Intelligent query optimization
    # When True, queries against large tables (1M+ rows) add optimized WHERE
    # clauses (e.g. EventDateTime > GETDATE()-7 for live view) to reduce scan
    'optimize_large_tables': config('TIMESHEET_OPTIMIZE_LARGE_TABLES', default='true', cast=bool),
    
    # Parallel query execution for monthly reports (when mirror mode is active)
    # Splits month into weeks and queries in parallel, then merges results
    'parallel_monthly_queries': config('TIMESHEET_PARALLEL_MONTHLY', default='false', cast=bool),
    'parallel_workers': _env_int('TIMESHEET_PARALLEL_WORKERS', 4),
}

# ─────────────────────────────────────────────────────────────────────────────
# INTELLIGENT FALLBACK CHAIN
# ─────────────────────────────────────────────────────────────────────────────
# Production-safe degradation strategy when SQL Server is unreachable.
# Order matters — each fallback is tried in sequence until one succeeds.

FALLBACK_CHAIN = {
    'enabled': config('TIMESHEET_FALLBACK_ENABLED', default='true', cast=bool),
    
    # 1. Try cache first (Redis) — fastest, stale data acceptable for live view
    'try_cache_first': config('TIMESHEET_TRY_CACHE_FIRST', default='true', cast=bool),
    
    # 2. Try mirror table (Postgres) — if sync agent has populated it
    'try_mirror_fallback': config('TIMESHEET_TRY_MIRROR_FALLBACK', default='true', cast=bool),
    
    # 3. Serve empty response with configured=False (graceful degradation)
    'graceful_empty': config('TIMESHEET_GRACEFUL_EMPTY', default='true', cast=bool),
    
    # Maximum age of stale cache data to serve (seconds)
    # 300 = 5 minutes (show 5-minute-old data rather than fail)
    'max_stale_age': _env_int('TIMESHEET_MAX_STALE_AGE', 300),
}

# ─────────────────────────────────────────────────────────────────────────────
# Export deduplication — removes duplicate employee_code rows from reports.
#
# Root cause: the biometric source can send the same employee under slightly
# different code strings (e.g. '22393', '22393 ', ' 22393') due to device or
# agent inconsistencies. Each variant creates a separate DB entry and then
# shows as a separate row in Excel / PDF.
#
# TIMESHEET_EXPORT_DEDUP  (default: true) — master toggle for dedup in exports.
# When true, rows are normalised (strip whitespace) and merged before writing.
# Disable only if you deliberately need raw (un-merged) rows for diagnostics.
#
# TIMESHEET_EXPORT_DEDUP_NORM  (default: 'strip') — normalisation applied to
# employee_code before grouping.  Only 'strip' is supported now; future values
# could add 'upper' or 'zfill_6' for systems with zero-padded codes.
# ─────────────────────────────────────────────────────────────────────────────
EXPORT_DEDUP = config('TIMESHEET_EXPORT_DEDUP', default='true').lower() in ('1', 'true', 'yes', 'on')
EXPORT_DEDUP_NORM = config('TIMESHEET_EXPORT_DEDUP_NORM', default='strip').lower().strip()


# Soft-coded biometric user-details enrichment.
# Pulls the configured columns (default Card1) from the Matrix user-details
# view and joins them onto attendance rows by UserID. Disable by setting
# TIMESHEET_USER_DETAILS_ENABLED=false. Cache TTL governs how long results
# are remembered per (set-of-userids) — default 10 minutes.
USER_DETAILS = {
    'enabled':     config('TIMESHEET_USER_DETAILS_ENABLED', default='true').lower() in ('1', 'true', 'yes', 'on'),
    'table':       config('TIMESHEET_USER_DETAILS_TABLE',   default='dbo.Mx_VEW_UserDetails'),
    'join_col':    config('TIMESHEET_USER_DETAILS_JOIN_COL', default='UserID'),
    # Comma-separated list of columns to expose. Each entry can be a bare
    # column name (used as-is for both source + payload key) or a
    # `Source:alias` pair so the JSON key stays stable when the source name
    # changes. Defaults expose Card1, OfficeEmail / PersEmail and FullName
    # so the Time Sheet table can (a) show the badge card, (b) fall back to
    # Matrix-side emails when the attendance view has no email column and
    # (c) drive name-based RAD AI matching when neither side has an email.
    'columns':     _env_csv(
        'TIMESHEET_USER_DETAILS_COLUMNS',
        ['Card1', 'OfficeEmail:office_email', 'PersEmail:personal_email', 'FullName:matrix_full_name'],
    ),
    'cache_ttl':   _env_int('TIMESHEET_USER_DETAILS_CACHE_TTL_SEC', 600),
}

# When True, rows still missing `radai_email` after the primary email/employee_id
# match are back-filled by tokenising the Matrix `FullName` and searching
# `UserProfile.user` (first_name/last_name/email/username). Soft cap on how many
# tokens must match for a hit — 1 = lenient (single distinctive token wins),
# 2 = stricter (e.g. first + last). Set to 0 to disable the backfill entirely.
NAME_BACKFILL = {
    'enabled':        config('TIMESHEET_NAME_BACKFILL_ENABLED', default='true').lower() in ('1', 'true', 'yes', 'on'),
    'min_token_hits': _env_int('TIMESHEET_NAME_BACKFILL_MIN_HITS', 2),
    'max_candidates': _env_int('TIMESHEET_NAME_BACKFILL_MAX_CANDIDATES', 8),
}

# Soft-coded user-mapping priority (email primary, employee_id fallback)
USER_MAPPING_PRIORITY = ['email', 'employee_id']


def is_configured() -> bool:
    """Quick check: do we have the bare minimum to attempt a query?"""
    if not FEATURE_ENABLED:
        return False
    # Mirror mode needs no SQL Server creds — the Postgres table is always
    # there. Just check the feature is on; reads return empty list naturally.
    if DATA_SOURCE == 'mirror':
        return True
    return bool(
        SQLSERVER['host']
        and SQLSERVER['user']
        and SQLSERVER['password']
        and SQLSERVER['database']
        and SCHEMA['table']
    )


def configuration_status() -> dict:
    """Detailed health snapshot for the Setup wizard."""
    return {
        'feature_enabled':   FEATURE_ENABLED,
        'data_source':       DATA_SOURCE,
        'host':              bool(SQLSERVER['host']),
        'credentials':       bool(SQLSERVER['user'] and SQLSERVER['password']),
        'database_selected': bool(SQLSERVER['database']),
        'table_selected':    bool(SCHEMA['table']),
        'schema_variant':    _detect_schema_variant(),
        'configured':        is_configured(),
        'polling_seconds':   UI['polling_seconds'],
    }


def _detect_schema_variant() -> str:
    cols = SCHEMA['columns']
    if cols['login_time'] and cols['logout_time']:
        return 'two_column'   # one row per user/day with separate in/out
    if cols['punch_time'] and cols['punch_type']:
        return 'event_stream'  # one row per punch with a type
    return 'unknown'


# ─────────────────────────────────────────────────────────────────────────────
# Celery beat schedule — picked up by config/celery.py via merge.
#   TIMESHEET_MONTHLY_REPORT_CRON  e.g. "0 8 1 * *"  (default: 1st of month, 08:00 UTC)
#   TIMESHEET_MONTHLY_REPORT_ENABLED  set 'false' to disable
# ─────────────────────────────────────────────────────────────────────────────
BEAT_SCHEDULE = {}
if config('TIMESHEET_MONTHLY_REPORT_ENABLED', default='true').lower() in ('1', 'true', 'yes', 'on'):
    _cron_raw = config('TIMESHEET_MONTHLY_REPORT_CRON', default='0 8 1 * *')
    try:
        from celery.schedules import crontab
        _parts = _cron_raw.split()
        if len(_parts) == 5:
            _minute, _hour, _dom, _month, _dow = _parts
            BEAT_SCHEDULE['timesheet-monthly-report'] = {
                'task': 'timesheet.send_monthly_report',
                'schedule': crontab(
                    minute=_minute, hour=_hour,
                    day_of_month=_dom, month_of_year=_month, day_of_week=_dow,
                ),
            }
    except Exception:
        # Don't let a malformed cron expression break Django boot.
        pass
