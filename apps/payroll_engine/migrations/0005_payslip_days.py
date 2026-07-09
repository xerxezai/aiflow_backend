"""Add ``Payslip.days`` (live work-days derived from ``hours``).

Days = hours ÷ HOURS_PER_WORKDAY (default 9). Backfilled from each
payslip's existing ``hours`` so the column is non-zero on day one.
"""
from decimal import Decimal

from django.db import migrations, models


HOURS_PER_WORKDAY = Decimal('9')


def _backfill_days(apps, schema_editor):
    Payslip = apps.get_model('payroll_engine', 'Payslip')
    zero = Decimal('0.00')
    for slip in Payslip.objects.only('id', 'hours').iterator():
        h = slip.hours or zero
        if HOURS_PER_WORKDAY > 0:
            slip.days = (h / HOURS_PER_WORKDAY).quantize(Decimal('0.01'))
        else:
            slip.days = zero
        slip.save(update_fields=['days'])


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0004_run_hours_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='payslip',
            name='days',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=8),
        ),
        migrations.RunPython(_backfill_days, reverse_code=_noop),
    ]
