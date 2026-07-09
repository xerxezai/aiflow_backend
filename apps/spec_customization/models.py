"""
Spec Customization — Paper Spec PDF Extraction Models
======================================================

Four-table schema:

  PaperSpecDocument   ──┐
                        │  (one-to-many)
  PaperSpecExtractionJob┘
        │
        │ (one-to-many)
        ▼
  PipingClass
        │
        │ (one-to-many)
        ▼
  PipingClassComponent

All AI / engine / chunking knobs live in `services/config.py` — these models
hold only the *persisted* outputs.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


# ─────────────────────────────────────────────────────────────────────────────
# 1. PaperSpecDocument — the uploaded source PDF
# ─────────────────────────────────────────────────────────────────────────────
class PaperSpecDocument(models.Model):
    """
    Uploaded Paper Specification PDF (e.g. ADNOC LNG Piping Specs).
    A single document can have many extraction jobs (re-runs with different
    configs); dedupe-by-sha256 lets the API reuse the latest completed job.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to='spec_customization/paper_specs/%Y/%m/')
    original_filename = models.CharField(max_length=512)
    file_size_bytes = models.BigIntegerField(default=0)
    total_pages = models.IntegerField(default=0)

    # SHA-256 of the binary content — enables dedupe across uploads.
    sha256_hash = models.CharField(max_length=64, db_index=True)

    # Optional project link (NOT required — user can extract specs standalone).
    project_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    title = models.CharField(max_length=512, blank=True, default='')
    document_number = models.CharField(max_length=128, blank=True, default='')

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='spec_customization_uploads',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sha256_hash']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.original_filename} ({self.total_pages}p)'


# ─────────────────────────────────────────────────────────────────────────────
# 2. PaperSpecExtractionJob — async extraction job + progress
# ─────────────────────────────────────────────────────────────────────────────
class PaperSpecExtractionJob(models.Model):
    STATUS_QUEUED     = 'queued'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED  = 'completed'
    STATUS_FAILED     = 'failed'
    STATUS_CANCELLED  = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_QUEUED,     'Queued'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED,  'Completed'),
        (STATUS_FAILED,     'Failed'),
        (STATUS_CANCELLED,  'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        PaperSpecDocument,
        on_delete=models.CASCADE,
        related_name='jobs',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)

    # Progress (0-100 inclusive).
    progress_percent = models.IntegerField(default=0)
    current_phase = models.CharField(max_length=128, blank=True, default='')

    # Chunking telemetry.
    pages_processed = models.IntegerField(default=0)
    chunks_total = models.IntegerField(default=0)
    chunks_done = models.IntegerField(default=0)

    # Snapshot of soft-coded config used for this run (for debugging / repro).
    config_snapshot = models.JSONField(default=dict, blank=True)

    celery_task_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    error_message = models.TextField(blank=True, default='')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='spec_customization_jobs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['celery_task_id']),
        ]

    def __str__(self) -> str:
        return f'Job {self.id} · {self.status} · {self.progress_percent}%'


# ─────────────────────────────────────────────────────────────────────────────
# 3. PipingClass — one extracted piping class (e.g. "PIPING SPEC: A")
# ─────────────────────────────────────────────────────────────────────────────
class PipingClass(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        PaperSpecExtractionJob,
        on_delete=models.CASCADE,
        related_name='piping_classes',
    )

    class_code = models.CharField(max_length=32, db_index=True)          # e.g. "A"
    class_full_code = models.CharField(max_length=256, blank=True, default='')
    material_grade = models.CharField(max_length=256, blank=True, default='')
    pressure_rating = models.CharField(max_length=64, blank=True, default='')
    flange_facing = models.CharField(max_length=64, blank=True, default='')
    corrosion_allowance = models.CharField(max_length=64, blank=True, default='')

    # List of applicable services (free text — "General Process", "Sweet Fuel Gas", ...)
    service_list = models.JSONField(default=list, blank=True)

    # P/T rating table — list of {"pressure_bar_g": float, "temperature_c": float, "notes": str}
    pt_rating_table = models.JSONField(default=list, blank=True)

    # Source page range in the original PDF — [start_page, end_page] (1-based).
    source_pages = models.JSONField(default=list, blank=True)

    # Confidence of the extraction for this class (0.0–1.0).
    confidence_score = models.FloatField(default=0.0)

    # Raw notes / preamble text captured for the class (for audit).
    raw_notes = models.TextField(blank=True, default='')

    extraction_engine = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['class_code']
        unique_together = [('job', 'class_code')]
        indexes = [
            models.Index(fields=['job', 'class_code']),
        ]

    def __str__(self) -> str:
        return f'{self.class_code} ({self.material_grade or "—"})'


