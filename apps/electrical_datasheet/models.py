from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
import json
import uuid
import secrets


class ElectricalEquipmentType(models.Model):
    """Model to store electrical equipment types with their configurations"""
    
    id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=10, blank=True)
    category = models.CharField(max_length=100)
    standards = models.JSONField(default=list)
    sections = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'electrical_equipment_types'
        verbose_name = 'Electrical Equipment Type'
        verbose_name_plural = 'Electrical Equipment Types'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class ElectricalDatasheet(models.Model):
    """Model to store electrical equipment datasheets"""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('revision_required', 'Revision Required'),
    ]

    # Basic Information
    equipment_type = models.ForeignKey(
        ElectricalEquipmentType,
        on_delete=models.PROTECT,
        related_name='datasheets'
    )
    tag_number = models.CharField(max_length=100, unique=True, db_index=True)
    service_description = models.TextField()
    location = models.CharField(max_length=200)
    
    # Form Data - stored as JSON for flexibility
    form_data = models.JSONField(default=dict)
    
    # Status and Workflow
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True
    )
    revision_number = models.IntegerField(default=1)
    revision_notes = models.TextField(blank=True)
    
    # Metadata
    project_name = models.CharField(max_length=200, blank=True)
    project_number = models.CharField(max_length=100, blank=True, db_index=True)
    discipline = models.CharField(max_length=50, default='Electrical')
    
    # File Attachments
    attachments = models.JSONField(default=list, blank=True)
    
    # User Tracking
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='electrical_datasheets_created'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='electrical_datasheets_updated'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='electrical_datasheets_reviewed'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='electrical_datasheets_approved'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Soft Delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='electrical_datasheets_deleted'
    )

    class Meta:
        db_table = 'electrical_datasheets'
        verbose_name = 'Electrical Datasheet'
        verbose_name_plural = 'Electrical Datasheets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['equipment_type', 'status']),
            models.Index(fields=['project_number', 'status']),
            models.Index(fields=['created_at', 'status']),
        ]

    def __str__(self):
        return f"{self.tag_number} - {self.equipment_type.name}"

    def get_field_value(self, field_id):
        """Helper method to retrieve a specific field value from form_data"""
        return self.form_data.get(field_id)

    def set_field_value(self, field_id, value):
        """Helper method to set a specific field value in form_data"""
        self.form_data[field_id] = value

    def to_dict(self):
        """Convert datasheet to dictionary format"""
        return {
            'id': self.id,
            'equipment_type': {
                'id': self.equipment_type.id,
                'name': self.equipment_type.name,
                'code': self.equipment_type.code,
            },
            'tag_number': self.tag_number,
            'service_description': self.service_description,
            'location': self.location,
            'form_data': self.form_data,
            'status': self.status,
            'revision_number': self.revision_number,
            'project_name': self.project_name,
            'project_number': self.project_number,
            'created_by': self.created_by.get_full_name() if self.created_by else None,
            'updated_by': self.updated_by.get_full_name() if self.updated_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class DatasheetRevisionHistory(models.Model):
    """Model to track revision history of datasheets"""
    
    datasheet = models.ForeignKey(
        ElectricalDatasheet,
        on_delete=models.CASCADE,
        related_name='revision_history'
    )
    revision_number = models.IntegerField()
    form_data = models.JSONField()
    status = models.CharField(max_length=20)
    revision_notes = models.TextField(blank=True)
    
    # User and Timestamp
    revised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    revised_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'electrical_datasheet_revisions'
        verbose_name = 'Datasheet Revision'
        verbose_name_plural = 'Datasheet Revisions'
        ordering = ['-revision_number']
        unique_together = ['datasheet', 'revision_number']

    def __str__(self):
        return f"{self.datasheet.tag_number} - Rev {self.revision_number}"


class DatasheetComment(models.Model):
    """Model for comments on datasheets"""
    
    datasheet = models.ForeignKey(
        ElectricalDatasheet,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    comment_text = models.TextField()
    field_id = models.CharField(max_length=100, blank=True)  # Optional field reference
    
    # User and Timestamp
    commented_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    commented_at = models.DateTimeField(auto_now_add=True)
    
    # Reply functionality
    parent_comment = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='replies'
    )
    
    is_resolved = models.BooleanField(default=False)

    class Meta:
        db_table = 'electrical_datasheet_comments'
        verbose_name = 'Datasheet Comment'
        verbose_name_plural = 'Datasheet Comments'
        ordering = ['commented_at']

    def __str__(self):
        return f"Comment on {self.datasheet.tag_number} by {self.commented_by}"


# ─────────────────────────────────────────────────────────────────────────────
# Smart Generator — persistence for AI-generated datasheets
# (transformer / dg_set / mv_switchgear). Decoupled from the legacy
# `ElectricalDatasheet` form-based model above.
# ─────────────────────────────────────────────────────────────────────────────

# Soft-coded enums
EQUIPMENT_TYPE_CHOICES = [
    ('transformer',    'Power / Distribution Transformer'),
    ('dg_set',         'Emergency Diesel Generator Set'),
    ('mv_switchgear',  '11KV Switchgear'),
]

VARIANT_CHOICES = [
    ('power',        'Power (e.g. 25 MVA)'),
    ('distribution', 'Distribution (e.g. 1.25 MVA)'),
    ('default',      'Default'),
]

GENERATED_STATUS_CHOICES = [
    ('draft',     'Draft'),
    ('in_review', 'In Review'),
    ('issued',    'Issued'),
    ('archived',  'Archived'),
]

CELL_EDIT_SOURCE_CHOICES = [
    ('manual',      'Manual edit'),
    ('recheck',     'Applied from recheck'),
    ('ai_suggest',  'AI suggestion'),
    ('revert',      'Revision revert'),
]


class GeneratedDatasheet(models.Model):
    """A persisted AI-generated datasheet (rows + summary + S3 artifacts)."""

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user            = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='generated_datasheets'
    )
    equipment_type  = models.CharField(max_length=32, choices=EQUIPMENT_TYPE_CHOICES, db_index=True)
    variant         = models.CharField(max_length=32, choices=VARIANT_CHOICES, default='default')
    title           = models.CharField(max_length=255, blank=True)
    revision        = models.CharField(max_length=8, default='A')
    status          = models.CharField(max_length=16, choices=GENERATED_STATUS_CHOICES, default='draft', db_index=True)

    rows            = models.JSONField(default=list)
    summary         = models.JSONField(default=dict, blank=True)
    metadata        = models.JSONField(default=dict, blank=True)  # original_filename, project_info, …
    source_files    = models.JSONField(default=list, blank=True)  # [{role, s3_key, filename, size, content_type}]

    excel_s3_key    = models.CharField(max_length=512, blank=True)
    pdf_s3_key      = models.CharField(max_length=512, blank=True)

    is_archived     = models.BooleanField(default=False, db_index=True)
    created_at      = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table         = 'electrical_generated_datasheets'
        verbose_name     = 'Generated Datasheet'
        verbose_name_plural = 'Generated Datasheets'
        ordering         = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'equipment_type', '-created_at']),
            models.Index(fields=['user', 'is_archived']),
        ]

    def __str__(self):
        return f"{self.get_equipment_type_display()} · {self.title or self.id} (rev {self.revision})"


