"""
Site Visit Tracking — Django Admin Configuration
=================================================
"""
from django.contrib import admin
from .models import ClientSite, SiteVisitRequest, SiteVisitCheckIn


@admin.register(ClientSite)
class ClientSiteAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'client_name', 'city', 'country',
        'geofence_radius', 'is_active', 'created_at'
    ]
    list_filter = ['is_active', 'city', 'country', 'require_approval', 'require_photo']
    search_fields = ['name', 'client_name', 'address', 'qr_code']
    readonly_fields = ['id', 'qr_code', 'created_at', 'updated_at']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'client_name', 'address', 'city', 'country')
        }),
        ('GPS & Geofencing', {
            'fields': ('latitude', 'longitude', 'geofence_radius')
        }),
        ('Settings', {
            'fields': ('require_photo', 'require_approval', 'is_active', 'notes')
        }),
        ('QR Code', {
            'fields': ('qr_code',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(SiteVisitRequest)
class SiteVisitRequestAdmin(admin.ModelAdmin):
    list_display = [
        'employee_name', 'site', 'start_date', 'end_date',
        'status', 'approved_by', 'created_at'
    ]
    list_filter = ['status', 'start_date', 'created_at']
    search_fields = ['employee_name', 'employee_code', 'site__name', 'purpose']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Employee', {
            'fields': ('employee', 'employee_code', 'employee_name', 'department')
        }),
        ('Visit Details', {
            'fields': ('site', 'start_date', 'end_date', 'purpose')
        }),
        ('Approval', {
            'fields': ('status', 'approved_by', 'approved_at', 'reviewer_note')
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(SiteVisitCheckIn)
class SiteVisitCheckInAdmin(admin.ModelAdmin):
    list_display = [
        'employee_name', 'site', 'check_in_time', 'check_out_time',
        'duration_hours', 'geofence_valid', 'gps_accuracy_ok'
    ]
    list_filter = [
        'check_in_time', 'geofence_valid', 'gps_accuracy_ok',
        'check_in_method', 'offline_created'
    ]
    search_fields = ['employee_name', 'employee_code', 'site__name']
    readonly_fields = [
        'id', 'duration_hours', 'geofence_valid', 'gps_accuracy_ok',
        'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Employee', {
            'fields': ('employee', 'employee_code', 'employee_name')
        }),
        ('Site', {
            'fields': ('site', 'visit_request')
        }),
        ('Check-In', {
            'fields': (
                'check_in_time', 'check_in_lat', 'check_in_lon',
                'check_in_accuracy', 'check_in_method', 'check_in_photo'
            )
        }),
        ('Check-Out', {
            'fields': (
                'check_out_time', 'check_out_lat', 'check_out_lon',
                'check_out_accuracy', 'check_out_method', 'check_out_photo'
            )
        }),
        ('Validation', {
            'fields': ('duration_hours', 'geofence_valid', 'gps_accuracy_ok')
        }),
        ('Notes', {
            'fields': ('employee_note', 'admin_note')
        }),
        ('Offline Sync', {
            'fields': ('offline_created', 'synced_at'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
