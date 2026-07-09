"""
Migration: Add MasterPayrollImport and MasterPayrollRow models.

These tables persist every Sympa + ValueFrame generation session so that:
  - HR managers can view historical imports
  - The generated Excel is uploaded to S3 asynchronously
  - Row-level data is queryable for future payroll automation pipelines
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0010_add_submitted_to_role_to_daily_work_log'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── MasterPayrollImport ────────────────────────────────────────────────
        migrations.CreateModel(
            name='MasterPayrollImport',
            fields=[
                ('id',                   models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('year',                 models.PositiveSmallIntegerField()),
                ('month',                models.PositiveSmallIntegerField()),
                ('generated_at',         models.DateTimeField(auto_now_add=True)),
                ('sympa_filename',       models.CharField(max_length=255, blank=True)),
                ('valueframe_filename',  models.CharField(max_length=255, blank=True)),
                ('s3_key',               models.CharField(max_length=500, blank=True)),
                ('status',               models.CharField(
                    max_length=20,
                    choices=[
                        ('processing', 'Processing'),
                        ('ready',      'Ready'),
                        ('uploaded',   'Uploaded to S3'),
                        ('failed',     'Failed'),
                    ],
                    default='processing',
                )),
                ('stats',      models.JSONField(default=dict, blank=True)),
                ('warnings',   models.JSONField(default=list, blank=True)),
                ('total_rows', models.PositiveIntegerField(default=0)),
                ('generated_by', models.ForeignKey(
                    settings.AUTH_USER_MODEL,
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name='master_payroll_imports',
                )),
            ],
            options={
                'db_table': 'payroll_master_import',
                'ordering': ['-year', '-month', '-generated_at'],
            },
        ),

        # Indexes for MasterPayrollImport
        migrations.AddIndex(
            model_name='masterpayrollimport',
            index=models.Index(fields=['year', 'month'], name='payroll_mi_year_month'),
        ),
        migrations.AddIndex(
            model_name='masterpayrollimport',
            index=models.Index(fields=['generated_by'], name='payroll_mi_generated_by'),
        ),
        migrations.AddIndex(
            model_name='masterpayrollimport',
            index=models.Index(fields=['status'], name='payroll_mi_status'),
        ),

        # ── MasterPayrollRow ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='MasterPayrollRow',
            fields=[
                ('id',              models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('employee_code',   models.CharField(max_length=60)),
                ('employee_name',   models.CharField(max_length=255)),
                ('joining_date',    models.CharField(max_length=50, blank=True)),
                ('total_hours',     models.DecimalField(max_digits=8,  decimal_places=2, default=Decimal('0'))),
                ('employee_salary', models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('basic_salary',    models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('total_allowances',    models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('transport_allowance', models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('housing_allowance',   models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('other_allowances',    models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('other_pay',           models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('details',             models.TextField(blank=True)),
                ('total_deductions',    models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('deduction_details',   models.TextField(blank=True)),
                ('final_salary',        models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))),
                ('sources',      models.JSONField(default=list, blank=True)),
                ('row_warnings', models.JSONField(default=list, blank=True)),
                ('raw_data',     models.JSONField(default=dict, blank=True)),
                ('import_session', models.ForeignKey(
                    'payroll.MasterPayrollImport',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='rows',
                )),
            ],
            options={
                'db_table': 'payroll_master_row',
                'ordering': ['import_session', 'employee_name'],
            },
        ),

        # Indexes for MasterPayrollRow
        migrations.AddIndex(
            model_name='masterpayrollrow',
            index=models.Index(fields=['import_session', 'employee_code'], name='payroll_mr_session_code'),
        ),
        migrations.AddIndex(
            model_name='masterpayrollrow',
            index=models.Index(fields=['employee_code'], name='payroll_mr_emp_code'),
        ),

        # Unique: one row per employee per session
        migrations.AlterUniqueTogether(
            name='masterpayrollrow',
            unique_together={('import_session', 'employee_code')},
        ),
    ]
