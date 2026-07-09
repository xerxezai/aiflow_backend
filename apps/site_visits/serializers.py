"""
Site Visit Tracking — DRF Serializers
======================================
REST API serializers for client sites, visit requests, and check-ins.
"""
from rest_framework import serializers
from .models import ClientSite, SiteVisitRequest, SiteVisitCheckIn


# ─────────────────────────────────────────────────────────────────────────────
# ClientSite Serializers
# ─────────────────────────────────────────────────────────────────────────────

class ClientSiteListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for site lists/dropdowns."""
    distance_km = serializers.SerializerMethodField()
    
    class Meta:
        model = ClientSite
        fields = [
            'id', 'name', 'client_name', 'city', 'country',
            'latitude', 'longitude', 'geofence_radius',
            'is_active', 'distance_km'
        ]
    
    def get_distance_km(self, obj):
        """Calculate distance from user's current location (if provided in context)."""
        user_lat = self.context.get('user_lat')
        user_lon = self.context.get('user_lon')
        if user_lat and user_lon and obj.latitude and obj.longitude:
            from math import radians, sin, cos, sqrt, atan2
            R = 6371  # Earth radius in km
            phi1 = radians(float(user_lat))
            phi2 = radians(float(obj.latitude))
            delta_phi = radians(float(obj.latitude) - float(user_lat))
            delta_lambda = radians(float(obj.longitude) - float(user_lon))
            a = sin(delta_phi/2)**2 + cos(phi1) * cos(phi2) * sin(delta_lambda/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            return round(R * c, 2)
        return None


class ClientSiteDetailSerializer(serializers.ModelSerializer):
    """Full serializer with all site details."""
    created_by_name = serializers.SerializerMethodField()
    active_requests_count = serializers.SerializerMethodField()
    total_visits_count = serializers.SerializerMethodField()
    
    class Meta:
        model = ClientSite
        fields = '__all__'
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return f'{obj.created_by.first_name} {obj.created_by.last_name}'.strip() or obj.created_by.email
        return None
    
    def get_active_requests_count(self, obj):
        return obj.visit_requests.filter(status='APPROVED').count()
    
    def get_total_visits_count(self, obj):
        return obj.check_ins.count()


# ─────────────────────────────────────────────────────────────────────────────
# SiteVisitRequest Serializers
# ─────────────────────────────────────────────────────────────────────────────

class SiteVisitRequestListSerializer(serializers.ModelSerializer):
    """List view with site details."""
    site_name = serializers.CharField(source='site.name', read_only=True)
    site_client = serializers.CharField(source='site.client_name', read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    days_until = serializers.SerializerMethodField()
    
    class Meta:
        model = SiteVisitRequest
        fields = [
            'id', 'employee_name', 'employee_code', 'department',
            'site', 'site_name', 'site_client',
            'start_date', 'end_date', 'purpose',
            'status', 'approved_by_name', 'approved_at',
            'created_at', 'days_until'
        ]
    
    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return f'{obj.approved_by.first_name} {obj.approved_by.last_name}'.strip() or obj.approved_by.email
        return None
    
    def get_days_until(self, obj):
        """Days until visit starts (negative if already started)."""
        from django.utils import timezone
        today = timezone.now().date()
        delta = (obj.start_date - today).days
        return delta


class SiteVisitRequestDetailSerializer(serializers.ModelSerializer):
    """Full request details including check-ins."""
    site_details = ClientSiteListSerializer(source='site', read_only=True)
    check_ins = serializers.SerializerMethodField()
    
    class Meta:
        model = SiteVisitRequest
        fields = '__all__'
    
    def get_check_ins(self, obj):
        """Return check-ins associated with this request."""
        check_ins = obj.check_ins.all()[:10]  # Latest 10
        return SiteVisitCheckInListSerializer(check_ins, many=True).data


class SiteVisitRequestCreateSerializer(serializers.ModelSerializer):
    """Create/update site visit request."""
    class Meta:
        model = SiteVisitRequest
        fields = [
            'employee', 'employee_code', 'employee_name', 'department',
            'site', 'start_date', 'end_date', 'purpose'
        ]
    
    def validate(self, data):
        """Validate date range and approval requirements."""
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError("start_date must be before end_date")
        
        # Check if approval is required for this site
        site = data.get('site')
        if site and site.require_approval:
            from . import config as site_config
            if not site_config.REQUIRE_APPROVAL:
                # Auto-approve if globally disabled
                data['status'] = 'APPROVED'
        
        return data


# ─────────────────────────────────────────────────────────────────────────────
# SiteVisitCheckIn Serializers
# ─────────────────────────────────────────────────────────────────────────────

class SiteVisitCheckInListSerializer(serializers.ModelSerializer):
    """List view with computed fields."""
    site_name = serializers.CharField(source='site.name', read_only=True)
    site_client = serializers.CharField(source='site.client_name', read_only=True)
    is_checked_out = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    
    class Meta:
        model = SiteVisitCheckIn
        fields = [
            'id', 'employee_name', 'employee_code',
            'site', 'site_name', 'site_client',
            'check_in_time', 'check_out_time', 'duration_hours',
            'check_in_method', 'geofence_valid', 'gps_accuracy_ok',
            'is_checked_out', 'status_label',
            'created_at'
        ]
    
    def get_is_checked_out(self, obj):
        return obj.check_out_time is not None
    
    def get_status_label(self, obj):
        if obj.check_out_time:
            return f'Completed ({obj.duration_hours}h)'
        else:
            return 'In Progress'


class SiteVisitCheckInDetailSerializer(serializers.ModelSerializer):
    """Full check-in details with GPS and photos."""
    site_details = ClientSiteListSerializer(source='site', read_only=True)
    request_details = SiteVisitRequestListSerializer(source='visit_request', read_only=True)
    
    class Meta:
        model = SiteVisitCheckIn
        fields = '__all__'


class SiteVisitCheckInCreateSerializer(serializers.ModelSerializer):
    """Create check-in event."""
    class Meta:
        model = SiteVisitCheckIn
        fields = [
            'visit_request', 'employee', 'employee_code', 'employee_name',
            'site', 'check_in_time',
            'check_in_lat', 'check_in_lon', 'check_in_accuracy',
            'check_in_method', 'check_in_photo',
            'employee_note'
        ]
    
    def validate(self, data):
        """Validate GPS accuracy and approval requirements."""
        from . import config as site_config
        
        # Check GPS accuracy
        accuracy = data.get('check_in_accuracy')
        if accuracy and accuracy > site_config.GPS_ACCURACY_THRESHOLD:
            if not site_config.ALLOW_OUT_OF_GEOFENCE:
                raise serializers.ValidationError(
                    f'GPS accuracy ({accuracy}m) exceeds threshold ({site_config.GPS_ACCURACY_THRESHOLD}m)'
                )
        
        # Check if approval is required
        site = data.get('site')
        if site and site.require_approval:
            # Look for approved request covering this date
            check_in_date = data.get('check_in_time').date()
            employee_code = data.get('employee_code')
            
            approved_request = SiteVisitRequest.objects.filter(
                employee_code=employee_code,
                site=site,
                status='APPROVED',
                start_date__lte=check_in_date,
                end_date__gte=check_in_date
            ).first()
            
            if not approved_request and site_config.REQUIRE_APPROVAL:
                raise serializers.ValidationError(
                    'No approved site visit request found for this date. Please request approval first.'
                )
            
            data['visit_request'] = approved_request
        
        return data


class SiteVisitCheckOutSerializer(serializers.Serializer):
    """Update check-in with check-out details."""
    check_out_lat = serializers.DecimalField(max_digits=10, decimal_places=7, required=False)
    check_out_lon = serializers.DecimalField(max_digits=10, decimal_places=7, required=False)
    check_out_accuracy = serializers.FloatField(required=False)
    check_out_method = serializers.ChoiceField(choices=['GPS', 'QR_CODE', 'MANUAL'], required=False)
    check_out_photo = serializers.FileField(required=False)
    employee_note = serializers.CharField(required=False, allow_blank=True)
