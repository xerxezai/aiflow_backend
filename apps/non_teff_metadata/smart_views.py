"""
Non-TEFF Smart Features - DRF views.

Additive endpoints — none touch the existing extraction pipeline. All accept
the already-extracted rows (sent by the frontend) and return analytical
insights. Stateless, idempotent, safe to call from any UI panel.

URL prefix mounted in urls.py:

    POST /api/v1/non-teff/smart/confidence/
    POST /api/v1/non-teff/smart/repair/
    POST /api/v1/non-teff/smart/consistency/
    POST /api/v1/non-teff/smart/query/
    POST /api/v1/non-teff/smart/classify/
    POST /api/v1/non-teff/smart/auto-link/
    POST /api/v1/non-teff/smart/timeline/
    POST /api/v1/non-teff/smart/bulk-suggest/
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services import smart_features

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SOFT-CODED safety caps — protect the service from giant payloads.
# ---------------------------------------------------------------------------

MAX_ITEMS_PER_REQUEST = 1000
MAX_EXCERPT_CHARS     = 8000


def _coerce_items(payload: Any) -> List[Dict[str, Any]]:
    items = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in items[:MAX_ITEMS_PER_REQUEST]:
        if isinstance(r, dict):
            out.append(r)
    return out


def _err(msg: str, code: int = http_status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({'error': msg}, status=code)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_confidence(request):
    items = _coerce_items(request.data)
    try:
        return Response(smart_features.compute_confidence_scores(items))
    except Exception as exc:
        logger.exception('smart_confidence failed: %s', exc)
        return _err('Confidence scoring failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_repair(request):
    row = request.data.get('row') if isinstance(request.data, dict) else None
    if not isinstance(row, dict):
        return _err('Body must include "row": <object>.')
    excerpt = (request.data.get('text_excerpt') or '')[:MAX_EXCERPT_CHARS]
    try:
        return Response(smart_features.repair_row(row=row, text_excerpt=excerpt))
    except Exception as exc:
        logger.exception('smart_repair failed: %s', exc)
        return _err('Repair suggestions failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_consistency(request):
    items = _coerce_items(request.data)
    try:
        return Response(smart_features.detect_consistency_issues(items))
    except Exception as exc:
        logger.exception('smart_consistency failed: %s', exc)
        return _err('Consistency check failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_query(request):
    query = (request.data.get('query') if isinstance(request.data, dict) else None) or ''
    items = _coerce_items(request.data)
    try:
        return Response(smart_features.translate_nl_query(query=query, items=items))
    except Exception as exc:
        logger.exception('smart_query failed: %s', exc)
        return _err('Query translation failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_classify(request):
    items = _coerce_items(request.data)
    try:
        return Response(smart_features.classify_documents(items))
    except Exception as exc:
        logger.exception('smart_classify failed: %s', exc)
        return _err('Classification failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_auto_link(request):
    items = _coerce_items(request.data)
    try:
        return Response(smart_features.auto_link_tags(items))
    except Exception as exc:
        logger.exception('smart_auto_link failed: %s', exc)
        return _err('Auto-link failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_timeline(request):
    items = _coerce_items(request.data)
    try:
        return Response(smart_features.build_revision_timeline(items))
    except Exception as exc:
        logger.exception('smart_timeline failed: %s', exc)
        return _err('Timeline build failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_bulk_suggest(request):
    items = _coerce_items(request.data)
    sel = request.data.get('selected_indexes') if isinstance(request.data, dict) else None
    if sel is not None and not isinstance(sel, list):
        return _err('"selected_indexes" must be a list of integers.')
    try:
        return Response(smart_features.suggest_bulk_edits(items, selected_indexes=sel))
    except Exception as exc:
        logger.exception('smart_bulk_suggest failed: %s', exc)
        return _err('Bulk-edit suggestions failed.', http_status.HTTP_500_INTERNAL_SERVER_ERROR)
