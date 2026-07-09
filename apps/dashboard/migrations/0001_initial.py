"""
Auto-generated migration for apps.dashboard
"""
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserDashboardInsight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=120)),
                ('body', models.TextField(max_length=400)),
                ('insight_type', models.CharField(
                    choices=[
                        ('tip', 'Productivity Tip'),
                        ('achievement', 'Achievement'),
                        ('alert', 'Usage Alert'),
                        ('suggestion', 'Feature Suggestion'),
                    ],
                    db_index=True,
                    default='tip',
                    max_length=20,
                )),
                ('icon_key', models.CharField(
                    choices=[
                        ('lightbulb', 'Lightbulb'),
                        ('trophy', 'Trophy'),
                        ('bell', 'Bell'),
                        ('sparkles', 'Sparkles'),
                        ('chart', 'Chart'),
                        ('rocket', 'Rocket'),
                        ('star', 'Star'),
                        ('check', 'Check'),
                    ],
                    default='lightbulb',
                    max_length=20,
                )),
                ('generated_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('expires_at', models.DateTimeField()),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='dashboard_insights',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'User Dashboard Insight',
                'verbose_name_plural': 'User Dashboard Insights',
                'ordering': ['-generated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='userdashboardinsight',
            index=models.Index(fields=['user', 'is_active'], name='dashboard_user_active_idx'),
        ),
        migrations.AddIndex(
            model_name='userdashboardinsight',
            index=models.Index(fields=['user', 'expires_at'], name='dashboard_user_expires_idx'),
        ),
    ]
