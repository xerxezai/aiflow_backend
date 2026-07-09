"""
Migration: AI Champion of the Month — tracking, cost analytics, gamification
Adds: AIPricingConfig, AIUsageLog, ActivityEvent, MonthlyChampion
"""
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0012_fix_org_role_module_access'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ----------------------------------------------------------------
        # AIPricingConfig
        # ----------------------------------------------------------------
        migrations.CreateModel(
            name='AIPricingConfig',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider', models.CharField(choices=[
                    ('openai', 'OpenAI'), ('google', 'Google / Gemini'),
                    ('anthropic', 'Anthropic'), ('azure_openai', 'Azure OpenAI'),
                    ('aws_bedrock', 'AWS Bedrock'), ('local', 'Local / Self-Hosted'),
                    ('other', 'Other'),
                ], db_index=True, max_length=32)),
                ('model_name', models.CharField(db_index=True, max_length=128)),
                ('input_cost_per_1k', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=10)),
                ('output_cost_per_1k', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=10)),
                ('currency', models.CharField(default='USD', max_length=8)),
                ('is_active', models.BooleanField(default=True)),
                ('effective_from', models.DateTimeField(db_index=True)),
                ('notes', models.TextField(blank=True)),
            ],
            options={
                'db_table': 'ai_pricing_config',
                'ordering': ['-effective_from'],
            },
        ),
        migrations.AddIndex(
            model_name='aipricingconfig',
            index=models.Index(fields=['provider', 'model_name', 'is_active'],
                               name='ai_pricing_provider_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='aipricingconfig',
            unique_together={('provider', 'model_name', 'effective_from')},
        ),

        # ----------------------------------------------------------------
        # AIUsageLog
        # ----------------------------------------------------------------
        migrations.CreateModel(
            name='AIUsageLog',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('provider', models.CharField(choices=[
                    ('openai', 'OpenAI'), ('google', 'Google / Gemini'),
                    ('anthropic', 'Anthropic'), ('azure_openai', 'Azure OpenAI'),
                    ('aws_bedrock', 'AWS Bedrock'), ('local', 'Local / Self-Hosted'),
                    ('other', 'Other'),
                ], db_index=True, max_length=32)),
                ('model_name', models.CharField(db_index=True, max_length=128)),
                ('application', models.CharField(blank=True, db_index=True, max_length=64)),
                ('feature', models.CharField(blank=True, db_index=True, max_length=64)),
                ('request_id', models.CharField(blank=True, max_length=64)),
                ('tokens_input', models.IntegerField(default=0)),
                ('tokens_output', models.IntegerField(default=0)),
                ('total_tokens', models.IntegerField(default=0)),
                ('cost_usd', models.DecimalField(decimal_places=6, default=Decimal('0'), max_digits=12)),
                ('latency_ms', models.IntegerField(default=0)),
                ('success', models.BooleanField(default=True)),
                ('error_code', models.CharField(blank=True, max_length=64)),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE,
                                           related_name='ai_usage_logs',
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'ai_usage_log',
                'ordering': ['-timestamp'],
            },
        ),
        migrations.AddIndex(
            model_name='aiusagelog',
            index=models.Index(fields=['user', '-timestamp'], name='ai_usage_user_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='aiusagelog',
            index=models.Index(fields=['provider', '-timestamp'], name='ai_usage_prov_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='aiusagelog',
            index=models.Index(fields=['application', '-timestamp'], name='ai_usage_app_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='aiusagelog',
            index=models.Index(fields=['-timestamp', 'success'], name='ai_usage_ts_success_idx'),
        ),

        # ----------------------------------------------------------------
        # ActivityEvent
        # ----------------------------------------------------------------
        migrations.CreateModel(
            name='ActivityEvent',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('application', models.CharField(db_index=True, max_length=64)),
                ('module', models.CharField(blank=True, max_length=64)),
                ('feature', models.CharField(blank=True, db_index=True, max_length=64)),
                ('action_type', models.CharField(choices=[
                    ('view', 'View'), ('click', 'Click'), ('upload', 'Upload'),
                    ('download', 'Download'), ('generate', 'Generate'), ('analyze', 'Analyze'),
                    ('edit', 'Edit'), ('delete', 'Delete'), ('export', 'Export'),
                    ('login', 'Login'), ('logout', 'Logout'), ('api_call', 'API Call'),
                    ('other', 'Other'),
                ], db_index=True, default='other', max_length=32)),
                ('session_id', models.CharField(blank=True, db_index=True, max_length=64)),
                ('duration_ms', models.IntegerField(default=0)),
                ('success', models.BooleanField(default=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE,
                                           related_name='activity_events',
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'activity_event',
                'ordering': ['-timestamp'],
            },
        ),
        migrations.AddIndex(
            model_name='activityevent',
            index=models.Index(fields=['user', '-timestamp'], name='activity_user_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='activityevent',
            index=models.Index(fields=['application', '-timestamp'], name='activity_app_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='activityevent',
            index=models.Index(fields=['feature', '-timestamp'], name='activity_feat_ts_idx'),
        ),

        # ----------------------------------------------------------------
        # MonthlyChampion
        # ----------------------------------------------------------------
        migrations.CreateModel(
            name='MonthlyChampion',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('period_year', models.IntegerField(db_index=True)),
                ('period_month', models.IntegerField(db_index=True)),
                ('rank', models.IntegerField()),
                ('champion_score', models.FloatField(default=0)),
                ('usage_frequency_score', models.FloatField(default=0)),
                ('feature_diversity_score', models.FloatField(default=0)),
                ('time_spent_score', models.FloatField(default=0)),
                ('ai_utilization_score', models.FloatField(default=0)),
                ('cost_efficiency_score', models.FloatField(default=0)),
                ('success_rate_score', models.FloatField(default=0)),
                ('total_actions', models.IntegerField(default=0)),
                ('total_ai_requests', models.IntegerField(default=0)),
                ('total_ai_cost_usd', models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=12)),
                ('distinct_features_used', models.IntegerField(default=0)),
                ('total_session_minutes', models.IntegerField(default=0)),
                ('success_rate', models.FloatField(default=100.0)),
                ('badge_tier', models.CharField(default='gold', max_length=24)),
                ('citation', models.TextField(blank=True)),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE,
                                           related_name='champion_titles',
                                           to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'monthly_champion',
                'ordering': ['-period_year', '-period_month', 'rank'],
            },
        ),
        migrations.AddIndex(
            model_name='monthlychampion',
            index=models.Index(fields=['-period_year', '-period_month'], name='champion_period_idx'),
        ),
        migrations.AddIndex(
            model_name='monthlychampion',
            index=models.Index(fields=['user', '-period_year', '-period_month'],
                               name='champion_user_period_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='monthlychampion',
            unique_together={('period_year', 'period_month', 'rank')},
        ),
    ]
