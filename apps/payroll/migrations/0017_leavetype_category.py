# Migration: add `category` field to LeaveType and back-fill existing records.
# The category key maps to ESS_LEAVE_TYPE_CONFIG in hrLeave.config.js so the
# ESS portal can filter leave types without hardcoded code comparisons.
from django.db import migrations, models


# ── Mapping: leave type code → ESS category key ───────────────────────────────
CODE_TO_CATEGORY = {
    'AL': 'annual',
    'SL': 'sick',
    'EL': 'emergency',
    'UL': 'unpaid',
    'ML': 'maternity',
    'PL': 'paternity',
    'CL': 'compensatory',
    'PH': 'public_holiday',
    'WO': 'work_off',
}


def populate_categories(apps, schema_editor):
    """Back-fill category for all existing LeaveType rows."""
    LeaveType = apps.get_model('payroll', 'LeaveType')
    for code, category in CODE_TO_CATEGORY.items():
        LeaveType.objects.filter(code=code).update(category=category)


def seed_missing_leave_types(apps, schema_editor):
    """
    Ensure Maternity (ML), Paternity (PL), and Compensatory (CL) exist.
    Migration 0003 already seeds ML and PL; this is a safe get_or_create.
    CL is added here for the first time if it does not already exist.
    """
    LeaveType = apps.get_model('payroll', 'LeaveType')
    extra_types = [
        # code, name, color_hex, badge_bg, badge_text, badge_border, paid, approval, doc, order, category
        ('ML', 'Maternity Leave', '#8b5cf6', 'bg-purple-100', 'text-purple-800', 'border-purple-300', True,  True, True,  5, 'maternity'),
        ('PL', 'Paternity Leave', '#6366f1', 'bg-indigo-100', 'text-indigo-800', 'border-indigo-300', True,  True, True,  6, 'paternity'),
        ('CL', 'Compensatory Leave', '#8b5cf6', 'bg-violet-100', 'text-violet-800', 'border-violet-300', True, True, False, 3, 'compensatory'),
    ]
    for code, name, color_hex, badge_bg, badge_text, badge_border, is_paid, req_approval, req_doc, order, category in extra_types:
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
                'category': category,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0016_add_monthly_leave_accrual_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='leavetype',
            name='category',
            field=models.CharField(
                blank=True,
                choices=[
                    ('annual',         'Annual Leave'),
                    ('sick',           'Sick Leave'),
                    ('emergency',      'Emergency Leave'),
                    ('unpaid',         'Unpaid Leave'),
                    ('maternity',      'Maternity Leave'),
                    ('paternity',      'Paternity Leave'),
                    ('compensatory',   'Compensatory Leave'),
                    ('public_holiday', 'Public Holiday'),
                    ('work_off',       'Work Off'),
                    ('other',          'Other'),
                ],
                db_index=True,
                default='other',
                help_text='Canonical category key — must match ESS_LEAVE_TYPE_CONFIG key in hrLeave.config.js',
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_missing_leave_types, migrations.RunPython.noop),
        migrations.RunPython(populate_categories,      migrations.RunPython.noop),
    ]
