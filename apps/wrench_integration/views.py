"""
Wrench Integration – API Views
All endpoints require IsAdmin permission (Admin or Super Admin only).
"""
import logging
import requests as http_lib
import time as _time
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

from apps.rbac.permissions import IsAdmin, IsSuperAdmin
from apps.rbac.utils import create_audit_log

from .models import WrenchConfig, WrenchSyncLog, WrenchS3SyncJob
from .serializers import (
    WrenchConfigReadSerializer,
    WrenchConfigWriteSerializer,
    WrenchSyncLogSerializer,
    WrenchS3SyncJobSerializer,
)
from . import service as wrench_service
from .s3_documents_service import _extract_genealogy_segments

logger = logging.getLogger(__name__)

# ─── Soft-coded in-memory cache for trans_documents responses ────────────────
# Keyed by (config_id, order_no, trans_id, page, page_size). TTL is short so
# users still see fresh data when they reload, but repeated clicks within a
# session return instantly. Tunable via constants below.
_TRANS_DOC_RESULT_CACHE: dict = {}
_TRANS_DOC_CACHE_TTL_SECONDS = 5 * 60   # 5 minutes per (project, page) entry

# ─── Soft-coded diagnostic / verification constants ──────────────────────────
# Used by the `verify_trans_documents` action. Each probe is short-timeout so
# the diagnostic always returns within a few seconds even on a sick host.
_VERIFY_PROBE_TIMEOUT     = 6        # seconds, per REST probe
_VERIFY_SEARCH_TIMEOUT    = 25       # seconds, per SearchObject probe
_VERIFY_SAMPLE_SIZE       = 3        # number of docs to include as evidence
_VERIFY_BROAD_PAGE_SIZE   = 5        # tiny page for the broad SVC connectivity check

# ─── Soft-coded constants for the Wrench project (transmittal) dropdown ─────
# Used by `list_projects` to power the project-number selector on the PID
# Verification page.
#
# IMPORTANT: this Wrench tenant returns the *entire* transmittal set on every
# GetTransmittalList call (the API ignores ROW_COUNT/PAGE_NUMBER). So we make
# ONE call with a very large page_size — paging would just re-download the
# whole 80k+ row payload N times and time out.
#
# Caching strategy (stale-while-revalidate):
#   • FRESH window  → instant response from Redis (no Wrench call)
#   • STALE window  → still serve the cached payload immediately, BUT mark it
#                     `is_stale=true` so the frontend can trigger a background
#                     `?refresh=1` to refresh without blocking the user.
#   • EXPIRED       → fetch synchronously from Wrench, replace cache.
_PROJECTS_CACHE_KEY_PREFIX  = 'wrench:projects:v1:'  # per-config Redis key
_PROJECTS_FRESH_SECONDS     = 30 * 60                # 30 min — considered fresh, no revalidation
_PROJECTS_STALE_SECONDS     = 6 * 60 * 60            # 6 h    — still served, but flagged stale
_PROJECTS_REDIS_TTL_SECONDS = 24 * 60 * 60           # 24 h   — hard TTL in Redis
_PROJECTS_SINGLE_FETCH_SIZE = 200_000                # cover any sane tenant in one call
_PROJECTS_ORDER_NO_KEYS     = ('ORDER_NO', 'OrderNo', 'order_no')
_PROJECTS_DESC_KEYS         = ('ORDER_DESCRIPTION', 'OrderDescription', 'order_description', 'PROJECT_NAME')
_PROJECTS_SINGLE_FETCH_SIZE = 200_000               # large enough to cover any sane tenant in one call
_PROJECTS_ORDER_NO_KEYS     = ('ORDER_NO', 'OrderNo', 'order_no')
_PROJECTS_DESC_KEYS         = ('ORDER_DESCRIPTION', 'OrderDescription', 'order_description', 'PROJECT_NAME')


