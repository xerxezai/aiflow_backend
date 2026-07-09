"""
Migration: Add other_filename to MasterPayrollImport.

Tracks the name of the supplementary / other file (bonuses, gratuity,
insurance, custom deductions) uploaded during a master payroll generation run.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0011_master_payroll_import'),
    ]

    operations = [
        migrations.AddField(
            model_name='masterpayrollimport',
            name='other_filename',
            field=models.CharField(blank=True, default='', max_length=255),
            preserve_default=False,
        ),
    ]
