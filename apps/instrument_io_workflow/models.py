"""
Lean data model — three tables only:

    IOListDocument          — one uploaded PDF (single revision)
    IOListExtractedComment  — rows from the Comments Resolution Sheet
    IOListExtractedRow      — rows from the structured IO table

Multi-revision chain tracking is delegated to the existing CRS chain backend
(apps.crs.CRSRevisionChain). We only store an optional FK reference to it.
"""

from django.conf import settings
from django.db import models


class IOListDocument(models.Model):
    """A single uploaded Instrument IO List PDF (one revision)."""

    STATUS_CHOICES = [
        ('uploaded',  'Uploaded'),
        ('extracting','Extracting'),
        ('completed', 'Completed'),
        ('failed',    'Failed'),
    ]

    # Identity
    project_name      = models.CharField(max_length=255, blank=True, default='')
    document_number   = models.CharField(max_length=255, blank=True, default='')
    revision_label    = models.CharField(max_length=20,  blank=True, default='')
    plant             = models.CharField(max_length=120, blank=True, default='')
    unit              = models.CharField(max_length=60,  blank=True, default='')

    # Storage
    pdf_file          = models.FileField(
        upload_to='instrument_io_workflow/%Y/%m/',
        storage='apps.core.storage_backends.IOListDocumentStorage',
    )
    pdf_sha256        = models.CharField(max_length=64, db_index=True)

    # Extraction state
    status            = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='uploaded',
    )
    extraction_stats  = models.JSONField(default=dict, blank=True)
    extraction_error  = models.TextField(blank=True, default='')

    # Optional link into existing CRS revision chain (NO core change to CRS)
    crs_chain_id      = models.CharField(max_length=64, blank=True, default='')

    # Audit
    uploaded_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='io_list_documents',
    )
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['pdf_sha256']),
            models.Index(fields=['crs_chain_id']),
            models.Index(fields=['document_number', 'revision_label']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f'{self.document_number or "—"} rev {self.revision_label or "?"}'


class IOListExtractedComment(models.Model):
    """One row from the Comments Resolution Sheet."""
    document          = models.ForeignKey(
        IOListDocument, on_delete=models.CASCADE,
        related_name='extracted_comments',
    )
    s_no              = models.CharField(max_length=40,  blank=True, default='')
    company_comment   = models.TextField(blank=True, default='')
    contractor_reply  = models.TextField(blank=True, default='')
    company_decision  = models.TextField(blank=True, default='')
    status_code       = models.CharField(max_length=20,  blank=True, default='')
    status_meaning    = models.CharField(max_length=120, blank=True, default='')
    page_number       = models.PositiveIntegerField(null=True, blank=True)
    linked_tags       = models.JSONField(default=list, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document_id', 'page_number', 'id']


class IOListExtractedRow(models.Model):
    """One row from the structured IO table (DCS or ESD sheet)."""
    document          = models.ForeignKey(
        IOListDocument, on_delete=models.CASCADE,
        related_name='extracted_rows',
    )
    tag_number        = models.CharField(max_length=80, db_index=True,
                                          blank=True, default='')
    page_number       = models.PositiveIntegerField(null=True, blank=True)
    # All other 39 columns live here as flexible JSON — keeps schema soft-coded.
    # Frontend reads via IO_LIST_CANONICAL_COLUMNS order.
    data              = models.JSONField(default=dict, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document_id', 'page_number', 'id']
        indexes  = [models.Index(fields=['tag_number'])]
