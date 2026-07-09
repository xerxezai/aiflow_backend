"""Show raw OCR context around problematic equipment tags."""
import sys, os, io, re
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
import django; django.setup()

from apps.pid_analysis.equipment_analysis_views import _extract_text_from_pdf, _load_config
from django.core.files.uploadedfile import InMemoryUploadedFile

with open('/tmp/PID5.pdf', 'rb') as f:
    fb = f.read()

config = _load_config()
pid_file = InMemoryUploadedFile(io.BytesIO(fb), 'file', 'PID5.pdf', 'application/pdf', len(fb), None)
text = _extract_text_from_pdf(pid_file, config)

for tag in ['V-308-TF', 'V-805-TF', 'V-803-TF']:
    m = re.search(r'\b' + re.escape(tag) + r'\b', text)
    if m:
        ctx = text[max(0, m.start() - 80):m.start() + 1200]
        print(f'=== {tag} (pos={m.start()}) ===')
        print(ctx)
        print('---END---')
        print()

print('DONE')
