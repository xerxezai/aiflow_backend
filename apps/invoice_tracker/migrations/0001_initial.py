"""Manually authored initial migration for apps.invoice_tracker.

Written by hand (instead of `makemigrations`) because an unrelated model
drift in another app (pumpcalculationdata.casing) makes the interactive
autodetector prompt, which conflicts with the workspace rule
"do not modify files belonging to a different feature".

If/when the upstream drift is resolved, this migration can be regenerated:
    python manage.py makemigrations invoice_tracker
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.invoice_tracker.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerInvoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('invoice_number', models.CharField(db_index=True, max_length=64, unique=True)),
                ('category', models.CharField(choices=[('external', 'External (Customer)'), ('internal', 'Internal (Rejlers Group)')], db_index=True, default='external', max_length=16)),
                ('credit_note_ref', models.CharField(blank=True, default='', max_length=128)),
                ('account', models.CharField(blank=True, db_index=True, default='', max_length=256)),
                ('company', models.CharField(blank=True, default='', max_length=256)),
                ('rad_project_no', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('project_name', models.TextField(blank=True, default='')),
                ('project_id', models.CharField(blank=True, default='', max_length=64)),
                ('invoice_date', models.DateField(blank=True, db_index=True, null=True)),
                ('invoice_sent_date', models.DateField(blank=True, null=True)),
                ('due_date', models.DateField(blank=True, db_index=True, null=True)),
                ('payment_date', models.DateField(blank=True, null=True)),
                ('payment_terms', models.CharField(blank=True, default='', max_length=64)),
                ('currency', models.CharField(choices=[('AED', 'AED'), ('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP'), ('SGD', 'SGD')], default='AED', max_length=4)),
                ('ppc_value', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('retention', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('icv_applicable', models.BooleanField(default=False)),
                ('invoice_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('invoice_amount_aed', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('amount_excl_vat', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('grand_total', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('balance_to_be_received', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('actual_payment_received', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('paid_amount_excl_vat', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('payment_status', models.CharField(choices=[('pending', 'Pending'), ('paid', 'Paid'), ('partial', 'Partially Paid'), ('overdue', 'Overdue'), ('cancelled', 'Cancelled'), ('credit_note', 'Credit Note')], db_index=True, default='pending', max_length=16)),
                ('days_overdue', models.IntegerField(blank=True, null=True)),
                ('bank_reference_code', models.CharField(blank=True, default='', max_length=64)),
                ('customer_inv_reference', models.CharField(blank=True, default='', max_length=128)),
                ('contract_clause', models.CharField(blank=True, default='', max_length=256)),
                ('finance_pm_email', models.CharField(blank=True, default='', max_length=256)),
                ('pm', models.CharField(blank=True, default='', max_length=128)),
                ('details', models.TextField(blank=True, default='')),
                ('remarks', models.TextField(blank=True, default='')),
                ('sent_by', models.CharField(blank=True, default='', max_length=128)),
                ('sent_to_account', models.CharField(blank=True, default='', max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-invoice_date', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='customerinvoice',
            index=models.Index(fields=['account', 'payment_status'], name='invoice_tra_account_dab1bb_idx'),
        ),
        migrations.AddIndex(
            model_name='customerinvoice',
            index=models.Index(fields=['rad_project_no', 'invoice_date'], name='invoice_tra_rad_pro_71f8b9_idx'),
        ),
        migrations.CreateModel(
            name='InvoiceAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(max_length=500, upload_to=apps.invoice_tracker.models.attachment_upload_path)),
                ('original_filename', models.CharField(blank=True, default='', max_length=255)),
                ('content_type', models.CharField(blank=True, default='', max_length=128)),
                ('size_bytes', models.BigIntegerField(blank=True, null=True)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='invoice_tracker.customerinvoice')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-uploaded_at'],
            },
        ),
    ]
