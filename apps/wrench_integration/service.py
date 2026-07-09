"""
Wrench SmartProject Integration – Service Layer
Implements the real SmartProject API authentication flow:
  1. POST /api/AccessControl/Login  → receive TOKEN
  2. Every subsequent request must include TOKEN in the request body
  3. Every response returns a refreshed TOKEN → must be stored for next call (rolling token)

API Reference: SmartProject API - Rejlers R0.pdf
"""
import logging
from urllib.parse import urlparse
import requests
from datetime import timedelta
from django.utils import timezone as dj_timezone

from .models import WrenchConfig, WrenchSyncLog
from .crypto import decrypt_value

logger = logging.getLogger(__name__)

# Timeouts
_TIMEOUT_FAST = 15       # login / health
_TIMEOUT_SEARCH = 90     # document/transmittal search (Wrench returns full dataset)
_TIMEOUT_PROBE  = 8      # soft-coded shorter timeout for endpoint-existence probes
                          # (used when iterating fallback REST paths that may 404)
# Token freshness window – re-login if token older than this
_TOKEN_MAX_AGE_MINUTES = 55

# ─── Soft-coded per-host endpoint capability cache ───────────────────────────
# Persists across requests within the Django process. Keyed by base_url so each
# Wrench instance has its own profile. Values:
#   • dead_paths      → set of REST paths that returned 404 (skip on retry)
#   • winning_path    → first path that returned data for get_transmittal_documents
#                       (tried FIRST on subsequent calls)
#   • exhausted_until → epoch timestamp; while in the future, skip all REST
#                       probes for trans-documents and jump straight to fallback
# The cache is in-memory only — restart the backend to clear it.
_WRENCH_ENDPOINT_PROFILE: dict = {}
# How long to remember "all REST paths failed for this host" before re-probing.
# Soft-coded; tune via WRENCH_EXHAUSTED_TTL_SECONDS env if needed.
_TRANS_DOC_EXHAUSTED_TTL_SECONDS = 15 * 60   # 15 minutes


def _host_profile(cfg: WrenchConfig) -> dict:
    """Get or create the per-host capability profile."""
    key = (cfg.base_url or '').strip().rstrip('/').lower()
    prof = _WRENCH_ENDPOINT_PROFILE.get(key)
    if prof is None:
        prof = {'dead_paths': set(), 'winning_path': None, 'exhausted_until': 0.0}
        _WRENCH_ENDPOINT_PROFILE[key] = prof
    return prof


# ─── Soft-coded SVC URL discovery patterns ───────────────────────────────────
# The DocumentSearch service may live on the same host as the WebAPI or on a
# dedicated SVC host. These patterns are tried in order; the first one that
# returns a non-404 response wins. Admin-set svc_url always overrides this list.
#
# Each entry is a callable that, given the parsed base_url components, returns
# a candidate base URL string (without trailing slash) — or None to skip.
#
# Real-world SmartProject installations observed:
#   • https://<host>/                              ← single-server / SVC at root
#   • https://<host>/<base_path>                   ← single-server / shared path
#   • https://<host>/WrenchSVC                     ← path-based SVC mount
#   • https://<host>/WrenchSearchSVC               ← dedicated search mount
#   • https://<host>/WrenchSVC_<project>_<env>     ← parallel to WebAPI naming
#   • https://<host>/WrenchService_<project>_<env>/SVC  ← Rejlers / newer SmartProject
#   • https://<host>/<base_path>/SVC               ← /SVC mount appended to WebAPI path
#   • https://svc.<host>/                          ← dedicated SVC subdomain
#   • https://<host-prefix>-svc.<rest>             ← dash-separated SVC host
#
# Soft-coded suffixes that are part of OData/AtomPub metadata endpoints — admins
# often paste these full metadata URLs (e.g. ".../SVC/AtomSVC.svc/") instead of the
# JSON SearchObject base. The normaliser below strips them so the JSON service URL
# is what gets used for /DocumentSearch/SearchObject calls.
_SVC_URL_TRAILING_NOISE_SUFFIXES = (
    # OData / AtomPub METADATA endpoints — admins often paste these directly.
    # Note: bare "/AtomSVC.svc" and "/odata.svc" are NOT noise — they are part of
    # the canonical SVC URL per the SmartProject docs (e.g. .../SVC/AtomSVC.svc).
    # Only the metadata sub-path itself is stripped, so ".../AtomSVC.svc" remains.
    '/$metadata',
    '/odata',
    # Operation-contract suffixes — admins may paste the full SearchObject
    # operation URL (e.g. ".../AtomSVC.svc/https/DocumentSearch/SearchObject").
    # Order matters: the longer / more-specific patterns are checked first so
    # only the operation tail is removed, leaving ".../AtomSVC.svc" intact.
    '/https/DocumentSearch/SearchObject',
    '/https/DocumentSearch',
    '/https/SearchObject',
    '/DocumentSearch/SearchObject',
    '/DocumentSearch',
    '/SearchObject',
)
# Path tokens that, when found at the end, mean the URL is pointing at a
# metadata sub-path. Currently no bare tokens are stripped — AtomSVC.svc /
# odata.svc are legitimate and must be preserved.
_SVC_URL_TRAILING_NOISE_TOKENS = ()


def _normalise_svc_url(value: str) -> str:
    """
    Clean a user/admin-pasted SVC URL.
    - Trim whitespace and trailing slashes.
    - Collapse accidental double slashes in the path (e.g. ".com//Wrench…").
    - Strip OData/AtomPub metadata suffixes so the JSON SVC base remains.
    - Pure-string transform — never touches the search core logic.
    """
    if not value:
        return value
    cleaned = value.strip().rstrip('/')
    # Collapse accidental "//" inside the path (after the scheme "://")
    if '://' in cleaned:
        scheme_part, rest = cleaned.split('://', 1)
        # rest may contain netloc + path; only collapse leading-path "//"
        while '//' in rest:
            rest = rest.replace('//', '/')
        cleaned = f'{scheme_part}://{rest}'
    # Strip well-known metadata suffixes (case-insensitive)
    lower = cleaned.lower()
    for suffix in _SVC_URL_TRAILING_NOISE_SUFFIXES:
        if lower.endswith(suffix.lower()):
            cleaned = cleaned[: -len(suffix)]
            lower = cleaned.lower()
    # Strip trailing path segments that match noise tokens
    parts = cleaned.split('/')
    while parts and parts[-1].lower() in (t.lower() for t in _SVC_URL_TRAILING_NOISE_TOKENS):
        parts.pop()
    cleaned = '/'.join(parts).rstrip('/')
    return cleaned


_SVC_URL_PATH_TEMPLATES = [
    # Same host, no path → most common when SVC sits at the root
    lambda scheme, netloc, path: f"{scheme}://{netloc}",
    # Same host, original path (single-server installs share the path)
    lambda scheme, netloc, path: f"{scheme}://{netloc}{path}" if path else None,
    # Same host, original path with /SVC appended (Rejlers WrenchService_*/SVC pattern)
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc}{path}/SVC"
        if path and not path.lower().endswith('/svc') else None
    ),
    # Path-replace WebAPI → Service + /SVC
    # (e.g. /WrenchWebAPI_Rejlers_Live → /WrenchService_Rejlers_Live/SVC)
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc}"
        f"{path.replace('WebAPI', 'Service').replace('webapi', 'service')}/SVC"
        if path and 'webapi' in path.lower() else None
    ),
    # Path-replace WebAPI → Service (no /SVC suffix — older deployments)
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc}{path.replace('WebAPI', 'Service').replace('webapi', 'service')}"
        if path and 'webapi' in path.lower() else None
    ),
    # Path-replace WebAPI → SVC (e.g. WrenchWebAPI_Rejlers_Live → WrenchSVC_Rejlers_Live)
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc}{path.replace('WebAPI', 'SVC').replace('webapi', 'svc')}"
        if path and 'webapi' in path.lower() else None
    ),
    # Path-replace WebAPI → SearchSVC
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc}{path.replace('WebAPI', 'SearchSVC').replace('webapi', 'searchsvc')}"
        if path and 'webapi' in path.lower() else None
    ),
    # Common path mounts on the same host
    lambda scheme, netloc, path: f"{scheme}://{netloc}/WrenchSVC",
    lambda scheme, netloc, path: f"{scheme}://{netloc}/WrenchSearchSVC",
    lambda scheme, netloc, path: f"{scheme}://{netloc}/SearchSVC",
    lambda scheme, netloc, path: f"{scheme}://{netloc}/SVC",
    # Subdomain variants — only when host has a parent domain (e.g. acme.example.com)
    lambda scheme, netloc, path: (
        f"{scheme}://svc.{netloc}" if netloc.count('.') >= 2 else None
    ),
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc.split('.', 1)[0]}-svc.{netloc.split('.', 1)[1]}"
        if netloc.count('.') >= 2 else None
    ),
    lambda scheme, netloc, path: (
        f"{scheme}://{netloc.split('.', 1)[0]}svc.{netloc.split('.', 1)[1]}"
        if netloc.count('.') >= 2 else None
    ),
]


