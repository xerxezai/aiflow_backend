import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.spec_customization.models import PaperSpecExtractionJob
from apps.spec_customization.services.exporters.workbook_preview import build_preview

job = PaperSpecExtractionJob.objects.order_by('-created_at').first()
data = build_preview(job, 'spec')
s = next((s for s in data['sheets'] if s['name'] == 'GasketSelectionFilter'), None)
if not s:
    print('NO GasketSelectionFilter SHEET FOUND')
else:
    print('rows:', s['row_count'])
    print('headers:', s['headers'])
    for idx in (2, 5, 40):
        if idx < len(s['rows']):
            print(f'--- row {idx} ---')
            for k, v in s['rows'][idx]['cells'].items():
                print(f'  {k}: {v!r}')
