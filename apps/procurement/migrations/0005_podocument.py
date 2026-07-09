"""
Migration: Add PODocument model for AI-based PO extraction.
"""

import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0004_purchaserequisition_requisition_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PODocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('original_filename', models.CharField(max_length=300)),
                ('s3_key', models.CharField(blank=True, max_length=500)),
                ('s3_url', models.URLField(blank=True, max_length=1000)),
                ('file_size_bytes', models.PositiveIntegerField(default=0)),
                ('document_type', models.CharField(
                    choices=[
                        ('purchase_order', 'Purchase Order'),
                        ('purchase_requisition', 'Purchase Requisition'),
                        ('unknown', 'Unknown'),
                    ],
                    default='unknown',
                    max_length=30,
                )),
                ('extraction_status', models.CharField(
                    choices=[
                        ('pending', 'Pending Extraction'),
                        ('processing', 'Processing'),
                        ('completed', 'Extraction Completed'),
                        ('failed', 'Extraction Failed'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('extraction_error', models.TextField(blank=True)),
                ('extracted_data', models.JSONField(blank=True, default=dict)),
                ('confirmed_po', models.ForeignKey(
                    blank=True,
                    help_text='Populated once user confirms the extracted data as a real PO',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='source_documents',
                    to='procurement.purchaseorder',
                )),
                ('uploaded_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='po_documents_uploaded',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'procurement_po_documents',
                'ordering': ['-created_at'],
            },
        ),
    ]
