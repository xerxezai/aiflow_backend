"""Add public_holidays_in_month to PayrollRun.

Auto-computed at run generation from the PublicHoliday register in apps.payroll.
Regions counted: AE (UAE national) + COMPANY (HR-added), soft-coded in catalog.py.
Default = 0 (updated immediately on first generation/regeneration).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0006_payrollrun_working_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollrun',
            name='public_holidays_in_month',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Official public holidays in this payroll month (auto-computed from PH register at generation).',
            ),
        ),
    ]
