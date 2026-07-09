"""
Migration 0005 — Add PublicHoliday and AttendanceOverride tables.

PublicHoliday: stores the official Abu Dhabi / UAE government holiday calendar
   plus any custom holidays added by HR Managers.  Seeded separately via the
   `seed_public_holidays` management command.

AttendanceOverride: HR Manager manual correction for a single (employee, date)
   cell in the Summary pivot table.  Corrections are never deleted — only
   deactivated — to maintain a full audit trail.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0004_employeeleaverecord_branch'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── PublicHoliday ────────────────────────────────────────────────────
        migrations.CreateModel(
            name='PublicHoliday',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date',       models.DateField(db_index=True)),
                ('name',       models.CharField(help_text='Official name in English, e.g. "UAE National Day".', max_length=255)),
                ('name_ar',    models.CharField(blank=True, help_text='Arabic name (optional).', max_length=255)),
                ('region',     models.CharField(
                    choices=[
                        ('AE-AZ',   'Abu Dhabi (UAE)'),
                        ('AE',      'UAE-wide'),
                        ('SA',      'Saudi Arabia'),
                        ('QA',      'Qatar'),
                        ('KW',      'Kuwait'),
                        ('BH',      'Bahrain'),
                        ('OM',      'Oman'),
                        ('COMPANY', 'Company-specific'),
                    ],
                    db_index=True,
                    default='AE-AZ',
                    help_text='Geographic scope of this holiday.',
                    max_length=20,
                )),
                ('source',     models.CharField(
                    choices=[
                        ('government', 'Abu Dhabi Government Official Calendar'),
                        ('hr_added',   'Added by HR Manager'),
                    ],
                    default='government',
                    help_text='Whether seeded from the official calendar or added by HR.',
                    max_length=20,
                )),
                ('note',       models.TextField(blank=True, help_text='HR note, e.g. confirmed date, subject to moon sighting.')),
                ('is_active',  models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_holidays',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='updated_holidays',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'payroll_public_holiday',
                'ordering': ['date'],
            },
        ),
        migrations.AddConstraint(
            model_name='publicholiday',
            constraint=models.UniqueConstraint(
                fields=['date', 'region'],
                name='payroll_ph_date_region_uniq',
            ),
        ),
        migrations.AddIndex(
            model_name='publicholiday',
            index=models.Index(fields=['date', 'is_active'], name='payroll_ph_date_active_idx'),
        ),

        # ── AttendanceOverride ───────────────────────────────────────────────
        migrations.CreateModel(
            name='AttendanceOverride',
            fields=[
                ('id',             models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('employee_code',  models.CharField(db_index=True, max_length=30)),
                ('employee_name',  models.CharField(blank=True, max_length=255)),
                ('date',           models.DateField(db_index=True)),
                ('original_hours', models.DecimalField(
                    blank=True,
                    decimal_places=2,
                    help_text='Biometric hours recorded before this correction.',
                    max_digits=5,
                    null=True,
                )),
                ('override_hours', models.DecimalField(
                    decimal_places=2,
                    help_text='Corrected hours to display instead of the biometric value.',
                    max_digits=5,
                )),
                ('reason', models.CharField(
                    choices=[
                        ('biometric_error', 'Biometric device error'),
                        ('system_outage',   'System / network outage'),
                        ('forgot_punch',    'Employee forgot to punch'),
                        ('site_visit',      'On-site client visit (no biometric access)'),
                        ('wfh',             'Work from home (WFH approved)'),
                        ('travel',          'Business travel'),
                        ('training',        'Approved external training'),
                        ('hr_correction',   'HR administrative correction'),
                        ('other',           'Other (see note)'),
                    ],
                    default='hr_correction',
                    max_length=30,
                )),
                ('note',       models.TextField(blank=True)),
                ('is_active',  models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='attendance_overrides_created',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'payroll_attendance_override',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='attendanceoverride',
            index=models.Index(
                fields=['employee_code', 'date', 'is_active'],
                name='payroll_ao_cd_active_idx',
            ),
        ),
    ]
