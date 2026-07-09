"""
Personal Dashboard Models
Stores AI-generated insights per user, served instantly on dashboard load.
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

User = get_user_model()

# ─── Soft-coded config ────────────────────────────────────────────────────────
INSIGHT_TYPE_CHOICES = [
    ('tip',         'Productivity Tip'),
    ('achievement', 'Achievement'),
    ('alert',       'Usage Alert'),
    ('suggestion',  'Feature Suggestion'),
]

INSIGHT_ICON_CHOICES = [
    ('lightbulb',   'Lightbulb'),
    ('trophy',      'Trophy'),
    ('bell',        'Bell'),
    ('sparkles',    'Sparkles'),
    ('chart',       'Chart'),
    ('rocket',      'Rocket'),
    ('star',        'Star'),
    ('check',       'Check'),
]

# Default insight TTL in hours — insights older than this are considered stale
INSIGHT_TTL_HOURS = 20

# Maximum insights to keep active per user
INSIGHT_MAX_ACTIVE = 3
# ─────────────────────────────────────────────────────────────────────────────


class UserDashboardInsight(models.Model):
    """
    AI-generated personalized insight for a specific user.
    Generated nightly by Celery task, served instantly from DB.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='dashboard_insights',
        db_index=True,
    )
    title = models.CharField(max_length=120)
    body = models.TextField(max_length=400)
    insight_type = models.CharField(
        max_length=20,
        choices=INSIGHT_TYPE_CHOICES,
        default='tip',
        db_index=True,
    )
    icon_key = models.CharField(
        max_length=20,
        choices=INSIGHT_ICON_CHOICES,
        default='lightbulb',
    )
    generated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['user', 'expires_at']),
        ]
        verbose_name = 'User Dashboard Insight'
        verbose_name_plural = 'User Dashboard Insights'

    def __str__(self):
        return f'{self.user.email} — {self.title[:40]}'

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=INSIGHT_TTL_HOURS)
        super().save(*args, **kwargs)

    @property
    def is_fresh(self):
        return self.is_active and self.expires_at > timezone.now()
