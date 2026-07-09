"""
API Views for Activity Tracking System
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Count, Avg, Q
from datetime import timedelta
import logging

from .models import SystemActivity, ActivityStream, ActivityStatistics, UserSession
from .serializers import (
    SystemActivitySerializer,
    ActivityStreamSerializer,
    ActivityStatisticsSerializer,
    UserSessionSerializer,
    ActivitySummarySerializer,
)
from .tracker import ActivityTracker

logger = logging.getLogger(__name__)


class SystemActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing system activities
    Read-only access to activity logs
    """
    queryset = SystemActivity.objects.all()
    serializer_class = SystemActivitySerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['activity_type', 'category', 'severity', 'status', 'user']
    search_fields = ['description', 'user__username', 'user__email']
    ordering_fields = ['timestamp', 'severity']
    ordering = ['-timestamp']
    
    def get_queryset(self):
        """Filter activities based on query parameters"""
        queryset = super().get_queryset()

        # Security: non-staff users may only see their own activity
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            queryset = queryset.filter(user=self.request.user)

        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            queryset = queryset.filter(timestamp__gte=start_date)
        if end_date:
            queryset = queryset.filter(timestamp__lte=end_date)
        
        # Filter by time range (hours)
        hours = self.request.query_params.get('hours')
        if hours:
            try:
                hours = int(hours)
                cutoff = timezone.now() - timedelta(hours=hours)
                queryset = queryset.filter(timestamp__gte=cutoff)
            except ValueError:
                pass
        
        return queryset.select_related('user')
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent activities (last 50 by default)"""
        limit = int(request.query_params.get('limit', 50))
        category = request.query_params.get('category')
        
        queryset = self.get_queryset()
        
        if category and category != 'all':
            queryset = queryset.filter(category=category)
        
        activities = queryset[:limit]
        serializer = self.get_serializer(activities, many=True)
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get activity statistics"""
        now = timezone.now()
        
        # Last hour
        last_hour = now - timedelta(hours=1)
        total_last_hour = SystemActivity.objects.filter(
            timestamp__gte=last_hour
        ).count()
        
        # Last 24 hours
        last_24h = now - timedelta(hours=24)
        activities_24h = SystemActivity.objects.filter(
            timestamp__gte=last_24h
        )
        total_last_24h = activities_24h.count()
        
        # By category
        by_category = dict(
            activities_24h.values('category')
            .annotate(count=Count('id'))
            .values_list('category', 'count')
        )
        
        # By severity
        by_severity = dict(
            activities_24h.values('severity')
            .annotate(count=Count('id'))
            .values_list('severity', 'count')
        )
        
        # By status
        by_status = dict(
            activities_24h.values('status')
            .annotate(count=Count('id'))
            .values_list('status', 'count')
        )
        
        # Success rate
        total_with_status = activities_24h.filter(
            status__in=['success', 'failure']
        ).count()
        success_count = activities_24h.filter(status='success').count()
        success_rate = (success_count / total_with_status * 100) if total_with_status > 0 else 100.0
        
        # Average duration
        avg_duration = activities_24h.filter(
            details__has_key='duration'
        ).aggregate(Avg('details__duration'))['details__duration__avg'] or 0
        
        # Active users
        active_users = UserSession.objects.filter(is_active=True).count()
        
        # Top activities
        top_activities = list(
            activities_24h.values('activity_type')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
            .values_list('activity_type', 'count')
        )
        
        data = {
            'total_last_hour': total_last_hour,
            'total_last_24h': total_last_24h,
            'by_category': by_category,
            'by_severity': by_severity,
            'by_status': by_status,
            'success_rate': round(success_rate, 2),
            'average_duration': round(avg_duration, 2),
            'active_users': active_users,
            'top_activities': top_activities,
        }
        
        serializer = ActivitySummarySerializer(data)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def by_user(self, request):
        """Get activities grouped by user"""
        hours = int(request.query_params.get('hours', 24))
        cutoff = timezone.now() - timedelta(hours=hours)
        
        activities = (
            SystemActivity.objects.filter(timestamp__gte=cutoff)
            .values('user__username', 'user__email')
            .annotate(
                total=Count('id'),
                success=Count('id', filter=Q(status='success')),
                failure=Count('id', filter=Q(status='failure')),
            )
            .order_by('-total')[:20]
        )
        
        return Response(list(activities))


class ActivityStreamViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing activity streams
    """
    queryset = ActivityStream.objects.all()
    serializer_class = ActivityStreamSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['stream_type', 'is_active']
    ordering = ['-created_at']


class ActivityStatisticsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing activity statistics
    """
    queryset = ActivityStatistics.objects.all()
    serializer_class = ActivityStatisticsSerializer
    permission_classes = [IsAuthenticated]
    ordering = ['-period_start']
    
    @action(detail=False, methods=['get'])
    def latest(self, request):
        """Get the latest statistics"""
        stats = ActivityStatistics.objects.order_by('-period_end').first()
        
        if stats:
            serializer = self.get_serializer(stats)
            return Response(serializer.data)
        else:
            return Response(
                {'message': 'No statistics available yet'},
                status=status.HTTP_404_NOT_FOUND
            )


class UserSessionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing user sessions
    """
    queryset = UserSession.objects.all()
    serializer_class = UserSessionSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['user', 'is_active']
    ordering = ['-started_at']
    
    def get_queryset(self):
        """Filter sessions based on query parameters"""
        queryset = super().get_queryset()
        
        # Show only active sessions if requested
        if self.request.query_params.get('active_only') == 'true':
            queryset = queryset.filter(is_active=True)
        
        return queryset.select_related('user')
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        """Get all active user sessions"""
        sessions = UserSession.objects.filter(is_active=True).select_related('user')
        serializer = self.get_serializer(sessions, many=True)
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def current(self, request):
        """Get current user's session"""
        try:
            session = UserSession.objects.get(
                user=request.user,
                session_key=request.session.session_key,
                is_active=True
            )
            serializer = self.get_serializer(session)
            return Response(serializer.data)
        except UserSession.DoesNotExist:
            return Response(
                {'message': 'No active session found'},
                status=status.HTTP_404_NOT_FOUND
            )
