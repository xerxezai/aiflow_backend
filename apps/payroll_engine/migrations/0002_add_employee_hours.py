"""Add monthly contracted `hours` to PayrollEmployee and snapshot `hours` on Payslip.

Default is sourced from apps.payroll_engine.config.DEFAULT_EMPLOYEE_HOURS
(soft-coded via PAYROLL_DEFAULT_HOURS env var; falls back to
STANDARD_WORKDAYS_PER_MONTH × STANDARD_HOURS_PER_DAY).
"""
from django.db import migrations, models

from apps.payroll_engine.config import DEFAULT_EMPLOYEE_HOURS


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollemployee',
            name='hours',
            field=models.DecimalField(
                decimal_places=2,
                default=DEFAULT_EMPLOYEE_HOURS,
                max_digits=8,
            ),
        ),
        migrations.AddField(
            model_name='payslip',
            name='hours',
            field=models.DecimalField(
                decimal_places=2,
                default=DEFAULT_EMPLOYEE_HOURS,
                max_digits=8,
            ),
        ),
    ]