class WrenchConfigViewSet(viewsets.ViewSet):
    """
    Manage the Wrench platform connection configuration.

    GET  /api/v1/wrench/config/           – retrieve active config (safe, no key)
    POST /api/v1/wrench/config/           – create / update config
    POST /api/v1/wrench/config/verify/    – test connection
    DELETE /api/v1/wrench/config/<id>/    – remove config (super admin only)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def list(self, request):
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response({'configured': False, 'config': None})
        serializer = WrenchConfigReadSerializer(cfg)
        return Response({'configured': True, 'config': serializer.data})

    def create(self, request):
        """Create or replace the active Wrench config."""
        serializer = WrenchConfigWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Deactivate any existing configs before creating (soft singleton)
        WrenchConfig.objects.filter(is_active=True).update(is_active=False)

        cfg = serializer.save(
            created_by=request.user,
            updated_by=request.user,
        )
        create_audit_log(
            user=request.user,
            action='create',
            resource_type='WrenchConfig',
            resource_id=None,
            resource_repr=str(cfg),
            metadata={'config_id': cfg.id},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        read_serializer = WrenchConfigReadSerializer(cfg)
        return Response(
            {'message': 'Wrench configuration saved.', 'config': read_serializer.data},
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        """Only super admins can delete the config."""
        if not (request.user.is_superuser or
                request.user.rbac_profile.roles.filter(code='super_admin', is_active=True).exists()):
            return Response({'detail': 'Super admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            cfg = WrenchConfig.objects.get(pk=pk)
        except WrenchConfig.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        create_audit_log(
            user=request.user,
            action='delete',
            resource_type='WrenchConfig',
            resource_id=None,
            resource_repr=str(cfg),
            metadata={'config_id': cfg.id},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        cfg.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='verify')
    def verify(self, request):
        """Test the connection to Wrench without storing anything."""
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'success': False, 'message': 'No active configuration found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        result = wrench_service.verify_connection(cfg)
        create_audit_log(
            user=request.user,
            action='read',
            resource_type='WrenchConfig',
            resource_id=None,
            resource_repr='Connection verification',
            metadata={'config_id': cfg.id, 'result': result},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        http_status = status.HTTP_200_OK if result['success'] else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=http_status)

    @action(detail=False, methods=['post'], url_path='discover-svc-url')
    def discover_svc_url(self, request):
        """
        Auto-detect the Wrench DocumentSearch SVC URL by probing common patterns
        derived from the configured base_url. Used by the "Auto-Detect" button
        on the configuration form.

        Optional body:
          { "base_url": "https://...", "svc_url": "" }
            – if provided, probes the supplied URLs INSTEAD of the saved config
              (so admins can test before saving).

        Response:
          {
            "recommended": "<url>" | null,
            "candidates": [{url, probe_url, reachable, status_code, note}, ...]
          }
        """
        # Allow probing with the in-progress form values (no DB write needed).
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        override_base = (request.data.get('base_url') or '').strip()
        override_svc  = (request.data.get('svc_url')  or '').strip()

        if override_base or not cfg:
            # Build a transient (unsaved) config so we don't pollute the DB.
            cfg = WrenchConfig(
                base_url=override_base or (cfg.base_url if cfg else ''),
                svc_url=override_svc,
            )

        if not cfg.base_url:
            return Response(
                {'detail': 'Provide base_url to probe (no active configuration found).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = wrench_service.probe_svc_url_candidates(cfg)
        except Exception as exc:
            logger.error('[Wrench] SVC URL probe failed: %s', exc, exc_info=True)
            return Response(
                {'detail': 'Auto-detect failed. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='inject-token')
    def inject_token(self, request):
        """
        Save a pre-shared Wrench session token directly — bypasses username/password login.
        POST /api/v1/wrench/config/inject-token/
        Body: { "token": "<TOKEN_STRING>" }

        Once saved, the backend uses this token for all Wrench API calls.
        Wrench’s rolling-token mechanism keeps it refreshed automatically.
        """
        token = request.data.get('token', '').strip()
        if not token:
            return Response({'detail': 'token field is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Minimum sanity check — Wrench tokens are long base-64 strings
        _MIN_TOKEN_LENGTH = 32
        if len(token) < _MIN_TOKEN_LENGTH:
            return Response(
                {'detail': f'Token appears too short (minimum {_MIN_TOKEN_LENGTH} characters). Check the value and try again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response({'detail': 'No active Wrench configuration found.'}, status=status.HTTP_404_NOT_FOUND)

        # Encrypt at rest via model helper — token is never stored in plaintext.
        cfg.set_pre_shared_token(token)
        # Clear the session_token mirror; `_ensure_token()` always prefers the
        # encrypted pre_shared_token, so storing a second plaintext copy is unsafe.
        cfg.session_token = ''
        cfg.token_obtained_at = timezone.now()
        cfg.save(update_fields=['pre_shared_token', 'session_token', 'token_obtained_at'])

        create_audit_log(
            user=request.user,
            action='update',
            resource_type='WrenchConfig',
            resource_id=None,
            resource_repr='Pre-shared token injection',
            metadata={'config_id': cfg.id, 'token_length': len(token)},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return Response(
            {'message': 'Token saved. All Wrench API calls will now use this token directly (login bypassed).'},
            status=status.HTTP_200_OK,
        )


class WrenchSyncViewSet(viewsets.ViewSet):
    """
    Trigger and view synchronisation between RADAI and Wrench.

    GET  /api/v1/wrench/sync/            – list recent sync logs
    POST /api/v1/wrench/sync/trigger/    – start a sync
    GET  /api/v1/wrench/sync/<id>/       – retrieve a specific log
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def list(self, request):
        logs = WrenchSyncLog.objects.select_related('triggered_by').order_by('-started_at')[:50]
        serializer = WrenchSyncLogSerializer(logs, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        try:
            log = WrenchSyncLog.objects.get(pk=pk)
        except WrenchSyncLog.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(WrenchSyncLogSerializer(log).data)

    @action(detail=False, methods=['post'], url_path='trigger')
    def trigger(self, request):
        """Kick off a synchronisation run."""
        direction = request.data.get('direction', 'wrench_to_radai')
        entity_type = request.data.get('entity_type', 'all')

        valid_directions = ['radai_to_wrench', 'wrench_to_radai']
        valid_entities = ['project', 'document', 'transmittal', 'user', 'all']

        if direction not in valid_directions:
            return Response(
                {'detail': f'Invalid direction. Choose from {valid_directions}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if entity_type not in valid_entities:
            return Response(
                {'detail': f'Invalid entity_type. Choose from {valid_entities}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            log = wrench_service.run_sync(
                direction=direction,
                entity_type=entity_type,
                triggered_by=request.user,
            )
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except Exception as exc:
            logger.error('Sync trigger failed: %s', exc, exc_info=True)
            return Response(
                {'detail': 'Sync failed. Check server logs for details.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        create_audit_log(
            user=request.user,
            action='execute',
            resource_type='WrenchSync',
            resource_id=None,
            resource_repr=str(log),
            metadata={'log_id': log.id, 'direction': direction, 'entity_type': entity_type, 'status': log.status},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        return Response(WrenchSyncLogSerializer(log).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='search-documents')
    def search_documents(self, request):
        """
        Search Wrench documents.
        Strategy: try REST GetDocumentList first (same host as transmittals, no SVC URL needed),
                  fall back to DocumentSearch/SearchObject (requires SVC URL).

        Request body (all optional):
          discipline  – filter by discipline code
          doc_no      – exact match on DOC_NO
          date_from   – APPROVED_ON >= this date ('YYYY/MM/DD HH:MM')  [DocumentSearch only]
          date_to     – APPROVED_ON <= this date ('YYYY/MM/DD HH:MM')  [DocumentSearch only]
          page        – page number (default 1)
          page_size   – results per page (default 50, max 200)
        """
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Please configure the integration first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page      = int(request.data.get('page', 1))
        page_size = min(int(request.data.get('page_size', 50)), 200)
        discipline = request.data.get('discipline') or None
        doc_no     = request.data.get('doc_no') or None
        date_from  = request.data.get('date_from') or None
        date_to    = request.data.get('date_to') or None

        # ── Strategy 1: REST GetDocumentList (no SVC URL required) ──────────
        try:
            result = wrench_service.get_document_list(
                cfg,
                page=page,
                page_size=page_size,
                discipline=discipline,
                doc_no=doc_no,
            )
            result['source'] = 'rest'
            return Response(result, status=status.HTTP_200_OK)
        except Exception as rest_exc:
            logger.info('[Wrench] REST document list failed (%s), trying DocumentSearch', rest_exc)

        # ── Strategy 2: DocumentSearch/SearchObject (requires SVC URL) ──────
        try:
            result = wrench_service.search_documents(
                cfg,
                page=page,
                page_size=page_size,
                discipline=discipline,
                date_from=date_from,
                date_to=date_to,
                doc_no=doc_no,
            )
            result['source'] = 'document_search'
            return Response(result, status=status.HTTP_200_OK)
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except http_lib.exceptions.ConnectionError:
            return Response({'detail': 'Unable to reach the Wrench server.'}, status=status.HTTP_502_BAD_GATEWAY)
        except http_lib.exceptions.HTTPError as exc:
            return Response({'detail': f'Wrench returned HTTP {exc.response.status_code}.'}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:
            logger.error('[Wrench] Document search failed: %s', exc, exc_info=True)
            return Response({'detail': 'Document search failed. Check server logs.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='document-choices')
    def document_choices(self, request):
        """
        Return unique discipline codes and document numbers drawn from a sample search.
        Used to populate dropdowns in the Document Search UI.

        GET /api/v1/wrench/sync/document-choices/
        Response: { disciplines: [...], doc_numbers: [...] }
        """
        # Soft-coded sample size — large enough to cover most project disciplines
        _CHOICES_SAMPLE_SIZE = 200

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Strategy 1: Try the REST GetDocumentList endpoint (same host as transmittals).
        # Strategy 2: Fall back to DocumentSearch/SearchObject (needs SVC URL).
        # Whichever succeeds, extract unique disciplines + doc numbers.
        result = None
        svc_url_required = False

        try:
            result = wrench_service.get_document_list(cfg, page=1, page_size=_CHOICES_SAMPLE_SIZE)
            logger.info('[Wrench] document-choices: loaded %d docs via REST', result['total'])
        except Exception as rest_exc:
            logger.warning('[Wrench] document-choices REST failed (%s), trying DocumentSearch', rest_exc)
            try:
                result = wrench_service.search_documents(cfg, page=1, page_size=_CHOICES_SAMPLE_SIZE)
            except RuntimeError as exc:
                err_msg = str(exc)
                # svc_url_required is set when auto-discovery exhausted all candidates
                svc_url_required = 'Could not find the DocumentSearch endpoint' in err_msg or 'DocumentSearch endpoint not found' in err_msg
                logger.warning('[Wrench] document-choices DocumentSearch also failed: %s', err_msg)
            except Exception as exc:
                logger.warning('[Wrench] document-choices unexpected error: %s', exc)

        if result is None:
            return Response(
                {'disciplines': [], 'doc_numbers': [], 'svc_url_required': svc_url_required},
                status=status.HTTP_200_OK,
            )

        disciplines = sorted({
            doc.get('DISCIPLINE', '').strip()
            for doc in result.get('documents', [])
            if doc.get('DISCIPLINE', '').strip()
        })
        doc_numbers = sorted({
            doc.get('DOC_NO', '').strip()
            for doc in result.get('documents', [])
            if doc.get('DOC_NO', '').strip()
        })

        return Response(
            {'disciplines': disciplines, 'doc_numbers': doc_numbers, 'svc_url_required': False},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='list-transmittals')
    def list_transmittals(self, request):
        """
        List transmittals from Wrench via the SmartProject REST WebAPI.
        GET /api/v1/wrench/sync/list-transmittals/?page=1&page_size=50
        """
        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Please configure the integration first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', 50)), 500)

        try:
            result = wrench_service.get_transmittals(cfg, page=page, page_size=page_size)
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except http_lib.exceptions.ConnectionError:
            return Response(
                {'detail': 'Unable to reach the Wrench server.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except http_lib.exceptions.HTTPError as exc:
            return Response(
                {'detail': f'Wrench returned HTTP {exc.response.status_code}.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            logger.error('[Wrench] List transmittals failed: %s', exc, exc_info=True)
            return Response(
                {'detail': 'Failed to list transmittals. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='trans-documents')
    def trans_documents(self, request):
        """
        Return documents linked to a specific transmittal.

        Strategy (transparent to frontend — first success wins):
          1. Transmittal-specific REST endpoints  (no SVC URL required)
          2. Generic Document REST GetDocumentList (no SVC URL required)
          3. DocumentSearch/SearchObject fallback  (uses SVC URL if configured)

        GET /api/v1/wrench/sync/trans-documents/?order_no=<ORDER_NO>&trans_id=<TRANS_ID>
        Response: { total, documents: [{DOC_NO, DOC_DESCRIPTION, ...}], source }
        """
        # Soft-coded default page size for per-transmittal document fetch
        _TRANS_DOC_DEFAULT_PAGE_SIZE = 200
        # When all Wrench REST + DocumentSearch strategies fail (instance does not
        # expose any per-transmittal document endpoint), return an empty 200
        # response with an informative `note` so the UI can render a friendly
        # "no documents" state instead of a hard error toast.
        _RETURN_EMPTY_ON_EXHAUSTED_STRATEGIES = True

        order_no = request.query_params.get('order_no', '').strip()
        trans_id = request.query_params.get('trans_id', '').strip() or None

        if not order_no:
            return Response(
                {'detail': 'order_no query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        page      = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', _TRANS_DOC_DEFAULT_PAGE_SIZE)), 500)

        # ── Soft-coded in-memory result cache ─────────────────────────────
        # Allows ?refresh=1 to bypass. Hits return instantly, skipping the
        # whole strategy chain.
        cache_key   = (cfg.id, order_no, trans_id or '', page, page_size)
        force_fresh = request.query_params.get('refresh', '').lower() in ('1', 'true', 'yes')
        if not force_fresh:
            cached = _TRANS_DOC_RESULT_CACHE.get(cache_key)
            if cached and cached['expires_at'] > _time.time():
                payload = dict(cached['payload'])
                payload['cached'] = True
                return Response(payload, status=status.HTTP_200_OK)

        try:
            result = wrench_service.get_transmittal_documents(
                cfg,
                order_no=order_no,
                trans_id=trans_id,
                page=page,
                page_size=page_size,
            )
            result['svc_url_required'] = False

            # ── Soft-coded transmittal-as-document fallback ────────────────
            # If the core strategies returned zero documents, try synthesizing
            # documents directly from the transmittal list (some tenants do not
            # expose any per-transmittal document endpoint; transmittal rows
            # themselves carry TRANS_REF_NO, TRANS_DESC, REPORT_FILE_ID, etc.).
            if not result.get('documents'):
                try:
                    fb = wrench_service.synthesize_documents_from_transmittal_list(
                        cfg, order_no=order_no, page=page, page_size=page_size,
                    )
                    if fb.get('documents'):
                        result = fb
                        result['fallback_used'] = 'transmittal_list'
                except Exception as fb_exc:  # noqa: BLE001
                    logger.warning('[Wrench] transmittal fallback failed for %s: %s', order_no, fb_exc)

            _TRANS_DOC_RESULT_CACHE[cache_key] = {
                'payload':    result,
                'expires_at': _time.time() + _TRANS_DOC_CACHE_TTL_SECONDS,
            }
            return Response(result, status=status.HTTP_200_OK)
        except RuntimeError as exc:
            logger.warning('[Wrench] trans_documents: all strategies failed for order_no=%s: %s', order_no, exc)
            # Try the transmittal-as-document fallback even when the core service raised
            try:
                fb = wrench_service.synthesize_documents_from_transmittal_list(
                    cfg, order_no=order_no, page=page, page_size=page_size,
                )
                if fb.get('documents'):
                    fb['fallback_used'] = 'transmittal_list'
                    _TRANS_DOC_RESULT_CACHE[cache_key] = {
                        'payload':    fb,
                        'expires_at': _time.time() + _TRANS_DOC_CACHE_TTL_SECONDS,
                    }
                    return Response(fb, status=status.HTTP_200_OK)
            except Exception as fb_exc:  # noqa: BLE001
                logger.warning('[Wrench] transmittal fallback (post-error) failed: %s', fb_exc)

            if _RETURN_EMPTY_ON_EXHAUSTED_STRATEGIES:
                # Soft-fail: respond 200 with an empty document list + diagnostic
                # note. This lets the frontend render an informative empty-state
                # instead of a 422 error toast — core service logic is unchanged.
                empty_payload = {
                    'total':            0,
                    'documents':        [],
                    'source':           'none',
                    'svc_url_required': False,
                    'note':             (
                        'No documents are indexed in Wrench for this project. '
                        'The Wrench WebAPI on this instance does not expose a '
                        'per-transmittal document endpoint, and the DocumentSearch '
                        'fallback returned no rows. If you expect documents here, '
                        'verify the Wrench DocumentSearch SVC URL in Configuration.'
                    ),
                }
                _TRANS_DOC_RESULT_CACHE[cache_key] = {
                    'payload':    empty_payload,
                    'expires_at': _time.time() + _TRANS_DOC_CACHE_TTL_SECONDS,
                }
                return Response(empty_payload, status=status.HTTP_200_OK)
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as exc:
            logger.error('[Wrench] trans_documents unexpected error for order_no=%s: %s', order_no, exc, exc_info=True)
            return Response(
                {'detail': 'Could not load documents for this transmittal. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Diagnostic: verify whether a project genuinely has no documents in
    # Wrench, or the empty result is due to a configuration / endpoint issue.
    # Soft-coded, read-only, fast — does NOT mutate any cache or DB state.
    # ─────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='trans-documents/verify')
    def verify_trans_documents(self, request):
        """
        GET /api/v1/wrench/sync/trans-documents/verify/?order_no=<ORDER_NO>

        Runs a multi-step diagnostic and returns a structured report:
          • config         — base/svc URL presence (redacted)
          • token          — login success / error
          • host_profile   — cached dead paths / winning path / exhausted state
          • rest_probes    — each transmittal-doc REST path with HTTP status
          • doc_search     — DocumentSearch broad probe + per-project probe
          • conclusion     — verdict + recommendations
        """
        order_no = (request.query_params.get('order_no') or '').strip()
        if not order_no:
            return Response(
                {'detail': 'order_no query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        def _redact(url: str) -> str:
            if not url:
                return ''
            try:
                from urllib.parse import urlparse
                u = urlparse(url)
                return f'{u.scheme}://{u.hostname}{u.path}' if u.hostname else url
            except Exception:
                return url

        report = {
            'order_no': order_no,
            'config': {
                'has_base_url': bool(cfg.base_url),
                'has_svc_url':  bool(cfg.svc_url),
                'base_url':     _redact(cfg.base_url),
                'svc_url':      _redact(cfg.svc_url) if cfg.svc_url else None,
                'server_id':    cfg.server_id,
            },
            'token':        {'acquired': False, 'error': None},
            'host_profile': None,
            'rest_probes':  [],
            'doc_search':   {'broad': None, 'by_order_no': None},
            'conclusion':   {'verdict': 'unknown', 'reasons': [], 'recommendations': []},
        }

        # 1. Token acquisition
        try:
            token = wrench_service._ensure_token(cfg)
            report['token']['acquired'] = bool(token)
        except Exception as exc:
            report['token']['error'] = str(exc)
            report['conclusion']['verdict']         = 'config_error'
            report['conclusion']['reasons'].append('Wrench login failed — cannot verify documents.')
            report['conclusion']['recommendations'].append(
                'Check WrenchConfig credentials (base_url, server_id, login_name, password).'
            )
            return Response(report, status=status.HTTP_200_OK)

        # 2. Host profile snapshot (read-only)
        try:
            prof = wrench_service._host_profile(cfg)
            report['host_profile'] = {
                'dead_paths':       sorted(prof.get('dead_paths') or []),
                'winning_path':     prof.get('winning_path'),
                'exhausted_until':  prof.get('exhausted_until'),
                'exhausted_now':    bool(prof.get('exhausted_until') and prof['exhausted_until'] > _time.time()),
            }
        except Exception as exc:
            report['host_profile'] = {'error': str(exc)}

        # 3. REST probes (lightweight) — record each path's HTTP status
        rest_paths = (
            list(getattr(wrench_service, '_TRANS_DOC_REST_PATHS', []))
            + [getattr(wrench_service, '_DOC_LIST_URL_PATH', '/api/Document/GetDocumentList')]
            + list(getattr(wrench_service, '_DOC_LIST_ALT_PATHS', []))
        )
        payload = {
            'TOKEN':       token,
            'SERVER_ID':   cfg.server_id,
            'LOGIN_NAME':  cfg.login_name,
            'ROW_COUNT':   1,
            'PAGE_NUMBER': 1,
            'ORDER_NO':    order_no,
        }
        any_rest_ok = False
        for path in rest_paths:
            try:
                url = wrench_service._api_url(cfg, path)
                resp = http_lib.post(url, json=payload, timeout=_VERIFY_PROBE_TIMEOUT)
                ok = resp.status_code == 200
                if ok:
                    any_rest_ok = True
                report['rest_probes'].append({
                    'path':        path,
                    'http_status': resp.status_code,
                    'ok':          ok,
                })
            except http_lib.exceptions.RequestException as exc:
                report['rest_probes'].append({
                    'path':        path,
                    'http_status': None,
                    'ok':          False,
                    'error':       str(exc)[:200],
                })

        # 4. DocumentSearch — broad probe (no ORDER_NO) to confirm SVC reachable
        try:
            broad = wrench_service.search_documents(cfg, page=1, page_size=_VERIFY_BROAD_PAGE_SIZE)
            report['doc_search']['broad'] = {
                'ok':     True,
                'total':  broad.get('total', 0),
                'sample': [d.get('DOC_NO') for d in (broad.get('documents') or [])[:_VERIFY_SAMPLE_SIZE]],
            }
        except Exception as exc:
            report['doc_search']['broad'] = {'ok': False, 'error': str(exc)[:300]}

        # 5. DocumentSearch — per-project probe (with ORDER_NO filter)
        try:
            scoped = wrench_service.search_documents(cfg, page=1, page_size=_VERIFY_SAMPLE_SIZE, order_no=order_no)
            report['doc_search']['by_order_no'] = {
                'ok':     True,
                'total':  scoped.get('total', 0),
                'sample': [d.get('DOC_NO') for d in (scoped.get('documents') or [])[:_VERIFY_SAMPLE_SIZE]],
            }
        except Exception as exc:
            report['doc_search']['by_order_no'] = {'ok': False, 'error': str(exc)[:300]}

        # 6. Build verdict from collected evidence
        broad      = report['doc_search']['broad']   or {}
        by_order   = report['doc_search']['by_order_no'] or {}
        verdict, reasons, recs = 'unknown', [], []

        if not cfg.svc_url and not broad.get('ok'):
            verdict = 'svc_url_missing'
            reasons.append('No DocumentSearch SVC URL is configured and auto-discovery failed.')
            recs.append('Set Wrench → Configuration → DocumentSearch SVC URL.')
        elif broad.get('ok') and broad.get('total', 0) > 0 and by_order.get('ok') and by_order.get('total', 0) == 0:
            verdict = 'no_documents_for_project'
            reasons.append(f'DocumentSearch reachable; total docs in instance ≥ {broad.get("total")}, but ORDER_NO={order_no} returns 0 rows.')
            recs.append('Confirm the project (ORDER_NO) is correct and that documents have been indexed in Wrench for it.')
            recs.append('Try opening the project in the Wrench portal to confirm document linkage.')
        elif broad.get('ok') and by_order.get('ok') and by_order.get('total', 0) > 0:
            verdict = 'documents_exist_unexpectedly'
            reasons.append('DocumentSearch returned documents for this project — the empty state was stale.')
            recs.append('Click "Refresh" (refresh=1) to bypass the in-memory cache and re-fetch.')
        elif not broad.get('ok'):
            verdict = 'doc_search_unreachable'
            reasons.append('DocumentSearch is unreachable: ' + str(broad.get('error', 'unknown error'))[:120])
            recs.append('Verify the SVC URL host is reachable from the backend and the credentials have search permission.')
        elif not any_rest_ok and broad.get('ok') and broad.get('total', 0) == 0:
            verdict = 'wrench_instance_empty'
            reasons.append('No REST per-transmittal endpoint exists AND DocumentSearch is reachable but empty.')
            recs.append('This Wrench instance has no documents indexed at all — check with the Wrench administrator.')

        report['conclusion'] = {'verdict': verdict, 'reasons': reasons, 'recommendations': recs}
        return Response(report, status=status.HTTP_200_OK)

    # ─────────────────────────────────────────────────────────────────────
    # Lightweight project / transmittal dropdown feed.
    # Powers the "Project Number" selector on the PID Verification page.
    # Soft-coded constants live at the top of this module — admins can tune
    # TTL / fresh / stale windows / page-size without touching this function.
    # Uses a stale-while-revalidate pattern backed by Django cache (Redis).
    # ─────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='projects')
    def list_projects(self, request):
        """
        GET /api/v1/wrench/sync/projects/?refresh=0
        Returns a deduplicated, alphabetically-sorted list of Wrench projects
        (one entry per unique ORDER_NO) suitable for a <select> dropdown.

        Response:
          {
            "total":             N,
            "transmittal_count": N,
            "cached":            bool,
            "is_stale":          bool,     # True → frontend may revalidate in background
            "fetched_at":        epoch_seconds,
            "age_seconds":       int,
            "fresh_for":         int,      # seconds remaining before becoming stale
            "projects": [ { "order_no", "order_description", "label" } ]
          }
        """
        from django.core.cache import cache  # local import — avoids touching module imports

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Please configure the integration first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        refresh   = str(request.query_params.get('refresh', '')).lower() in ('1', 'true', 'yes')
        cache_key = f'{_PROJECTS_CACHE_KEY_PREFIX}{cfg.id}'
        now       = _time.time()

        # ── Cache hit: serve immediately if fresh OR stale (frontend revalidates) ──
        if not refresh:
            cached = cache.get(cache_key)
            if cached:
                age = max(0, int(now - cached.get('fetched_at', now)))
                if age < _PROJECTS_STALE_SECONDS:
                    payload = dict(cached)
                    payload['cached']      = True
                    payload['age_seconds'] = age
                    payload['is_stale']    = age >= _PROJECTS_FRESH_SECONDS
                    payload['fresh_for']   = max(0, _PROJECTS_FRESH_SECONDS - age)
                    return Response(payload, status=status.HTTP_200_OK)

        # ── Cache miss / forced refresh: hit Wrench in one big call ───────
        projects: dict = {}   # order_no → order_description (longest wins)
        try:
            chunk = wrench_service.get_transmittals(
                cfg, page=1, page_size=_PROJECTS_SINGLE_FETCH_SIZE,
            )
            rows = chunk.get('transmittals') or []
            total_available = chunk.get('total') or len(rows)

            for row in rows:
                order_no = next((str(row.get(k)).strip() for k in _PROJECTS_ORDER_NO_KEYS
                                 if row.get(k) is not None and str(row.get(k)).strip()), '')
                if not order_no:
                    continue
                desc = next((str(row.get(k)).strip() for k in _PROJECTS_DESC_KEYS
                             if row.get(k) is not None and str(row.get(k)).strip()), '')
                existing = projects.get(order_no, '')
                if len(desc) > len(existing):
                    projects[order_no] = desc
        except RuntimeError as exc:
            # If we have any stale cache, prefer serving that over an error.
            cached = cache.get(cache_key)
            if cached:
                payload = dict(cached)
                payload['cached']    = True
                payload['is_stale']  = True
                payload['note']      = f'Upstream refresh failed: {exc}. Serving last-good snapshot.'
                return Response(payload, status=status.HTTP_200_OK)
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except Exception as exc:  # noqa: BLE001
            logger.error('[Wrench] list_projects unexpected error: %s', exc, exc_info=True)
            cached = cache.get(cache_key)
            if cached:
                payload = dict(cached)
                payload['cached']    = True
                payload['is_stale']  = True
                payload['note']      = 'Upstream refresh failed. Serving last-good snapshot.'
                return Response(payload, status=status.HTTP_200_OK)
            return Response(
                {'detail': 'Failed to load Wrench projects. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        items = [
            {
                'order_no':          ono,
                'order_description': desc,
                'label':             f'{ono} — {desc}' if desc else ono,
            }
            for ono, desc in projects.items()
        ]
        items.sort(key=lambda r: r['order_no'])

        payload = {
            'total':             len(items),
            'transmittal_count': total_available,
            'cached':            False,
            'is_stale':          False,
            'fetched_at':        int(now),
            'age_seconds':       0,
            'fresh_for':         _PROJECTS_FRESH_SECONDS,
            'projects':          items,
        }
        cache.set(cache_key, payload, timeout=_PROJECTS_REDIS_TTL_SECONDS)
        return Response(payload, status=status.HTTP_200_OK)

    # ─────────────────────────────────────────────────────────────────────
    # Sub-folder listing for a Wrench project (used by AI Document Assist
    # panel to let users scope the recommendation to one or more sub-folders).
    # Additive, soft-coded, no LLM call. Re-uses the same fuzzy project-name
    # resolution and document fetch as pid_recommendations() so behaviour
    # stays consistent across the two endpoints.
    # ─────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='project-folders')
    def project_folders(self, request):
        """
        GET /api/v1/wrench/sync/project-folders/
              ?order_no=<ORDER_NO>                 (one of order_no / project_name required)
              &project_name=<RADAI_PROJECT_NAME>   (fuzzy-matched against ORDER_DESCRIPTION)
              &depth=<int>                         (optional, cap returned segments per path)

        Returns:
          {
            "order_no":       "...",
            "matched_via":    "explicit" | "fuzzy_project_name(...)" | "none",
            "total_scanned":  N,
            "total_folders":  M,
            "folders":        [ { path, segments, count, file_exts[] } ]
          }
        """
        # Soft-coded knobs (mirror pid_recommendations for consistency).
        _PAGE_SIZE_SCAN  = 500   # docs per Wrench page request
        _MAX_PAGES       = 40    # safety cap → up to 20 000 docs scanned
        _FUZZY_MIN_SCORE = 60
        _MAX_FOLDERS     = 500   # safety cap on response size
        _DEPTH_DEFAULT   = 0     # 0 = no cap
        _DEPTH_MAX       = 12

        try:
            depth_cap = max(0, min(int(request.query_params.get('depth', _DEPTH_DEFAULT)), _DEPTH_MAX))
        except (TypeError, ValueError):
            depth_cap = _DEPTH_DEFAULT

        order_no     = (request.query_params.get('order_no') or '').strip()
        project_name = (request.query_params.get('project_name') or '').strip()

        if not order_no and not project_name:
            return Response(
                {'detail': 'Either order_no or project_name query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        matched_via = 'explicit' if order_no else 'none'

        # Re-use the same fuzzy resolver shape as pid_recommendations.
        if not order_no and project_name:
            try:
                from difflib import SequenceMatcher
                tlist = wrench_service.get_transmittals(cfg, page=1, page_size=500)
                rows  = tlist.get('transmittals') or tlist.get('TransmittalList') or []
                best, best_score = None, 0
                pn = project_name.lower()
                for row in rows:
                    desc = (row.get('ORDER_DESCRIPTION') or row.get('ORDER_DESC') or '').lower()
                    if not desc:
                        continue
                    s = int(SequenceMatcher(None, pn, desc).ratio() * 100)
                    if s > best_score:
                        best_score, best = s, row
                if best and best_score >= _FUZZY_MIN_SCORE:
                    order_no    = str(best.get('ORDER_NO') or '').strip()
                    matched_via = f'fuzzy_project_name(score={best_score})'
            except Exception as exc:
                logger.warning('[Wrench] project-folders: fuzzy match failed: %s', exc)

        if not order_no:
            return Response(
                {
                    'order_no':      None,
                    'matched_via':   matched_via,
                    'total_scanned': 0,
                    'total_folders': 0,
                    'folders':       [],
                    'note':          f'Could not resolve a Wrench ORDER_NO from project name "{project_name}".',
                },
                status=status.HTTP_200_OK,
            )

        # Fetch ALL documents across pages so the folder list is complete.
        # Strategy ladder (each tier is additive — results are merged &
        # de-duplicated by DOC_NO so the broadest possible folder list is
        # returned even when one Wrench API is unavailable):
        #   0. get_project_folder_tree(order_no=…) — direct Wrench Folder/
        #      ProjectLibrary REST. Best source for the human-curated tree
        #      (e.g. "Engineering / Records / Final Handover Package").
        #   1. search_documents(order_no=…) — explicit DocumentSearch
        #   2. get_transmittal_documents(order_no=…)  paginated
        #   3. synthesize_documents_from_transmittal_list  (transmittal rows)
        documents = []
        seen_doc_nos = set()
        pages_used = 0
        sources = []

        # ── Tier 0 — direct folder-tree REST. If this returns folders we
        # surface them immediately (with count=0 placeholders) so the user
        # sees the real Wrench library tree even when no document carries a
        # GENEALOGY field. Document-derived counts merge on top below.
        tree_folders = []
        try:
            tree = wrench_service.get_project_folder_tree(cfg, order_no=order_no)
            tree_folders = tree.get('folders') or []
            if tree_folders:
                sources.append(f"folder_tree({tree.get('source')},{len(tree_folders)})")
        except Exception as exc:
            logger.warning('[Wrench] project-folders: folder-tree failed: %s', exc)

        def _merge(batch):
            added = 0
            for d in batch or []:
                key = (
                    str(d.get('DOC_NO') or '').strip().lower()
                    or str(d.get('DOC_DESCRIPTION') or '').strip().lower()[:80]
                    or f'__row_{len(documents)}'
                )
                if key in seen_doc_nos:
                    continue
                seen_doc_nos.add(key)
                documents.append(d)
                added += 1
            return added

        # Tier 1 — DocumentSearch / SearchObject (broadest results when SVC URL set).
        try:
            sd = wrench_service.search_documents(
                cfg, page=1, page_size=_PAGE_SIZE_SCAN, order_no=order_no,
            )
            t1_added = _merge(sd.get('documents'))
            if t1_added:
                sources.append(f'search_documents({t1_added})')
        except Exception as exc:
            logger.warning('[Wrench] project-folders: search_documents failed: %s', exc)

        # Tier 2 — paginated transmittal-document REST.
        for page in range(1, _MAX_PAGES + 1):
            try:
                doc_payload = wrench_service.get_transmittal_documents(
                    cfg, order_no=order_no, page=page, page_size=_PAGE_SIZE_SCAN,
                )
                batch = doc_payload.get('documents') or []
            except Exception as exc:
                logger.warning('[Wrench] project-folders: doc fetch failed (page=%d): %s', page, exc)
                batch = []
            if not batch:
                break
            t2_added = _merge(batch)
            pages_used = page
            if t2_added == 0 and page == 1:
                # Tier-2 returned the same docs already seen from tier 1 — stop.
                break
            if len(batch) < _PAGE_SIZE_SCAN:
                break   # last page reached
        if pages_used:
            sources.append(f'transmittal_documents(pages={pages_used})')

        # Tier 3 — transmittal-list synthesis (last-resort).
        if not documents:
            try:
                fb = wrench_service.synthesize_documents_from_transmittal_list(
                    cfg, order_no=order_no, page=1, page_size=_PAGE_SIZE_SCAN,
                )
                t3_added = _merge(fb.get('documents'))
                if t3_added:
                    sources.append(f'transmittal_list({t3_added})')
            except Exception as fb_exc:  # noqa: BLE001
                logger.warning('[Wrench] project-folders: transmittal fallback failed: %s', fb_exc)

        logger.info(
            '[Wrench] project-folders: order_no=%s scanned=%d sources=%s',
            order_no, len(documents), ','.join(sources) or 'none',
        )

        # ── Soft-coded fallback ladder ─────────────────────────────────
        # Many Wrench tenants do not populate GENEALOGY_STRING, so we
        # synthesise a 2-level hierarchy from the structured metadata that
        # IS always present (DISCIPLINE → DOC_TYPE / TRANS_TYPE_CODE).
        # This mirrors how documents are physically filed on R:\Projects
        # ("…\<Discipline>\<DocType>\…") and satisfies the user request to
        # list sub-folders "with respect to the discipline".
        _FALLBACK_FIELDS_L1 = ('DISCIPLINE',     'Discipline',     'DISC')
        _FALLBACK_FIELDS_L2 = ('DOC_TYPE',       'DocType',
                               'TRANS_TYPE_CODE','TransTypeCode',
                               'DOC_CATEGORY',   'DocCategory')
        _FALLBACK_DEFAULT_L1 = 'General'

        def _first_nonempty(d, fields):
            for f in fields:
                v = d.get(f)
                if v and str(v).strip():
                    return str(v).strip()
            return ''

        # Aggregate folder paths from GENEALOGY_STRING / FOLDER_PATH / etc.
        folder_buckets = {}   # path -> {segments, count, file_exts:set}
        root_count = 0
        synth_used = 0
        for doc in documents:
            segs = _extract_genealogy_segments(doc, order_no) or []
            if not segs:
                # Fallback: build a Discipline / DocType tree from metadata.
                lvl1 = _first_nonempty(doc, _FALLBACK_FIELDS_L1) or _FALLBACK_DEFAULT_L1
                lvl2 = _first_nonempty(doc, _FALLBACK_FIELDS_L2)
                segs = [lvl1] + ([lvl2] if lvl2 else [])
                if segs and segs != [_FALLBACK_DEFAULT_L1]:
                    synth_used += 1
            if depth_cap and len(segs) > depth_cap:
                segs = segs[:depth_cap]
            if not segs:
                root_count += 1
                continue
            path = '/'.join(segs)
            bucket = folder_buckets.setdefault(path, {
                'path':      path,
                'segments':  segs,
                'count':     0,
                'file_exts': set(),
            })
            bucket['count'] += 1
            ext = (doc.get('FILE_EXT') or doc.get('EXTENSION') or '').lower().lstrip('.')
            if not ext:
                dn = str(doc.get('DOC_NO') or '')
                if '.' in dn:
                    ext = dn.rsplit('.', 1)[-1].lower()
            if ext:
                bucket['file_exts'].add(ext)

        # Merge Tier-0 tree folders so the user sees the real Wrench library
        # even for folders that contain no documents indexed by the search /
        # transmittal APIs (placeholders show count=0).
        for tf in tree_folders:
            path = tf.get('path')
            if not path or path in folder_buckets:
                continue
            folder_buckets[path] = {
                'path':      path,
                'segments':  tf.get('segments') or path.split('/'),
                'count':     0,
                'file_exts': set(),
            }

        folders = sorted(
            folder_buckets.values(),
            key=lambda b: (-b['count'], b['path']),
        )[:_MAX_FOLDERS]
        # Serialise sets → lists for JSON safety.
        for b in folders:
            b['file_exts'] = sorted(b['file_exts'])

        return Response(
            {
                'order_no':      order_no,
                'matched_via':   matched_via,
                'pages_used':    pages_used,
                'sources':       sources,
                'total_scanned': len(documents),
                'total_folders': len(folder_buckets),
                'unfiled_count': root_count,
                'synth_used':    synth_used,
                'folders':       folders,
            },
            status=status.HTTP_200_OK,
        )

    # ─────────────────────────────────────────────────────────────────────
    # AI-assisted P&ID document recommendation (PID Verification page).
    # Optional, soft-coded, no LLM call — uses deterministic scoring
    # heuristics that admins can tune via the constants below.
    # ─────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='pid-recommendations')
    def pid_recommendations(self, request):
        """
        GET /api/v1/wrench/sync/pid-recommendations/
              ?order_no=<ORDER_NO>                 (one of order_no / project_name required)
              &project_name=<RADAI_PROJECT_NAME>   (fuzzy-matched against ORDER_DESCRIPTION)
              &drawing_hint=<optional drawing/tag hint>
              &top=<int, default 5>

        Returns:
          {
            "order_no":            "...",
            "matched_via":         "explicit" | "fuzzy_project_name" | "none",
            "total_scanned":       N,
            "recommendations":     [ { doc_no, doc_description, score, reasons[],
                                       download_url, discipline, revision, file_ext } ],
            "note":                "..." (optional diagnostic)
          }
        """
        # ── Soft-coded recommendation knobs (tunable, no core-logic change) ──
        # Higher score = more likely to be a P&ID. Negative = penalty.
        _SCORE_WEIGHTS = {
            'pid_keyword_strong': 50,   # "P&ID", "PIPING & INSTRUMENTATION"
            'pid_keyword_loose':  30,   # "PID", "P-AND-ID"
            'discipline_process': 25,
            'discipline_instr':   15,
            'pdf_extension':      20,
            'dwg_extension':      15,
            'drawing_hint_token': 10,
            'latest_revision':    8,
            'penalty_legend':    -15,   # legend sheets aren't the target P&ID
            'penalty_index':     -20,   # document indexes / lists
            'penalty_report':    -10,
        }
        _PID_KEYWORD_STRONG = ('P&ID', 'P AND ID', 'PIPING & INSTRUMENTATION', 'PIPING AND INSTRUMENTATION')
        _PID_KEYWORD_LOOSE  = ('PID', 'P-ID', 'P-AND-ID')
        _DISCIPLINE_PROCESS = ('PROCESS', 'PROC')
        _DISCIPLINE_INSTR   = ('INSTRUMENT', 'INSTR', 'I&C', 'CONTROL')
        _NEGATIVE_KEYWORDS  = {
            'penalty_legend': ('LEGEND', 'SYMBOLS', 'NOTES SHEET', 'TYPICAL'),
            'penalty_index':  ('DOCUMENT INDEX', 'DRAWING INDEX', 'DOC LIST', 'MASTER LIST', 'DRAWING LIST'),
            'penalty_report': ('REPORT', 'CALCULATION', 'STUDY', 'MINUTES'),
        }
        _TOP_DEFAULT      = 5
        _TOP_MAX          = 25
        _PAGE_SIZE_SCAN   = 200       # docs scanned from Wrench per project
        _FUZZY_MIN_SCORE  = 60        # 0-100 cutoff for project-name fuzzy match

        try:
            top_n = max(1, min(int(request.query_params.get('top', _TOP_DEFAULT)), _TOP_MAX))
        except (TypeError, ValueError):
            top_n = _TOP_DEFAULT

        order_no      = (request.query_params.get('order_no') or '').strip()
        project_name  = (request.query_params.get('project_name') or '').strip()
        drawing_hint  = (request.query_params.get('drawing_hint') or '').strip()
        # Optional sub-folder filter (comma-separated folder paths returned by
        # the /project-folders/ endpoint). Empty = no filter (legacy behaviour).
        folders_raw   = (request.query_params.get('folders') or '').strip()
        folder_filter = [
            f.strip().strip('/').lower()
            for f in folders_raw.split(',') if f and f.strip()
        ]

        if not order_no and not project_name:
            return Response(
                {'detail': 'Either order_no or project_name query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        matched_via = 'explicit' if order_no else 'none'

        # ── Fuzzy-match project_name → ORDER_NO via transmittal list ──────
        if not order_no and project_name:
            try:
                from difflib import SequenceMatcher
                tlist = wrench_service.get_transmittals(cfg, page=1, page_size=500)
                rows  = tlist.get('transmittals') or tlist.get('TransmittalList') or []
                best, best_score = None, 0
                pn = project_name.lower()
                for row in rows:
                    desc = (row.get('ORDER_DESCRIPTION') or row.get('ORDER_DESC') or '').lower()
                    if not desc:
                        continue
                    s = int(SequenceMatcher(None, pn, desc).ratio() * 100)
                    if s > best_score:
                        best_score, best = s, row
                if best and best_score >= _FUZZY_MIN_SCORE:
                    order_no    = str(best.get('ORDER_NO') or '').strip()
                    matched_via = f'fuzzy_project_name(score={best_score})'
            except Exception as exc:
                logger.warning('[Wrench] pid-recommendations: fuzzy match failed: %s', exc)

        if not order_no:
            return Response(
                {
                    'order_no':         None,
                    'matched_via':      matched_via,
                    'total_scanned':    0,
                    'recommendations':  [],
                    'note':             f'Could not resolve a Wrench ORDER_NO from project name "{project_name}".',
                },
                status=status.HTTP_200_OK,
            )

        # ── Fetch documents for this project ─────────────────────────────
        try:
            doc_payload = wrench_service.get_transmittal_documents(
                cfg, order_no=order_no, page=1, page_size=_PAGE_SIZE_SCAN,
            )
            documents = doc_payload.get('documents') or []
        except Exception as exc:
            logger.warning('[Wrench] pid-recommendations: doc fetch failed: %s', exc)
            documents = []

        # Soft-coded fallback: synthesize documents from transmittal rows
        # whenever the core fetch returned nothing (common on tenants where
        # DocumentSearch is empty and per-transmittal REST is unavailable).
        if not documents:
            try:
                fb = wrench_service.synthesize_documents_from_transmittal_list(
                    cfg, order_no=order_no, page=1, page_size=_PAGE_SIZE_SCAN,
                )
                documents = fb.get('documents') or []
            except Exception as fb_exc:  # noqa: BLE001
                logger.warning('[Wrench] pid-recommendations: transmittal fallback failed: %s', fb_exc)

        if not documents:
            return Response(
                {
                    'order_no':         order_no,
                    'matched_via':      matched_via,
                    'total_scanned':    0,
                    'recommendations':  [],
                    'note':             'No documents are indexed in Wrench for this project.',
                },
                status=status.HTTP_200_OK,
            )

        # ── Optional folder-scope filter (additive, soft-coded) ───────────
        # When the user picks one or more sub-folders in the AI Document
        # Assist panel, narrow the scoring pool to docs whose folder path
        # starts with one of the selected paths. The folder path is taken
        # from GENEALOGY_STRING when present, otherwise synthesised from
        # DISCIPLINE / DOC_TYPE — identical logic to /project-folders/.
        if folder_filter:
            _FB_L1 = ('DISCIPLINE', 'Discipline', 'DISC')
            _FB_L2 = ('DOC_TYPE', 'DocType', 'TRANS_TYPE_CODE',
                      'TransTypeCode', 'DOC_CATEGORY', 'DocCategory')

            def _doc_folder_path(doc):
                segs = _extract_genealogy_segments(doc, order_no) or []
                if not segs:
                    def _fne(fields):
                        for f in fields:
                            v = doc.get(f)
                            if v and str(v).strip():
                                return str(v).strip()
                        return ''
                    lvl1 = _fne(_FB_L1) or 'General'
                    lvl2 = _fne(_FB_L2)
                    segs = [lvl1] + ([lvl2] if lvl2 else [])
                return '/'.join(s.lower() for s in segs)
            scoped = []
            for d in documents:
                fp = _doc_folder_path(d)
                if any(fp == f or fp.startswith(f + '/') for f in folder_filter):
                    scoped.append(d)
            documents = scoped

        # ── Score every document with soft-coded heuristics ───────────────
        hint_tokens = [t.lower() for t in drawing_hint.replace('-', ' ').replace('_', ' ').split() if len(t) >= 3]

        def _score(doc):
            text = ' '.join([
                str(doc.get('DOC_NO') or ''),
                str(doc.get('DOC_DESCRIPTION') or ''),
                str(doc.get('DOC_TYPE') or ''),
                str(doc.get('GENEALOGY_STRING') or ''),
            ]).upper()
            disc = (doc.get('DISCIPLINE') or '').upper()
            doc_no_upper = str(doc.get('DOC_NO') or '').upper()
            file_ext = (doc.get('FILE_EXT') or doc.get('EXTENSION') or '').lower().lstrip('.')
            if not file_ext and '.' in doc_no_upper:
                file_ext = doc_no_upper.rsplit('.', 1)[-1].lower()

            score   = 0
            reasons = []

            if any(k in text for k in _PID_KEYWORD_STRONG):
                score += _SCORE_WEIGHTS['pid_keyword_strong']
                reasons.append('Strong P&ID keyword match')
            elif any(k in text for k in _PID_KEYWORD_LOOSE):
                score += _SCORE_WEIGHTS['pid_keyword_loose']
                reasons.append('P&ID keyword match')

            if any(k in disc for k in _DISCIPLINE_PROCESS):
                score += _SCORE_WEIGHTS['discipline_process']
                reasons.append(f'Process discipline ({disc})')
            elif any(k in disc for k in _DISCIPLINE_INSTR):
                score += _SCORE_WEIGHTS['discipline_instr']
                reasons.append(f'Instrument discipline ({disc})')

            if file_ext == 'pdf':
                score += _SCORE_WEIGHTS['pdf_extension']
                reasons.append('PDF format')
            elif file_ext == 'dwg':
                score += _SCORE_WEIGHTS['dwg_extension']
                reasons.append('DWG format')

            for tok in hint_tokens:
                if tok.upper() in text:
                    score += _SCORE_WEIGHTS['drawing_hint_token']
                    reasons.append(f'Hint match: "{tok}"')

            # Revision recency — soft, prefer higher numeric revision
            rev = str(doc.get('REVISION') or doc.get('REV') or '').strip()
            if rev:
                try:
                    rev_num = int(''.join(c for c in rev if c.isdigit()) or 0)
                    if rev_num >= 1:
                        score += _SCORE_WEIGHTS['latest_revision']
                        reasons.append(f'Revision {rev}')
                except Exception:
                    pass

            for label, words in _NEGATIVE_KEYWORDS.items():
                if any(w in text for w in words):
                    score += _SCORE_WEIGHTS[label]
                    reasons.append(f'Penalty: {label.replace("penalty_", "")}')
                    break

            return score, reasons, file_ext

        scored = []
        for d in documents:
            s, why, ext = _score(d)
            if s <= 0:
                continue   # not even loosely a P&ID
            doc_no = str(d.get('DOC_NO') or '').strip()
            idoc_id = str(d.get('IDOC_ID') or d.get('iDoc_Id') or d.get('iDocId') or '').strip()
            scored.append({
                'doc_no':           doc_no,
                'doc_description':  d.get('DOC_DESCRIPTION') or '',
                'discipline':       d.get('DISCIPLINE') or '',
                'revision':         d.get('REVISION') or d.get('REV') or '',
                'file_ext':         ext,
                'score':            int(min(s, 100)),
                'reasons':          why,
                'download_url':     (
                    f'/api/v1/wrench/sync/document-download/'
                    f'?idoc_id={idoc_id}&doc_no={doc_no}'
                ) if (idoc_id or doc_no) else None,
            })

        scored.sort(key=lambda r: r['score'], reverse=True)

        return Response(
            {
                'order_no':         order_no,
                'matched_via':      matched_via,
                'total_scanned':    len(documents),
                'recommendations':  scored[:top_n],
                'note':             (
                    f'AI ranked {len(scored)} candidate document(s) for project {order_no} using soft-coded '
                    f'pattern-based scoring (no LLM call). Tune weights in pid_recommendations() if needed.'
                ),
            },
            status=status.HTTP_200_OK,
        )


    def document_download(self, request):
        """
        Proxy a Wrench document file download through the backend (auth handled server-side).
        GET /api/v1/wrench/sync/document-download/?idoc_id=<IDOC_ID>&doc_no=<DOC_NO>

        Returns:
          - Streamed binary file (application/octet-stream or PDF) with Content-Disposition, OR
          - JSON { download_url } when Wrench returns a redirect URL instead of file bytes.
        """
        from django.http import HttpResponse

        idoc_id = request.query_params.get('idoc_id', '').strip()
        doc_no  = request.query_params.get('doc_no', '').strip() or None

        if not idoc_id:
            return Response(
                {'detail': 'idoc_id query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response({'detail': 'No active Wrench configuration.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = wrench_service.download_document(cfg, idoc_id=idoc_id, doc_no=doc_no)
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)
        except Exception as exc:
            logger.error('[Wrench] document_download failed (idoc_id=%s): %s', idoc_id, exc, exc_info=True)
            return Response(
                {'detail': 'Document download failed. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # When Wrench returns a redirect URL, pass it to the client
        if result.get('url'):
            return Response({'download_url': result['url']}, status=status.HTTP_200_OK)

        # Stream binary content back to the browser
        content      = result.get('content', b'')
        filename     = result.get('filename', f'{idoc_id}.bin')
        content_type = result.get('content_type', 'application/octet-stream')

        http_resp = HttpResponse(content, content_type=content_type)
        http_resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        http_resp['Content-Length']      = str(len(content))
        return http_resp

    @action(detail=False, methods=['post'], url_path='pid-cross-search',
            permission_classes=[IsAuthenticated])
    def pid_cross_search(self, request):
        """
        AI-powered Wrench DMS search scoped to a P&ID drawing context.

        Uses the drawing name, extracted tags, and finding categories to
        automatically build smart Wrench queries, then ranks results with
        GPT-4o-mini (falls back to heuristic scoring when OpenAI unavailable).

        POST /api/v1/wrench/sync/pid-cross-search/
        Body (all optional):
          drawing_name  – raw file name of the P&ID  (e.g. "3500-PL-PID-001-Rev3.pdf")
          tags          – list of tag strings found on the drawing
          issues        – list of {category, severity} finding summaries
          discipline    – explicit discipline override (e.g. "PROCESS")
          free_text     – optional user-typed search query
          page          – page number (default 1)
          page_size     – results per page (default 30, max 100)

        Response:
          { documents, total, ai_powered, query_used }
        """
        # Soft-coded: max docs sent to AI for ranking (keeps prompt within token budget)
        _MAX_AI_RANK_DOCS = 40
        # Soft-coded: default/max page sizes for this endpoint
        _DEFAULT_PAGE_SIZE = 30
        _MAX_PAGE_SIZE     = 100

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Ask an admin to configure the Wrench integration.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        drawing_name = (request.data.get('drawing_name') or '').strip()
        tags         = request.data.get('tags') or []
        issues       = request.data.get('issues') or []
        discipline   = (request.data.get('discipline') or '').strip() or None
        free_text    = (request.data.get('free_text') or '').strip() or None
        page         = int(request.data.get('page', 1))
        page_size    = min(int(request.data.get('page_size', _DEFAULT_PAGE_SIZE)), _MAX_PAGE_SIZE)

        # ── Build smart query context from P&ID signals ────────────────────────
        query_used = wrench_service.build_pid_search_query(
            drawing_name=drawing_name,
            tags=tags,
            issues=issues,
            discipline=discipline,
            free_text=free_text,
        )

        # ── Fetch documents from Wrench (REST first, SearchObject fallback) ────
        raw_docs = []
        total    = 0
        try:
            result   = wrench_service.get_document_list(
                cfg,
                page=page,
                page_size=_MAX_AI_RANK_DOCS,   # fetch more so AI can rank effectively
                discipline=query_used.get('discipline'),
                doc_no=query_used.get('doc_no'),
            )
            raw_docs = result.get('documents', [])
            total    = result.get('total', len(raw_docs))
        except Exception as rest_exc:
            logger.info('[Wrench/PID] REST list failed (%s), trying SearchObject', rest_exc)
            try:
                result = wrench_service.search_documents(
                    cfg,
                    page=page,
                    page_size=_MAX_AI_RANK_DOCS,
                    discipline=query_used.get('discipline'),
                    doc_no=query_used.get('doc_no'),
                )
                raw_docs = result.get('documents', [])
                total    = result.get('total', len(raw_docs))
            except (RuntimeError, Exception) as search_exc:
                # ── Fallback: expand transmittals to collect linked documents ──────────
                # Triggered when both GetDocumentList (REST) and DocumentSearch/SearchObject
                # return 404 — common on Wrench installations that expose only the
                # Transmittal and AccessControl namespaces.
                logger.info(
                    '[Wrench/PID] SearchObject unavailable (%s). '
                    'Attempting transmittal-expansion fallback.', search_exc,
                )
                try:
                    expand_result = wrench_service.get_documents_from_transmittals(cfg)
                    raw_docs = expand_result.get('documents', [])
                    total    = expand_result.get('total', len(raw_docs))
                    logger.info(
                        '[Wrench/PID] Transmittal expansion yielded %d unique documents.', total,
                    )
                except http_lib.exceptions.ConnectionError:
                    return Response(
                        {'detail': 'Unable to reach Wrench server.'},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )
                except Exception as expand_exc:
                    logger.warning('[Wrench/PID] All document sources failed: %s', expand_exc)
                    # Return a graceful empty result — panel loads without error banner
                    return Response(
                        {
                            'documents':   [],
                            'total':       0,
                            'ai_powered':  False,
                            'query_used':  query_used,
                            'warning':     (
                                'No document list endpoint is available on this Wrench '
                                'installation. Configure a Document Search Service URL in '
                                'Admin → Wrench → Configuration, or contact your Wrench admin.'
                            ),
                        },
                        status=status.HTTP_200_OK,
                    )

        # ── AI-rank the results by relevance to this P&ID context ─────────────
        ai_powered = False
        try:
            raw_docs, ai_powered = wrench_service.ai_rank_pid_documents(
                documents=raw_docs[:_MAX_AI_RANK_DOCS],
                drawing_name=drawing_name,
                tags=tags[:20],
                issues=issues[:15],
                discipline=discipline,
            )
        except Exception as ai_exc:
            logger.warning('[Wrench/PID] AI ranking failed, using heuristic: %s', ai_exc)

        # Apply final page slice after ranking
        start      = (page - 1) * page_size
        page_slice = raw_docs[start: start + page_size]

        return Response({
            'documents':  page_slice,
            'total':      total,
            'ai_powered': ai_powered,
            'query_used': query_used,
        }, status=status.HTTP_200_OK)


class WrenchS3SyncViewSet(viewsets.ViewSet):
    """
    Wrench → RADAI → AWS S3 export jobs.

    GET  /api/v1/wrench/s3-sync/             – list recent jobs
    POST /api/v1/wrench/s3-sync/start/       – start a batch or real-time job
    GET  /api/v1/wrench/s3-sync/<id>/        – retrieve job detail
    POST /api/v1/wrench/s3-sync/<id>/stop/   – stop a real-time job
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    # Soft-coded: allowed values validated here so the frontend can rely on them
    _VALID_MODES    = [WrenchS3SyncJob.MODE_BATCH, WrenchS3SyncJob.MODE_REALTIME]
    _VALID_ENTITIES = [
        WrenchS3SyncJob.ENTITY_TRANSMITTALS,
        WrenchS3SyncJob.ENTITY_DOCUMENTS,
        WrenchS3SyncJob.ENTITY_ALL,
    ]
    _DEFAULT_S3_PREFIX = 'wrench/'

    # Soft-coded: when the Celery broker is unreachable on Railway, .apply_async()
    # raises kombu.exceptions.OperationalError. We surface a clean JSON 503 so the
    # frontend can show a useful message instead of a generic HTML 500 page.
    @staticmethod
    def _safe_dispatch(task_func, *, args, job):
        """Dispatch a Celery task and translate broker errors into clean responses."""
        try:
            return task_func.apply_async(args=args), None
        except Exception as exc:                                   # noqa: BLE001
            logger.exception('[S3 View] dispatch failed for job_id=%s task=%s: %s',
                             job.id, getattr(task_func, 'name', task_func), exc)
            # Mark the job failed so it doesn't sit in pending forever
            job.status = WrenchS3SyncJob.STATUS_FAILED
            job.error_message = f'Celery dispatch failed: {exc}'[:500]
            try:
                job.save(update_fields=['status', 'error_message', 'updated_at'])
            except Exception:                                      # noqa: BLE001
                pass
            return None, Response(
                {'detail': 'Could not enqueue the export task. The task broker '
                          f'(Redis) appears unreachable. ({exc})',
                 'job_id': job.id, 'job_status': job.status},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def list(self, request):
        jobs = WrenchS3SyncJob.objects.select_related('triggered_by').order_by('-started_at')[:50]
        return Response(WrenchS3SyncJobSerializer(jobs, many=True).data)

    def retrieve(self, request, pk=None):
        try:
            job = WrenchS3SyncJob.objects.get(pk=pk)
        except WrenchS3SyncJob.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(WrenchS3SyncJobSerializer(job).data)

    @action(detail=False, methods=['post'], url_path='start')
    def start(self, request):
        """
        Start an S3 export job.

        Request body:
          mode        – 'batch' | 'realtime'   (default: 'batch')
          entity_type – 'transmittals' | 'documents' | 'all'  (default: 'transmittals')
          s3_prefix   – optional S3 key prefix  (default: 'wrench/')
        """
        from .tasks import wrench_s3_batch_export, wrench_s3_realtime_tick

        mode        = request.data.get('mode', WrenchS3SyncJob.MODE_BATCH)
        entity_type = request.data.get('entity_type', WrenchS3SyncJob.ENTITY_TRANSMITTALS)
        s3_prefix   = request.data.get('s3_prefix', self._DEFAULT_S3_PREFIX) or self._DEFAULT_S3_PREFIX

        if mode not in self._VALID_MODES:
            return Response(
                {'detail': f'Invalid mode. Choose from {self._VALID_MODES}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if entity_type not in self._VALID_ENTITIES:
            return Response(
                {'detail': f'Invalid entity_type. Choose from {self._VALID_ENTITIES}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration. Configure the integration first.'},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        # Prevent duplicate in-progress real-time jobs
        if mode == WrenchS3SyncJob.MODE_REALTIME:
            running = WrenchS3SyncJob.objects.filter(
                mode=WrenchS3SyncJob.MODE_REALTIME,
                status=WrenchS3SyncJob.STATUS_IN_PROGRESS,
            ).first()
            if running:
                return Response(
                    {'detail': f'A real-time job (id={running.id}) is already running. Stop it first.'},
                    status=status.HTTP_409_CONFLICT,
                )

        job = WrenchS3SyncJob.objects.create(
            config=cfg,
            triggered_by=request.user,
            mode=mode,
            entity_type=entity_type,
            s3_prefix=s3_prefix,
            status=WrenchS3SyncJob.STATUS_PENDING,
        )

        # Dispatch async — never block the request. Broker failure -> clean 503.
        if mode == WrenchS3SyncJob.MODE_BATCH:
            task, err = self._safe_dispatch(wrench_s3_batch_export, args=[job.id], job=job)
        else:
            task, err = self._safe_dispatch(wrench_s3_realtime_tick, args=[job.id], job=job)
        if err is not None:
            return err

        job.celery_task_id = task.id
        job.save(update_fields=['celery_task_id', 'updated_at'])

        create_audit_log(
            user=request.user,
            action='execute',
            resource_type='WrenchS3SyncJob',
            resource_id=None,
            resource_repr=str(job),
            metadata={'job_id': job.id, 'mode': mode, 'entity_type': entity_type},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        logger.info('[S3 View] Dispatched %s job id=%d task=%s', mode, job.id, task.id)
        return Response(WrenchS3SyncJobSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='stop')
    def stop(self, request, pk=None):
        """Stop a running real-time job."""
        from .s3_service import stop_realtime_job

        try:
            job = WrenchS3SyncJob.objects.get(pk=pk)
        except WrenchS3SyncJob.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if job.status not in (WrenchS3SyncJob.STATUS_IN_PROGRESS, WrenchS3SyncJob.STATUS_PENDING):
            return Response(
                {'detail': f'Job is not running (status={job.status}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stop_realtime_job(job)
        create_audit_log(
            user=request.user,
            action='update',
            resource_type='WrenchS3SyncJob',
            resource_id=None,
            resource_repr=f'Stop job {pk}',
            metadata={'job_id': job.id},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return Response(WrenchS3SyncJobSerializer(job).data)

    # ────────────────────────────────────────────────────────────────────
    # PROJECT DOCUMENT MIRROR (Wrench → S3, actual file bytes)
    # Additive: reuses existing job model with entity_type='documents' and
    # stores the ORDER_NO inside job.job_details. No core logic modified.
    # ────────────────────────────────────────────────────────────────────

    # Soft-coded label for project-export jobs in audit logs.
    _PROJECT_EXPORT_LABEL = 'project_documents'

    @action(detail=False, methods=['post'], url_path='project-export')
    def project_export(self, request):
        """
        Start a Wrench → S3 mirror for an entire project (ORDER_NO).

        Body:
          order_no  – required (e.g. "5900620")
          mode      – 'batch' | 'realtime'   (default: 'batch')
          s3_prefix – optional                (default: 'wrench/')
        """
        from .tasks import wrench_s3_project_export, wrench_s3_project_export_tick

        order_no  = str(request.data.get('order_no') or '').strip()
        mode      = request.data.get('mode', WrenchS3SyncJob.MODE_BATCH)
        s3_prefix = request.data.get('s3_prefix', self._DEFAULT_S3_PREFIX) or self._DEFAULT_S3_PREFIX

        if not order_no:
            return Response({'detail': 'order_no is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if mode not in self._VALID_MODES:
            return Response({'detail': f'Invalid mode. Choose from {self._VALID_MODES}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        # Avoid duplicate running export for the same project
        running = WrenchS3SyncJob.objects.filter(
            status=WrenchS3SyncJob.STATUS_IN_PROGRESS,
            entity_type=WrenchS3SyncJob.ENTITY_DOCUMENTS,
            job_details__order_no=order_no,
        ).first()
        if running:
            return Response(
                {'detail': f'Project export already running (job_id={running.id}).',
                 'job_id': running.id},
                status=status.HTTP_409_CONFLICT,
            )

        job = WrenchS3SyncJob.objects.create(
            config=cfg,
            triggered_by=request.user,
            mode=mode,
            entity_type=WrenchS3SyncJob.ENTITY_DOCUMENTS,
            s3_prefix=s3_prefix,
            status=WrenchS3SyncJob.STATUS_PENDING,
            job_details={'order_no': order_no, 'kind': self._PROJECT_EXPORT_LABEL},
        )

        if mode == WrenchS3SyncJob.MODE_BATCH:
            task, err = self._safe_dispatch(wrench_s3_project_export, args=[job.id], job=job)
        else:
            task, err = self._safe_dispatch(wrench_s3_project_export_tick, args=[job.id], job=job)
        if err is not None:
            return err

        job.celery_task_id = task.id
        job.save(update_fields=['celery_task_id', 'updated_at'])

        create_audit_log(
            user=request.user,
            action='execute',
            resource_type='WrenchS3SyncJob',
            resource_id=None,
            resource_repr=str(job),
            metadata={'job_id': job.id, 'mode': mode,
                      'kind': self._PROJECT_EXPORT_LABEL, 'order_no': order_no},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )

        logger.info('[S3 ProjDocs] Dispatched %s job=%d order_no=%s task=%s',
                    mode, job.id, order_no, task.id)
        return Response(WrenchS3SyncJobSerializer(job).data,
                        status=status.HTTP_201_CREATED)

    # ────────────────────────────────────────────────────────────────────
    # LIBRARY MIRROR WATCHER — keep S3 in sync with Wrench library in realtime
    # ────────────────────────────────────────────────────────────────────

    _LIBRARY_WATCHER_LABEL = 'library_watcher'

    @action(detail=False, methods=['post'], url_path='library-watch/start')
    def library_watch_start(self, request):
        """
        Start a continuous Wrench → S3 library-mirror watcher for a project.

        Body:
          order_no  – required
          s3_prefix – optional (default 'wrench/')
        """
        from .tasks import wrench_s3_library_watcher_tick

        order_no  = str(request.data.get('order_no') or '').strip()
        s3_prefix = request.data.get('s3_prefix', self._DEFAULT_S3_PREFIX) or self._DEFAULT_S3_PREFIX

        if not order_no:
            return Response({'detail': 'order_no is required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        cfg = WrenchConfig.objects.filter(is_active=True).first()
        if not cfg:
            return Response(
                {'detail': 'No active Wrench configuration.'},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        running = WrenchS3SyncJob.objects.filter(
            status=WrenchS3SyncJob.STATUS_IN_PROGRESS,
            entity_type=WrenchS3SyncJob.ENTITY_DOCUMENTS,
            job_details__order_no=order_no,
            job_details__kind=self._LIBRARY_WATCHER_LABEL,
        ).first()
        if running:
            return Response(
                {'detail': f'Library watcher already running (job_id={running.id}).',
                 'job_id': running.id},
                status=status.HTTP_409_CONFLICT,
            )

        job = WrenchS3SyncJob.objects.create(
            config=cfg,
            triggered_by=request.user,
            mode=WrenchS3SyncJob.MODE_REALTIME,
            entity_type=WrenchS3SyncJob.ENTITY_DOCUMENTS,
            s3_prefix=s3_prefix,
            status=WrenchS3SyncJob.STATUS_PENDING,
            job_details={'order_no': order_no, 'kind': self._LIBRARY_WATCHER_LABEL},
        )

        task, err = self._safe_dispatch(wrench_s3_library_watcher_tick, args=[job.id], job=job)
        if err is not None:
            return err
        job.celery_task_id = task.id
        job.save(update_fields=['celery_task_id', 'updated_at'])

        create_audit_log(
            user=request.user, action='execute',
            resource_type='WrenchS3SyncJob', resource_id=None,
            resource_repr=str(job),
            metadata={'job_id': job.id, 'kind': self._LIBRARY_WATCHER_LABEL,
                      'order_no': order_no},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        logger.info('[S3 LibWatcher] Started job=%d order_no=%s', job.id, order_no)
        return Response(WrenchS3SyncJobSerializer(job).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='library-watch/status')
    def library_watch_status(self, request):
        """
        List active library-mirror watchers, optionally filtered by order_no.
        """
        order_no = (request.query_params.get('order_no') or '').strip()
        qs = WrenchS3SyncJob.objects.filter(
            job_details__kind=self._LIBRARY_WATCHER_LABEL,
        )
        if order_no:
            qs = qs.filter(job_details__order_no=order_no)
        qs = qs.order_by('-started_at')[:20]
        return Response(WrenchS3SyncJobSerializer(qs, many=True).data)
