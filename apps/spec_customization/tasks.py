"""
Spec Customization — Celery Orchestration
==========================================

`extract_paper_spec(job_id)` is the entry point invoked from the API view.
It processes the PDF in chunks, writing live progress + partial results to
Redis cache so the UI can poll status without waiting for the full run.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
    PipingClassComponent,
)
from .services.config import (
    SPEC_EXTRACTION_CONFIG,
    PROGRESS_CACHE_KEY_TPL,
    PARTIAL_CACHE_KEY_TPL,
    PROGRESS_CACHE_TIMEOUT,
)
from .services.extraction_service import PaperSpecExtractionService

logger = logging.getLogger(__name__)


def _write_progress(job_id: str, **fields: Any) -> None:
    key = PROGRESS_CACHE_KEY_TPL.format(job_id=job_id)
    existing = cache.get(key) or {}
    existing.update(fields)
    cache.set(key, existing, timeout=PROGRESS_CACHE_TIMEOUT)


def _persist_classes(job: PaperSpecExtractionJob, merged: List[Dict[str, Any]]) -> int:
    """Create PipingClass + PipingClassComponent rows. Returns class count."""
    count = 0
    min_components = SPEC_EXTRACTION_CONFIG["min_components_to_keep"]
    for cls in merged:
        code = (cls.get("class_code") or "").strip().upper()
        if not code:
            continue
        components = cls.get("components", []) or []
        # Keep classes with header-only detection too — user may want to see them.
        # But discard truly empty entries that the AI hallucinated.
        if len(components) < min_components and not cls.get("class_full_code"):
            continue
        pclass, _created = PipingClass.objects.update_or_create(
            job=job,
            class_code=code,
            defaults={
                "class_full_code":     cls.get("class_full_code", "") or "",
                "material_grade":      cls.get("material_grade", "") or "",
                "pressure_rating":     cls.get("pressure_rating", "") or "",
                "flange_facing":       cls.get("flange_facing", "") or "",
                "corrosion_allowance": cls.get("corrosion_allowance", "") or "",
                "service_list":        cls.get("service_list", []) or [],
                "pt_rating_table":     cls.get("pt_rating_table", []) or [],
                "source_pages":        cls.get("_source_pages", []) or [],
                "confidence_score":    float(cls.get("confidence", 0.0) or 0.0),
                "raw_notes":           cls.get("raw_notes", "") or "",
                "extraction_engine":   cls.get("_engine", "") or "",
            },
        )
        # Replace components fresh.
        PipingClassComponent.objects.filter(piping_class=pclass).delete()
        rows: List[PipingClassComponent] = []
        for idx, comp in enumerate(components):
            rows.append(PipingClassComponent(
                piping_class=pclass,
                component_type=(comp.get("component_type") or "other").lower(),
                sub_type=comp.get("sub_type", "") or "",
                size_from=comp.get("size_from", "") or "",
                size_to=comp.get("size_to", "") or "",
                description=comp.get("description", "") or "",
                schedule_or_rating=comp.get("schedule_or_rating", "") or "",
                material_standard=comp.get("material_standard", "") or "",
                end_connection=comp.get("end_connection", "") or "",
                notes=comp.get("notes", "") or "",
                display_order=idx,
            ))
        if rows:
            PipingClassComponent.objects.bulk_create(rows)
        count += 1
    return count


@shared_task(
    bind=True,
    soft_time_limit=SPEC_EXTRACTION_CONFIG["job_total_timeout_s"] - 30,
    time_limit=SPEC_EXTRACTION_CONFIG["job_total_timeout_s"],
)
def extract_paper_spec(self, job_id: str) -> Dict[str, Any]:
    """Orchestrate per-chunk extraction for one PaperSpecExtractionJob."""
    try:
        job = PaperSpecExtractionJob.objects.select_related("document").get(pk=job_id)
    except PaperSpecExtractionJob.DoesNotExist:
        logger.error("[SpecExtraction] Job %s not found", job_id)
        return {"success": False, "error": "job not found"}

    cfg = SPEC_EXTRACTION_CONFIG
    job.status = PaperSpecExtractionJob.STATUS_PROCESSING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.config_snapshot = dict(cfg)
    job.save(update_fields=["status", "started_at", "celery_task_id", "config_snapshot"])

    service = PaperSpecExtractionService(cfg)
    pdf_path = job.document.file.path
    total_pages = service.get_page_count(pdf_path) or job.document.total_pages

    if total_pages <= 0:
        job.status = PaperSpecExtractionJob.STATUS_FAILED
        job.error_message = "Unable to read PDF (zero pages)"
        job.completed_at = timezone.now()
        job.save()
        return {"success": False, "error": job.error_message}

    chunks = service.chunk_ranges(total_pages)
    job.chunks_total = len(chunks)
    job.save(update_fields=["chunks_total"])

    _write_progress(
        str(job.id),
        state="PROGRESS",
        status="Splitting PDF into chunks",
        percent=cfg["chunk_progress_start"],
        chunks_total=len(chunks),
        chunks_done=0,
        total_pages=total_pages,
        classes_found=0,
    )

    all_results: List[List[Dict[str, Any]]] = []
    classes_found = 0
    cprog_start = cfg["chunk_progress_start"]
    cprog_end = cfg["chunk_progress_end"]
    band = max(1, cprog_end - cprog_start)

    for idx, (start_page, end_page) in enumerate(chunks):
        # Cancellation check.
        job.refresh_from_db(fields=["status"])
        if job.status == PaperSpecExtractionJob.STATUS_CANCELLED:
            logger.info("[SpecExtraction] Job %s cancelled mid-flight", job_id)
            return {"success": False, "cancelled": True}

        try:
            chunk_result = service.extract_chunk(pdf_path, start_page, end_page)
        except Exception as e:
            logger.exception("[SpecExtraction] chunk %d-%d failed: %s", start_page, end_page, e)
            chunk_result = {"piping_classes": [], "engine_used": "error"}

        all_results.append(chunk_result.get("piping_classes", []))
        classes_found = len({
            (c.get("class_code") or "").upper()
            for lst in all_results for c in lst
            if (c.get("class_code") or "").strip()
        })

        pct = cprog_start + int(((idx + 1) / max(1, len(chunks))) * band)
        job.pages_processed = end_page + 1
        job.chunks_done = idx + 1
        job.progress_percent = pct
        job.current_phase = (
            f"Pages {start_page + 1}-{end_page + 1} of {total_pages} "
            f"· {classes_found} classes · engine={chunk_result.get('engine_used')}"
        )
        job.save(update_fields=["pages_processed", "chunks_done", "progress_percent", "current_phase"])

        _write_progress(
            str(job.id),
            state="PROGRESS",
            status=job.current_phase,
            percent=pct,
            chunks_total=len(chunks),
            chunks_done=idx + 1,
            pages_processed=end_page + 1,
            total_pages=total_pages,
            classes_found=classes_found,
            engine_used=chunk_result.get("engine_used"),
        )

        # Stash partial classes for the UI to peek at.
        cache.set(
            PARTIAL_CACHE_KEY_TPL.format(job_id=str(job.id)),
            [c for lst in all_results for c in lst],
            timeout=PROGRESS_CACHE_TIMEOUT,
        )

    # ── Merge + persist ─────────────────────────────────────────────────
    merged = service.merge_classes(all_results)
    saved = _persist_classes(job, merged)

    job.status = PaperSpecExtractionJob.STATUS_COMPLETED
    job.progress_percent = 100
    job.completed_at = timezone.now()
    job.current_phase = f"Completed · {saved} piping classes persisted"
    job.save(update_fields=["status", "progress_percent", "completed_at", "current_phase"])

    _write_progress(
        str(job.id),
        state="SUCCESS",
        status=job.current_phase,
        percent=100,
        chunks_total=len(chunks),
        chunks_done=len(chunks),
        classes_found=saved,
    )

    return {"success": True, "job_id": str(job.id), "classes": saved}
