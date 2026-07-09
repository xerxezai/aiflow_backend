"""
Site Visit Tracking — DRF ViewSets and APIViews
================================================
REST API endpoints for managing client sites, visit requests, and check-ins.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q
from datetime import timedelta

from .models import ClientSite, SiteVisitRequest, SiteVisitCheckIn
from .serializers import (
    ClientSiteListSerializer, ClientSiteDetailSerializer,
    SiteVisitRequestListSerializer, SiteVisitRequestDetailSerializer,
    SiteVisitRequestCreateSerializer,
    SiteVisitCheckInListSerializer, SiteVisitCheckInDetailSerializer,
    SiteVisitCheckInCreateSerializer, SiteVisitCheckOutSerializer
)
from . import config as site_config


# ─────────────────────────────────────────────────────────────────────────────
# ClientSite ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class ClientSiteViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for client sites.
    List, create, update, delete client locations.
    """
    permission_classes = [IsAuthenticated]
    queryset = ClientSite.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'list':
            return ClientSiteListSerializer
        return ClientSiteDetailSerializer
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Filter by active status
        if self.request.query_params.get('is_active'):
            qs = qs.filter(is_active=True)
        
        # Search by name or client
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | 
                Q(client_name__icontains=search) |
                Q(city__icontains=search)
            )
        
        return qs
    
    def get_serializer_context(self):
        """Pass user GPS coordinates for distance calculation."""
        context = super().get_serializer_context()
        context['user_lat'] = self.request.query_params.get('lat')
        context['user_lon'] = self.request.query_params.get('lon')
        return context
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def generate_qr(self, request, pk=None):
        """Generate or regenerate QR code for site."""
        site = self.get_object()
        import uuid
        site.qr_code = f'SITE_{str(uuid.uuid4()).replace("-", "")[:12].upper()}'
        site.save()
        return Response({
            'qr_code': site.qr_code,
            'message': 'QR code generated successfully'
        })


