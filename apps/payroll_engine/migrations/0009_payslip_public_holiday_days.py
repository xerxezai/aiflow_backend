"""Add public_holiday_days to Payslip.

Per-employee editable field (replaces the read-only run-level SerializerMethodField).
Seeded from run.public_holidays_in_month at generation; HR can override per employee.
"""
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0008_payslip_leave_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='payslip',
            name='public_holiday_days',
            field=models.DecimalField(
                max_digits=8, decimal_places=2, default=Decimal('0.00'),
                help_text='Public holiday days for this employee in this payroll month.',
            ),
        ),
    ]
