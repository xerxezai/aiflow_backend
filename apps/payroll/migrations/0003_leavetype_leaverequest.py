# Generated migration for LeaveType and LeaveRequest models
from django.conf import settings
import django.db.models.deletion
import uuid
from decimal import Decimal
from django.db import migrations, models


# ─── Seed data — mirrors hrLeave.config.js DEFAULT_LEAVE_TYPES ───────────────
def seed_leave_types(apps, schema_editor):
    LeaveType = apps.get_model('payroll', 'LeaveType')
    defaults = [
        # code, name, color_hex, badge_bg, badge_text, badge_border, paid, approval, doc, order
        ('AL', 'Annual Leave',    '#10b981', 'bg-emerald-100', 'text-emerald-800', 'border-emerald-300', True,  True,  False, 1),
        ('SL', 'Sick Leave',      '#3b82f6', 'bg-blue-100',    'text-blue-800',    'border-blue-300',    True,  True,  True,  2),
        ('EL', 'Emergency Leave', '#f59e0b', 'bg-amber-100',   'text-amber-800',   'border-amber-300',   True,  True,  False, 3),
        ('UL', 'Unpaid Leave',    '#ef4444', 'bg-red-100',     'text-red-800',     'border-red-300',     False, True,  False, 4),
        ('ML', 'Maternity Leave', '#8b5cf6', 'bg-purple-100',  'text-purple-800',  'border-purple-300',  True,  True,  True,  5),
        ('PL', 'Paternity Leave', '#6366f1', 'bg-indigo-100',  'text-indigo-800',  'border-indigo-300',  True,  True,  True,  6),
        ('PH', 'Public Holiday',  '#6b7280', 'bg-slate-100',   'text-slate-700',   'border-slate-300',   False, False, False, 7),
        ('WO', 'Work Off',        '#14b8a6', 'bg-teal-100',    'text-teal-800',    'border-teal-300',    False, True,  False, 8),
    ]
    for code, name, color_hex, badge_bg, badge_text, badge_border, is_paid, req_approval, req_doc, order in defaults:
        LeaveType.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'color_hex': color_hex,
                'badge_bg': badge_bg,
                'badge_text': badge_text,
                'badge_border': badge_border,
                'is_paid': is_paid,
                'requires_approval': req_approval,
                'requires_document': req_doc,
                'is_active': True,
                'display_order': order,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0002_employeeleaverecord_employeeleavemonthly'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── LeaveType ──────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='LeaveType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code',              models.CharField(db_index=True, max_length=10, unique=True)),
                ('name',              models.CharField(max_length=100)),
                ('color_hex',         models.CharField(default='#6b7280', max_length=7)),
                ('badge_bg',          models.CharField(blank=True, default='bg-slate-100', max_length=60)),
                ('badge_text',        models.CharField(blank=True, default='text-slate-700', max_length=60)),
                ('badge_border',      models.CharField(blank=True, default='border-slate-300', max_length=60)),
                ('is_paid',           models.BooleanField(default=True)),
                ('requires_approval', models.BooleanField(default=True)),
                ('requires_document', models.BooleanField(default=False)),
                ('is_active',         models.BooleanField(default=True)),
                ('display_order',     models.PositiveSmallIntegerField(default=99)),
            ],
            options={'db_table': 'payroll_leave_type', 'ordering': ['display_order', 'code']},
        ),
        # ── LeaveRequest ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name='LeaveRequest',
            fields=[
                ('id',            models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('employee',      models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='leave_requests', to=settings.AUTH_USER_MODEL)),
                ('employee_code', models.CharField(blank=True, db_index=True, max_length=30, null=True)),
                ('employee_name', models.CharField(db_index=True, max_length=255)),
                ('department',    models.CharField(blank=True, max_length=100)),
                ('leave_type',    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='requests', to='payroll.leavetype')),
                ('start_date',    models.DateField()),
                ('end_date',      models.DateField()),
                ('days_requested', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=6)),
                ('reason',        models.TextField(blank=True)),
                ('status',        models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected'), ('CANCELLED', 'Cancelled')], db_index=True, default='PENDING', max_length=20)),
                ('reviewed_by',   models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_leave_requests', to=settings.AUTH_USER_MODEL)),
                ('reviewed_at',   models.DateTimeField(blank=True, null=True)),
                ('reviewer_note', models.TextField(blank=True)),
                ('created_at',    models.DateTimeField(auto_now_add=True)),
                ('updated_at',    models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'payroll_leave_request', 'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='leaverequest',
            index=models.Index(fields=['employee_code', 'start_date', 'end_date'], name='payroll_lr_code_dates_idx'),
        ),
        migrations.AddIndex(
            model_name='leaverequest',
            index=models.Index(fields=['status', 'start_date'], name='payroll_lr_status_date_idx'),
        ),
        # ── Seed default leave types ───────────────────────────────────────────
        migrations.RunPython(seed_leave_types, migrations.RunPython.noop),
    ]
