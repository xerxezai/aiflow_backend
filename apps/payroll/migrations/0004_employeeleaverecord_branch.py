"""
Migration 0004 — Add branch field to EmployeeLeaveRecord
Backfills all existing records to branch='RAD' (they all came from the RAD Excel).
"""
from django.db import migrations, models


def backfill_rad(apps, schema_editor):
    EmployeeLeaveRecord = apps.get_model('payroll', 'EmployeeLeaveRecord')
    EmployeeLeaveRecord.objects.filter(branch='').update(branch='RAD')
    # Also cover any rows that may already have default (CharField default isn't applied
    # by DB for existing rows in some setups)
    EmployeeLeaveRecord.objects.all().update(branch='RAD')


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0003_leavetype_leaverequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeleaverecord',
            name='branch',
            field=models.CharField(
                choices=[('RAD', 'Rejlers AB'), ('RIN', 'Rejlers IN')],
                db_index=True,
                default='RAD',
                max_length=10,
            ),
        ),
        migrations.RunPython(backfill_rad, migrations.RunPython.noop),
    ]
