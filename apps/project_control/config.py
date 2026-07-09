"""
Project Management — Soft-coded configuration & phase flags.

Every threshold, S3 prefix, AI model name and rollout flag lives here.
Add a new option by editing this file only — never inline magic values
in views or services.

Phase rollout
-------------
The PHASE_FLAGS dict drives both the backend (501 stubs vs live endpoints)
and the frontend (which tabs render real UI vs a "Coming in Phase X" card).

Override any flag from the environment:
    PROJECT_CONTROL_PHASE_1_COST_DASHBOARD=true
    PROJECT_CONTROL_PHASE_2_AI_TAKEOFF=true
    ...

The same dict is returned by GET /api/v1/project-control/phase-flags/ so
the frontend stays in lock-step with the backend without a rebuild.
"""
from celery.schedules import crontab
from decouple import config

# ─────────────────────────────────────────────────────────────────────────────
# Phase rollout — defaults
# ─────────────────────────────────────────────────────────────────────────────
_PHASE_DEFAULTS = {
    # Phase 1 — Fastest to Production (ships ON)
    # Project Dashboard reads from the core Project model — always available.
    'phase_1_project_dashboard': True,
    'phase_1_cost_dashboard':    True,
    'phase_1_estimate_variance': True,
    'phase_1_finance_sync':      True,
    'phase_1_documents':         True,
    # Phase 2 — Assisted Intelligence (3–6 months) — stub
    'phase_2_ai_takeoff':        False,
    'phase_2_wbs_alignment':     False,
    # Phase 3 — Predictive & Control (6–9 months) — stub
    'phase_3_evm_forecast':      False,
    'phase_3_cashflow_curve':    False,
    # Phase 4 — Advanced Intelligence (9–12 months) — stub
    'phase_4_risk_analytics':    False,
    'phase_4_change_detection':  False,
}


def _bool_env(key, default):
    """Read an env-var as a bool, accepting only true-ish strings."""
    raw = config(key, default=str(default))
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _build_phase_flags():
    """Materialise PHASE_FLAGS with per-flag env-var overrides."""
    out = {}
    for flag, default in _PHASE_DEFAULTS.items():
        env_key = f'PROJECT_CONTROL_{flag.upper()}'
        out[flag] = _bool_env(env_key, default)
    return out


PHASE_FLAGS = _build_phase_flags()


def is_phase_enabled(flag: str) -> bool:
    """Return True if the given phase flag is currently enabled.

    Falls back to False for unknown flags so a typo never opens a stub.
    """
    return bool(PHASE_FLAGS.get(flag, False))


# ─────────────────────────────────────────────────────────────────────────────
# S3 layout
# ─────────────────────────────────────────────────────────────────────────────
# Top-level prefix inside the media bucket. Documents are stored at:
#   {S3_BASE_PREFIX}/{project_code}/{kind}/{uuid}_{filename}
S3_BASE_PREFIX = config('PROJECT_CONTROL_S3_PREFIX', default='project-control')

# Presigned download URL TTL (seconds). 1 h default.
S3_PRESIGN_TTL_SEC = int(config('PROJECT_CONTROL_S3_PRESIGN_TTL', default='3600'))

# Allowed document upload size (bytes). Defaults to 200 MB.
MAX_DOCUMENT_BYTES = int(
    config('PROJECT_CONTROL_MAX_DOC_BYTES', default=str(200 * 1024 * 1024))
)

# Soft-coded list of accepted document kinds (must match models.DOCUMENT_KIND_CHOICES).
DOCUMENT_KINDS = [
    'boq', 'tender', 'contract', 'change_order', 'drawing',
    'progress_report', 'minutes', 'specification', 'other',
]

# ─────────────────────────────────────────────────────────────────────────────
# Excel BOQ import — fuzzy header detection
# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded synonym map: canonical_name → list of header substrings (lowercased,
# whitespace-insensitive match). First substring match in a header row wins.
# Add a new language / synonym by appending to the list — no service changes.
BOQ_HEADER_SYNONYMS = {
    'wbs':          ['wbs', 'item no', 'item code', 'sr no', 'sr.', 'sl no', 's/n'],
    'description':  ['description', 'item description', 'particular', 'scope'],
    'discipline':   ['discipline', 'trade', 'category'],
    'unit':         ['unit', 'uom', 'u.o.m'],
    'quantity':     ['qty', 'quantity', 'nos'],
    'unit_rate':    ['rate', 'unit rate', 'unit price', 'price'],
    'line_total':   ['amount', 'total', 'value', 'cost'],
}

# Maximum rows to scan when auto-locating the header row in an Excel sheet.
BOQ_HEADER_SCAN_ROWS = int(config('PROJECT_CONTROL_BOQ_HEADER_SCAN_ROWS', default='25'))

# ─────────────────────────────────────────────────────────────────────────────
# Estimate variance — colour thresholds (also exposed to frontend via flags API)
# ─────────────────────────────────────────────────────────────────────────────
VARIANCE_THRESHOLDS = {
    # delta_pct ≤ green_max → green, ≤ amber_max → amber, else red
    'green_max': float(config('PROJECT_CONTROL_VARIANCE_GREEN_MAX', default='0')),
    'amber_max': float(config('PROJECT_CONTROL_VARIANCE_AMBER_MAX', default='10')),
}

# ─────────────────────────────────────────────────────────────────────────────
# Finance sync — how invoices link to projects (Phase 1 best-effort)
# ─────────────────────────────────────────────────────────────────────────────
# The Finance Invoice model has no FK to Project today, so we do a soft join:
#   1. If Invoice.line_items JSON contains a `project_code` matching Project.code → count it.
#   2. Else if Invoice.extracted_text contains the Project.code → count it.
# Both rules are deliberately conservative; users can always edit Project.spent manually.
FINANCE_SYNC_ENABLED = is_phase_enabled('phase_1_finance_sync')
FINANCE_INVOICE_PROJECT_KEY = config(
    'PROJECT_CONTROL_FINANCE_INVOICE_KEY', default='project_code'
)

# ─────────────────────────────────────────────────────────────────────────────
# AI (Phase 2 take-off, Phase 4 change detection) — model + prompt constants
# ─────────────────────────────────────────────────────────────────────────────
AI_TAKEOFF_MODEL    = config('PROJECT_CONTROL_AI_TAKEOFF_MODEL',    default='gpt-4o-mini')
AI_CHANGE_DET_MODEL = config('PROJECT_CONTROL_AI_CHANGE_DET_MODEL', default='gpt-4o-mini')
AI_TEMPERATURE      = float(config('PROJECT_CONTROL_AI_TEMPERATURE', default='0.1'))
AI_MAX_TOKENS       = int(config('PROJECT_CONTROL_AI_MAX_TOKENS',   default='4000'))

# ─────────────────────────────────────────────────────────────────────────────
# Celery beat — periodic tasks merged into config/celery.py
# ─────────────────────────────────────────────────────────────────────────────
# Schedule entries are conditional on the relevant phase flag so disabling a
# phase also disables its background work. Empty dict ⇒ no beat entries.
BEAT_SCHEDULE = {}

if is_phase_enabled('phase_1_finance_sync'):
    BEAT_SCHEDULE['project_control_finance_sync_nightly'] = {
        'task': 'apps.project_control.tasks.finance_sync_all_projects',
        'schedule': crontab(hour=2, minute=0),
    }

if is_phase_enabled('phase_3_evm_forecast'):
    BEAT_SCHEDULE['project_control_daily_cost_snapshot'] = {
        'task': 'apps.project_control.tasks.compute_daily_cost_snapshot_all',
        'schedule': crontab(hour=3, minute=0),
    }
