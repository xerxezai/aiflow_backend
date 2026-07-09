"""Add PayrollRunUpload model for external file imports (ValueFrame/Sympa/Generic)."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0009_payslip_public_holiday_days'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollRunUpload',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('run', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='uploads',
                    to='payroll_engine.payrollrun',
                )),
                ('file_type', models.CharField(
                    max_length=20, default='valueframe',
                    choices=[
                        ('valueframe', 'ValueFrame (hours + leave)'),
                        ('sympa',      'Sympa (salary components)'),
                        ('generic',    'Generic XLSX'),
                    ],
                )),
                ('original_filename', models.CharField(max_length=255)),
                ('s3_key', models.CharField(max_length=500, blank=True)),
                ('uploaded_by', models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name='payroll_run_uploads',
                )),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('rows_matched', models.PositiveIntegerField(default=0)),
                ('rows_updated', models.PositiveIntegerField(default=0)),
                ('unmatched',     models.JSONField(default=list, blank=True)),
                ('updated_fields', models.JSONField(default=list, blank=True)),
                ('status', models.CharField(
                    max_length=20, default='applied',
                    choices=[('applied', 'Applied'), ('failed', 'Failed')],
                )),
                ('error_message', models.TextField(blank=True)),
            ],
            options={
                'db_table': 'payroll_engine_run_upload',
                'ordering': ['-uploaded_at'],
            },
        ),
    ]
