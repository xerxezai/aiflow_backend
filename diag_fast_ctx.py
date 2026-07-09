"""Fast vector-only context diagnostic — no OCR, so runs in seconds."""
import sys, os, io, re
import fitz

PDF_PATH = '/tmp/PID5.pdf'

doc = fitz.open(PDF_PATH)
print(f'Total pages: {doc.page_count}')

for pg_idx in range(min(doc.page_count, 5)):  # check first 5 pages
    page = doc[pg_idx]
    text = page.get_text('text')
    words_raw = page.get_text('words')
    spatial = sorted(words_raw, key=lambda w: (round(w[1] / 20) * 20, w[0]))
    spatial_text = ' '.join(w[4] for w in spatial)

    full = text + '\n' + spatial_text

    # Find all equipment tags on this page
    tags = re.findall(r'\b[A-Z]{1,3}-\d{3,4}[A-Z]?(?:-[A-Z]{1,4})?\b', full)
    tags = list(dict.fromkeys(tags))[:5]  # unique, first 5

    print(f'\n=== PAGE {pg_idx} (tags: {tags}) ===')
    for tag in tags:
        m = re.search(re.escape(tag), full)
        if m:
            ctx = full[max(0, m.start()-50):m.start()+800]
            print(f'  -- {tag} --')
            print(f'  {repr(ctx[:500])}')

doc.close()
print('\nDONE')
