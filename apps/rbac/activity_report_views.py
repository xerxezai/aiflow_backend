"""
Activity Report ViewSet — Admin-only REST API for user engagement reports
=========================================================================

Endpoints:
  GET  /rbac/activity-reports/summary/?window=month
  GET  /rbac/activity-reports/by-user/?window=month&limit=50
  GET  /rbac/activity-reports/by-feature/?window=month
  GET  /rbac/activity-reports/daily/?window=week
  GET  /rbac/activity-reports/export/csv/?window=month&format=user|feature|daily
  POST /rbac/activity-reports/custom/
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .activity_report_service import (
    generate_summary_report,
    generate_user_report,
    generate_feature_report,
    generate_daily_breakdown,
    DEFAULT_TIME_WINDOWS,
)
from .permissions import IsAdmin

logger = logging.getLogger(__name__)


class ActivityReportViewSet(viewsets.ViewSet):
    """Admin-only endpoints for user engagement analytics."""
    permission_classes = [IsAuthenticated, IsAdmin]

    # -----------------------------------------------------------------------
    # GET /summary/?window=month
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Aggregate summary for all users in time window."""
        window = request.query_params.get('window', 'month')
        report = generate_summary_report(window=window)
        return Response(report)

    # -----------------------------------------------------------------------
    # GET /by-user/?window=month&limit=50
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='by-user')
    def by_user(self, request):
        """Per-user activity breakdown, ranked by activity."""
        window = request.query_params.get('window', 'month')
        try:
            limit = int(request.query_params.get('limit', 50))
        except (ValueError, TypeError):
            limit = 50
        limit = max(1, min(limit, 500))  # soft-coded clamp
        
        report = generate_user_report(window=window, limit=limit)
        return Response(report)

    # -----------------------------------------------------------------------
    # GET /by-feature/?window=month
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='by-feature')
    def by_feature(self, request):
        """Per-feature adoption breakdown."""
        window = request.query_params.get('window', 'month')
        report = generate_feature_report(window=window)
        return Response(report)

    # -----------------------------------------------------------------------
    # GET /daily/?window=week
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'])
    def daily(self, request):
        """Daily activity heatmap."""
        window = request.query_params.get('window', 'week')
        report = generate_daily_breakdown(window=window)
        return Response(report)

    # -----------------------------------------------------------------------
    # GET /time-windows/
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='time-windows')
    def time_windows(self, request):
        """List available time windows for report filtering."""
        return Response({
            'available_windows': list(DEFAULT_TIME_WINDOWS.keys()),
            'descriptions': DEFAULT_TIME_WINDOWS,
        })

    # -----------------------------------------------------------------------
    # GET /export/csv/?window=month&format=user
    # -----------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='export/csv')
    def export_csv(self, request):
        """Export activity report as CSV."""
        window = request.query_params.get('window', 'month')
        report_format = request.query_params.get('format', 'user')  # user|feature|daily

        if report_format == 'user':
            data = generate_user_report(window=window, limit=500)
            if 'error' in data:
                return Response(data, status=status.HTTP_400_BAD_REQUEST)
            
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = (
                f'attachment; filename="activity-report-user-{window}.csv"'
            )
            writer = csv.writer(response)
            writer.writerow([
                'Rank', 'Email', 'Name', 'Total Actions', 'AI Requests',
                'AI Cost (USD)', 'Features Used', 'Modules Used',
                'Session Minutes', 'Success Rate (%)',
            ])
            for row in data.get('results', []):
                m = row['metrics']
                writer.writerow([
                    row['rank'],
                    row['user']['email'],
                    row['user']['name'],
                    m['total_actions'],
                    m['total_ai_requests'],
                    f"{m['total_ai_cost_usd']:.4f}",
                    m['distinct_features'],
                    m['distinct_modules'],
                    m['session_minutes'],
                    f"{m['success_rate_pct']:.2f}",
                ])
            return response

        elif report_format == 'feature':
            data = generate_feature_report(window=window)
            if 'error' in data:
                return Response(data, status=status.HTTP_400_BAD_REQUEST)
            
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = (
                f'attachment; filename="activity-report-feature-{window}.csv"'
            )
            writer = csv.writer(response)
            writer.writerow([
                'Feature', 'Module', 'Total Actions', 'Distinct Users',
                'Session Minutes', 'Success Rate (%)',
            ])
            for row in data.get('results', []):
                m = row['metrics']
                writer.writerow([
                    row['feature'],
                    row['module'],
                    m['total_actions'],
                    m['distinct_users'],
                    m['session_minutes'],
                    f"{m['success_rate_pct']:.2f}",
                ])
            return response

        elif report_format == 'daily':
            data = generate_daily_breakdown(window=window)
            if 'error' in data:
                return Response(data, status=status.HTTP_400_BAD_REQUEST)
            
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = (
                f'attachment; filename="activity-report-daily-{window}.csv"'
            )
            writer = csv.writer(response)
            writer.writerow([
                'Date', 'Total Actions', 'AI Requests', 'AI Cost (USD)',
                'Distinct Features', 'Distinct Modules', 'Success Rate (%)',
                'Session Minutes',
            ])
            for row in data.get('results', []):
                m = row['metrics']
                writer.writerow([
                    row['date'],
                    m.get('total_actions', 0),
                    m.get('total_ai_requests', 0),
                    f"{m.get('total_ai_cost_usd', 0):.4f}",
                    m.get('distinct_features', 0),
                    m.get('distinct_modules', 0),
                    f"{m.get('success_rate_pct', 100):.2f}",
                    m.get('session_minutes', 0),
                ])
            return response

        else:
            return Response(
                {'error': 'format must be: user, feature, or daily'},
                status=status.HTTP_400_BAD_REQUEST,
            )
