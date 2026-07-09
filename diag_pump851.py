"""
Show OCR context around P-851* pump tags to identify exact label text on the data box.
"""
import sys, os, io, re
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
import django; django.setup()

from apps.pid_analysis.equipment_analysis_views import _extract_text_from_pdf, _load_config
from django.core.files.uploadedfile import InMemoryUploadedFile

PDF_PATH = '/tmp/PID5.pdf'
with open(PDF_PATH, 'rb') as f:
    fb = f.read()

config = _load_config()
pid_file = InMemoryUploadedFile(io.BytesIO(fb), 'file', 'PID5.pdf', 'application/pdf', len(fb), None)
text = _extract_text_from_pdf(pid_file, config)

# Find all P-851 tag positions
TAG_RE = re.compile(r'\bP-851[A-Z]?(?:-TF)?\b', re.IGNORECASE)
for m in TAG_RE.finditer(text):
    tag = m.group(0)
    ctx = text[max(0, m.start()-50):min(len(text), m.end()+600)]
    print(f'\n=== {tag} at pos {m.start()} ===')
    print(repr(ctx))

print('\nDONE')
