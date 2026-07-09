"""
Base models with common fields and methods.
Smart reusable model patterns.
"""
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """
    Abstract base model with created and updated timestamps.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']


class SoftDeleteModel(models.Model):
    """
    Abstract base model for soft deletion.
    """
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def soft_delete(self):
        """Soft delete the object."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        """Restore a soft-deleted object."""
        self.is_deleted = False
        self.deleted_at = None
        self.save()


class BaseModel(TimeStampedModel, SoftDeleteModel):
    """
    Comprehensive base model combining timestamps and soft deletion.
    """
    class Meta:
        abstract = True


class Enquiry(TimeStampedModel):
    """
    Customer enquiry submitted from the public /enquiry form.
    Persisted so admins can review, triage and reply from the
    9.6 Enquiry admin page (in addition to the email notification).
    """

    URGENCY_CHOICES = [
        ('low',    'Low Priority'),
        ('normal', 'Normal Priority'),
        ('high',   'High Priority'),
        ('urgent', 'Urgent'),
    ]

    STATUS_CHOICES = [
        ('new',        'New'),
        ('in_review',  'In Review'),
        ('contacted',  'Contacted'),
        ('resolved',   'Resolved'),
        ('spam',       'Spam'),
    ]

    name        = models.CharField(max_length=120)
    email       = models.EmailField()
    phone       = models.CharField(max_length=40)
    company     = models.CharField(max_length=160, blank=True, default='')
    subject     = models.CharField(max_length=200)
    message     = models.TextField()
    service     = models.CharField(max_length=60, blank=True, default='')
    urgency     = models.CharField(max_length=10, choices=URGENCY_CHOICES, default='normal')

    status      = models.CharField(max_length=12, choices=STATUS_CHOICES, default='new', db_index=True)
    admin_notes = models.TextField(blank=True, default='')

    source_ip   = models.GenericIPAddressField(null=True, blank=True)
    user_agent  = models.CharField(max_length=400, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]
        verbose_name = 'Enquiry'
        verbose_name_plural = 'Enquiries'

    def __str__(self):
        return f'{self.name} <{self.email}> — {self.subject[:60]}'


# Smart Project Collection Models for Multi-Disciplinary Document Organization

from django.contrib.auth.models import User
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
import json

class ProjectCollection(BaseModel):
    """Track project-based document collections"""
    
    project_code = models.CharField(max_length=50, unique=True, db_index=True)
    project_name = models.CharField(max_length=200)
    
    # Project metadata
    client_name = models.CharField(max_length=100, null=True, blank=True)
    project_type = models.CharField(max_length=50, default='engineering')
    project_status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('completed', 'Completed'),
            ('on_hold', 'On Hold'),
            ('archived', 'Archived')
        ],
        default='active'
    )
    
    # Statistics
    total_documents = models.IntegerField(default=0)
    total_size_bytes = models.BigIntegerField(default=0)
    discipline_count = models.IntegerField(default=0)
    
    # Folder organization
    s3_root_path = models.CharField(max_length=500)
    folder_structure = models.JSONField(default=dict, blank=True)
    
    # Auto-organization settings
    auto_organize_enabled = models.BooleanField(default=True)
    ai_classification_enabled = models.BooleanField(default=True)
    cross_discipline_recommendations = models.BooleanField(default=True)
    
    # Additional timestamps
    last_document_upload = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'core_project_collections'
        indexes = [
            models.Index(fields=['project_code']),
            models.Index(fields=['project_status', 'updated_at']),
            models.Index(fields=['last_document_upload']),
        ]
    
    def __str__(self):
        return f"{self.project_code} - {self.project_name}"
    
    @property
    def total_size_mb(self):
        return self.total_size_bytes / (1024 * 1024) if self.total_size_bytes else 0

class ProjectDiscipline(BaseModel):
    """Track disciplines within each project"""
    
    project = models.ForeignKey(ProjectCollection, on_delete=models.CASCADE, related_name='disciplines')
    discipline_name = models.CharField(max_length=50)
    
    DISCIPLINE_CHOICES = [
        ('process', 'Process Engineering'),
        ('mechanical', 'Mechanical Engineering'),
        ('electrical', 'Electrical Engineering'),
        ('instrumentation', 'Instrumentation & Control'),
        ('civil', 'Civil & Structural'),
        ('piping', 'Piping Engineering'),
        ('safety', 'Safety & Risk'),
        ('environmental', 'Environmental'),
        ('general', 'General Documents'),
    ]
    
    discipline_type = models.CharField(max_length=20, choices=DISCIPLINE_CHOICES, default='general')
    
    # Statistics
    document_count = models.IntegerField(default=0)
    size_bytes = models.BigIntegerField(default=0)
    
    # Folder path within project
    s3_discipline_path = models.CharField(max_length=600)
    
    # Document types in this discipline
    document_types = models.JSONField(default=list, blank=True)
    
    # Lead engineer/responsible person
    lead_engineer = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='led_disciplines'
    )
    
    class Meta:
        db_table = 'core_project_disciplines'
        unique_together = ['project', 'discipline_name']
        indexes = [
            models.Index(fields=['project', 'discipline_type']),
            models.Index(fields=['discipline_type', 'document_count']),
        ]
    
    def __str__(self):
        return f"{self.project.project_code} - {self.get_discipline_type_display()}"
    
    @property
    def size_mb(self):
        return self.size_bytes / (1024 * 1024) if self.size_bytes else 0

class SmartProjectDocument(BaseModel):
    """Enhanced document model with project and discipline organization"""
    
    # Document identification
    document_id = models.CharField(max_length=100, unique=True, db_index=True)
    filename = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    
    # Project organization
    project = models.ForeignKey(ProjectCollection, on_delete=models.CASCADE, related_name='documents')
    discipline = models.ForeignKey(ProjectDiscipline, on_delete=models.CASCADE, related_name='documents')
    
    # Document classification
    document_type = models.CharField(max_length=100)
    document_subtype = models.CharField(max_length=100, null=True, blank=True)
    
    DOCUMENT_CATEGORIES = [
        ('drawing', 'Technical Drawing'),
        ('specification', 'Specification Document'),
        ('datasheet', 'Equipment Datasheet'),
        ('calculation', 'Engineering Calculation'),
        ('report', 'Technical Report'),
        ('procedure', 'Operating Procedure'),
        ('manual', 'Equipment Manual'),
        ('certificate', 'Certificate/Approval'),
        ('other', 'Other Document'),
    ]
    
    document_category = models.CharField(max_length=20, choices=DOCUMENT_CATEGORIES, default='other')
    
    # File information
    s3_key = models.CharField(max_length=1000)  # Organized S3 location
    original_s3_key = models.CharField(max_length=1000, null=True, blank=True)  # Original upload location
    file_size = models.BigIntegerField()
    file_extension = models.CharField(max_length=10)
    content_hash = models.CharField(max_length=64, db_index=True)  # SHA-256 for duplicate detection
    
    # User information
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    upload_date = models.DateTimeField(auto_now_add=True)
    
    # AI classification results
    ai_classification_confidence = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="AI confidence in document classification"
    )
    ai_extracted_metadata = models.JSONField(default=dict, blank=True)
    
    # Document relationships
    related_documents = models.ManyToManyField(
        'self',
        through='DocumentRelationship',
        symmetrical=False,
        blank=True,
        help_text="Related documents in the project"
    )
    
    # Document status
    is_active = models.BooleanField(default=True)
    is_superseded = models.BooleanField(default=False)
    superseded_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='supersedes'
    )
    
    # Version control
    revision = models.CharField(max_length=20, default='A')
    revision_date = models.DateField(null=True, blank=True)
    
    class Meta:
        db_table = 'core_smart_project_documents'
        indexes = [
            models.Index(fields=['project', 'discipline', 'document_type']),
            models.Index(fields=['content_hash']),
            models.Index(fields=['uploaded_by', 'upload_date']),
            models.Index(fields=['document_category', 'is_active']),
            models.Index(fields=['ai_classification_confidence']),
        ]
    
    def __str__(self):
        return f"{self.project.project_code} - {self.filename}"
    
    @property
    def file_size_mb(self):
        return self.file_size / (1024 * 1024) if self.file_size else 0

class DocumentRelationship(models.Model):
    """Define relationships between documents"""
    
    from_document = models.ForeignKey(
        SmartProjectDocument,
        on_delete=models.CASCADE,
        related_name='relationships_from'
    )
    to_document = models.ForeignKey(
        SmartProjectDocument,
        on_delete=models.CASCADE,
        related_name='relationships_to'
    )
    
    RELATIONSHIP_TYPES = [
        ('references', 'References'),
        ('supersedes', 'Supersedes'),
        ('complements', 'Complements'),
        ('depends_on', 'Depends On'),
        ('similar_to', 'Similar To'),
        ('part_of', 'Part Of'),
    ]
    
    relationship_type = models.CharField(max_length=20, choices=RELATIONSHIP_TYPES)
    confidence_score = models.FloatField(
        default=1.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    
    # Relationship metadata
    created_by_ai = models.BooleanField(default=False)
    verified_by_user = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'core_document_relationships'
        unique_together = ['from_document', 'to_document', 'relationship_type']
    
    def __str__(self):
        return f"{self.from_document.filename} {self.relationship_type} {self.to_document.filename}"

class CrossDisciplineRecommendation(BaseModel):
    """Store cross-discipline document recommendations"""
    
    project = models.ForeignKey(ProjectCollection, on_delete=models.CASCADE, related_name='cross_discipline_recs')
    
    source_discipline = models.ForeignKey(
        ProjectDiscipline,
        on_delete=models.CASCADE,
        related_name='recommendations_from'
    )
    target_discipline = models.ForeignKey(
        ProjectDiscipline,
        on_delete=models.CASCADE,
        related_name='recommendations_to'
    )
    
    source_document = models.ForeignKey(
        SmartProjectDocument,
        on_delete=models.CASCADE,
        related_name='triggers_recommendations'
    )
    
    # Recommendation details
    recommendation_type = models.CharField(
        max_length=50,
        choices=[
            ('complementary_doc', 'Complementary Document Needed'),
            ('interface_check', 'Interface Check Required'),
            ('specification_update', 'Specification Update Needed'),
            ('review_request', 'Review Request'),
            ('dependency_alert', 'Dependency Alert'),
        ]
    )
    
    recommendation_text = models.TextField()
    suggested_document_types = models.JSONField(default=list)
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('acknowledged', 'Acknowledged'),
            ('in_progress', 'In Progress'),
            ('completed', 'Completed'),
            ('dismissed', 'Dismissed'),
        ],
        default='pending'
    )
    
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_cross_discipline_recs'
    )
    
    # AI confidence
    ai_confidence = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        default=0.5
    )
    
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'core_cross_discipline_recommendations'
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['source_discipline', 'target_discipline']),
            models.Index(fields=['recommendation_type', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.source_discipline} → {self.target_discipline}: {self.recommendation_type}"
