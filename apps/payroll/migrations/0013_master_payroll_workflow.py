"""
Migration: Add approval workflow stage + WorkflowLog to MasterPayrollImport.

Adds:
  - MasterPayrollImport.workflow_stage
  - MasterPayrollImport.frozen_by / frozen_at
  - MasterPayrollImport.hr_approved_by / hr_approved_at / hr_approval_note
  - MasterPayrollImport.finance_approved_by / finance_approved_at / finance_approval_note
  - MasterPayrollImport.released_by / released_at / release_note
  - New table: payroll_workflow_log
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0012_masterpayrollimport_other_filename'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Workflow stage on MasterPayrollImport ────────────────────────────
        migrations.AddField(
            model_name='masterpayrollimport',
            name='workflow_stage',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('draft',            'Draft — HR Editing'),
                    ('frozen',           'Frozen — Awaiting HR Approval'),
                    ('hr_approved',      'HR Approved — Finance Review'),
                    ('finance_review',   'Finance Review — In Progress'),
                    ('finance_approved', 'Finance Approved — Awaiting Release'),
                    ('released',         'Released — Salary Disbursed'),
                ],
                default='draft',
                db_index=True,
            ),
        ),
        # ── Freeze tracking ──────────────────────────────────────────────────
        migrations.AddField(
            model_name='masterpayrollimport',
            name='frozen_by',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='frozen_master_payrolls',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='frozen_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        # ── HR approval tracking ─────────────────────────────────────────────
        migrations.AddField(
            model_name='masterpayrollimport',
            name='hr_approved_by',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='hr_approved_master_payrolls',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='hr_approved_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='hr_approval_note',
            field=models.TextField(blank=True, default=''),
        ),
        # ── Finance approval tracking ────────────────────────────────────────
        migrations.AddField(
            model_name='masterpayrollimport',
            name='finance_approved_by',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='finance_approved_master_payrolls',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='finance_approved_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='finance_approval_note',
            field=models.TextField(blank=True, default=''),
        ),
        # ── Salary release tracking ──────────────────────────────────────────
        migrations.AddField(
            model_name='masterpayrollimport',
            name='released_by',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='released_master_payrolls',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='released_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='masterpayrollimport',
            name='release_note',
            field=models.TextField(blank=True, default=''),
        ),
        # ── New table: payroll_workflow_log ──────────────────────────────────
        migrations.CreateModel(
            name='MasterPayrollWorkflowLog',
            fields=[
                ('id',           models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('from_stage',   models.CharField(max_length=20, blank=True)),
                ('to_stage',     models.CharField(max_length=20)),
                ('action',       models.CharField(max_length=30)),
                ('performed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('note',         models.TextField(blank=True)),
                ('master_import', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='workflow_logs',
                    to='payroll.masterpayrollimport',
                )),
                ('performed_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='payroll_workflow_actions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'payroll_workflow_log',
                'ordering': ['performed_at'],
            },
        ),
        migrations.AddIndex(
            model_name='masterpayrollworkflowlog',
            index=models.Index(
                fields=['master_import', 'performed_at'],
                name='payroll_wfl_import_at',
            ),
        ),
        migrations.AddIndex(
            model_name='masterpayrollimport',
            index=models.Index(
                fields=['workflow_stage'],
                name='payroll_mi_workflow_stage',
            ),
        ),
    ]