# ─────────────────────────────────────────────────────────────────────────────
# 4. PipingClassComponent — pipe / valve / fitting / flange row inside a class
# ─────────────────────────────────────────────────────────────────────────────
class PipingClassComponent(models.Model):
    TYPE_PIPE    = 'pipe'
    TYPE_VALVE   = 'valve'
    TYPE_FITTING = 'fitting'
    TYPE_FLANGE  = 'flange'
    TYPE_GASKET  = 'gasket'
    TYPE_BOLT    = 'bolt'
    TYPE_OTHER   = 'other'

    TYPE_CHOICES = [
        (TYPE_PIPE,    'Pipe'),
        (TYPE_VALVE,   'Valve'),
        (TYPE_FITTING, 'Fitting'),
        (TYPE_FLANGE,  'Flange'),
        (TYPE_GASKET,  'Gasket'),
        (TYPE_BOLT,    'Bolt / Stud'),
        (TYPE_OTHER,   'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    piping_class = models.ForeignKey(
        PipingClass,
        on_delete=models.CASCADE,
        related_name='components',
    )

    component_type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True)
    sub_type = models.CharField(max_length=128, blank=True, default='')   # e.g. "Gate", "Globe", "Ball", "Elbow", "Tee"

    size_from = models.CharField(max_length=32, blank=True, default='')   # e.g. '1/2"'
    size_to = models.CharField(max_length=32, blank=True, default='')

    description = models.TextField(blank=True, default='')
    schedule_or_rating = models.CharField(max_length=128, blank=True, default='')
    material_standard = models.CharField(max_length=256, blank=True, default='')
    end_connection = models.CharField(max_length=128, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['piping_class', 'display_order', 'component_type']
        indexes = [
            models.Index(fields=['piping_class', 'component_type']),
        ]

    def __str__(self) -> str:
        return f'{self.component_type} · {self.sub_type or "—"} · {self.size_from}–{self.size_to}'


# ─────────────────────────────────────────────────────────────────────────────
# 5. WorkbookCellOverride — per-cell user edits on the SPEC/CAT canvas
# ─────────────────────────────────────────────────────────────────────────────
class WorkbookCellOverride(models.Model):
    """
    User-edited override for a single cell in the SPEC or CAT workbook preview.

    Identifies a cell by (job, workbook, sheet_name, row_key, column_name).
    `row_key` is a deterministic string computed by the preview builder — it is
    stable across rebuilds as long as the underlying class/component ids do
    not change.

    Overrides are applied at *render* time (preview JSON) AND at *export* time
    (xlsx generation), so what the user sees in the canvas matches the file.
    """
    WORKBOOK_SPEC = 'spec'
    WORKBOOK_CAT  = 'cat'
    WORKBOOK_CHOICES = [
        (WORKBOOK_SPEC, 'SPEC'),
        (WORKBOOK_CAT,  'CAT'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        PaperSpecExtractionJob,
        on_delete=models.CASCADE,
        related_name='workbook_overrides',
    )
    workbook    = models.CharField(max_length=8, choices=WORKBOOK_CHOICES, db_index=True)
    sheet_name  = models.CharField(max_length=128, db_index=True)
    row_key     = models.CharField(max_length=256, db_index=True)
    column_name = models.CharField(max_length=128)
    value       = models.TextField(blank=True, default='')

    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='spec_workbook_edits',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('job', 'workbook', 'sheet_name', 'row_key', 'column_name')]
        indexes = [
            models.Index(fields=['job', 'workbook', 'sheet_name']),
        ]

    def __str__(self) -> str:
        return f'{self.workbook}/{self.sheet_name}/{self.row_key}/{self.column_name} = {self.value!r}'

# ---------------------------------------------------------------------------
# Project organiser (additive — see project_models.py)
# ---------------------------------------------------------------------------
from .project_models import SpecProject  # noqa: E402,F401
