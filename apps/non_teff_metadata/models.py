import uuid
from django.db import models
from django.conf import settings


# ---------------------------------------------------------------------------
# PROJECT MODEL (additive, RBAC-aligned)
# ---------------------------------------------------------------------------
# A NonTeffProject groups multiple extraction sessions (single-file jobs +
# bulk batches) under one logical engineering project. RBAC is enforced at
# the view layer using the existing services.history_archive.resolve_user_role
# helper (admins see all projects, regular users see ones they created).
#
# Status lifecycle (soft-coded — adjust without code changes):
#   active → on_hold → completed → archived
# ---------------------------------------------------------------------------


class NonTeffProject(models.Model):
    STATUS_ACTIVE    = 'active'
    STATUS_ON_HOLD   = 'on_hold'
    STATUS_COMPLETED = 'completed'
    STATUS_ARCHIVED  = 'archived'

    STATUS_CHOICES = [
        (STATUS_ACTIVE,    'Active'),
        (STATUS_ON_HOLD,   'On hold'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_ARCHIVED,  'Archived'),
    ]

    project_id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name         = models.CharField(max_length=255)
    code         = models.CharField(max_length=64, blank=True, db_index=True)
    client       = models.CharField(max_length=128, blank=True)
    plant        = models.CharField(max_length=128, blank=True)
    discipline   = models.CharField(max_length=64, blank=True)
    description  = models.TextField(blank=True)
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    tags         = models.JSONField(default=list, blank=True)
    metadata     = models.JSONField(default=dict, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='non_teff_projects',
    )

    class Meta:
        ordering = ['-updated_at', '-created_at']
        verbose_name = 'Non-TEFF Project'
        verbose_name_plural = 'Non-TEFF Projects'
        indexes = [
            models.Index(fields=['status', '-updated_at']),
            models.Index(fields=['created_by', '-updated_at']),
        ]

    def __str__(self):
        return f"{self.name} [{self.status}] ({self.project_id})"


# ---------------------------------------------------------------------------
# BULK MASTER INDEX MODELS (additive — do not alter NonTeffExtractionJob)
# ---------------------------------------------------------------------------
# A NonTeffBatch groups many files uploaded in one session.
# Each file becomes a NonTeffBatchItem whose `fields` JSON holds one value per
# column defined in config/master_index_template.json.
#
# Status lifecycle (batch):
#   draft → uploading → processing → ready → exported
# Status lifecycle (item):
#   pending → uploaded → extracting → ready | failed
#
# Note: all status strings are compared as lower-case — do not rename without
# updating the service layer constants.
# ---------------------------------------------------------------------------


class NonTeffBatch(models.Model):
    BATCH_STATUS_DRAFT      = 'draft'
    BATCH_STATUS_UPLOADING  = 'uploading'
    BATCH_STATUS_PROCESSING = 'processing'
    BATCH_STATUS_READY      = 'ready'
    BATCH_STATUS_EXPORTED   = 'exported'
    BATCH_STATUS_FAILED     = 'failed'

    BATCH_STATUS_CHOICES = [
        (BATCH_STATUS_DRAFT,      'Draft'),
        (BATCH_STATUS_UPLOADING,  'Uploading'),
        (BATCH_STATUS_PROCESSING, 'Processing'),
        (BATCH_STATUS_READY,      'Ready for review'),
        (BATCH_STATUS_EXPORTED,   'Exported'),
        (BATCH_STATUS_FAILED,     'Failed'),
    ]

    batch_id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name           = models.CharField(max_length=255)
    plant          = models.CharField(max_length=64, blank=True)
    project        = models.ForeignKey(
        'NonTeffProject',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='batches',
    )
    # Batch-default column values (column.key → value) applied to every item
    batch_defaults = models.JSONField(default=dict, blank=True)
    status         = models.CharField(max_length=20, choices=BATCH_STATUS_CHOICES, default=BATCH_STATUS_DRAFT)
    total_files    = models.PositiveIntegerField(default=0)
    ready_files    = models.PositiveIntegerField(default=0)
    failed_files   = models.PositiveIntegerField(default=0)
    storage_prefix = models.CharField(max_length=512, blank=True)  # local media or S3 key prefix
    error_message  = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)
    created_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='non_teff_batches',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Non-TEFF Batch'
        verbose_name_plural = 'Non-TEFF Batches'

    def __str__(self):
        return f"{self.name} [{self.status}] ({self.batch_id})"


class NonTeffBatchItem(models.Model):
    ITEM_STATUS_PENDING    = 'pending'
    ITEM_STATUS_UPLOADED   = 'uploaded'
    ITEM_STATUS_EXTRACTING = 'extracting'
    ITEM_STATUS_READY      = 'ready'
    ITEM_STATUS_FAILED     = 'failed'

    ITEM_STATUS_CHOICES = [
        (ITEM_STATUS_PENDING,    'Pending'),
        (ITEM_STATUS_UPLOADED,   'Uploaded'),
        (ITEM_STATUS_EXTRACTING, 'Extracting'),
        (ITEM_STATUS_READY,      'Ready'),
        (ITEM_STATUS_FAILED,     'Failed'),
    ]

    item_id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch          = models.ForeignKey(NonTeffBatch, on_delete=models.CASCADE, related_name='items')
    file_name      = models.CharField(max_length=512)
    relative_path  = models.CharField(max_length=1024, blank=True)
    storage_key    = models.CharField(max_length=1024, blank=True)  # local path or S3 key
    size_bytes     = models.BigIntegerField(default=0)
    sha256         = models.CharField(max_length=64, blank=True, db_index=True)
    status         = models.CharField(max_length=20, choices=ITEM_STATUS_CHOICES, default=ITEM_STATUS_PENDING)
    # One dict keyed by column.key from master_index_template.json
    fields         = models.JSONField(default=dict, blank=True)
    reviewed       = models.BooleanField(default=False)
    error          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['batch', 'file_name']
        verbose_name = 'Non-TEFF Batch Item'
        verbose_name_plural = 'Non-TEFF Batch Items'
        indexes = [
            models.Index(fields=['batch', 'status']),
        ]

    def __str__(self):
        return f"{self.file_name} [{self.status}]"


class NonTeffExtractionJob(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    job_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    file_name = models.CharField(max_length=512, blank=True)
    file_format = models.CharField(max_length=20, blank=True)  # pdf / excel / word / autocad / other
    progress = models.IntegerField(default=0)
    status_message = models.CharField(max_length=512, default='Queued')
    result_json = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='non_teff_jobs',
    )
    project = models.ForeignKey(
        NonTeffProject,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='jobs',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Non-TEFF Extraction Job'
        verbose_name_plural = 'Non-TEFF Extraction Jobs'

    def __str__(self):
        return f"{self.file_name} [{self.status}] ({self.job_id})"
