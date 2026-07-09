"""
HR Core Signals - Automated Employee Data Sync

Handles automatic updates between EmployeeMaster and related models.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

# Signals will be implemented in Phase 2 (dual-write)
# For now, this file exists to prevent import errors

User = get_user_model()
