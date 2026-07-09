from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from . import config as ga_cfg
from .service import fetch_realtime_snapshot


_permission = [IsAdminUser] if ga_cfg.GA4_REQUIRE_ADMIN else [IsAuthenticated]


@api_view(['GET'])
@permission_classes(_permission)
def realtime_snapshot(request):
    """GET /api/v1/marketing-analytics/realtime/

    Returns a small JSON snapshot of GA4 real-time activity for the
    dashboard widget. Always responds 200 — credential / API issues are
    surfaced via `configured` and `error` fields so the UI can render a
    helpful setup hint instead of crashing.
    """
    return Response(fetch_realtime_snapshot())
