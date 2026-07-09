"""
Diagnose which fields are empty across extracted items from P&ID_5.pdf
and show the OCR context around 3 sample tags to understand what text is available.
"""
import sys, os, io
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
import django
django.setup()

from apps.pid_analysis.equipment_analysis_views import (
    _extract_equipment_items, _extract_text_from_pdf, _load_config
)
from django.core.files.uploadedfile import InMemoryUploadedFile
import collections

PDF_PATH = '/tmp/PID5.pdf'

with open(PDF_PATH, 'rb') as f:
    fb = f.read()

config = _load_config()
pid_file = InMemoryUploadedFile(io.BytesIO(fb), 'file', 'PID5.pdf', 'application/pdf', len(fb), None)
text = _extract_text_from_pdf(pid_file, config)
print(f'Total text len: {len(text)}')

items = _extract_equipment_items(text, 'PID5', config)
print(f'Total items: {len(items)}')

# Count empty fields per column
field_empty = collections.Counter()
fields_to_check = [
    'description','area','service_fluid','material_class','process_notes',
    'design_flowrate','oper_pressure','oper_temperature',
    'design_pressure_min','design_pressure_max',
    'design_temp_min','design_temp_max',
    'insulation','dimension_length','dimension_diameter',
    'motor_rating','quality_required','remarks'
]

for item in items:
    for f in fields_to_check:
        v = item.get(f, '')
        if not v or v in ('No', '1'):
            field_empty[f] += 1

print('\nEmpty field counts (out of %d items):' % len(items))
for f, cnt in sorted(field_empty.items(), key=lambda x: -x[1]):
    pct = cnt * 100 // max(len(items), 1)
    print(f'  {f:<30} {cnt:3d} ({pct}% empty)')

# Show first 3 items with their OCR context
print('\n=== SAMPLE ITEMS (first 3) ===')
import re
for item in items[:3]:
    tag = item['tag']
    print(f'\n--- {tag} ---')
    for f in fields_to_check:
        v = item.get(f, '')
        if v:
            print(f'  {f}: {v}')
    # Show OCR context around this tag
    m = re.search(re.escape(tag), text)
    if m:
        ctx = text[max(0, m.start()-200):m.start()+400]
        print(f'  OCR context (400 chars after tag):')
        print('  ' + repr(ctx[:300]))

print('\nDONE')