def build_svc_url_candidates(cfg: 'WrenchConfig') -> list:
    """
    Return an ordered, deduplicated list of candidate SVC base URLs to try.
    Admin-configured cfg.svc_url always takes precedence and is the only entry returned
    (after running through `_normalise_svc_url` so an admin can paste the AtomSVC.svc
    metadata URL directly).
    """
    if cfg.svc_url:
        normalised = _normalise_svc_url(cfg.svc_url)
        if normalised:
            return [normalised]

    parsed = urlparse(cfg.base_url)
    scheme = parsed.scheme or 'https'
    netloc = parsed.netloc
    path   = (parsed.path or '').rstrip('/')

    seen = set()
    candidates = []
    for template in _SVC_URL_PATH_TEMPLATES:
        try:
            value = template(scheme, netloc, path)
        except Exception:  # noqa: BLE001 - defensive against bad input
            value = None
        if not value:
            continue
        value = value.rstrip('/')
        if value in seen:
            continue
        seen.add(value)
        candidates.append(value)
    return candidates


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _get_active_config() -> WrenchConfig:
    cfg = WrenchConfig.objects.filter(is_active=True).first()
    if not cfg:
        raise RuntimeError(
            'No active Wrench configuration. Please configure the integration first.'
        )
    return cfg


def _api_url(cfg: WrenchConfig, path: str) -> str:
    """Build absolute URL: base_url + path (handles trailing slash)."""
    return f"{cfg.base_url.rstrip('/')}/{path.lstrip('/')}"


def _is_token_fresh(cfg: WrenchConfig) -> bool:
    """Return True if we have a valid, recent session token."""
    if not cfg.session_token or not cfg.token_obtained_at:
        return False
    age = dj_timezone.now() - cfg.token_obtained_at
    return age < timedelta(minutes=_TOKEN_MAX_AGE_MINUTES)


def _save_token(cfg: WrenchConfig, token: str) -> None:
    """Persist the rolling session token to the database."""
    cfg.session_token = token
    cfg.token_obtained_at = dj_timezone.now()
    cfg.save(update_fields=['session_token', 'token_obtained_at'])


def _login(cfg: WrenchConfig) -> str:
    """
    Authenticate with Wrench SmartProject.
    POST /api/AccessControl/Login
    Returns the session TOKEN string.
    """
    password = decrypt_value(cfg.encrypted_password)
    payload = {
        'SERVER_ID': cfg.server_id,
        'LOGIN_NAME': cfg.login_name,
        'PASSWORD': password,
        'IS_PASSWORD_ENCRYPTED': cfg.is_password_encrypted,
        'OTP': cfg.otp or '',
    }
    # Add optional session parameters only when configured
    if cfg.language:
        payload['LANGUAGE'] = cfg.language
    if cfg.time_zone_id:
        payload['TIME_ZONE_ID'] = cfg.time_zone_id
    if cfg.workstation_name:
        payload['WORKSTATION_NAME'] = cfg.workstation_name
        payload['WORKSTATION_ID'] = cfg.workstation_name  # API accepts both forms
    url = _api_url(cfg, '/api/AccessControl/Login')
    logger.info('[Wrench] Authenticating: POST %s (user=%s)', url, cfg.login_name)

    resp = requests.post(url, json=payload, timeout=_TIMEOUT_FAST)
    resp.raise_for_status()
    data = resp.json()

    # Extract TOKEN from DataList.LOGIN[0] structure
    login_list = data.get('DataList', {}).get('LOGIN', [[]])[0]
    token = None
    for field in login_list:
        if field.get('FieldName') == 'TOKEN':
            token = field.get('Value')
            break

    # Fallback: some versions return token at top level
    if not token:
        token = data.get('Token') or data.get('token')

    if not token:
        raise RuntimeError('Wrench login succeeded but no TOKEN found in response.')

    _save_token(cfg, token)
    logger.info('[Wrench] Login successful, TOKEN obtained (length=%d)', len(token))
    return token


def _ensure_token(cfg: WrenchConfig) -> str:
    """
    Return a valid session token (decrypted, ready to send).
    Priority:
      1. pre_shared_token – used as-is, no expiry check (Wrench rolling refresh keeps it current).
      2. session_token    – used when still fresh (< _TOKEN_MAX_AGE_MINUTES).
      3. Fresh login      – called when no valid token is available.
    """
    if cfg.has_pre_shared_token():
        return cfg.get_pre_shared_token()
    if _is_token_fresh(cfg):
        return cfg.session_token
    return _login(cfg)


def _refresh_token_from_response(cfg: WrenchConfig, data: dict) -> None:
    """
    Wrench returns a refreshed token in every response (rolling token).
    - When pre_shared_token mode is active: update that field (encrypted) so future calls stay authenticated.
    - Otherwise: update the standard session_token.
    """
    new_token = data.get('Token') or data.get('token')
    if not new_token:
        return
    if cfg.has_pre_shared_token():
        # Compare against decrypted current value to avoid useless writes.
        current = cfg.get_pre_shared_token()
        if new_token != current:
            cfg.set_pre_shared_token(new_token)
            cfg.save(update_fields=['pre_shared_token'])
    elif new_token != cfg.session_token:
        _save_token(cfg, new_token)


# ─── Public API ───────────────────────────────────────────────────────────────

def verify_connection(cfg: WrenchConfig) -> dict:
    """
    Test connection by performing a real login.
    Returns {'success': bool, 'message': str}.
    """
    try:
        _login(cfg)
        cfg.connection_verified = True
        cfg.last_verified_at = dj_timezone.now()
        cfg.save(update_fields=['connection_verified', 'last_verified_at'])
        return {
            'success': True,
            'message': 'Login successful. Wrench connection verified.',
        }
    except requests.exceptions.SSLError as exc:
        return {'success': False, 'message': f'SSL error: {exc}'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'message': 'Unable to reach the Wrench server. Check the base URL.'}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': f'Login timed out after {_TIMEOUT_FAST}s.'}
    except requests.exceptions.HTTPError as exc:
        return {'success': False, 'message': f'Wrench returned HTTP {exc.response.status_code}.'}
    except RuntimeError as exc:
        cfg.connection_verified = False
        cfg.save(update_fields=['connection_verified'])
        return {'success': False, 'message': str(exc)}
    except Exception as exc:
        logger.error('[Wrench] verify_connection error: %s', exc, exc_info=True)
        cfg.connection_verified = False
        cfg.save(update_fields=['connection_verified'])
        return {'success': False, 'message': 'Unexpected error during connection test.'}


# ─── SearchObject route + payload constants (soft-coded per SmartProject docs) ───
# Per the official SmartProject API spec, the SearchObject operation lives at:
#   <<SVC URL>>/https/DocumentSearch/SearchObject
# The leading "/https" is the WCF binding name (AtomSVC.svc routes by binding).
# Older / non-AtomSVC deployments expose the operation directly at
#   <<SVC URL>>/DocumentSearch/SearchObject
# We try the AtomSVC form first, then the bare form. Order matters — the first
# non-404 response wins.
_SEARCH_OBJECT_ROUTE_PREFIXES = ('/https', '')
_SEARCH_OBJECT_OPERATION = '/DocumentSearch/SearchObject'
# Result mode 1 returns full document property rows (matches the docs sample).
# Mode 0 returns a schema-only / minimal result on most installations.
_SEARCH_RESULT_MODE_DEFAULT = 1
# Date filter field. CREATED_ON is universal (set on every document); APPROVED_ON
# is only set for approved docs and is NULL for many results.
_SEARCH_DATE_FIELD = 'CREATED_ON'


