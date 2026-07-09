"""
Adds NonTeffProject and links existing NonTeffBatch / NonTeffExtractionJob to
it via nullable ForeignKey fields. Hand-written to scope only the
non_teff_metadata app and avoid interactive prompts from unrelated apps.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('non_teff_metadata', '0002_batch_models'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NonTeffProject',
            fields=[
                ('project_id',  models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('name',        models.CharField(max_length=255)),
                ('code',        models.CharField(max_length=64, blank=True, db_index=True)),
                ('client',      models.CharField(max_length=128, blank=True)),
                ('plant',       models.CharField(max_length=128, blank=True)),
                ('discipline',  models.CharField(max_length=64, blank=True)),
                ('description', models.TextField(blank=True)),
                ('status',      models.CharField(max_length=20, default='active', db_index=True,
                                                 choices=[
                                                     ('active', 'Active'),
                                                     ('on_hold', 'On hold'),
                                                     ('completed', 'Completed'),
                                                     ('archived', 'Archived'),
                                                 ])),
                ('tags',        models.JSONField(default=list, blank=True)),
                ('metadata',    models.JSONField(default=dict, blank=True)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('created_by',  models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='non_teff_projects',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Non-TEFF Project',
                'verbose_name_plural': 'Non-TEFF Projects',
                'ordering': ['-updated_at', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='nonteffproject',
            index=models.Index(fields=['status', '-updated_at'], name='ntm_proj_status_upd_idx'),
        ),
        migrations.AddIndex(
            model_name='nonteffproject',
            index=models.Index(fields=['created_by', '-updated_at'], name='ntm_proj_creator_upd_idx'),
        ),
        migrations.AddField(
            model_name='nonteffbatch',
            name='project',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='batches',
                to='non_teff_metadata.nonteffproject',
            ),
        ),
        migrations.AddField(
            model_name='nonteffextractionjob',
            name='project',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='jobs',
                to='non_teff_metadata.nonteffproject',
            ),
        ),
    ]
