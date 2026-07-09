"""
Serializers for Activity Tracking System
"""
from rest_framework import serializers
from .models import SystemActivity, ActivityStream, ActivityStatistics, UserSession


class SystemActivitySerializer(serializers.ModelSerializer):
    """Serializer for SystemActivity model"""
    
    user_name = serializers.SerializerMethodField()
    user_email = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    
    class Meta:
        model = SystemActivity
        fields = [
            'id',
            'activity_type',
            'category',
            'description',
            'user',
            'user_name',
            'user_email',
            'user_agent',
            'ip_address',
            'severity',
            'success',  # FIXED: Changed from 'status' to 'success' (actual model field)
            'error_message',
            'timestamp',
            'duration',
            'details',
        ]
        read_only_fields = ['id', 'timestamp']
    
    def get_user_name(self, obj):
        """Get user's full name or username"""
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return None
    
    def get_user_email(self, obj):
        """Get user's email"""
        if obj.user:
            return obj.user.email
        return None
    
    def get_duration(self, obj):
        """Get duration in milliseconds if available"""
        if obj.details and 'duration' in obj.details:
            return obj.details['duration']
        return None


class ActivityStreamSerializer(serializers.ModelSerializer):
    """Serializer for ActivityStream model"""
    
    class Meta:
        model = ActivityStream
        fields = [
            'id',
            'stream_type',
            'name',
            'description',
            'filters',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ActivityStatisticsSerializer(serializers.ModelSerializer):
    """Serializer for ActivityStatistics model"""
    
    class Meta:
        model = ActivityStatistics
        fields = [
            'id',
            'period_start',
            'period_end',
            'total_activities',
            'activities_by_type',
            'activities_by_category',
            'activities_by_severity',
            'activities_by_status',
            'unique_users',
            'success_rate',
            'average_duration',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class UserSessionSerializer(serializers.ModelSerializer):
    """Serializer for UserSession model"""
    
    user_name = serializers.SerializerMethodField()
    user_email = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    
    class Meta:
        model = UserSession
        fields = [
            'id',
            'user',
            'user_name',
            'user_email',
            'session_key',
            'started_at',
            'last_activity',
            'ended_at',
            'duration',
            'ip_address',
            'user_agent',
            'is_active',
        ]
        read_only_fields = ['id', 'started_at', 'last_activity', 'ended_at']
    
    def get_user_name(self, obj):
        """Get user's full name or username"""
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return None
    
    def get_user_email(self, obj):
        """Get user's email"""
        if obj.user:
            return obj.user.email
        return None
    
    def get_duration(self, obj):
        """Get session duration in seconds"""
        if obj.ended_at and obj.started_at:
            return (obj.ended_at - obj.started_at).total_seconds()
        elif obj.started_at:
            from django.utils import timezone
            return (timezone.now() - obj.started_at).total_seconds()
        return None


class ActivitySummarySerializer(serializers.Serializer):
    """Serializer for activity summary statistics"""
    
    total_last_hour = serializers.IntegerField()
    total_last_24h = serializers.IntegerField()
    by_category = serializers.DictField()
    by_severity = serializers.DictField()
    by_status = serializers.DictField()
    success_rate = serializers.FloatField()
    average_duration = serializers.FloatField()
    active_users = serializers.IntegerField()
    top_activities = serializers.ListField()
