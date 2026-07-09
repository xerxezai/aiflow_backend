"""
Site Visit Tracking — Database Models
======================================
Tracks engineers visiting client sites with GPS verification, approval workflow,
and integration with the timesheet system for unified attendance reporting.
"""
import uuid
from decimal import Decimal
from datetime import timedelta
from django.db import models
from django.utils import timezone
from apps.users.models import User
from . import config as site_config


# ─────────────────────────────────────────────────────────────────────────────
# 1. ClientSite — Registry of client locations for site visits
# ─────────────────────────────────────────────────────────────────────────────

class ClientSite(models.Model):
    """
    Client locations where employees can perform site visits.
    Pre-registered by Admin/Manager with GPS coordinates for geofencing.
    """
    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name            = models.CharField(max_length=200, help_text='Site name (e.g., "ADNOC Tower 2")')
    client_name     = models.CharField(max_length=200, db_index=True, help_text='Client/Company name')
    address         = models.TextField()
    city            = models.CharField(max_length=100, default='Abu Dhabi')
    country         = models.CharField(max_length=100, default='UAE')
    
    # GPS coordinates (decimal degrees)
    latitude        = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True,
        help_text='Latitude in decimal degrees (e.g., 24.4539)'
    )
    longitude       = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True,
        help_text='Longitude in decimal degrees (e.g., 54.3773)'
    )
    
    # Geofencing configuration (soft-coded via env var default)
    geofence_radius = models.IntegerField(
        default=site_config.GEOFENCE_RADIUS,
        help_text='Radius in meters for auto check-in geofence'
    )
    
    # Site-specific settings
    require_photo   = models.BooleanField(default=site_config.REQUIRE_PHOTO)
    require_approval= models.BooleanField(default=site_config.REQUIRE_APPROVAL)
    
    # QR code for check-in (optional method)
    qr_code         = models.CharField(max_length=100, unique=True, blank=True)
    
    # Status
    is_active       = models.BooleanField(default=True, db_index=True)
    notes           = models.TextField(blank=True)
    
    created_by      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_sites'
    )
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'site_visit_client_site'
        ordering = ['client_name', 'name']
        verbose_name = 'Client Site'
        verbose_name_plural = 'Client Sites'
        indexes = [
            models.Index(fields=['client_name', 'is_active']),
            models.Index(fields=['is_active', 'created_at']),
        ]
    
    def __str__(self):
        return f'{self.client_name} — {self.name}'
    
    def save(self, *args, **kwargs):
        # Generate QR code on first save if not provided
        if not self.qr_code:
            self.qr_code = f'SITE_{str(self.id).replace("-", "")[:12].upper()}'
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 2. SiteVisitRequest — Pre-approval workflow (similar to leave requests)
# ─────────────────────────────────────────────────────────────────────────────

class SiteVisitRequestStatus(models.TextChoices):
    PENDING     = 'PENDING',     'Pending Approval'
    APPROVED    = 'APPROVED',    'Approved'
    REJECTED    = 'REJECTED',    'Rejected'
    CANCELLED   = 'CANCELLED',   'Cancelled'


class SiteVisitRequest(models.Model):
    """
    Employee requests permission for a site visit (similar to leave request).
    Manager approves before employee can check in at the site.
    """
    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Employee (RAD AI user or plain text for non-system employees)
    employee        = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='site_visit_requests'
    )
    employee_code   = models.CharField(max_length=30, db_index=True, blank=True)
    employee_name   = models.CharField(max_length=255)
    department      = models.CharField(max_length=100, blank=True)
    
    # Site details
    site            = models.ForeignKey(
        ClientSite, on_delete=models.PROTECT,
        related_name='visit_requests'
    )
    
    # Visit schedule
    start_date      = models.DateField()
    end_date        = models.DateField()
    purpose         = models.TextField(help_text='Reason for site visit')
    
    # Approval workflow
    status          = models.CharField(
        max_length=20, choices=SiteVisitRequestStatus.choices,
        default=SiteVisitRequestStatus.PENDING, db_index=True
    )
    approved_by     = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_site_visit_requests'
    )
    approved_at     = models.DateTimeField(null=True, blank=True)
    reviewer_note   = models.TextField(blank=True)
    
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'site_visit_request'
        ordering = ['-created_at']
        verbose_name = 'Site Visit Request'
        verbose_name_plural = 'Site Visit Requests'
        indexes = [
            models.Index(fields=['employee_code', 'start_date', 'end_date']),
            models.Index(fields=['status', 'start_date']),
            models.Index(fields=['site', 'status']),
        ]
    
    def __str__(self):
        site_name = self.site.name if self.site else '?'
        return f'{self.employee_name} → {site_name} ({self.start_date} – {self.end_date}) [{self.status}]'
    
    def save(self, *args, **kwargs):
        # Auto-populate employee fields from User FK
        if self.employee:
            u = self.employee
            if not self.employee_name:
                self.employee_name = f'{u.first_name} {u.last_name}'.strip() or u.email
            if not self.employee_code:
                try:
                    from apps.rbac.models import UserProfile
                    profile = UserProfile.objects.filter(user=u).first()
                    if profile and profile.employee_code:
                        self.employee_code = profile.employee_code
                except:
                    pass
            if not self.department:
                try:
                    from apps.rbac.models import UserProfile
                    profile = UserProfile.objects.filter(user=u).first()
                    if profile and profile.department:
                        self.department = profile.department
                except:
                    pass
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SiteVisitCheckIn — Actual GPS check-in/out events
# ─────────────────────────────────────────────────────────────────────────────

