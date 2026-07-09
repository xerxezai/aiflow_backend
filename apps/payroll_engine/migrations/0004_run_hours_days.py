"""Add denormalised total_hours / total_days to PayrollRun.

The two new columns surface the live biometric hours sum and the same
value expressed in work-days (hours ÷ HOURS_PER_WORKDAY, default 9 at
Rejlers Abu Dhabi). Backfilled from existing Payslip.hours so already-
generated runs immediately show real numbers in the UI.
"""
from decimal import Decimal

from django.db import migrations, models


HOURS_PER_WORKDAY = Decimal('9')


def _backfill_run_hours(apps, schema_editor):
    PayrollRun = apps.get_model('payroll_engine', 'PayrollRun')
    Payslip = apps.get_model('payroll_engine', 'Payslip')
    zero = Decimal('0.00')
    for run in PayrollRun.objects.all():
        total = zero
        for slip in Payslip.objects.filter(run=run).only('hours'):
            total += slip.hours or zero
        run.total_hours = total.quantize(Decimal('0.01'))
        if HOURS_PER_WORKDAY > 0:
            run.total_days = (total / HOURS_PER_WORKDAY).quantize(Decimal('0.01'))
        else:
            run.total_days = zero
        run.save(update_fields=['total_hours', 'total_days'])


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0003_add_comparison'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollrun',
            name='total_hours',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
        migrations.AddField(
            model_name='payrollrun',
            name='total_days',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
        migrations.RunPython(_backfill_run_hours, reverse_code=_noop),
    ]
