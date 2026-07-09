# Generated manually — assigns IOListDocumentStorage to IOListDocument.pdf_file
# so that presigned S3 URLs are generated for private file access (me-central-1 region).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='iolistdocument',
            name='pdf_file',
            field=models.FileField(
                storage='apps.core.storage_backends.IOListDocumentStorage',
                upload_to='instrument_io_workflow/%Y/%m/',
            ),
        ),
    ]
