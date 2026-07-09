"""
Onboarding & Offboarding Admin Configuration
"""
from django.contrib import admin
from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist
)


@admin.register(OnboardingRecord)
class OnboardingRecordAdmin(admin.ModelAdmin):
    list_display = ['employee_name', 'position', 'department', 'branch', 'joining_date', 'status', 'progress_percentage']
    list_filter = ['status', 'branch', 'department', 'joining_date']
    search_fields = ['employee_name', 'employee_email', 'employee_id', 'position']
    date_hierarchy = 'joining_date'
    ordering = ['-joining_date']


@admin.register(OffboardingRecord)
class OffboardingRecordAdmin(admin.ModelAdmin):
    list_display = ['employee_name', 'position', 'department', 'branch', 'last_working_day', 'exit_reason', 'status', 'progress_percentage']
    list_filter = ['status', 'branch', 'department', 'exit_reason', 'last_working_day']
    search_fields = ['employee_name', 'employee_email', 'employee_id', 'position']
    date_hierarchy = 'last_working_day'
    ordering = ['-last_working_day']


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ['equipment_type', 'item_name', 'serial_number', 'condition', 'assigned_date', 'returned_date']
    list_filter = ['equipment_type', 'condition']
    search_fields = ['item_name', 'serial_number', 'asset_tag']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['document_type', 'document_name', 'submitted', 'verified', 'verified_date']
    list_filter = ['document_type', 'submitted', 'verified']
    search_fields = ['document_name']


@admin.register(AccessProvisioning)
class AccessProvisioningAdmin(admin.ModelAdmin):
    list_display = ['access_type', 'access_name', 'account_username', 'provisioned', 'revoked']
    list_filter = ['access_type', 'provisioned', 'revoked']
    search_fields = ['access_name', 'account_username']


@admin.register(Checklist)
class ChecklistAdmin(admin.ModelAdmin):
    list_display = ['task_name', 'priority', 'completed', 'due_date']
    list_filter = ['priority', 'completed']
    search_fields = ['task_name', 'description']
    date_hierarchy = 'due_date'
