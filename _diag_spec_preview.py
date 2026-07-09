import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.spec_customization import models as m
from apps.spec_customization.services.exporters import workbook_preview as wp
from apps.spec_customization.services.exporters import smartplant_config as cfg

Job = m.PaperSpecExtractionJob
job = Job.objects.order_by('-id').first()
print('Job:', job and job.id, 'classes:', job and job.piping_classes.count())
if job:
    print('SPEC_SHEET_BUILDERS keys:', list(cfg.SPEC_SHEET_BUILDERS.keys()))
    p = wp.build_preview(job, 'spec')
    print('Sheets returned:', len(p['sheets']))
    for s in p['sheets']:
        print(f"  [{s['row_count']:4d} rows, {len(s['headers']):2d} hdr] {s['name']}")
