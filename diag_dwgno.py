"""Diagnostic script - dumps PyMuPDF word layout for DWG NO extraction."""
import fitz
import re
import sys

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else '/tmp/test_pid.pdf'

TRIGGER = re.compile(r'^(DWG|DRG|DRAWING|DOC|DOCUMENT)\.?$', re.IGNORECASE)
DWG_NO_RE = re.compile(r'^[A-Z0-9]{2,8}(?:-[A-Z0-9]{2,8}){2,5}$', re.IGNORECASE)

doc = fitz.open(PDF_PATH)
page = doc[-1]
h = page.rect.height
strip_top = h * 0.70
print(f"Page height={h:.0f}, strip_top={strip_top:.0f}")

words = page.get_text('words')
all_words = [(w[0], w[1], w[2], w[3], w[4]) for w in words]
strip_words = [w for w in all_words if w[1] >= strip_top]
print(f"Total words: {len(all_words)}, strip words (bottom 30%): {len(strip_words)}")

print("\n=== DWG/DOC trigger words in strip ===")
for i, w in enumerate(strip_words):
    if TRIGGER.match(w[4]):
        print(f"  TRIGGER #{i}: x0={w[0]:.0f} y0={w[1]:.0f} y1={w[3]:.0f} text={repr(w[4])}")
        print(f"    next 20 words:")
        for w2 in strip_words[i:i+20]:
            marker = " <-- DWG_NO_MATCH" if DWG_NO_RE.match(w2[4]) else ""
            print(f"      x0={w2[0]:.0f} y0={w2[1]:.0f}: {repr(w2[4])}{marker}")
        print()

print("\n=== All doc-number pattern matches in strip ===")
for w in strip_words:
    if DWG_NO_RE.match(w[4]):
        print(f"  x0={w[0]:.0f} y0={w[1]:.0f} y1={w[3]:.0f}: {repr(w[4])}")

print("\n=== All strip words sorted by y then x ===")
for w in sorted(strip_words, key=lambda x: (x[1], x[0])):
    print(f"  ({w[0]:.0f},{w[1]:.0f})-({w[2]:.0f},{w[3]:.0f}): {repr(w[4])}")

print("\n=== Full page text (last 2000 chars) ===")
full_text = page.get_text('text')
print(repr(full_text[-2000:]))