class CheckInMethod(models.TextChoices):
    GPS         = 'GPS',        'GPS Location'
    QR_CODE     = 'QR_CODE',    'QR Code Scan'
    MANUAL      = 'MANUAL',     'Manual Entry'
    GEOFENCE    = 'GEOFENCE',   'Auto Geofence'


class SiteVisitCheckIn(models.Model):
    """
    Individual check-in/check-out event at a client site.
    Captures GPS, photo, timestamp for audit and payroll integration.
    """
    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Link to pre-approved request (optional if approval disabled)
    visit_request   = models.ForeignKey(
        SiteVisitRequest, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='check_ins'
    )
    
    # Employee identity
    employee        = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='site_check_ins'
    )
    employee_code   = models.CharField(max_length=30, db_index=True)
    employee_name   = models.CharField(max_length=255)
    
    # Site
    site            = models.ForeignKey(
        ClientSite, on_delete=models.PROTECT,
        related_name='check_ins'
    )
    
    # Check-in timestamp and location
    check_in_time   = models.DateTimeField(db_index=True)
    check_in_lat    = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    check_in_lon    = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    check_in_accuracy = models.FloatField(
        null=True, blank=True,
        help_text='GPS accuracy in meters'
    )
    check_in_method = models.CharField(
        max_length=20, choices=CheckInMethod.choices,
        default=CheckInMethod.GPS
    )
    check_in_photo  = models.FileField(
        upload_to='site_visits/check_in/', null=True, blank=True
    )
    
    # Check-out timestamp and location
    check_out_time  = models.DateTimeField(null=True, blank=True, db_index=True)
    check_out_lat   = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    check_out_lon   = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    check_out_accuracy = models.FloatField(null=True, blank=True)
    check_out_method = models.CharField(
        max_length=20, choices=CheckInMethod.choices,
        null=True, blank=True
    )
    check_out_photo = models.FileField(
        upload_to='site_visits/check_out/', null=True, blank=True
    )
    
    # Computed duration (hours)
    duration_hours  = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='Total hours on-site'
    )
    
    # Validation flags
    geofence_valid  = models.BooleanField(
        default=False,
        help_text='Check-in within geofence radius'
    )
    gps_accuracy_ok = models.BooleanField(
        default=True,
        help_text='GPS accuracy meets threshold'
    )
    
    # Notes
    employee_note   = models.TextField(blank=True)
    admin_note      = models.TextField(blank=True)
    
    # Offline sync support
    offline_created = models.BooleanField(default=False)
    synced_at       = models.DateTimeField(null=True, blank=True)
    
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'site_visit_check_in'
        ordering = ['-check_in_time']
        verbose_name = 'Site Visit Check-In'
        verbose_name_plural = 'Site Visit Check-Ins'
        indexes = [
            models.Index(fields=['employee_code', 'check_in_time']),
            models.Index(fields=['site', 'check_in_time']),
            models.Index(fields=['check_in_time', 'check_out_time']),
        ]
    
    def __str__(self):
        site_name = self.site.name if self.site else '?'
        status = 'in-progress' if not self.check_out_time else f'{self.duration_hours}h'
        return f'{self.employee_name} @ {site_name} ({status})'
    
    def save(self, *args, **kwargs):
        # Auto-populate employee fields from User FK
        if self.employee and not self.employee_code:
            try:
                from apps.rbac.models import UserProfile
                profile = UserProfile.objects.filter(user=self.employee).first()
                if profile and profile.employee_code:
                    self.employee_code = profile.employee_code
            except:
                pass
        
        # Calculate duration if both check-in and check-out are present
        if self.check_in_time and self.check_out_time and not self.duration_hours:
            delta = self.check_out_time - self.check_in_time
            self.duration_hours = Decimal(str(delta.total_seconds() / 3600))
        
        # Validate geofence if site has coordinates
        if self.site and self.site.latitude and self.site.longitude:
            if self.check_in_lat and self.check_in_lon:
                self.geofence_valid = self._is_within_geofence(
                    float(self.check_in_lat),
                    float(self.check_in_lon),
                    float(self.site.latitude),
                    float(self.site.longitude),
                    self.site.geofence_radius
                )
        
        # Validate GPS accuracy
        if self.check_in_accuracy:
            self.gps_accuracy_ok = self.check_in_accuracy <= site_config.GPS_ACCURACY_THRESHOLD
        
        super().save(*args, **kwargs)
    
    @staticmethod
    def _is_within_geofence(lat1, lon1, lat2, lon2, radius_meters):
        """
        Calculate if point (lat1, lon1) is within radius_meters of (lat2, lon2).
        Uses Haversine formula for great-circle distance.
        """
        from math import radians, sin, cos, sqrt, atan2
        R = 6371000  # Earth radius in meters
        
        phi1 = radians(lat1)
        phi2 = radians(lat2)
        delta_phi = radians(lat2 - lat1)
        delta_lambda = radians(lon2 - lon1)
        
        a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        distance = R * c
        
        return distance <= radius_meters
