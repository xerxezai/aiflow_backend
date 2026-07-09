"""Per-page extraction diagnostic — shows what each page yields."""
import sys, os, io, re
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
import django; django.setup()

from apps.pid_analysis.equipment_analysis_views import (
    _extract_text_from_pdf, _extract_equipment_items, _load_config
)
from django.core.files.uploadedfile import InMemoryUploadedFile
import fitz

PDF_PATH = '/tmp/PID5.pdf'
with open(PDF_PATH, 'rb') as f:
    fb = f.read()

config = _load_config()
doc = fitz.open(stream=fb, filetype='pdf')
pages = doc.page_count
doc.close()
print(f'Pages: {pages}')

for pg in range(pages):
    pid_file = InMemoryUploadedFile(io.BytesIO(fb), 'file', 'PID5.pdf', 'application/pdf', len(fb), None)
    text = _extract_text_from_pdf(pid_file, config, _page_index=pg)
    items = _extract_equipment_items(text, f'PID5_P{pg}', config)
    tags = [i['tag'] for i in items]
    # Show OCR text snippet (first 500 chars)
    preview = text[:500].replace('\n', ' ')
    print(f'\nPAGE {pg}: {len(items)} items {tags}')
    print(f'  text_preview: {preview[:300]}')

print('\nDONE')
