"""
Hand-written migration that adds the Enquiry model used by the public
/enquiry contact form and the 9.6 Enquiry admin page.

Written manually (instead of running `makemigrations`) because unrelated
apps in the project currently have model drift that breaks the interactive
auto-detector.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_project_contract_scope'),
    ]

    operations = [
        migrations.CreateModel(
            name='Enquiry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('email', models.EmailField(max_length=254)),
                ('phone', models.CharField(max_length=40)),
                ('company', models.CharField(blank=True, default='', max_length=160)),
                ('subject', models.CharField(max_length=200)),
                ('message', models.TextField()),
                ('service', models.CharField(blank=True, default='', max_length=60)),
                ('urgency', models.CharField(
                    choices=[
                        ('low', 'Low Priority'),
                        ('normal', 'Normal Priority'),
                        ('high', 'High Priority'),
                        ('urgent', 'Urgent'),
                    ],
                    default='normal',
                    max_length=10,
                )),
                ('status', models.CharField(
                    choices=[
                        ('new', 'New'),
                        ('in_review', 'In Review'),
                        ('contacted', 'Contacted'),
                        ('resolved', 'Resolved'),
                        ('spam', 'Spam'),
                    ],
                    db_index=True,
                    default='new',
                    max_length=12,
                )),
                ('admin_notes', models.TextField(blank=True, default='')),
                ('source_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, default='', max_length=400)),
            ],
            options={
                'verbose_name': 'Enquiry',
                'verbose_name_plural': 'Enquiries',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='enquiry',
            index=models.Index(fields=['-created_at'], name='core_enquir_created_idx'),
        ),
        migrations.AddIndex(
            model_name='enquiry',
            index=models.Index(fields=['status', '-created_at'], name='core_enquir_status_idx'),
        ),
    ]
