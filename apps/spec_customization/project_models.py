"""
Spec Customization — Project Model
====================================

Lightweight, RBAC-aware project organiser for the Paper Spec PDF extraction
workflow. Mirrors the additive pattern used by ``NonTeffProject`` in
``apps.non_teff_metadata`` so the front-end can reuse the same UX.

A ``SpecProject`` groups one or more extraction jobs under a logical
engineering project (e.g. "ADNOC LNG Train-3 PMS"). The link to existing
``PaperSpecDocument`` records is intentionally soft — the document already
has a ``project_id: CharField`` field, populated at upload time with this
model's ``project_id`` UUID. No core extraction logic is touched.

Status lifecycle (soft-coded — adjust without code changes):
    active → on_hold → completed → archived
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class SpecProject(models.Model):
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
    status       = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    tags         = models.JSONField(default=list, blank=True)
    metadata     = models.JSONField(default=dict, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='spec_customization_projects',
    )

    class Meta:
        ordering = ['-updated_at', '-created_at']
        verbose_name = 'Spec Customization Project'
        verbose_name_plural = 'Spec Customization Projects'
        indexes = [
            models.Index(fields=['status', '-updated_at']),
            models.Index(fields=['created_by', '-updated_at']),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}] ({self.project_id})"
