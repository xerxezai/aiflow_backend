"""
Cable Block Diagram Views
-------------------------
REST endpoints powering the rebuilt
`/engineering/instrument/datasheet/cable-block-diagram` page.

Endpoints (mounted under /api/v1/pid/ via backend/config/urls.py)
=========
  POST  cable-block-diagram/analyze/                  → extract_cable_block_diagram
  GET   cable-block-diagram/download-excel/<id>/      → download_cable_block_excel

`extract_cable_block_diagram` reuses `InstrumentIndexService.extract_instruments`
for the raw AI-OCR pass, then funnels the result through
`cable_block_service.build_cable_block_rows` to produce ADNOC-aligned rows
(JB numbers, multicore tiers, cabinet allocations).  Excel is generated
synchronously and cached for the follow-up download call.
"""

import os
import uuid
import logging

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .cable_block_service import (
    CABLE_BLOCK_COLUMNS,
    build_cable_block_rows,
    generate_excel,
)
from .instrument_index_service import InstrumentIndexService

logger = logging.getLogger(__name__)

# How long generated Excel bytes survive in Django cache (seconds)
EXCEL_CACHE_TTL = 600  # 10 minutes
EXCEL_CACHE_KEY = "cable_block_excel_{upload_id}"

# Soft-coded URL prefix — must match the include() in backend/config/urls.py
# (`path('api/v1/pid/', include('apps.pid_analysis.urls'))`).  Keeping this in
# one place avoids the kind of mount-path drift that produced the 404 reported
# on the cable-block-diagram page.
CABLE_BLOCK_URL_PREFIX = "/api/v1/pid/cable-block-diagram"
EXCEL_DOWNLOAD_URL_TEMPLATE = CABLE_BLOCK_URL_PREFIX + "/download-excel/{upload_id}/"


def _get_service():
    return InstrumentIndexService()


@api_view(["POST"])
@permission_classes([AllowAny])
def extract_cable_block_diagram(request):
    """POST /api/v1/pid/cable-block-diagram/analyze/

    Multipart form data:
        pid_file        — uploaded PDF (required)
        legend_file     — optional legend sheet PDF
        drawing_number  — drawing no. string
        drawing_title   — drawing title string
        revision        — revision string (default "0")
        project_name    — project name
        project_unit    — plant unit prefix (e.g. "15"); falls back to default
    """
    pid_file = request.FILES.get("pid_file")
    legend_file = request.FILES.get("legend_file")
    if not pid_file:
        return Response({"error": "No P&ID file uploaded. Please attach a PDF."}, status=400)
    if not pid_file.name.lower().endswith(".pdf"):
        return Response({"error": "Only PDF files are supported."}, status=400)
    if legend_file and not legend_file.name.lower().endswith(".pdf"):
        return Response({"error": "Legend sheet must be a PDF file."}, status=400)

    pid_bytes = pid_file.read()
    filename_stem = os.path.splitext(pid_file.name)[0]

    drawing_info = {
        "drawing_number": request.data.get("drawing_number") or filename_stem,
        "drawing_title":  request.data.get("drawing_title")  or "",
        "revision":       request.data.get("revision")       or "0",
        "project_name":   request.data.get("project_name")   or "",
        "pid_no":         request.data.get("drawing_number") or filename_stem,
        "project_category": (request.data.get("project_category") or "default").strip().lower(),
        "project_code":   request.data.get("project_code")   or "",
        "project_client": request.data.get("project_client") or "",
        "plant_unit":     (request.data.get("plant_unit") or request.data.get("project_unit") or "").strip(),
        "ies_area":       (request.data.get("ies_area") or "").strip(),
    }

    logger.info(
        "[CableBlock] Received '%s' (%.1f KB) — drawing: %s",
        pid_file.name, len(pid_bytes) / 1024, drawing_info["drawing_number"],
    )

    service = _get_service()
    legend_context_override = None
    if legend_file:
        legend_bytes = legend_file.read()
        legend_context_override = service.build_legend_context_from_uploaded_file(
            legend_bytes, legend_file.name,
        )
        drawing_info["legend_sheet_name"] = legend_file.name

    instruments = service.extract_instruments(
        pid_bytes, drawing_info,
        legend_context_override=legend_context_override,
    )

    if not instruments:
        logger.warning("[CableBlock] Extractor returned 0 instruments.")
        return Response({
            "success": False,
            "error": "No instruments could be extracted from the drawing.",
            "instruments": [],
            "rows": [],
            "total_instruments": 0,
            "total_rows": 0,
        }, status=200)

    rows = build_cable_block_rows(
        instruments,
        plant_unit=drawing_info["plant_unit"] or None,
        ies_area=drawing_info["ies_area"] or None,
        pid_no=drawing_info["pid_no"],
        rev=drawing_info["revision"],
    )

    # ── Excel cache for follow-up download ─────────────────────────────────
    upload_id = str(uuid.uuid4())
    excel_url = None
    try:
        excel_bytes = generate_excel(rows, drawing_info)
        cache.set(EXCEL_CACHE_KEY.format(upload_id=upload_id),
                  excel_bytes, timeout=EXCEL_CACHE_TTL)
        excel_url = EXCEL_DOWNLOAD_URL_TEMPLATE.format(upload_id=upload_id)
        logger.info("[CableBlock] Excel cached: %s (%d rows)", upload_id, len(rows))
    except Exception as exc:
        logger.error("[CableBlock] Excel generation failed: %s", exc, exc_info=True)

    return Response({
        "success": True,
        "upload_id": upload_id,
        "drawing_info": drawing_info,
        "columns": CABLE_BLOCK_COLUMNS,
        "instruments": instruments,
        "rows": rows,
        "total_instruments": len(instruments),
        "total_rows": len(rows),
        "excel_url": excel_url,
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def download_cable_block_excel(request, upload_id):
    """GET /api/v1/pid/cable-block-diagram/download-excel/<upload_id>/"""
    excel_bytes = cache.get(EXCEL_CACHE_KEY.format(upload_id=upload_id))
    if not excel_bytes:
        return Response(
            {"error": "Excel file not found or expired. Re-run the extraction to regenerate."},
            status=404,
        )
    resp = HttpResponse(
        content=excel_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = (
        f'attachment; filename="cable_block_diagram_{upload_id[:8]}.xlsx"'
    )
    return resp
