"""
Hand-authored initial migration for spec_customization.

Auto-generation via `manage.py makemigrations` was blocked by interactive
prompts coming from *unrelated* apps that have pending model changes.
This migration only creates the four tables owned by this app and is
identical in shape to what Django would have produced.
"""
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaperSpecDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('file', models.FileField(upload_to='spec_customization/paper_specs/%Y/%m/')),
                ('original_filename', models.CharField(max_length=512)),
                ('file_size_bytes', models.BigIntegerField(default=0)),
                ('total_pages', models.IntegerField(default=0)),
                ('sha256_hash', models.CharField(db_index=True, max_length=64)),
                ('project_id', models.CharField(blank=True, db_index=True, max_length=64, null=True)),
                ('title', models.CharField(blank=True, default='', max_length=512)),
                ('document_number', models.CharField(blank=True, default='', max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='spec_customization_uploads', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='paperspecdocument',
            index=models.Index(fields=['sha256_hash'], name='spec_cust_pa_sha256__idx'),
        ),
        migrations.AddIndex(
            model_name='paperspecdocument',
            index=models.Index(fields=['-created_at'], name='spec_cust_pa_created_idx'),
        ),

        migrations.CreateModel(
            name='PaperSpecExtractionJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', max_length=20)),
                ('progress_percent', models.IntegerField(default=0)),
                ('current_phase', models.CharField(blank=True, default='', max_length=128)),
                ('pages_processed', models.IntegerField(default=0)),
                ('chunks_total', models.IntegerField(default=0)),
                ('chunks_done', models.IntegerField(default=0)),
                ('config_snapshot', models.JSONField(blank=True, default=dict)),
                ('celery_task_id', models.CharField(blank=True, db_index=True, default='', max_length=128)),
                ('error_message', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='spec_customization_jobs', to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='jobs', to='spec_customization.paperspecdocument')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='paperspecextractionjob',
            index=models.Index(fields=['status', '-created_at'], name='spec_cust_jo_status_idx'),
        ),
        migrations.AddIndex(
            model_name='paperspecextractionjob',
            index=models.Index(fields=['celery_task_id'], name='spec_cust_jo_celery_idx'),
        ),

        migrations.CreateModel(
            name='PipingClass',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('class_code', models.CharField(db_index=True, max_length=32)),
                ('class_full_code', models.CharField(blank=True, default='', max_length=256)),
                ('material_grade', models.CharField(blank=True, default='', max_length=256)),
                ('pressure_rating', models.CharField(blank=True, default='', max_length=64)),
                ('flange_facing', models.CharField(blank=True, default='', max_length=64)),
                ('corrosion_allowance', models.CharField(blank=True, default='', max_length=64)),
                ('service_list', models.JSONField(blank=True, default=list)),
                ('pt_rating_table', models.JSONField(blank=True, default=list)),
                ('source_pages', models.JSONField(blank=True, default=list)),
                ('confidence_score', models.FloatField(default=0.0)),
                ('raw_notes', models.TextField(blank=True, default='')),
                ('extraction_engine', models.CharField(blank=True, default='', max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('job', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='piping_classes', to='spec_customization.paperspecextractionjob')),
            ],
            options={
                'ordering': ['class_code'],
                'unique_together': {('job', 'class_code')},
            },
        ),
        migrations.AddIndex(
            model_name='pipingclass',
            index=models.Index(fields=['job', 'class_code'], name='spec_cust_pc_job_code_idx'),
        ),

        migrations.CreateModel(
            name='PipingClassComponent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('component_type', models.CharField(choices=[('pipe', 'Pipe'), ('valve', 'Valve'), ('fitting', 'Fitting'), ('flange', 'Flange'), ('gasket', 'Gasket'), ('bolt', 'Bolt / Stud'), ('other', 'Other')], db_index=True, max_length=20)),
                ('sub_type', models.CharField(blank=True, default='', max_length=128)),
                ('size_from', models.CharField(blank=True, default='', max_length=32)),
                ('size_to', models.CharField(blank=True, default='', max_length=32)),
                ('description', models.TextField(blank=True, default='')),
                ('schedule_or_rating', models.CharField(blank=True, default='', max_length=128)),
                ('material_standard', models.CharField(blank=True, default='', max_length=256)),
                ('end_connection', models.CharField(blank=True, default='', max_length=128)),
                ('notes', models.TextField(blank=True, default='')),
                ('display_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('piping_class', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='components', to='spec_customization.pipingclass')),
            ],
            options={
                'ordering': ['piping_class', 'display_order', 'component_type'],
            },
        ),
        migrations.AddIndex(
            model_name='pipingclasscomponent',
            index=models.Index(fields=['piping_class', 'component_type'], name='spec_cust_pcc_pc_type_idx'),
        ),
    ]
