"""DRF views for Instrument Tools (IO List / Cable Block / Cable Schedule).

Each endpoint accepts either:
  • multipart upload (`file` field) — parsed server-side; OR
  • JSON body with `rows: [...]` — already-parsed rows.

A `mode` query/form param selects 'generate' (default) or 'qc'.
The response is always JSON; if `download=1` is supplied and the request
succeeded, an XLSX of the result rows is returned as a binary attachment.
"""
from __future__ import annotations

import base64
import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as svc
from . import services_ai as ai_svc
from . import ai_features
from . import ai_smart_parser

logger = logging.getLogger(__name__)


def _ai_enabled() -> bool:
    """Return True if any AI capability is currently enabled."""
    try:
        return any(ai_features.all_flags().values())
    except Exception:
        return False

# ─── Soft-coded request-handling constants ───────────────────────────────────
_DEFAULT_MODE = svc.MODE_GENERATE
_FILE_FIELD   = 'file'
_ROWS_FIELD   = 'rows'
_MODE_FIELD   = 'mode'
_DOWNLOAD_FLAG = 'download'


def _resolve_mode(request) -> str:
    raw = (request.data.get(_MODE_FIELD)
           or request.query_params.get(_MODE_FIELD)
           or _DEFAULT_MODE)
    mode = str(raw).strip().lower()
    if mode not in svc.SUPPORTED_MODES:
        return _DEFAULT_MODE
    return mode


def _resolve_rows(request) -> list[dict]:
    """Extract rows from a multipart upload or JSON body."""
    uploaded = request.FILES.get(_FILE_FIELD)
    if uploaded is not None:
        # Use the smart (multi-format) parser when AI is enabled; otherwise
        # fall back to the deterministic spreadsheet parser.
        if _ai_enabled():
            try:
                return ai_smart_parser.parse(uploaded)
            except ValueError:
                raise
            except Exception:
                logger.exception('Smart parser failed; falling back to core parser.')
                # Reset the stream if possible before retrying with the core.
                try:
                    uploaded.seek(0)
                except Exception:
                    pass
        return svc.parse_uploaded_table(uploaded)
    rows = request.data.get(_ROWS_FIELD)
    if isinstance(rows, list):
        return rows
    return []


def _wants_download(request) -> bool:
    raw = (request.data.get(_DOWNLOAD_FLAG)
           or request.query_params.get(_DOWNLOAD_FLAG)
           or '')
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


class _BaseToolView(APIView):
    """Shared POST logic — concrete subclasses set `tool`."""
    permission_classes = [IsAuthenticated]
    parser_classes     = [JSONParser, MultiPartParser, FormParser]
    tool: str = ''

    def post(self, request, *args, **kwargs):
        try:
            mode = _resolve_mode(request)
            rows = _resolve_rows(request)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:                                      # noqa: BLE001
            logger.exception('Failed to parse instrument-tool input')
            return Response({'detail': f'Could not read input: {exc}'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            engine = ai_svc if _ai_enabled() else svc
            if mode == svc.MODE_QC:
                result = engine.run_qc(self.tool, rows)
                # For consistency with the generator response, expose rows + columns.
                result['columns'] = list(svc._TOOL_SCHEMAS[self.tool].keys())
                result['rows']    = result.pop('normalised', [])
            else:
                result = engine.run_generator(self.tool, rows)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:                                      # noqa: BLE001
            logger.exception('Instrument-tool execution failed (tool=%s mode=%s)', self.tool, mode)
            return Response({'detail': f'Processing failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if _wants_download(request) and result.get('rows'):
            xlsx = svc.rows_to_xlsx_bytes(self.tool, result['rows'])
            result['download'] = {
                'filename':     f'{self.tool}.xlsx',
                'content_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'base64':       base64.b64encode(xlsx).decode('ascii'),
            }

        return Response(result, status=status.HTTP_200_OK)


class IOListView(_BaseToolView):
    tool = svc.TOOL_IO_LIST


class CableBlockDiagramView(_BaseToolView):
    tool = svc.TOOL_CABLE_BLOCK


class CableScheduleView(_BaseToolView):
    tool = svc.TOOL_CABLE_SCHED


class MetaView(APIView):
    """Expose the supported tools, modes and canonical schemas for the UI."""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return Response({
            'tools': list(svc.SUPPORTED_TOOLS),
            'modes': list(svc.SUPPORTED_MODES),
            'schemas': {
                t: list(svc._TOOL_SCHEMAS[t].keys()) for t in svc.SUPPORTED_TOOLS
            },
            'ai': {
                'enabled': _ai_enabled(),
                'flags':   ai_features.all_flags(),
            },
        })
