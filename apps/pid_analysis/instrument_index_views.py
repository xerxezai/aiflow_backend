"""
Instrument Index Views
----------------------
REST API endpoints for extracting the full instrument index from P&ID drawings.

Endpoints (all registered in urls.py):
  POST  instrument-index/analyze/          → extract_instrument_index
  GET   instrument-index/download-excel/<upload_id>/  → download_instrument_index_excel
  GET   instrument-index/categories/       → get_instrument_categories
"""

import io
import os
import uuid
import logging

from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.http import HttpResponse

from .instrument_index_service import InstrumentIndexService, INSTRUMENT_CATEGORIES

logger = logging.getLogger(__name__)

# How long to keep the generated Excel bytes in Django cache (seconds)
EXCEL_CACHE_TTL = 600  # 10 minutes


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_service():
    """Instantiate InstrumentIndexService lazily (one per request is fine)."""
    return InstrumentIndexService()


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Extract instrument index (POST)
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def extract_instrument_index(request):
    """
    POST /api/v1/pid_analysis/instrument-index/analyze/

    Accepts multipart/form-data:
      pid_file        — uploaded PDF (required)
    drawing_number  — drawing number string
      drawing_title   — title string
      revision        — revision string (default "0")
      project_name    — project name string
    legend_file     — optional legend/symbol sheet PDF for cross-verification

    Returns JSON:
      {
        "success": true,
        "upload_id": "<uuid>",
        "drawing_info": { … },
        "instruments": [ … ],
        "total": <int>,
        "category_summary": { "Flow": 5, "Pressure": 12, … },
        "excel_url": "/api/v1/pid_analysis/instrument-index/download-excel/<uuid>/"
      }
    """
    pid_file = request.FILES.get("pid_file")
    legend_file = request.FILES.get("legend_file")
    if not pid_file:
        return Response({"error": "No P&ID file uploaded. Please attach a PDF."}, status=400)

    if not pid_file.name.lower().endswith(".pdf"):
        return Response({"error": "Only PDF files are supported."}, status=400)

    if legend_file and not legend_file.name.lower().endswith(".pdf"):
        return Response({"error": "Legend sheet must be a PDF file."}, status=400)

    # Drawing metadata (all optional)
    pid_bytes = pid_file.read()
    filename_stem = os.path.splitext(pid_file.name)[0]

    drawing_info = {
        "drawing_number": request.data.get("drawing_number") or filename_stem,
        "drawing_title":  request.data.get("drawing_title")  or "",
        "revision":       request.data.get("revision")       or "0",
        "project_name":   request.data.get("project_name")   or "",
        "pid_no":         request.data.get("drawing_number") or filename_stem,
        # Soft-coded category context — drives template-aware prompt + defaults.
        # Falls back to 'default' when not supplied (back-compat with old clients).
        "project_category": (request.data.get("project_category") or "default").strip().lower(),
        "project_code":     request.data.get("project_code")  or "",
        "project_client":   request.data.get("project_client") or "",
        # Explicit unit/area code — used by tag-format normaliser as the
        # authoritative source for the {unit} prefix (e.g. ADNOC Gas '562').
        # Falls back to derivation from pid_no / project_code when blank.
        "project_unit":     (request.data.get("project_unit") or "").strip(),
    }

    logger.info(
        f"[InstrumentIndex] Received '{pid_file.name}' "
        f"({len(pid_bytes) / 1024:.1f} KB) — drawing: {drawing_info['drawing_number']}"
    )

    service = _get_service()
    legend_context_override = None
    if legend_file:
        legend_bytes = legend_file.read()
        legend_context_override = service.build_legend_context_from_uploaded_file(
            legend_bytes,
            legend_file.name,
        )
        drawing_info["legend_sheet_name"] = legend_file.name

    instruments = service.extract_instruments(
        pid_bytes,
        drawing_info,
        legend_context_override=legend_context_override,
    )

    if not instruments:
        logger.warning("[InstrumentIndex] No instruments extracted — returning empty result")

    # Build category summary
    category_summary: dict[str, int] = {}
    for inst in instruments:
        cat = inst.get("category") or "Unknown"
        category_summary[cat] = category_summary.get(cat, 0) + 1

    # Generate Excel and cache it
    upload_id = str(uuid.uuid4())
    try:
        excel_bytes = service.generate_excel(instruments, drawing_info)
        cache.set(f"instrument_index_excel_{upload_id}", excel_bytes, timeout=EXCEL_CACHE_TTL)
        logger.info(f"[InstrumentIndex] Excel cached under key: {upload_id}")
        excel_available = True
    except Exception as exc:
        logger.error(f"[InstrumentIndex] Excel generation failed: {exc}", exc_info=True)
        excel_available = False

    excel_url = (
        f"/api/v1/pid_analysis/instrument-index/download-excel/{upload_id}/"
        if excel_available else None
    )

    return Response(
        {
            "success": True,
            "upload_id": upload_id,
            "drawing_info": drawing_info,
            "instruments": instruments,
            "total": len(instruments),
            "category_summary": category_summary,
            "excel_url": excel_url,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Download Excel (GET)
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def download_instrument_index_excel(request, upload_id):
    """
    GET /api/v1/pid_analysis/instrument-index/download-excel/<upload_id>/

    Streams the previously generated Excel workbook.
    """
    excel_bytes = cache.get(f"instrument_index_excel_{upload_id}")
    if not excel_bytes:
        return Response(
            {"error": "Excel file not found or expired. Re-run the extraction to regenerate."},
            status=404,
        )

    resp = HttpResponse(
        content=excel_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="instrument_index_{upload_id[:8]}.xlsx"'
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Get instrument categories / types (GET)
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def get_instrument_categories(request):
    """
    GET /api/v1/pid_analysis/instrument-index/categories/

    Returns the soft-coded INSTRUMENT_CATEGORIES dict so the frontend can
    display labels and colour-code rows without hardcoding anything.
    """
    categories: dict[str, list] = {}
    for code, cfg in INSTRUMENT_CATEGORIES.items():
        cat = cfg["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({"code": code, "name": cfg["name"]})

    return Response(
        {
            "categories": categories,
            "total_types": len(INSTRUMENT_CATEGORIES),
        }
    )
