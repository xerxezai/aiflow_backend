"""
AI Champion of the Month — Data Models
=======================================

Production-grade tracking + analytics + gamification system for RADAI.

Mirrors the AWS reference architecture (DynamoDB activity logs + RDS analytics
tables) inside Django/PostgreSQL so the same conceptual model runs on:

    Local / Railway PostgreSQL          ←→  AWS RDS (analytics)
    These models (high-volume tables)   ←→  AWS DynamoDB (activity / ai_usage)
    Celery beat job                     ←→  AWS EventBridge cron
    DRF endpoints                       ←→  AWS API Gateway + Lambda

All thresholds, weights, and pricing are stored in DB rows or module-level
constants — never hard-coded inside business logic.
"""
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


# ---------------------------------------------------------------------------
# Soft-coded provider catalogue
# ---------------------------------------------------------------------------
AI_PROVIDER_CHOICES = [
    ('openai', 'OpenAI'),
    ('google', 'Google / Gemini'),
    ('anthropic', 'Anthropic'),
    ('azure_openai', 'Azure OpenAI'),
    ('aws_bedrock', 'AWS Bedrock'),
    ('local', 'Local / Self-Hosted'),
    ('other', 'Other'),
]

ACTION_TYPE_CHOICES = [
    ('view', 'View'),
    ('click', 'Click'),
    ('upload', 'Upload'),
    ('download', 'Download'),
    ('generate', 'Generate'),
    ('analyze', 'Analyze'),
    ('edit', 'Edit'),
    ('delete', 'Delete'),
    ('export', 'Export'),
    ('login', 'Login'),
    ('logout', 'Logout'),
    ('api_call', 'API Call'),
    ('other', 'Other'),
]


# ---------------------------------------------------------------------------
# Pricing configuration — editable without code change (Django admin)
# ---------------------------------------------------------------------------
class AIPricingConfig(TimeStampedModel):
    """
    Dynamic AI pricing. One row per (provider, model_name).
    Costs are USD per 1,000 tokens (industry-standard granularity).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, choices=AI_PROVIDER_CHOICES, db_index=True)
    model_name = models.CharField(max_length=128, db_index=True)

    input_cost_per_1k = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal('0'))
    output_cost_per_1k = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal('0'))
    currency = models.CharField(max_length=8, default='USD')

    is_active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(default=timezone.now, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'ai_pricing_config'
        ordering = ['-effective_from']
        unique_together = ['provider', 'model_name', 'effective_from']
        indexes = [
            models.Index(fields=['provider', 'model_name', 'is_active']),
        ]

    def __str__(self):
        return f"{self.provider}/{self.model_name} (in={self.input_cost_per_1k}, out={self.output_cost_per_1k})"

    def compute_cost(self, tokens_input: int, tokens_output: int) -> Decimal:
        """Compute USD cost for a request given token counts."""
        ti = Decimal(tokens_input or 0) / Decimal('1000')
        to = Decimal(tokens_output or 0) / Decimal('1000')
        return (ti * self.input_cost_per_1k) + (to * self.output_cost_per_1k)


# ---------------------------------------------------------------------------
# Per-request AI usage log (high-volume, append-only)
# Equivalent to DynamoDB table: ai_usage_logs (PK: user_id, SK: timestamp)
# ---------------------------------------------------------------------------
class AIUsageLog(TimeStampedModel):
    """
    Every AI request is logged here. Powers cost analytics + champion scoring.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_usage_logs'
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    provider = models.CharField(max_length=32, choices=AI_PROVIDER_CHOICES, db_index=True)
    model_name = models.CharField(max_length=128, db_index=True)

    # Context
    application = models.CharField(max_length=64, blank=True, db_index=True)  # e.g. 'pid-verification'
    feature = models.CharField(max_length=64, blank=True, db_index=True)      # e.g. 'ocr-extract'
    request_id = models.CharField(max_length=64, blank=True)                  # client correlation id

    # Token + cost data
    tokens_input = models.IntegerField(default=0)
    tokens_output = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('0'))

    # Performance
    latency_ms = models.IntegerField(default=0)
    success = models.BooleanField(default=True)
    error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = 'ai_usage_log'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['provider', '-timestamp']),
            models.Index(fields=['application', '-timestamp']),
            models.Index(fields=['-timestamp', 'success']),
        ]

    def __str__(self):
        return f"{self.user_id} {self.provider}/{self.model_name} ${self.cost_usd}"

    def save(self, *args, **kwargs):
        # Soft-coded auto-fill
        if not self.total_tokens:
            self.total_tokens = (self.tokens_input or 0) + (self.tokens_output or 0)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Lightweight activity event (real-time UX events)
# Equivalent to DynamoDB table: activity_events (PK: user_id, SK: timestamp)
# ---------------------------------------------------------------------------
class ActivityEvent(TimeStampedModel):
    """
    Every meaningful UI action is logged. Drives engagement scoring.
    Kept lean: index on user+timestamp; heavy aggregation done by Celery task.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='activity_events'
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    application = models.CharField(max_length=64, db_index=True)
    module = models.CharField(max_length=64, blank=True)
    feature = models.CharField(max_length=64, blank=True, db_index=True)
    action_type = models.CharField(max_length=32, choices=ACTION_TYPE_CHOICES, default='other', db_index=True)

    session_id = models.CharField(max_length=64, blank=True, db_index=True)
    duration_ms = models.IntegerField(default=0)
    success = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'activity_event'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['application', '-timestamp']),
            models.Index(fields=['feature', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.user_id} {self.application}/{self.feature} {self.action_type}"


# ---------------------------------------------------------------------------
# Monthly Champion — historical record (the "trophy cabinet")
# ---------------------------------------------------------------------------
class MonthlyChampion(TimeStampedModel):
    """
    One row per (period_year, period_month, rank). Top-3 stored per month.
    Computed by Celery beat task on the 1st of every month at 00:05 UTC.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    period_year = models.IntegerField(db_index=True)
    period_month = models.IntegerField(db_index=True)
    rank = models.IntegerField()  # 1, 2, 3

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='champion_titles'
    )
    champion_score = models.FloatField(default=0)  # 0-100

    # Score breakdown (transparency)
    usage_frequency_score = models.FloatField(default=0)
    feature_diversity_score = models.FloatField(default=0)
    time_spent_score = models.FloatField(default=0)
    ai_utilization_score = models.FloatField(default=0)
    cost_efficiency_score = models.FloatField(default=0)
    success_rate_score = models.FloatField(default=0)

    # Raw stats snapshot
    total_actions = models.IntegerField(default=0)
    total_ai_requests = models.IntegerField(default=0)
    total_ai_cost_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0'))
    distinct_features_used = models.IntegerField(default=0)
    total_session_minutes = models.IntegerField(default=0)
    success_rate = models.FloatField(default=100.0)

    # Award metadata
    badge_tier = models.CharField(max_length=24, default='gold')  # diamond/platinum/gold...
    citation = models.TextField(blank=True)  # AI-generated short citation

    class Meta:
        db_table = 'monthly_champion'
        ordering = ['-period_year', '-period_month', 'rank']
        unique_together = ['period_year', 'period_month', 'rank']
        indexes = [
            models.Index(fields=['-period_year', '-period_month']),
            models.Index(fields=['user', '-period_year', '-period_month']),
        ]

    def __str__(self):
        return f"{self.period_year}-{self.period_month:02d} #{self.rank} {self.user_id}"
