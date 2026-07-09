"""Add source_type to PayrollRun — 'system' (default) or 'import'."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_engine', '0010_payrollrunupload'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollrun',
            name='source_type',
            field=models.CharField(
                max_length=20,
                choices=[('system', 'System Generated'), ('import', 'Imported from File')],
                default='system',
                db_index=True,
                help_text='Origin of this run (system-generated vs imported from Excel).',
            ),
        ),
    ]