def search_documents(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
    discipline: str = None,
    doc_type: str = None,
    date_from: str = None,   # format: 'YYYY/MM/DD HH:MM'
    date_to: str = None,
    doc_no: str = None,
    order_no: str = None,    # filter by Transmittal ORDER_NO (used as fallback by get_transmittal_documents)
) -> dict:
    """
    Search Wrench documents using the SearchObject API.
    POST <<SVC URL>>/https/DocumentSearch/SearchObject  (per SmartProject docs)

    Returns the parsed response dict with:
      - 'total': int
      - 'documents': list of flat dicts (DOC_NO, DOC_DESCRIPTION, etc.)
      - 'token': refreshed token
    """
    token = _ensure_token(cfg)

    search_criteria = []
    criterion_id = 1

    # Date range filter (Operator 4=GT, 5=LT). Field is soft-coded — see
    # _SEARCH_DATE_FIELD. The docs sample uses CREATED_ON.
    if date_from:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': _SEARCH_DATE_FIELD,
            'FieldValue': date_from,
            'Operator': 4,
            'RangeId': 0,
        })
        criterion_id += 1
    if date_to:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': _SEARCH_DATE_FIELD,
            'FieldValue': date_to,
            'Operator': 5,
            'RangeId': 0,
        })
        criterion_id += 1
    if doc_no:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'DOC_NO',
            'FieldValue': doc_no,
            'Operator': 0,
            'RangeId': 0,
        })
        criterion_id += 1
    if discipline:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'DISCIPLINE',
            'FieldValue': discipline,
            'Operator': 0,
            'RangeId': 0,
        })
        criterion_id += 1
    if order_no:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'ORDER_NO',
            'FieldValue': order_no,
            'Operator': 0,
            'RangeId': 0,
        })
        criterion_id += 1

    # Fields we want returned. Per the SmartProject docs sample request,
    # ObjectSearchFilterDetails must be a SINGLE entry whose FieldName is a
    # comma-separated list of column names — not one entry per field.
    RETURN_FIELDS = [
        'FILE_ID', 'ORIGINAL_F_NAME',
        'DOC_NO', 'DOC_DESCRIPTION', 'ORDER_NO', 'ORDER_DESCRIPTION',
        'GENEALOGY_STRING', 'CREATED_BY_USER', 'WF_TEAM_NAME', 'IDOC_ID',
        'DOC_TYPE', 'IS_DEPENDENT', _SEARCH_DATE_FIELD,
    ]
    filter_fields = [{
        'ProcessID': 1,
        'FieldName': ','.join(RETURN_FIELDS),
    }]

    payload = {
        'SearchObjectType': 0,
        'SearchType': 0,
        'SearchResultMode': _SEARCH_RESULT_MODE_DEFAULT,
        'ObjectSearchDetails': [{
            'ProcessID': 1,
            'RowCount': page_size,
            'PageNumber': page,
            'SearchType': 0,
            'SearchPurpose': 0,
            'SchemaOnly': 0,
        }],
        'ObjectSearchCriteriaDetails': search_criteria,
        'ObjectSearchFilterDetails': filter_fields,
        'Token': token,
        'LoginName': cfg.login_name,
        'LoggedinUserId': 0,
        'ServerId': cfg.server_id,
    }

    # ── Build candidate base URLs to try in order ─────────────────────────────
    # Soft-coded auto-discovery: the SVC host typically uses well-known patterns
    # derived from the WebAPI base_url. Admin-set svc_url always wins.
    url_candidates = build_svc_url_candidates(cfg)

    last_404_url = None
    # Try each (base, route_prefix) combination. SmartProject AtomSVC installs
    # require '/https' before the operation; legacy direct mounts use ''.
    search_targets = [
        (base, prefix)
        for base in url_candidates
        for prefix in _SEARCH_OBJECT_ROUTE_PREFIXES
    ]
    for search_base, route_prefix in search_targets:
        url = f"{search_base}{route_prefix}{_SEARCH_OBJECT_OPERATION}"
        logger.info(
            '[Wrench] Searching documents: POST %s (page=%d, size=%d)', url, page, page_size
        )
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
        except requests.exceptions.ConnectionError as conn_exc:
            logger.debug('[Wrench] Connection error on %s: %s', url, conn_exc)
            continue

        if resp.status_code == 404:
            logger.debug('[Wrench] %s → 404, trying next candidate', url)
            last_404_url = url
            continue

        resp.raise_for_status()

        data = resp.json()
        # Refresh rolling token
        _refresh_token_from_response(cfg, data)

        # Flatten ObjectSearchResults (list of lists of {PropertyName, PropertyValue})
        raw_results = data.get('ObjectSearchResults', [])
        documents = []
        for row in raw_results:
            doc = {}
            for prop in row:
                name = prop.get('PropertyName') or prop.get('FieldName', '')
                value = prop.get('PropertyValue') or prop.get('Value', '')
                doc[name] = value
            if doc:
                documents.append(doc)

        return {
            'total': data.get('TotalSearchResultCount', len(documents)),
            'documents': documents,
            'operation_status': data.get('OperationStatus', -1),
            'error_msg': data.get('ErrorMsg'),
        }

    # ── OData / AtomSVC fallback ─────────────────────────────────────────────
    # Some SmartProject installations (e.g. rejlers.wrenchsp.com) expose
    # documents only through the WCF/OData layer at <svc>/AtomSVC.svc/, and not
    # via the JSON /DocumentSearch/SearchObject route. When the JSON route is
    # exhausted, attempt an OData query against the configured base.
    # This is purely additive — JSON search core logic above is untouched.
    if cfg.svc_url:
        try:
            odata_result = _search_documents_via_odata(
                cfg,
                page=page,
                page_size=page_size,
                doc_no=doc_no,
                discipline=discipline,
                doc_type=doc_type,
                date_from=date_from,
                date_to=date_to,
                order_no=order_no,
            )
            if odata_result is not None:
                return odata_result
        except _ODataNotApplicable:
            # Configured svc_url isn't an OData service — fall through to error.
            pass
        except Exception as exc:  # noqa: BLE001 - surface OData errors clearly
            logger.warning('[Wrench] OData fallback failed: %s', exc)
            raise RuntimeError(
                f'Document search failed via both JSON SearchObject and OData fallback. '
                f'OData error: {exc}'
            ) from exc

    # All candidates exhausted — raise a helpful error
    if cfg.svc_url:
        raise RuntimeError(
            f'DocumentSearch endpoint not found at the configured SVC URL '
            f'({cfg.svc_url.rstrip("/")}{_SEARCH_OBJECT_OPERATION}). '
            'Verify the URL in Configuration → "Document Search Service URL" is correct.'
        )
    # No svc_url – guide the user: auto-discovery failed, manual entry needed
    tried = ', '.join(
        f'{c}{prefix}{_SEARCH_OBJECT_OPERATION}'
        for c in url_candidates
        for prefix in _SEARCH_OBJECT_ROUTE_PREFIXES
    )
    raise RuntimeError(
        f'Could not find the DocumentSearch endpoint. Tried: {tried}. '
        'The DocumentSearch service may run on a dedicated host separate from the WebAPI. '
        'Ask your Wrench admin for the "SVC URL" and add it in '
        'Configuration → "Document Search Service URL".'
    )


# ─── Soft-coded OData / AtomSVC fallback (additive) ─────────────────────────
# Activated only when JSON DocumentSearch is unavailable AND cfg.svc_url is set.
# Many SmartProject installations expose documents at <svc>/AtomSVC.svc/<EntitySet>.
# All constants below are soft-coded so admins can extend without touching logic.
_ATOMSVC_SUFFIX = '/AtomSVC.svc'
# Common OData entity-set names that hold the document index. Tried in order.
_ODATA_DOCUMENT_ENTITY_SETS = [
    'Documents',
    'DocumentMaster',
    'DocumentRevisions',
    'WrenchDocuments',
    'IDocs',
    'DocumentList',
]
# Filterable OData fields → SmartProject column names
_ODATA_FILTER_FIELD_MAP = {
    'doc_no':     'DOC_NO',
    'discipline': 'DISCIPLINE',
    'order_no':   'ORDER_NO',
    'doc_type':   'DOC_TYPE',
    'date_from':  'APPROVED_ON',
    'date_to':    'APPROVED_ON',
}
_ODATA_TIMEOUT = 60


class _ODataNotApplicable(Exception):
    """Raised when OData fallback cannot be used for this configuration."""
    pass


def _resolve_atomsvc_base(cfg: WrenchConfig) -> str:
    """
    Build the AtomSVC.svc base URL from cfg.svc_url.
    cfg.svc_url has already been normalised (AtomSVC.svc stripped) so we re-add it.
    """
    if not cfg.svc_url:
        raise _ODataNotApplicable('No svc_url configured')
    base = cfg.svc_url.rstrip('/')
    return f'{base}{_ATOMSVC_SUFFIX}'


def _odata_quote(value: str) -> str:
    """OData v3 string-literal escape: doubled single-quotes."""
    return str(value).replace("'", "''")


def _build_odata_filter(*, doc_no=None, discipline=None, doc_type=None,
                       date_from=None, date_to=None, order_no=None) -> str:
    """Compose a SmartProject-friendly OData $filter clause from search args."""
    clauses = []
    if doc_no:
        clauses.append(
            f"substringof('{_odata_quote(doc_no)}',{_ODATA_FILTER_FIELD_MAP['doc_no']})"
        )
    if discipline:
        clauses.append(
            f"{_ODATA_FILTER_FIELD_MAP['discipline']} eq '{_odata_quote(discipline)}'"
        )
    if doc_type:
        clauses.append(
            f"{_ODATA_FILTER_FIELD_MAP['doc_type']} eq '{_odata_quote(doc_type)}'"
        )
    if order_no:
        clauses.append(
            f"{_ODATA_FILTER_FIELD_MAP['order_no']} eq '{_odata_quote(order_no)}'"
        )
    if date_from:
        clauses.append(
            f"{_ODATA_FILTER_FIELD_MAP['date_from']} ge '{_odata_quote(date_from)}'"
        )
    if date_to:
        clauses.append(
            f"{_ODATA_FILTER_FIELD_MAP['date_to']} le '{_odata_quote(date_to)}'"
        )
    return ' and '.join(clauses)


def _odata_discover_entity_sets(odata_base: str, token: str) -> list:
    """
    GET <odata_base>/  → AtomPub or OData JSON service document.
    Extract entity-set names; fall back to soft-coded list if not parseable.
    """
    headers = {'Accept': 'application/json'}
    params = {'TOKEN': token} if token else {}
    try:
        resp = requests.get(odata_base + '/', headers=headers, params=params, timeout=15)
    except requests.exceptions.RequestException as exc:
        logger.debug('[Wrench OData] discovery GET failed: %s', exc)
        return list(_ODATA_DOCUMENT_ENTITY_SETS)
    if resp.status_code == 404:
        raise _ODataNotApplicable(f'AtomSVC.svc not found at {odata_base}')
    if not resp.ok:
        return list(_ODATA_DOCUMENT_ENTITY_SETS)
    try:
        data = resp.json()
        if isinstance(data, dict):
            d = data.get('d', data)
            if isinstance(d, dict):
                sets = d.get('EntitySets')
                if isinstance(sets, list) and sets:
                    return [s for s in sets if isinstance(s, str)]
            value = data.get('value')
            if isinstance(value, list) and value:
                names = [v.get('name') for v in value if isinstance(v, dict) and v.get('name')]
                if names:
                    return names
    except ValueError:
        # AtomPub XML — fall through to soft-coded list.
        pass
    return list(_ODATA_DOCUMENT_ENTITY_SETS)


