import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.spec_customization.models import PaperSpecExtractionJob
from apps.spec_customization.services.exporters.workbook_preview import build_preview

job = PaperSpecExtractionJob.objects.order_by('-created_at').first()
print('Job:', job.id)
preview = build_preview(job, 'spec')
sheets = {s['name']: s for s in preview['sheets']}
s = sheets.get('BoltSelectionFilter')
if not s:
    print('NO BoltSelectionFilter sheet')
else:
    print('rows:', len(s['rows']))
    for idx in (2, 5, 30, 60):
        if idx < len(s['rows']):
            print(f'--- row {idx} ---')
            for k, v in s['rows'][idx]['cells'].items():
                print(f'  {k}: {v!r}')
