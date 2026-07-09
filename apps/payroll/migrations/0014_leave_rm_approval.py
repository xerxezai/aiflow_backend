"""
Migration: Add Reporting Manager (Stage 1) approval fields to LeaveRequest.

Two-stage leave approval workflow:
  Stage 1 — Reporting Manager: PENDING → RM_APPROVED | RM_REJECTED
  Stage 2 — HR Manager:        RM_APPROVED → APPROVED | REJECTED
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0013_master_payroll_workflow'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Add RM reviewer FK
        migrations.AddField(
            model_name='leaverequest',
            name='rm_reviewed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='rm_reviewed_leave_requests',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 2. Add RM reviewed timestamp
        migrations.AddField(
            model_name='leaverequest',
            name='rm_reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # 3. Add RM reviewer note
        migrations.AddField(
            model_name='leaverequest',
            name='rm_note',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        # 4. Expand the status choices to include RM_APPROVED and RM_REJECTED
        #    (CharField max_length stays 20 — 'RM_APPROVED' = 11 chars, fits)
        migrations.AlterField(
            model_name='leaverequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING',     'Pending'),
                    ('RM_APPROVED', 'Pending HR Approval'),
                    ('RM_REJECTED', 'Rejected by Manager'),
                    ('APPROVED',    'Approved'),
                    ('REJECTED',    'Rejected'),
                    ('CANCELLED',   'Cancelled'),
                ],
                db_index=True,
                default='PENDING',
                max_length=20,
            ),
        ),
    ]
