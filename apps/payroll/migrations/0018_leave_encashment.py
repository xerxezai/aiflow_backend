"""
Migration 0018 — Leave Encashment System

1. Add EmployeeLeaveMonthly.encashment_pay
2. Create LeaveEncashmentRun model
3. Add MasterPayrollRow.leave_encashment_days + leave_encashment_pay
4. Fresh-start data reset:
   - Zero EmployeeLeaveRecord.total_encashed and leave_balance
   - Zero EmployeeLeaveMonthly.encashed, encashment_pay, balance
   - Clear MonthlyLeaveAccrualLog so July 2026 re-accrues cleanly
"""
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


def fresh_start_reset(apps, schema_editor):
    """
    Zero out all previously-imported leave balances and encashment data.
    The monthly accrual task will rebuild EmployeeLeaveMonthly going forward.
    """
    EmployeeLeaveRecord  = apps.get_model('payroll', 'EmployeeLeaveRecord')
    EmployeeLeaveMonthly = apps.get_model('payroll', 'EmployeeLeaveMonthly')
    MonthlyLeaveAccrualLog = apps.get_model('payroll', 'MonthlyLeaveAccrualLog')

    # Zero out annual totals on all leave records (fresh-start)
    EmployeeLeaveRecord.objects.all().update(
        total_encashed=Decimal('0'),
        leave_balance=Decimal('0'),
    )

    # Zero out all monthly breakdowns — the accrual task rebuilds these
    EmployeeLeaveMonthly.objects.all().update(
        earned=Decimal('0'),
        taken=Decimal('0'),
        encashed=Decimal('0'),
        balance=Decimal('0'),
    )

    # Clear accrual log so the July 2026 accrual can be re-run via the
    # existing POST /api/v1/payroll/initialize-current-month-leave/ endpoint
    MonthlyLeaveAccrualLog.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0017_leavetype_category'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── 1. EmployeeLeaveMonthly: add encashment_pay ────────────────────────
        migrations.AddField(
            model_name='employeeleavemonthly',
            name='encashment_pay',
            field=models.DecimalField(
                max_digits=10, decimal_places=2, default=Decimal('0'),
                help_text='Monetary value of encashed days (days × daily_rate)',
            ),
        ),

        # ── 2. LeaveEncashmentRun model ────────────────────────────────────────
        migrations.CreateModel(
            name='LeaveEncashmentRun',
            fields=[
                ('id',                  models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('year',                models.PositiveIntegerField()),
                ('month',               models.PositiveSmallIntegerField(choices=[
                    (1,'January'),(2,'February'),(3,'March'),(4,'April'),
                    (5,'May'),(6,'June'),(7,'July'),(8,'August'),
                    (9,'September'),(10,'October'),(11,'November'),(12,'December'),
                ])),
                ('triggered_by',        models.ForeignKey(
                    settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True, related_name='encashment_runs',
                    help_text='HR Manager who triggered this run',
                )),
                ('executed_at',         models.DateTimeField(auto_now_add=True)),
                ('status',              models.CharField(max_length=20, default='success', choices=[
                    ('success','Success'),('partial','Partial'),('failed','Failed'),
                ])),
                ('records_processed',   models.PositiveIntegerField(default=0)),
                ('total_days_encashed', models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('0'),
                    help_text='Sum of all encashed days across all employees')),
                ('total_pay',           models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'),
                    help_text='Sum of all encashment pay amounts (AED)')),
                ('missing_salaries',    models.JSONField(default=list, blank=True,
                    help_text='employee_codes with no salary on record')),
                ('branch_filter',       models.CharField(max_length=10, blank=True, null=True)),
                ('notes',               models.TextField(blank=True)),
            ],
            options={
                'db_table': 'payroll_leave_encashment_run',
                'ordering': ['-year', '-month'],
            },
        ),
        migrations.AddIndex(
            model_name='leaveencashmentrun',
            index=models.Index(fields=['year', 'month'], name='payroll_enc_yr_mo'),
        ),
        migrations.AlterUniqueTogether(
            name='leaveencashmentrun',
            unique_together={('year', 'month')},
        ),

        # ── 3. MasterPayrollRow: add encashment columns ────────────────────────
        migrations.AddField(
            model_name='masterpayrollrow',
            name='leave_encashment_days',
            field=models.DecimalField(
                max_digits=6, decimal_places=2, default=Decimal('0'),
                help_text='Encashed leave days for this payroll period',
            ),
        ),
        migrations.AddField(
            model_name='masterpayrollrow',
            name='leave_encashment_pay',
            field=models.DecimalField(
                max_digits=14, decimal_places=2, default=Decimal('0'),
                help_text='Monetary value of encashed leave (AED)',
            ),
        ),

        # ── 4. Fresh-start data reset ──────────────────────────────────────────
        migrations.RunPython(fresh_start_reset, migrations.RunPython.noop),
    ]
