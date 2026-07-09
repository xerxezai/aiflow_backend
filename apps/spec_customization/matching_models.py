"""
Spec Customization — Component Matching Models
==============================================

Three-table schema for isometric software component matching:

  SpecProject  ──┬─→ MatchingWorkbookSet
                 │
                 └─→ MatchingRule (extracted from Match.xlsx)
                 
Workflow:
  1. User uploads Match.xlsx, SPEC.xlsx, CAT.xlsx to a SpecProject
  2. System parses Match.xlsx and creates MatchingRule records
  3. When matching components, system queries rules + applies SPEC filters + matches CAT catalog
  
All matching configuration is soft-coded in services/component_matcher.py
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from .project_models import SpecProject


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded workbook type constants
# ─────────────────────────────────────────────────────────────────────────────
WORKBOOK_TYPE_MATCH = 'match'
WORKBOOK_TYPE_SPEC = 'spec'
WORKBOOK_TYPE_CAT = 'cat'

WORKBOOK_TYPE_CHOICES = [
    (WORKBOOK_TYPE_MATCH, 'Match Lookup Table'),
    (WORKBOOK_TYPE_SPEC,  'SPEC Rules Workbook'),
    (WORKBOOK_TYPE_CAT,   'CAT Component Catalog'),
]

# ─────────────────────────────────────────────────────────────────────────────
# 1. MatchingWorkbookSet — stores the three Excel files per project
# ─────────────────────────────────────────────────────────────────────────────
class MatchingWorkbookSet(models.Model):
    """
    A set of three workbooks (Match, SPEC, CAT) linked to a SpecProject.
    Each project can have multiple versions, but only one can be active at a time.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        SpecProject,
        on_delete=models.CASCADE,
        related_name='matching_workbook_sets',
    )
    
    # Version/label for this set (e.g. "Rev A", "Initial", "2024-Q1")
    version_label = models.CharField(max_length=128, blank=True, default='')
    
    # File uploads
    match_file = models.FileField(
        upload_to='spec_customization/matching/%Y/%m/',
        null=True, blank=True,
        help_text='Match.xlsx — PDF component name to Excel catalog name mapping'
    )
    spec_file = models.FileField(
        upload_to='spec_customization/matching/%Y/%m/',
        null=True, blank=True,
        help_text='SPEC.xlsx — Specification rules (25 sheets)'
    )
    cat_file = models.FileField(
        upload_to='spec_customization/matching/%Y/%m/',
        null=True, blank=True,
        help_text='CAT.xlsx — Component catalog (23 sheets)'
    )
    
    # Metadata
    match_file_name = models.CharField(max_length=256, blank=True, default='')
    spec_file_name = models.CharField(max_length=256, blank=True, default='')
    cat_file_name = models.CharField(max_length=256, blank=True, default='')
    
    # Parsing status
    is_parsed = models.BooleanField(default=False, db_index=True)
    parse_error = models.TextField(blank=True, default='')
    rules_count = models.IntegerField(default=0, help_text='Number of MatchingRules extracted from Match.xlsx')
    spec_sheets_count = models.IntegerField(default=0)
    cat_sheets_count = models.IntegerField(default=0)
    
    # Only one active set per project
    is_active = models.BooleanField(default=True, db_index=True)
    
    # Audit
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='uploaded_matching_workbooks',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Matching Workbook Set'
        verbose_name_plural = 'Matching Workbook Sets'
        indexes = [
            models.Index(fields=['project', 'is_active']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self) -> str:
        label = self.version_label or 'Unnamed'
        return f"{self.project.name} — {label} [{self.rules_count} rules]"


# ─────────────────────────────────────────────────────────────────────────────
# 2. MatchingRule — parsed mapping from Match.xlsx
# ─────────────────────────────────────────────────────────────────────────────
class MatchingRule(models.Model):
    """
    Single component name mapping rule extracted from Match.xlsx.
    
    Example:
      pdf_component_name   = "GATE VALVE"
      catalog_component_name = "Gate Valve"
      cat_sheet_name       = "GateValve"
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workbook_set = models.ForeignKey(
        MatchingWorkbookSet,
        on_delete=models.CASCADE,
        related_name='rules',
    )
    
    # Mapping: PDF name → Excel catalog name
    pdf_component_name = models.CharField(
        max_length=256,
        db_index=True,
        help_text='Component name as it appears in PDF (e.g., "GATE VALVE", "90 DEGREE ELBOW")'
    )
    catalog_component_name = models.CharField(
        max_length=256,
        db_index=True,
        help_text='Corresponding name in Excel CAT workbook (e.g., "Gate Valve", "90 Degree Direction Change")'
    )
    
    # Derived CAT sheet name (normalized from catalog_component_name)
    cat_sheet_name = models.CharField(
        max_length=128,
        blank=True,
        help_text='CAT.xlsx sheet name (spaces removed, e.g., "GateValve", "90DegElbow")'
    )
    
    # Soft-coded matching configuration (JSON — component-specific overrides)
    matching_config = models.JSONField(
        default=dict,
        blank=True,
        help_text='Optional: size/rating/material overrides for this component type'
    )
    
    # Audit
    row_number = models.IntegerField(default=0, help_text='Row number in Match.xlsx for debugging')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['pdf_component_name']
        verbose_name = 'Matching Rule'
        verbose_name_plural = 'Matching Rules'
        indexes = [
            models.Index(fields=['workbook_set', 'pdf_component_name']),
            models.Index(fields=['catalog_component_name']),
        ]
        # Unique constraint: one mapping per component name per workbook set
        constraints = [
            models.UniqueConstraint(
                fields=['workbook_set', 'pdf_component_name'],
                name='unique_rule_per_workbook_set'
            )
        ]
    
    def __str__(self) -> str:
        return f"{self.pdf_component_name} → {self.catalog_component_name}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. ComponentMatchingResult — stores matching results (optional cache/audit)
# ─────────────────────────────────────────────────────────────────────────────
class ComponentMatchingResult(models.Model):
    """
    Optional: stores component matching results for audit trail and caching.
    Links a PDF-extracted component to its matched CAT commodity code.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workbook_set = models.ForeignKey(
        MatchingWorkbookSet,
        on_delete=models.CASCADE,
        related_name='matching_results',
    )
    
    # Input criteria
    pdf_component_name = models.CharField(max_length=256, db_index=True)
    nominal_size = models.CharField(max_length=32, blank=True, help_text='e.g., "2", "4", "DN50"')
    pressure_rating = models.CharField(max_length=32, blank=True, help_text='e.g., "150#", "300#", "PN16"')
    material_grade = models.CharField(max_length=64, blank=True, help_text='e.g., "A105", "316SS"')
    
    # Matching output
    matched_commodity_code = models.CharField(
        max_length=128,
        blank=True,
        help_text='IndustryCommodityCode from CAT.xlsx (e.g., "DHGA01CMA1")'
    )
    matched_description = models.TextField(blank=True, default='')
    
    # Match quality
    match_score = models.FloatField(default=0.0, help_text='Confidence score 0-1')
    match_method = models.CharField(
        max_length=64,
        blank=True,
        help_text='e.g., "exact", "fuzzy", "rule-based"'
    )
    
    # Full result payload (for debugging)
    result_data = models.JSONField(default=dict, blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Component Matching Result'
        verbose_name_plural = 'Component Matching Results'
        indexes = [
            models.Index(fields=['workbook_set', 'pdf_component_name']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self) -> str:
        return f"{self.pdf_component_name} → {self.matched_commodity_code}"