class DatasheetCellEdit(models.Model):
    """Append-only audit trail for cell edits on `GeneratedDatasheet`."""

    id            = models.BigAutoField(primary_key=True)
    datasheet     = models.ForeignKey(
        GeneratedDatasheet, on_delete=models.CASCADE, related_name='cell_edits'
    )
    user          = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    row_index     = models.IntegerField()
    column_key    = models.CharField(max_length=32)        # 'vendor_data' | 'rev'
    old_value     = models.TextField(blank=True)
    new_value     = models.TextField(blank=True)
    source        = models.CharField(max_length=16, choices=CELL_EDIT_SOURCE_CHOICES, default='manual')
    changed_at    = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table     = 'electrical_datasheet_cell_edits'
        ordering     = ['-changed_at']
        indexes = [
            models.Index(fields=['datasheet', 'row_index']),
        ]

    def __str__(self):
        return f"#{self.datasheet_id} row={self.row_index} {self.column_key}: {self.old_value!r}→{self.new_value!r}"


class GeneratedDatasheetRevision(models.Model):
    """Snapshot of `rows` for a `GeneratedDatasheet` at a point in time."""

    id            = models.BigAutoField(primary_key=True)
    datasheet     = models.ForeignKey(
        GeneratedDatasheet, on_delete=models.CASCADE, related_name='snapshots'
    )
    revision_label = models.CharField(max_length=16)       # e.g. 'v1', 'v2'
    rows          = models.JSONField(default=list)
    summary       = models.JSONField(default=dict, blank=True)
    note          = models.CharField(max_length=255, blank=True)
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at    = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'electrical_generated_datasheet_snapshots'
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.datasheet_id} {self.revision_label}"


class GeneratedDatasheetCellComment(models.Model):
    """Cell-anchored discussion thread on a `GeneratedDatasheet`."""

    id            = models.BigAutoField(primary_key=True)
    datasheet     = models.ForeignKey(
        GeneratedDatasheet, on_delete=models.CASCADE, related_name='cell_comments'
    )
    row_index     = models.IntegerField(null=True, blank=True)   # null = page-level
    column_key    = models.CharField(max_length=32, blank=True)
    user          = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='generated_datasheet_cell_comments'
    )
    text          = models.TextField()
    is_resolved   = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'electrical_generated_datasheet_comments'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['datasheet', 'row_index']),
        ]


def _share_token_default():
    return secrets.token_urlsafe(32)


class DatasheetShareLink(models.Model):
    """Public read-only share token for a `GeneratedDatasheet`."""

    id            = models.BigAutoField(primary_key=True)
    datasheet     = models.ForeignKey(
        GeneratedDatasheet, on_delete=models.CASCADE, related_name='share_links'
    )
    token         = models.CharField(max_length=64, unique=True, default=_share_token_default, db_index=True)
    created_by    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    expires_at    = models.DateTimeField(null=True, blank=True)
    max_views     = models.IntegerField(null=True, blank=True)
    view_count    = models.IntegerField(default=0)
    revoked       = models.BooleanField(default=False)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'electrical_datasheet_share_links'
        ordering = ['-created_at']

    def is_valid(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_views is not None and self.view_count >= self.max_views:
            return False
        return True