# ─────────────────────────────────────────────────────────────────────────────
# SiteVisitRequest ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class SiteVisitRequestViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for site visit requests.
    Employees submit requests, managers approve/reject.
    """
    permission_classes = [IsAuthenticated]
    queryset = SiteVisitRequest.objects.all()
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return SiteVisitRequestCreateSerializer
        if self.action == 'list':
            return SiteVisitRequestListSerializer
        return SiteVisitRequestDetailSerializer
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        
        # Filter by employee (my requests)
        if self.request.query_params.get('my_requests'):
            try:
                from apps.rbac.models import UserProfile
                profile = UserProfile.objects.get(user=self.request.user)
                if profile.employee_code:
                    qs = qs.filter(employee_code=profile.employee_code)
            except:
                qs = qs.filter(employee=self.request.user)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            qs = qs.filter(start_date__gte=start_date)
        if end_date:
            qs = qs.filter(end_date__lte=end_date)
        
        return qs
    
    def perform_create(self, serializer):
        """Auto-populate employee fields from current user."""
        employee = self.request.user
        data = {'employee': employee}
        
        # Get employee code from profile
        try:
            from apps.rbac.models import UserProfile
            profile = UserProfile.objects.get(user=employee)
            data['employee_code'] = profile.employee_code or ''
            data['department'] = profile.department or ''
        except:
            pass
        
        if not serializer.validated_data.get('employee_name'):
            data['employee_name'] = f'{employee.first_name} {employee.last_name}'.strip() or employee.email
        
        serializer.save(**data)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Manager approves site visit request."""
        obj = self.get_object()
        
        if obj.status != 'PENDING':
            return Response(
                {'error': 'Request is not pending'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        obj.status = 'APPROVED'
        obj.approved_by = request.user
        obj.approved_at = timezone.now()
        obj.reviewer_note = request.data.get('note', '')
        obj.save()
        
        return Response({
            'message': 'Site visit request approved',
            'status': obj.status
        })
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Manager rejects site visit request."""
        obj = self.get_object()
        
        if obj.status != 'PENDING':
            return Response(
                {'error': 'Request is not pending'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        obj.status = 'REJECTED'
        obj.approved_by = request.user
        obj.approved_at = timezone.now()
        obj.reviewer_note = request.data.get('note', '')
        obj.save()
        
        return Response({
            'message': 'Site visit request rejected',
            'status': obj.status
        })


# ─────────────────────────────────────────────────────────────────────────────
# SiteVisitCheckIn ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class SiteVisitCheckInViewSet(viewsets.ModelViewSet):
    """
    Check-in/out operations for site visits.
    Employees check in with GPS, check out when leaving.
    """
    permission_classes = [IsAuthenticated]
    queryset = SiteVisitCheckIn.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return SiteVisitCheckInCreateSerializer
        if self.action == 'list':
            return SiteVisitCheckInListSerializer
        return SiteVisitCheckInDetailSerializer
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Filter by employee (my check-ins)
        if self.request.query_params.get('my_checkins'):
            try:
                from apps.rbac.models import UserProfile
                profile = UserProfile.objects.get(user=self.request.user)
                if profile.employee_code:
                    qs = qs.filter(employee_code=profile.employee_code)
            except:
                qs = qs.filter(employee=self.request.user)
        
        # Filter by check-out status (active visits)
        if self.request.query_params.get('active_only'):
            qs = qs.filter(check_out_time__isnull=True)
        
        # Filter by site
        site_id = self.request.query_params.get('site')
        if site_id:
            qs = qs.filter(site_id=site_id)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            qs = qs.filter(check_in_time__gte=start_date)
        if end_date:
            qs = qs.filter(check_in_time__lte=end_date)
        
        return qs
    
    def perform_create(self, serializer):
        """Auto-populate employee fields and sync to timesheet."""
        employee = self.request.user
        data = {'employee': employee}
        
        # Get employee code from profile
        try:
            from apps.rbac.models import UserProfile
            profile = UserProfile.objects.get(user=employee)
            data['employee_code'] = profile.employee_code or ''
        except:
            pass
        
        if not serializer.validated_data.get('employee_name'):
            data['employee_name'] = f'{employee.first_name} {employee.last_name}'.strip() or employee.email
        
        check_in = serializer.save(**data)
        
        # Sync to timesheet if enabled
        if site_config.SYNC_TO_TIMESHEET:
            self._sync_to_timesheet(check_in, 'check_in')
    
    @action(detail=True, methods=['post'])
    def checkout(self, request, pk=None):
        """Check out from site visit."""
        obj = self.get_object()
        
        if obj.check_out_time:
            return Response(
                {'error': 'Already checked out'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = SiteVisitCheckOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Update check-out fields
        obj.check_out_time = timezone.now()
        obj.check_out_lat = serializer.validated_data.get('check_out_lat')
        obj.check_out_lon = serializer.validated_data.get('check_out_lon')
        obj.check_out_accuracy = serializer.validated_data.get('check_out_accuracy')
        obj.check_out_method = serializer.validated_data.get('check_out_method', 'GPS')
        obj.check_out_photo = serializer.validated_data.get('check_out_photo')
        
        if serializer.validated_data.get('employee_note'):
            obj.employee_note = serializer.validated_data['employee_note']
        
        # Calculate duration
        delta = obj.check_out_time - obj.check_in_time
        obj.duration_hours = round(delta.total_seconds() / 3600, 2)
        
        obj.save()
        
        # Sync to timesheet
        if site_config.SYNC_TO_TIMESHEET:
            self._sync_to_timesheet(obj, 'check_out')
        
        return Response({
            'message': 'Checked out successfully',
            'duration_hours': obj.duration_hours
        })
    
    @action(detail=False, methods=['get'])
    def live(self, request):
        """Get all employees currently on-site (not checked out)."""
        active = self.queryset.filter(
            check_out_time__isnull=True,
            check_in_time__gte=timezone.now() - timedelta(hours=site_config.AUTO_CHECKOUT_HOURS)
        ).select_related('site').order_by('-check_in_time')
        
        serializer = SiteVisitCheckInListSerializer(active, many=True)
        return Response({
            'count': active.count(),
            'check_ins': serializer.data
        })
    
    def _sync_to_timesheet(self, check_in, event_type):
        """
        Sync site visit to TimesheetEvent table for unified attendance.
        Creates SITE_VISIT event type that appears in Live/Daily reports.
        """
        try:
            from apps.timesheet.models import TimesheetEvent
            
            if event_type == 'check_in':
                TimesheetEvent.objects.create(
                    employee_code=check_in.employee_code,
                    employee_name=check_in.employee_name,
                    event_time=check_in.check_in_time,
                    event_type='SITE_IN',  # IN for check-in
                    event_details=f'Site visit: {check_in.site.name}',
                    source='site_visit',
                    latitude=float(check_in.check_in_lat) if check_in.check_in_lat else None,
                    longitude=float(check_in.check_in_lon) if check_in.check_in_lon else None,
                )
            else:  # check_out
                TimesheetEvent.objects.create(
                    employee_code=check_in.employee_code,
                    employee_name=check_in.employee_name,
                    event_time=check_in.check_out_time,
                    event_type='SITE_OUT',  # OUT for check-out
                    event_details=f'Site visit: {check_in.site.name} ({check_in.duration_hours}h)',
                    source='site_visit',
                    latitude=float(check_in.check_out_lat) if check_in.check_out_lat else None,
                    longitude=float(check_in.check_out_lon) if check_in.check_out_lon else None,
                )
        except Exception as e:
            # Don't fail the check-in/out if timesheet sync fails
            print(f'Warning: Failed to sync to timesheet: {e}')
