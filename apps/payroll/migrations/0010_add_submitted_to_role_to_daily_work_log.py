# Generated manually 2026-06-19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0009_add_approval_workflow_to_daily_work_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyworklog',
            name='submitted_to_role',
            field=models.CharField(
                blank=True,
                choices=[
                    ('project_manager',   'Project Manager'),
                    ('reporting_manager', 'Reporting Manager'),
                ],
                default='',
                help_text='Manager role type the employee directed this log to.',
                max_length=20,
            ),
        ),
    ]
