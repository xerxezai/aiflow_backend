"""Add working_days_in_month to PayrollRun.

HR enters the total working days for the selected month before generating
a payroll run. Default = 22 (UAE standard, soft-coded in catalog.py).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0005_payslip_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollrun',
            name='working_days_in_month',
            field=models.PositiveSmallIntegerField(
                default=22,
                help_text='Total working days in this payroll month (HR-entered at generation). Default 22.',
            ),
        ),
    ]