def _search_documents_via_odata(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
    doc_no: str = None,
    discipline: str = None,
    doc_type: str = None,
    date_from: str = None,
    date_to: str = None,
    order_no: str = None,
) -> dict:
    """
    Query documents via the OData/AtomPub layer at <svc>/AtomSVC.svc/.
    Returns same shape as `search_documents`, with extra 'source': 'odata'.
    Raises _ODataNotApplicable when the service is absent (404 on root).
    """
    odata_base = _resolve_atomsvc_base(cfg)
    token = _ensure_token(cfg)

    entity_sets = _odata_discover_entity_sets(odata_base, token)

    skip = max((page - 1) * page_size, 0)
    base_params = {
        '$top':         page_size,
        '$skip':        skip,
        '$format':      'json',
        '$inlinecount': 'allpages',
    }
    filter_str = _build_odata_filter(
        doc_no=doc_no, discipline=discipline, doc_type=doc_type,
        date_from=date_from, date_to=date_to, order_no=order_no,
    )
    if filter_str:
        base_params['$filter'] = filter_str

    last_status = None
    for entity in entity_sets:
        url = f'{odata_base}/{entity}'
        params = dict(base_params)
        if token:
            params['TOKEN'] = token
        logger.info('[Wrench OData] GET %s (page=%d, size=%d)', url, page, page_size)
        try:
            resp = requests.get(
                url,
                params=params,
                headers={'Accept': 'application/json'},
                timeout=_ODATA_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            logger.debug('[Wrench OData] %s failed: %s', url, exc)
            continue
        last_status = resp.status_code
        if resp.status_code == 404:
            continue
        if not resp.ok:
            raise RuntimeError(
                f'OData query to {url} returned HTTP {resp.status_code}: {resp.text[:200]}'
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(f'OData response from {url} is not JSON: {exc}')

        if isinstance(data, dict):
            _refresh_token_from_response(cfg, data)

        documents = []
        total = None
        if isinstance(data, dict):
            d = data.get('d', data)
            if isinstance(d, dict):
                results = d.get('results')
                if isinstance(results, list):
                    documents = results
                count = d.get('__count')
                if count is not None:
                    try:
                        total = int(count)
                    except (TypeError, ValueError):
                        total = None
            if not documents and isinstance(data.get('value'), list):
                documents = data['value']
            if total is None and data.get('@odata.count') is not None:
                try:
                    total = int(data['@odata.count'])
                except (TypeError, ValueError):
                    pass

        cleaned_docs = []
        for row in documents:
            if isinstance(row, dict):
                cleaned_docs.append({k: v for k, v in row.items() if not str(k).startswith('__')})
        if total is None:
            total = len(cleaned_docs)

        return {
            'total':            total,
            'documents':        cleaned_docs,
            'operation_status': 0,
            'error_msg':        None,
            'source':           'odata',
        }

    if last_status is None:
        raise _ODataNotApplicable(f'AtomSVC.svc unreachable at {odata_base}')
    raise RuntimeError(
        f'OData fallback could not locate a document entity set under {odata_base}/. '
        f'Tried: {", ".join(entity_sets)}. Ask your Wrench admin which OData '
        f'collection holds the document index.'
    )


def probe_svc_url_candidates(cfg: WrenchConfig) -> dict:
    """
    Auto-discovery probe — used by the "Auto-Detect" button on the config form.
    For each candidate base URL, check whether `/DocumentSearch/SearchObject`
    responds with anything other than 404 / DNS failure. The first reachable
    one is returned as the recommended svc_url.

    Note: this only validates *reachability* — it does NOT log into Wrench or
    submit a real search payload. Core search logic is untouched.

    Returns:
      {
        'recommended': str | None,    # First reachable candidate (or None)
        'candidates': [
          { 'url': str, 'reachable': bool, 'status_code': int|None, 'note': str }
        ]
      }
    """
    _PROBE_TIMEOUT = 6  # seconds — keep UI responsive
    _SEARCH_OBJECT_SUFFIX = '/DocumentSearch/SearchObject'

    candidates = build_svc_url_candidates(cfg)
    results = []
    recommended = None

    for base in candidates:
        url = f"{base}{_SEARCH_OBJECT_SUFFIX}"
        entry = {'url': base, 'probe_url': url, 'reachable': False, 'status_code': None, 'note': ''}
        try:
            # POST with an empty body — a real DocumentSearch endpoint will reply
            # with 400/401/500 (auth/payload error) but NOT 404. DNS / connection
            # failures bubble up as ConnectionError.
            resp = requests.post(url, json={}, timeout=_PROBE_TIMEOUT)
            entry['status_code'] = resp.status_code
            if resp.status_code == 404:
                entry['note'] = 'Endpoint not found'
            else:
                entry['reachable'] = True
                entry['note'] = f'Reachable (HTTP {resp.status_code})'
                if recommended is None:
                    recommended = base
        except requests.exceptions.ConnectionError:
            entry['note'] = 'DNS / connection failed'
        except requests.exceptions.Timeout:
            entry['note'] = f'Timed out after {_PROBE_TIMEOUT}s'
        except requests.exceptions.RequestException as exc:
            entry['note'] = f'Request error: {type(exc).__name__}'
        results.append(entry)

    return {'recommended': recommended, 'candidates': results}


# ─── Soft-coded constants for document file download ─────────────────────────
# Wrench REST endpoints tried in order; first non-404, non-error success wins.
# Covers both SmartProject SOAP-style and newer REST installations.
_DOC_DOWNLOAD_PATHS = [
    '/api/Document/GetDocumentLatestRevisionFile',
    '/api/Document/GetDocumentFile',
    '/api/DocumentRevision/GetDocumentFile',
    '/api/Document/DownloadDocument',
    '/api/Document/GetFile',
    '/api/Idoc/GetFile',
]
# Response JSON fields that may contain a redirect URL to the actual binary
_DOC_FILE_URL_FIELDS = ['FILE_PATH', 'FILE_URL', 'DOWNLOAD_URL', 'URL', 'FilePath', 'DownloadUrl']
# Response JSON fields that may contain inline base-64 file content
_DOC_FILE_CONTENT_FIELDS = ['FILE_CONTENT', 'FileContents', 'Content', 'FileData', 'Base64Content']


def download_document(
    cfg: WrenchConfig,
    *,
    idoc_id: str,
    doc_no: str = None,
) -> dict:
    """
    Retrieve a downloadable file reference for a Wrench document.
    Tries _DOC_DOWNLOAD_PATHS in order; first success returns:
      { 'url': str (optional), 'content': bytes (optional), 'filename': str,
        'content_type': str, 'source': str }
    Raises RuntimeError if all strategies fail.
    """
    import base64 as _b64

    token = _ensure_token(cfg)
    base_payload = {
        'TOKEN':      token,
        'SERVER_ID':  cfg.server_id,
        'LOGIN_NAME': cfg.login_name,
        'IDOC_ID':    idoc_id,
    }
    if doc_no:
        base_payload['DOC_NO'] = doc_no

    for path in _DOC_DOWNLOAD_PATHS:
        url = _api_url(cfg, path)
        logger.info('[Wrench] download_document: trying %s (idoc_id=%s)', url, idoc_id)
        try:
            resp = requests.post(url, json=base_payload, timeout=_TIMEOUT_SEARCH)
            if resp.status_code == 404:
                logger.debug('[Wrench] %s → 404, trying next', url)
                continue
            resp.raise_for_status()

            content_type = resp.headers.get('Content-Type', '')
            fallback_name = f'{doc_no or idoc_id}.pdf'

            if 'application/json' in content_type:
                data = resp.json()
                _refresh_token_from_response(cfg, data)

                # Look for a redirect URL
                file_url = None
                for field in _DOC_FILE_URL_FIELDS:
                    candidate = data.get(field)
                    if not candidate and isinstance(data.get('DataList'), dict):
                        candidate = data['DataList'].get(field)
                    if candidate:
                        file_url = candidate
                        break

                # Look for inline base-64 content
                file_content = None
                for field in _DOC_FILE_CONTENT_FIELDS:
                    raw = data.get(field)
                    if raw:
                        try:
                            file_content = _b64.b64decode(raw)
                        except Exception:
                            file_content = raw.encode() if isinstance(raw, str) else bytes(raw)
                        break

                if file_url:
                    return {
                        'url': file_url, 'filename': fallback_name,
                        'content_type': 'application/octet-stream', 'source': path,
                    }
                if file_content:
                    return {
                        'content': file_content, 'filename': fallback_name,
                        'content_type': 'application/pdf', 'source': path,
                    }
                logger.debug('[Wrench] %s → JSON but no file URL/content found', url)
                continue

            else:
                # Binary stream — return directly
                filename = fallback_name
                cd = resp.headers.get('Content-Disposition', '')
                if 'filename=' in cd:
                    filename = cd.split('filename=')[-1].strip('"\'') or filename
                return {
                    'content': resp.content,
                    'filename': filename,
                    'content_type': content_type or 'application/octet-stream',
                    'source': path,
                }

        except requests.exceptions.HTTPError as exc:
            logger.debug('[Wrench] HTTP error on %s: %s', url, exc)
            continue
        except Exception as exc:
            logger.debug('[Wrench] Unexpected error on %s: %s', url, exc)
            continue

    raise RuntimeError(
        f'Could not retrieve document file for IDOC_ID={idoc_id}. '
        f'Tried: {_DOC_DOWNLOAD_PATHS}. '
        'The Wrench instance may not expose a document download endpoint, or the IDOC_ID may be invalid.'
    )


# ─── Soft-coded constants for the REST document-list endpoint ─────────────────
# Wrench SmartProject REST document fields returned by GetDocumentList.
# Mirrors the TRANSMITTAL_LIST pattern — adjust if your Wrench version uses different keys.
_DOC_LIST_DATA_KEY   = 'DOCUMENT_LIST'    # DataList key containing the document rows
_DOC_LIST_URL_PATH   = '/api/Document/GetDocumentList'   # REST path on main WebAPI host
_DOC_LIST_ALT_PATHS  = [                  # fallback paths tried in order if primary 404s
    '/api/Documents/GetDocumentList',
    '/api/Document/GetDocList',
    '/api/Docs/GetDocumentList',
]

# ─── Soft-coded constants for per-transmittal document fetch ──────────────────
# Transmittal-specific REST endpoints tried before falling back to DocumentSearch.
# These share the same WebAPI host as GetTransmittalList (no separate SVC URL needed).
_TRANS_DOC_REST_PATHS = [
    '/api/Transmittal/GetTransmittalDocumentList',
    '/api/Transmittal/GetTransmittalDocuments',
    '/api/Transmittal/GetDocumentListByTransmittal',
    '/api/Transmittal/GetTransmittalDetail',
]
# DataList key names tried (in order) when parsing the transmittal-doc response
_TRANS_DOC_DATA_KEYS = [
    'TRANSMITTAL_DOCUMENT_LIST',
    'DOCUMENT_LIST',
    'TRANS_DOCUMENT_LIST',
    'DOCUMENT',
]


def _flatten_doc_rows(raw_list: list) -> list:
    """
    Normalise a raw list of document rows from any Wrench REST response into
    a flat list of dicts — handles both list-of-{FieldName,Value} and flat-dict formats.
    """
    documents = []
    for row in raw_list:
        item = {}
        if isinstance(row, list):
            for field in row:
                name = field.get('FieldName', '')
                value = field.get('Value')
                if name:
                    item[name] = value
        elif isinstance(row, dict):
            item = row
        if item:
            documents.append(item)
    return documents


# ─── Soft-coded transmittal-as-document fallback ─────────────────────────────
# Some Wrench tenants (including Rejlers Live) do not expose any per-transmittal
# document endpoint, and DocumentSearch returns zero rows. In that case the
# transmittal rows themselves carry enough metadata to *be* documents:
#   • TRANS_REF_NO       → unique document number
#   • TRANS_DESC         → document title (falls back to TRANS_TYPE_CODE)
#   • REPORT_FILE_ID     → file identifier for download
#   • STATUS_DESC        → revision/status
#   • CREATED_ON         → date
# This helper synthesizes a documents[] payload from the transmittal list,
# keyed by ORDER_NO. It is invoked by the view layer ONLY when the existing
# core strategies return zero documents — core logic in get_transmittal_documents
# stays untouched.
_TRANSMITTAL_DOC_FIELD_MAP = {
    'DOC_NO':          ('TRANS_REF_NO',  'TRANS_ID'),
    'DOC_DESCRIPTION': ('TRANS_DESC',    'TRANS_TYPE_CODE', 'ORDER_DESCRIPTION'),
    'REVISION':        ('STATUS_DESC',   'REV_SERIES_ID'),
    'DOC_DATE':        ('CREATED_ON',    'RELEASED_ON'),
    'FILE_ID':         ('REPORT_FILE_ID',),
    'DISCIPLINE':      ('TRANS_TYPE_CODE',),
    'ORDER_NO':        ('ORDER_NO',),
    'TRANS_ID':        ('TRANS_ID',),
}
# Document number prefixes that strongly suggest a P&ID-related drawing.
# Used downstream by the recommender; harmless if no match.
_TRANSMITTAL_FALLBACK_MAX_ROWS = 200_000   # large slice covers the full tenant in one call
                                            # (upstream API returns everything in a single page anyway)


def _row_first_nonempty(row: dict, keys: tuple) -> str:
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ''


def synthesize_documents_from_transmittal_list(
    cfg: WrenchConfig,
    *,
    order_no: str,
    page: int = 1,
    page_size: int = 200,
) -> dict:
    """
    Synthesize a documents[] payload by filtering the transmittal list to a
    given ORDER_NO. Used as a last-resort fallback when neither per-transmittal
    REST endpoints nor DocumentSearch return any rows.

    Returns the same shape as `get_transmittal_documents`:
      { 'total', 'documents', 'source': 'transmittal_list', 'page', 'page_size' }
    """
    tx = get_transmittals(cfg, page=1, page_size=_TRANSMITTAL_FALLBACK_MAX_ROWS)
    all_rows = tx.get('transmittals') or []
    matching = [r for r in all_rows
                if (str(r.get('ORDER_NO') or '').strip() == str(order_no).strip())]

    documents = []
    for row in matching:
        synth = {}
        for out_key, src_keys in _TRANSMITTAL_DOC_FIELD_MAP.items():
            synth[out_key] = _row_first_nonempty(row, src_keys)
        # Keep the original raw row under a discoverable key for the recommender.
        synth['_raw_transmittal'] = row
        documents.append(synth)

    # Apply pagination on the synthesized list.
    total = len(documents)
    start = max(0, (page - 1) * page_size)
    end   = start + page_size
    page_slice = documents[start:end]

    logger.info(
        '[Wrench] transmittal-as-document fallback: %d transmittals for ORDER_NO=%s → %d synthesized docs',
        len(matching), order_no, total,
    )

    return {
        'total':            total,
        'documents':        page_slice,
        'source':           'transmittal_list',
        'page':             page,
        'page_size':        page_size,
        'svc_url_required': False,
    }


def get_transmittal_documents(
    cfg: WrenchConfig,
    *,
    order_no: str,
    trans_id: str = None,
    page: int = 1,
    page_size: int = 200,
) -> dict:
    """
    Fetch documents linked to a specific transmittal.

    Strategy (tried in order, first success wins):
      1. Transmittal-specific REST endpoints on the same host as GetTransmittalList
         (_TRANS_DOC_REST_PATHS) — no SVC URL needed.
      2. Generic Document REST endpoint (GetDocumentList + _DOC_LIST_ALT_PATHS).
      3. DocumentSearch/SearchObject fallback with ORDER_NO criterion (uses SVC URL if set,
         else the main host — same behaviour as search_documents()).

    Returns { total, documents, source }.
    """
    token = _ensure_token(cfg)

    # Payload common to all REST attempts
    base_payload = {
        'TOKEN':       token,
        'SERVER_ID':   cfg.server_id,
        'LOGIN_NAME':  cfg.login_name,
        'ROW_COUNT':   page_size,
        'PAGE_NUMBER': page,
        'ORDER_NO':    order_no,
    }
    if trans_id:
        base_payload['TRANS_ID'] = trans_id

    # ── Soft-coded fast-path via per-host capability cache ───────────────────
    # Skip REST probing entirely when:
    #   • we already learned all REST paths 404 for this host (within TTL), OR
    #   • we remember a winning path → try it FIRST (and only it from strategy 1+2)
    import time as _time
    profile        = _host_profile(cfg)
    dead_paths     = profile['dead_paths']
    winning_path   = profile['winning_path']
    exhausted_until = profile['exhausted_until']
    skip_rest      = (exhausted_until and _time.time() < exhausted_until)

    # Build the ordered REST candidate list, honouring cache hints
    strategy1_paths = list(_TRANS_DOC_REST_PATHS)
    strategy2_paths = [_DOC_LIST_URL_PATH] + list(_DOC_LIST_ALT_PATHS)
    if winning_path:
        # Try winner first; remove duplicates further down the list
        if winning_path in strategy1_paths:
            strategy1_paths = [winning_path] + [p for p in strategy1_paths if p != winning_path]
        elif winning_path in strategy2_paths:
            strategy2_paths = [winning_path] + [p for p in strategy2_paths if p != winning_path]

    def _attempt_rest(path: str, timeout: int) -> dict | None:
        """Returns a result dict on success, None on 404/error (so caller continues)."""
        if path in dead_paths:
            return None
        url = _api_url(cfg, path)
        try:
            resp = requests.post(url, json=base_payload, timeout=timeout)
            if resp.status_code == 404:
                dead_paths.add(path)
                logger.debug('[Wrench] %s → 404 (cached as dead)', url)
                return None
            resp.raise_for_status()
            data = resp.json()
            _refresh_token_from_response(cfg, data)
            raw_list = []
            for key in _TRANS_DOC_DATA_KEYS:
                raw_list = data.get('DataList', {}).get(key, [])
                if raw_list:
                    break
            if not raw_list:
                raw_list = (
                    data.get('DataList', {}).get(_DOC_LIST_DATA_KEY, [])
                    or data.get('DataList', {}).get('DOCUMENT', [])
                    or data.get('DocumentList', [])
                    or data.get('ObjectSearchResults', [])
                )
            documents = _flatten_doc_rows(raw_list)
            total = len(documents)
            start = (page - 1) * page_size
            profile['winning_path'] = path           # remember success
            profile['exhausted_until'] = 0.0
            return {
                'total':     total,
                'documents': documents[start: start + page_size],
                'source':    f'rest:{path}',
            }
        except requests.exceptions.HTTPError as exc:
            logger.debug('[Wrench] HTTP error on %s: %s', url, exc)
            return None
        except Exception as exc:
            logger.debug('[Wrench] Unexpected error on %s: %s', url, exc)
            return None

    if not skip_rest:
        # ── Strategy 1: Transmittal-specific REST paths ──────────────────────
        for path in strategy1_paths:
            result = _attempt_rest(path, _TIMEOUT_PROBE if winning_path != path else _TIMEOUT_SEARCH)
            if result is not None:
                return result

        # ── Strategy 2: Generic Document list endpoints ──────────────────────
        for path in strategy2_paths:
            result = _attempt_rest(path, _TIMEOUT_PROBE if winning_path != path else _TIMEOUT_SEARCH)
            if result is not None:
                return result

        # All REST paths failed → mark host as exhausted to skip probes for TTL
        profile['exhausted_until'] = _time.time() + _TRANS_DOC_EXHAUSTED_TTL_SECONDS
        logger.info(
            '[Wrench] All REST trans-doc paths failed for host; caching exhausted state for %ds',
            _TRANS_DOC_EXHAUSTED_TTL_SECONDS,
        )
    else:
        logger.info(
            '[Wrench] Skipping REST trans-doc probing (host previously exhausted; %.0fs remain)',
            exhausted_until - _time.time(),
        )

    # ── Strategy 3: DocumentSearch/SearchObject with ORDER_NO criterion ───────
    logger.info('[Wrench] get_transmittal_documents: trying DocumentSearch fallback (order_no=%s)', order_no)
    try:
        result = search_documents(cfg, page=page, page_size=page_size, order_no=order_no)
        result['source'] = 'document_search'
        return result
    except Exception as svc_exc:
        logger.warning('[Wrench] DocumentSearch fallback also failed: %s', svc_exc)

    raise RuntimeError(
        f'No Wrench endpoint returned document data for transmittal ORDER_NO={order_no}. '
        f'Tried transmittal-specific paths ({_TRANS_DOC_REST_PATHS}), '
        f'generic document paths ({[_DOC_LIST_URL_PATH] + list(_DOC_LIST_ALT_PATHS)}), and DocumentSearch. '
        'Check that the Wrench WebAPI exposes document listing, or configure a DocumentSearch SVC URL.'
    )


def get_document_list(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
    discipline: str = None,
    doc_no: str = None,
    order_no: str = None,   # filter by transmittal ORDER_NO to list linked documents
) -> dict:
    """
    Fetch documents via the Wrench SmartProject REST WebAPI — same host as transmittals.
    POST <<base_url>>/api/Document/GetDocumentList

    This does NOT require the separate DocumentSearch SVC URL.
    Falls back through _DOC_LIST_ALT_PATHS if the primary path returns 404.

    Returns:
      - 'total': int
      - 'documents': list[dict]  (DOC_NO, DOC_DESCRIPTION, DISCIPLINE, etc.)
      - 'source': 'rest'
    """
    token = _ensure_token(cfg)

    # Build payload — same ALL_CAPS flat format as GetTransmittalList
    payload = {
        'TOKEN':       token,
        'SERVER_ID':   cfg.server_id,
        'LOGIN_NAME':  cfg.login_name,
        'ROW_COUNT':   page_size,
        'PAGE_NUMBER': page,
    }
    if discipline:
        payload['DISCIPLINE'] = discipline
    if doc_no:
        payload['DOC_NO'] = doc_no
    if order_no:
        payload['ORDER_NO'] = order_no

    # Try primary path, then fallbacks
    paths_to_try = [_DOC_LIST_URL_PATH] + _DOC_LIST_ALT_PATHS
    last_exc = None

    for path in paths_to_try:
        url = _api_url(cfg, path)
        logger.info('[Wrench] Fetching document list: POST %s (page=%d, size=%d)', url, page, page_size)
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
            if resp.status_code == 404:
                logger.debug('[Wrench] %s → 404, trying next path', url)
                last_exc = RuntimeError(f'404 at {url}')
                continue
            resp.raise_for_status()
            data = resp.json()
            _refresh_token_from_response(cfg, data)

            # Flatten DataList.<_DOC_LIST_DATA_KEY> — same pattern as transmittals
            raw_list = data.get('DataList', {}).get(_DOC_LIST_DATA_KEY, [])

            # Some Wrench versions return a flat list of property dicts at top level
            if not raw_list:
                raw_list = data.get('DataList', {}).get('DOCUMENT', [])
            if not raw_list:
                raw_list = data.get('DocumentList', [])

            documents = []
            for row in raw_list:
                item = {}
                if isinstance(row, list):
                    # List-of-{FieldName, Value} pairs (TRANSMITTAL_LIST style)
                    for field in row:
                        name = field.get('FieldName', '')
                        value = field.get('Value')
                        if name:
                            item[name] = value
                elif isinstance(row, dict):
                    # Flat dict (some REST APIs return this directly)
                    item = row
                if item:
                    documents.append(item)

            total_available = len(documents)

            # In-service pagination (consistent with transmittal pattern)
            start = (page - 1) * page_size
            end   = start + page_size
            page_slice = documents[start:end]

            return {
                'total':    total_available,
                'documents': page_slice,
                'source':   'rest',
                'operation_status': data.get('OperationStatus', -1),
            }

        except RuntimeError:
            raise
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            logger.debug('[Wrench] HTTP error on %s: %s', url, exc)
            continue
        except Exception as exc:
            last_exc = exc
            break

    raise RuntimeError(
        f'Could not reach the Wrench document list endpoint. Tried: {paths_to_try}. '
        f'Last error: {last_exc}'
    )


def get_transmittals(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """
    Fetch transmittals via the Wrench SmartProject REST WebAPI.
    POST <<base_url>>/api/Transmittal/GetTransmittalList

    Note: this Wrench instance returns all records regardless of ROW_COUNT/PAGE_NUMBER,
    so pagination is applied in-service after receiving the full result set.

    Returns:
      - 'total': int          – total available records from Wrench
      - 'transmittals': list  – the requested page slice
      - 'page': int, 'page_size': int
      - 'operation_status': int
    """
    token = _ensure_token(cfg)
    url = _api_url(cfg, '/api/Transmittal/GetTransmittalList')
    # Wrench REST API: flat ALL_CAPS fields, no wrapper object
    payload = {
        'TOKEN': token,
        'SERVER_ID': cfg.server_id,
        'LOGIN_NAME': cfg.login_name,
        'ROW_COUNT': page_size,
        'PAGE_NUMBER': page,
    }
    logger.info('[Wrench] Fetching transmittals: POST %s (page=%d, size=%d)', url, page, page_size)
    resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
    resp.raise_for_status()
    data = resp.json()

    _refresh_token_from_response(cfg, data)

    # Flatten DataList.TRANSMITTAL_LIST — list-of-lists, each inner list is FieldName/Value pairs
    raw_list = data.get('DataList', {}).get('TRANSMITTAL_LIST', [])
    transmittals = []
    for row in raw_list:
        item = {}
        for field in row:
            name = field.get('FieldName', '')
            value = field.get('Value')
            if name:
                item[name] = value
        if item:
            transmittals.append(item)

    total_available = len(transmittals)

    # Apply in-service pagination (API ignores ROW_COUNT on this instance)
    start = (page - 1) * page_size
    end = start + page_size
    page_slice = transmittals[start:end]

    op_status = -1
    process_details = data.get('ProcessDetails', [{}])
    if process_details:
        op_status = process_details[0].get('ProcessStatus', -1)

    return {
        'total': total_available,
        'transmittals': page_slice,
        'page': page,
        'page_size': page_size,
        'operation_status': op_status,
        'error_msg': data.get('ErrorMsg'),
    }


# ─── Soft-coded: Wrench project-library / folder-tree REST endpoints ─────────
# Wrench SmartProject exposes the project's R:\Projects-style folder tree
# under one of several REST paths depending on the deployment version. We try
# each path in order and stop at the first one that returns folder rows. The
# response shape across versions also varies — we accept any "FOLDER_LIST" /
# "GENEALOGY_LIST" / "FOLDER" DataList key and any list-of-{FieldName,Value}
# OR flat-dict row format (same as TRANSMITTAL_LIST).
#
# To add a new tenant-specific path, just append it here — no code change
# elsewhere is required (soft-coded).
_FOLDER_LIST_PATHS = [
    '/api/Folder/GetFolderList',
    '/api/Folder/GetFolderTree',
    '/api/Folder/GetGenealogy',
    '/api/ProjectLibrary/GetFolderList',
    '/api/ProjectLibrary/GetFolderTree',
    '/api/Document/GetFolderList',
    '/api/Document/GetGenealogyList',
    '/api/Genealogy/GetGenealogyList',
    '/api/Library/GetFolderList',
]
_FOLDER_LIST_DATA_KEYS = [
    'FOLDER_LIST', 'FolderList',
    'GENEALOGY_LIST', 'GenealogyList',
    'FOLDER', 'Folder',
    'TREE', 'Tree',
]
# Candidate fields that hold the folder name within a row.
_FOLDER_NAME_FIELDS = (
    'FOLDER_NAME', 'FolderName', 'NAME', 'Name',
    'FOLDER', 'Folder', 'TITLE', 'Title',
    'GENEALOGY_NAME', 'GenealogyName',
)
# Candidate fields that hold the full path (slash-separated).
_FOLDER_PATH_FIELDS = (
    'GENEALOGY_STRING', 'GenealogyString',
    'FOLDER_PATH', 'FolderPath',
    'PATH', 'Path',
    'FULL_PATH', 'FullPath',
)


def get_project_folder_tree(cfg: 'WrenchConfig', *, order_no: str) -> dict:
    """
    Return the Wrench project-library folder tree for `order_no`.

    Tries each path in `_FOLDER_LIST_PATHS` against the main WebAPI host (no
    SVC URL required). The first endpoint that returns a non-empty folder
    list wins. Soft-coded so additional tenant-specific paths can be added
    without changing call-sites.

    Returns:
      { 'folders': [ { 'path': 'Engineering', 'segments': ['Engineering'] }, ... ],
        'source':  '<path>' | 'none',
        'total':   N }
    """
    token = _ensure_token(cfg)
    payload = {
        'TOKEN':       token,
        'SERVER_ID':   cfg.server_id,
        'LOGIN_NAME':  cfg.login_name,
        'ORDER_NO':    order_no,
        'ROW_COUNT':   500,
        'PAGE_NUMBER': 1,
    }

    def _row_to_path(row):
        # Accept BOTH list-of-{FieldName,Value} and flat-dict row shapes.
        flat = {}
        if isinstance(row, list):
            for f in row:
                n = f.get('FieldName') or ''
                if n:
                    flat[n] = f.get('Value')
        elif isinstance(row, dict):
            flat = row
        # Prefer an explicit full path.
        for f in _FOLDER_PATH_FIELDS:
            v = flat.get(f)
            if v and str(v).strip():
                return str(v).strip()
        # Fall back to a single folder name.
        for f in _FOLDER_NAME_FIELDS:
            v = flat.get(f)
            if v and str(v).strip():
                return str(v).strip()
        return ''

    for path in _FOLDER_LIST_PATHS:
        url = _api_url(cfg, path)
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
        except Exception as exc:
            logger.info('[Wrench] folder-tree %s → exception %s', path, exc)
            continue
        if resp.status_code == 404:
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        _refresh_token_from_response(cfg, data)

        raw = []
        for key in _FOLDER_LIST_DATA_KEYS:
            raw = data.get('DataList', {}).get(key) or data.get(key) or []
            if raw:
                break
        if not raw:
            continue

        folders = []
        seen = set()
        for row in raw:
            p = _row_to_path(row)
            if not p:
                continue
            # Normalise separators → forward slash and trim leading/trailing.
            normalised = re.sub(r'[\\>|]', '/', p).strip().strip('/')
            if not normalised or normalised.lower() in seen:
                continue
            seen.add(normalised.lower())
            segments = [s.strip() for s in normalised.split('/') if s.strip()]
            if not segments:
                continue
            # Skip a leading segment that duplicates ORDER_NO.
            if segments[0].strip().lower() == str(order_no).strip().lower():
                segments = segments[1:]
            if not segments:
                continue
            folders.append({
                'path':     '/'.join(segments),
                'segments': segments,
            })
        if folders:
            logger.info('[Wrench] folder-tree: %s returned %d folders for ORDER_NO=%s',
                        path, len(folders), order_no)
            return {'folders': folders, 'source': path, 'total': len(folders)}

    return {'folders': [], 'source': 'none', 'total': 0}


# ─── Soft-coded: max transmittals to expand when no direct document endpoint exists ──
# Increase to search more transmittals (slower); decrease for faster but narrower results.
_MAX_TRANS_FOR_DOC_EXPANSION  = 15



# Per-transmittal HTTP timeout (seconds). Short because these fire in parallel.
_TRANS_EXPAND_CALL_TIMEOUT    = 10
# Parallel worker cap — avoids overwhelming the Wrench server.
_TRANS_EXPAND_MAX_WORKERS     = 5
# Overall parallel-fetch timeout (seconds). Must leave headroom for AI ranking.
_TRANS_EXPAND_OVERALL_TIMEOUT = 30


def get_documents_from_transmittals(
    cfg: WrenchConfig,
    *,
    max_transmittals: int = _MAX_TRANS_FOR_DOC_EXPANSION,
) -> dict:
    """
    Fallback document source for installations where GetDocumentList and
    DocumentSearch/SearchObject are not exposed.

    Strategy:
      1. Call GetTransmittalList (1 request, known to work).
      2. Snapshot the rolling token to avoid database calls from worker threads.
      3. Parallel-fetch per-transmittal document lists via the Transmittal-scoped
         REST paths (_TRANS_DOC_REST_PATHS), using the pre-obtained token.
      4. Deduplicate by DOC_NO and return a flat document list.

    Thread safety: worker threads do NOT touch the database — they read cfg attributes
    directly and skip token-refresh to stay DB-free.

    Returns { total, documents, source }.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Step 1: fetch transmittals (token is refreshed here — DB call is safe in the main thread)
    tx_result     = get_transmittals(cfg, page=1, page_size=max_transmittals)
    transmittals  = tx_result.get('transmittals', [])
    if not transmittals:
        return {'total': 0, 'documents': [], 'source': 'transmittals_expanded'}

    order_nos = [t.get('ORDER_NO') for t in transmittals if t.get('ORDER_NO')]
    if not order_nos:
        return {'total': 0, 'documents': [], 'source': 'transmittals_expanded'}

    # Step 2: snapshot token for thread use (no DB calls in workers)
    current_token = cfg.session_token
    base_url      = cfg.base_url.rstrip('/')
    server_id     = cfg.server_id
    login_name    = cfg.login_name

    def _fetch_docs(order_no: str) -> list:
        """
        Fetch documents for a single transmittal using the pre-obtained token.
        Returns a (possibly empty) list of flat document dicts.
        Does NOT touch the database.
        """
        payload = {
            'TOKEN':       current_token,
            'SERVER_ID':   server_id,
            'LOGIN_NAME':  login_name,
            'ORDER_NO':    order_no,
            'ROW_COUNT':   200,
            'PAGE_NUMBER': 1,
        }
        for path in _TRANS_DOC_REST_PATHS:
            url = f"{base_url}/{path.lstrip('/')}"
            try:
                resp = requests.post(url, json=payload, timeout=_TRANS_EXPAND_CALL_TIMEOUT)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()

                raw_list = []
                for key in _TRANS_DOC_DATA_KEYS:
                    raw_list = data.get('DataList', {}).get(key, [])
                    if raw_list:
                        break
                if not raw_list:
                    raw_list = data.get('DocumentList', data.get('ObjectSearchResults', []))

                docs = _flatten_doc_rows(raw_list)
                if docs is not None:     # accept empty list as valid success
                    return docs
            except Exception:
                continue
        return []

    # Step 3: fetch in parallel — worker threads are DB-free
    seen_doc_nos = set()
    all_docs: list = []

    with ThreadPoolExecutor(max_workers=min(_TRANS_EXPAND_MAX_WORKERS, len(order_nos))) as pool:
        futures = {pool.submit(_fetch_docs, ono): ono for ono in order_nos}
        try:
            for future in as_completed(futures, timeout=_TRANS_EXPAND_OVERALL_TIMEOUT):
                try:
                    for doc in future.result():
                        doc_no = doc.get('DOC_NO', '')
                        if doc_no and doc_no not in seen_doc_nos:
                            seen_doc_nos.add(doc_no)
                            all_docs.append(doc)
                except Exception:
                    pass
        except Exception:
            pass   # TimeoutError → return whatever we have so far

    logger.info(
        '[Wrench] get_documents_from_transmittals: expanded %d transmittals → %d unique docs',
        len(order_nos), len(all_docs),
    )
    return {
        'total':     len(all_docs),
        'documents': all_docs,
        'source':    'transmittals_expanded',
    }


def run_sync(direction: str, entity_type: str, triggered_by, filters: dict = None) -> WrenchSyncLog:
    """
    Perform a data sync between RADAI and Wrench.
    For wrench_to_radai + document: calls SearchObject.
    Creates + updates a WrenchSyncLog record.
    """
    cfg = _get_active_config()
    log = WrenchSyncLog.objects.create(
        config=cfg,
        triggered_by=triggered_by,
        direction=direction,
        entity_type=entity_type,
        status='in_progress',
    )

    try:
        if direction == 'wrench_to_radai':
            if entity_type in ('document', 'doc_search'):
                result = search_documents(cfg, **(filters or {}))
                log.records_requested = result['total']
                log.records_synced = len(result['documents'])
                log.records_failed = 0
                log.sync_details = {
                    'total_in_wrench': result['total'],
                    'fetched': len(result['documents']),
                    'sample_doc_nos': [d.get('DOC_NO', '') for d in result['documents'][:5]],
                    'operation_status': result.get('operation_status'),
                }
            elif entity_type == 'transmittal':
                result = get_transmittals(cfg, **(filters or {}))
                log.records_requested = result['total']
                log.records_synced = len(result['transmittals'])
                log.records_failed = 0
                log.sync_details = {
                    'total_fetched': result['total'],
                    'sample_transmittals': result['transmittals'][:3],
                    'operation_status': result.get('operation_status'),
                }
            elif entity_type == 'all':
                # Try transmittals (REST endpoint); documents require SVC URL configuration
                result = get_transmittals(cfg)
                log.records_requested = result['total']
                log.records_synced = len(result['transmittals'])
                log.records_failed = 0
                log.sync_details = {
                    'entity_types_attempted': ['transmittal'],
                    'transmittals_fetched': result['total'],
                    'operation_status': result.get('operation_status'),
                }
            else:
                # project / user – placeholder
                _ensure_token(cfg)  # validate connection is alive
                log.records_requested = 0
                log.records_synced = 0
                log.sync_details = {'note': f'Sync for entity_type={entity_type} – implement specific endpoint.'}
        else:
            # RADAI → Wrench: future implementation
            log.records_requested = 0
            log.records_synced = 0
            log.sync_details = {'note': 'Push direction reserved for future implementation.'}

        log.status = 'success'

    except Exception as exc:
        log.status = 'failed'
        log.error_message = str(exc)
        logger.error('[Wrench] sync failed: %s', exc, exc_info=True)
    finally:
        log.completed_at = dj_timezone.now()
        log.save()

    return log


# ─── P&ID Cross-Reference Search ─────────────────────────────────────────────
# Soft-coded discipline token → Wrench DISCIPLINE code mapping.
# Covers common EPC discipline prefixes found in drawing file names.
_DISCIPLINE_TOKEN_MAP = {
    'pid':          'PROCESS',
    'p&id':         'PROCESS',
    'process':      'PROCESS',
    'pfd':          'PROCESS',
    'pfs':          'PROCESS',
    'pip':          'PIPING',
    'piping':       'PIPING',
    'iso':          'PIPING',
    'ins':          'INSTRUMENT',
    'instr':        'INSTRUMENT',
    'instrument':   'INSTRUMENT',
    'mec':          'MECHANICAL',
    'mech':         'MECHANICAL',
    'mechanical':   'MECHANICAL',
    'elec':         'ELECTRICAL',
    'ele':          'ELECTRICAL',
    'electrical':   'ELECTRICAL',
    'civ':          'CIVIL',
    'civil':        'CIVIL',
    'str':          'STRUCTURAL',
    'structural':   'STRUCTURAL',
    'hvac':         'HVAC',
    'fire':         'FIRE',
    'safety':       'SAFETY',
}

# Soft-coded: regex patterns to extract an area/system code from a drawing file name.
# Example: "3500-PL-PID-001-Rev3.pdf" → area="3500", "PID-001" → doc_no hint "001"
# Tried in order; first match wins.
_DRAWING_AREA_PATTERNS = [
    r'^(\d{3,5})',                    # leading digit block: 3500-...
    r'[-_](\d{3,5})[-_]',            # digit block between separators: ...-001-...
    r'[A-Z]{1,6}[-_](\d{3,5})',      # prefix-number: PID-001
]


def build_pid_search_query(
    drawing_name: str = '',
    tags: list = None,
    issues: list = None,
    discipline: str = None,
    free_text: str = None,
) -> dict:
    """
    Derive a smart Wrench search query from P&ID drawing context signals.

    Extracts:
    - discipline code from the drawing name tokens or explicit override
    - doc_no hint from the drawing file name (area code / document number fragment)
    - term_hints list for filter-strip display in the frontend

    Returns:
        { discipline, doc_no, area_code, term_hints }
    """
    import re as _re
    tags   = tags   or []
    issues = issues or []

    # ── Discipline inference from drawing file name ────────────────────────────
    inferred_discipline = discipline
    if not inferred_discipline:
        name_lower = drawing_name.lower()
        for token, disc in _DISCIPLINE_TOKEN_MAP.items():
            if token in name_lower:
                inferred_discipline = disc
                break

    # ── Doc-no hint extraction from drawing file name ─────────────────────────
    doc_no_hint = None
    if free_text:
        doc_no_hint = free_text
    else:
        base = drawing_name.rsplit('.', 1)[0]   # strip file extension
        for pat in _DRAWING_AREA_PATTERNS:
            m = _re.search(pat, base, _re.IGNORECASE)
            if m:
                doc_no_hint = m.group(1)
                break

    # ── Issue category summary (top 5 by frequency) ───────────────────────────
    cat_counts: dict = {}
    for iss in issues:
        c = iss.get('category', 'general')
        cat_counts[c] = cat_counts.get(c, 0) + 1
    top_categories = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]

    return {
        'discipline':     inferred_discipline,
        'doc_no':         doc_no_hint,
        'area_code':      doc_no_hint,
        'term_hints':     [t for t in (tags or [])[:5]],
        'top_categories': [c for c, _ in top_categories],
    }


def ai_rank_pid_documents(
    documents: list,
    drawing_name: str = '',
    tags: list = None,
    issues: list = None,
    discipline: str = None,
) -> tuple:
    """
    Rank Wrench documents by relevance to the P&ID drawing context.

    Stage 1 (always executed): heuristic keyword scoring
      - Discipline match:   +40 pts
      - Process/instr disc: +25 pts
      - Per matching tag:   +10 pts each (first 10 tags)
      - Drawing-name token: +5 pts each token ≥ 3 chars

    Stage 2 (requires OPENAI_API_KEY): GPT-4o-mini semantic scoring
      - Sends compact doc list (≤30 docs) in a single prompt
      - Returns score (0-100), plain-English reason, match_type per document
      - Falls back to Stage 1 scores if OpenAI call fails

    Returns:
        (ranked_documents, ai_powered_bool)
    Each document dict gains: relevance_score, relevance_reason, match_type
    """
    import re as _re
    tags   = tags   or []
    issues = issues or []

    if not documents:
        return documents, False

    # ── Stage 1: heuristic scoring ────────────────────────────────────────────
    name_tokens = set(
        t.lower() for t in _re.split(r'[-_.\s]', drawing_name.rsplit('.', 1)[0])
        if len(t) >= 3
    )
    disc_lower = (discipline or '').lower()

    def _heuristic(doc):
        text = ' '.join([
            (doc.get('DOC_NO') or ''),
            (doc.get('DOC_DESCRIPTION') or ''),
            (doc.get('GENEALOGY_STRING') or ''),
            (doc.get('WF_TEAM_NAME') or ''),
        ]).lower()
        score = 0
        doc_disc = (doc.get('DISCIPLINE') or '').lower()
        # Discipline match
        if disc_lower and disc_lower in doc_disc:
            score += 40
        elif any(k in doc_disc for k in ('process', 'instrument', 'piping')):
            score += 20
        # Tag overlap
        for tag in tags[:10]:
            if tag.lower() in text:
                score += 10
        # Drawing name token overlap
        for tok in name_tokens:
            if tok in text:
                score += 5
        return min(score, 99)

    for doc in documents:
        doc['relevance_score']  = _heuristic(doc)
        doc['relevance_reason'] = 'Matched on discipline / document-number keywords'
        doc['match_type']       = 'keyword'

    # ── Stage 2: OpenAI semantic ranking ──────────────────────────────────────
    try:
        from openai import OpenAI
        import os
        import json as _json

        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError('OPENAI_API_KEY not set — skipping AI ranking')

        client = OpenAI(api_key=api_key)

        # Cap to 30 documents to keep prompt within token limits
        docs_for_ai = documents[:30]
        doc_rows = [
            {
                'idx':  i,
                'no':   d.get('DOC_NO', ''),
                'desc': (d.get('DOC_DESCRIPTION') or '')[:100],
                'disc': d.get('DISCIPLINE', ''),
            }
            for i, d in enumerate(docs_for_ai)
        ]

        # Build issue category summary
        cat_counts: dict = {}
        for iss in issues:
            c = iss.get('category', 'general')
            cat_counts[c] = cat_counts.get(c, 0) + 1
        issue_summary = ', '.join(
            f"{k}({v})" for k, v in sorted(cat_counts.items(), key=lambda x: -x[1])[:5]
        ) or 'general'

        system_prompt = (
            "You are an EPC document classification specialist. "
            "You assess Wrench DMS document relevance for P&ID cross-reference QC. "
            "Be precise and concise in your reasons (≤12 words)."
        )
        user_prompt = (
            f"P&ID drawing being verified:\n"
            f"  File name:   {drawing_name}\n"
            f"  Discipline:  {discipline or 'PROCESS'}\n"
            f"  Key tags:    {', '.join(tags[:12])}\n"
            f"  Finding categories: {issue_summary}\n\n"
            f"Wrench DMS documents (JSON):\n{_json.dumps(doc_rows)}\n\n"
            "For EVERY document return a JSON array of objects:\n"
            '  {"idx":N,"score":0-100,"reason":"≤12 words","type":"pid|datasheet|spec|sld|iso|procedure|vendor|other"}\n'
            "score 0=irrelevant, 100=directly referenced by this P&ID. "
            "Return ONLY the JSON array, no markdown."
        )

        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
            temperature=0,
            max_tokens=900,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip optional markdown fences
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        rankings = _json.loads(raw)

        for rank in rankings:
            idx = int(rank.get('idx', -1))
            if 0 <= idx < len(docs_for_ai):
                docs_for_ai[idx]['relevance_score']  = max(0, min(100, int(rank.get('score', 0))))
                docs_for_ai[idx]['relevance_reason']  = rank.get('reason', '')
                docs_for_ai[idx]['match_type']        = rank.get('type', 'other')

        # Merge AI-scored docs back + sort descending by score
        remaining = documents[30:]
        for doc in remaining:
            doc['match_type'] = 'keyword'   # not AI-ranked
        combined = sorted(docs_for_ai, key=lambda d: -d.get('relevance_score', 0)) + remaining
        return combined, True

    except Exception as exc:
        logger.warning('[Wrench/PID] AI ranking skipped: %s', exc)
        documents.sort(key=lambda d: -d.get('relevance_score', 0))
        return documents, False
