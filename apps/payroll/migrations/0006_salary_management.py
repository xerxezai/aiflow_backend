"""
Migration 0006 -- Add Salary Management tables.

SalaryComponent    : master catalogue of reusable salary component types
EmployeeSalaryStructure : per-employee salary definition with DRAFT->APPROVED workflow
SalaryHistory      : immutable audit trail written on each approval
"""
import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0005_publicholiday_attendanceoverride'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── SalaryComponent ─────────────────────────────────────────────────
        migrations.CreateModel(
            name='SalaryComponent',
            fields=[
                ('id',          models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('code',        models.CharField(db_index=True, max_length=30, unique=True)),
                ('name',        models.CharField(max_length=100)),
                ('category',    models.CharField(
                    choices=[
                        ('allowance', 'Allowance'),
                        ('deduction', 'Deduction'),
                        ('gross',     'Gross Component'),
                    ],
                    default='allowance',
                    max_length=20,
                )),
                ('is_taxable',  models.BooleanField(default=False)),
                ('description', models.TextField(blank=True)),
                ('is_active',   models.BooleanField(default=True)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('created_by',  models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_salary_components',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'payroll_salary_component',
                'ordering': ['category', 'code'],
            },
        ),

        # ── EmployeeSalaryStructure ─────────────────────────────────────────
        migrations.CreateModel(
            name='EmployeeSalaryStructure',
            fields=[
                ('id',               models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('employee_code',    models.CharField(db_index=True, max_length=30)),
                ('employee_name',    models.CharField(db_index=True, max_length=255)),
                ('department',       models.CharField(blank=True, max_length=100)),
                ('effective_date',   models.DateField()),
                ('currency',         models.CharField(
                    choices=[
                        ('AED', 'UAE Dirham (AED)'),
                        ('USD', 'US Dollar (USD)'),
                        ('EUR', 'Euro (EUR)'),
                        ('SAR', 'Saudi Riyal (SAR)'),
                        ('GBP', 'British Pound (GBP)'),
                        ('INR', 'Indian Rupee (INR)'),
                    ],
                    default='AED',
                    max_length=3,
                )),
                ('basic_salary',     models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('components',       models.JSONField(blank=True, default=list)),
                ('total_gross',      models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('total_deductions', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('net_salary',       models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('status',           models.CharField(
                    choices=[
                        ('DRAFT',            'Draft'),
                        ('PENDING_APPROVAL', 'Pending Approval'),
                        ('APPROVED',         'Approved'),
                        ('REJECTED',         'Rejected'),
                    ],
                    db_index=True,
                    default='DRAFT',
                    max_length=20,
                )),
                ('submitted_at',     models.DateTimeField(blank=True, null=True)),
                ('reviewed_at',      models.DateTimeField(blank=True, null=True)),
                ('reviewer_note',    models.TextField(blank=True)),
                ('is_active',        models.BooleanField(db_index=True, default=True)),
                ('created_at',       models.DateTimeField(auto_now_add=True)),
                ('updated_at',       models.DateTimeField(auto_now=True)),
                ('created_by',       models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_salary_structures',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('reviewed_by',      models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_salary_structures',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('submitted_by',     models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='submitted_salary_structures',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('superseded_by',    models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='supersedes',
                    to='payroll.employeesalarystructure',
                )),
            ],
            options={
                'db_table': 'payroll_salary_structure',
                'ordering': ['-effective_date', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='employeesalarystructure',
            index=models.Index(fields=['employee_code', 'status'],    name='payroll_ss_code_status'),
        ),
        migrations.AddIndex(
            model_name='employeesalarystructure',
            index=models.Index(fields=['employee_code', 'is_active'], name='payroll_ss_code_active'),
        ),

        # ── SalaryHistory ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='SalaryHistory',
            fields=[
                ('id',             models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('employee_code',  models.CharField(db_index=True, max_length=30)),
                ('employee_name',  models.CharField(max_length=255)),
                ('change_date',    models.DateField()),
                ('previous_basic', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('new_basic',      models.DecimalField(decimal_places=2, max_digits=14)),
                ('previous_net',   models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('new_net',        models.DecimalField(decimal_places=2, max_digits=14)),
                ('change_percent', models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True)),
                ('change_reason',  models.TextField(blank=True)),
                ('created_at',     models.DateTimeField(auto_now_add=True)),
                ('approved_by',    models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='approved_salary_histories',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('structure',      models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='history_entries',
                    to='payroll.employeesalarystructure',
                )),
            ],
            options={
                'db_table': 'payroll_salary_history',
                'ordering': ['-change_date', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='salaryhistory',
            index=models.Index(fields=['employee_code', 'change_date'], name='payroll_sh_code_date'),
        ),
    ]
