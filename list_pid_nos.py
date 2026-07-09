import fitz
import re
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

doc = fitz.open('/app/eq_sample.pdf')
print('Total pages:', doc.page_count)
print()

DWG_RE = re.compile(r'PJ\d-[A-Z]{3}-[A-Z]{3,5}-[A-Z]{4}-\d{4}', re.IGNORECASE)
TITLE_RE = re.compile(r'PIPING AND INSTRUMENTATION DIAGRAM\s*\n([^\n]+)', re.IGNORECASE)

results = []
for i in range(doc.page_count):
    txt = doc[i].get_text()
    dwg_nos = DWG_RE.findall(txt)
    # First hit is the sheet's own DWG NO; subsequent ones are cross-references
    main_dwg = ''
    for d in dwg_nos:
        # The sheet's own DWG NO appears near the title block label "DWG. NO."
        if d in txt:
            main_dwg = d
            break
    title_m = TITLE_RE.search(txt)
    title = title_m.group(1).strip() if title_m else ''
    results.append((i + 1, main_dwg, title))
    label = main_dwg if main_dwg else '(no dwg no found)'
    print('Page %02d | %-32s | %s' % (i + 1, label, title[:70]))

print()
unique = sorted(set(r[1] for r in results if r[1]))
print('Total unique P&ID Nos:', len(unique))
for idx, d in enumerate(unique, 1):
    # Find the title for this dwg no
    t = next((r[2] for r in results if r[1] == d), '')
    print('  %02d. %s  —  %s' % (idx, d, t))
