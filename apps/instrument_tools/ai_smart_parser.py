"""
AI Smart File Parser  (Step 10 / accept-any-format).

Wraps `services.parse_uploaded_table` with multi-format intelligence so the
Instrument Tools can ingest virtually any vendor deliverable:

  • .xlsx / .xlsm / .xls / .csv        → deterministic core (unchanged)
  • .pdf                                → pdfplumber table extraction with
                                          text-fallback heuristics
  • .json                               → list[dict] passthrough / unwrap
  • .html / .htm                        → first table extracted
  • .txt / .tsv                         → whitespace / tab-delimited
  • .png / .jpg / .jpeg / .gif / .tif   → optional OCR via OpenAI Vision
                                          (only if OPENAI_API_KEY is set)

All thresholds and the accepted-extension list are soft-coded.  Failure of an
AI step is silent — the caller falls back to the original parser, which still
raises its clean "Unsupported file type" message if everything failed.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Optional

from . import services as svc

logger = logging.getLogger(__name__)

# ─── Soft-coded configuration ────────────────────────────────────────────────
_PDF_EXTS   = ('.pdf',)
_JSON_EXTS  = ('.json',)
_HTML_EXTS  = ('.html', '.htm')
_TEXT_EXTS  = ('.txt', '.tsv')
_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.tif', '.tiff', '.bmp', '.webp')

# All extensions accepted by the smart parser. Order matters only for the
# user-facing message.
SMART_ACCEPTED_EXTS = (
    svc._ACCEPTED_EXTS
    + _PDF_EXTS
    + _JSON_EXTS
    + _HTML_EXTS
    + _TEXT_EXTS
    + _IMAGE_EXTS
)

# Minimum cells a PDF table candidate must have to be considered usable.
_MIN_PDF_TABLE_CELLS = 6
# Maximum number of PDF pages we scan (soft cap; can be overridden by env var).
_MAX_PDF_PAGES = int(os.getenv('INSTRUMENT_TOOLS_PDF_MAX_PAGES', '30'))
# Maximum rows returned from a single file — keeps downstream AI pipeline fast.
_MAX_OUTPUT_ROWS = int(os.getenv('INSTRUMENT_TOOLS_MAX_ROWS', '2000'))
# Per-page wall-time budget for pdfplumber table extraction.
_PDF_PAGE_BUDGET_S = float(os.getenv('INSTRUMENT_TOOLS_PDF_PAGE_BUDGET_S', '6'))
# Overall PDF parse wall-time budget.
_PDF_TOTAL_BUDGET_S = float(os.getenv('INSTRUMENT_TOOLS_PDF_BUDGET_S', '60'))

# pdfplumber table-extraction settings (lines-based is significantly faster
# on engineering drawings that have explicit rules).
_PDF_TABLE_SETTINGS = {
    'vertical_strategy':   'lines',
    'horizontal_strategy': 'lines',
    'snap_tolerance':      4,
}


# ─── Public entry point ─────────────────────────────────────────────────────
def parse(uploaded_file) -> list[dict]:
    """Smart-parse an uploaded file into a list[dict].

    Tries format-specific extractors in order and silently falls through to
    the deterministic spreadsheet parser if nothing else applies.
    """
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    # Read once — UploadedFile is a file-like object that may not be seekable.
    data = uploaded_file.read() if hasattr(uploaded_file, 'read') else uploaded_file

    # 1) Spreadsheets / CSV → defer to the deterministic core.
    if name.endswith(svc._ACCEPTED_EXTS):
        return _parse_via_core(uploaded_file, data, name)

    # 2) PDF
    if name.endswith(_PDF_EXTS):
        rows = _parse_pdf(data)
        if rows:
            return rows

    # 3) JSON
    if name.endswith(_JSON_EXTS):
        rows = _parse_json(data)
        if rows:
            return rows

    # 4) HTML
    if name.endswith(_HTML_EXTS):
        rows = _parse_html(data)
        if rows:
            return rows

    # 5) Plain text / TSV
    if name.endswith(_TEXT_EXTS):
        rows = _parse_text(data)
        if rows:
            return rows

    # 6) Images (OCR via OpenAI Vision; silent no-op without API key).
    if name.endswith(_IMAGE_EXTS):
        rows = _parse_image(data, name)
        if rows:
            return rows

    # 7) Final fallback — raise a friendly error listing every accepted format.
    raise ValueError(
        f'Unsupported file type: {name or "<unnamed>"}. '
        f'Accepted: {", ".join(SMART_ACCEPTED_EXTS)}'
    )


# ─── Format-specific extractors ─────────────────────────────────────────────
def _parse_via_core(uploaded_file, data: bytes, name: str) -> list[dict]:
    """Delegate to services.parse_uploaded_table without consuming the stream twice."""
    # Re-wrap the bytes in a file-like with .name and .read for the core parser.
    class _FakeFile(io.BytesIO):
        def __init__(self, buf, n):
            super().__init__(buf)
            self.name = n
    return svc.parse_uploaded_table(_FakeFile(data, name))


def _parse_pdf(data: bytes) -> list[dict]:
    """Extract tables from a PDF using pdfplumber.

    The strategy:
      • Walk each page, call `page.extract_tables()`.
      • Concatenate compatible tables (same column count) under one header.
      • Discard rows that look like page-headers / footers (all empty cells
        in the leftmost column).
    """
    try:
        import pdfplumber                                            # type: ignore
    except ImportError:
        logger.warning('pdfplumber not available; cannot parse PDF.')
        return []

    import time
    rows: list[dict] = []
    header: list[str] = []
    deadline = time.monotonic() + _PDF_TOTAL_BUDGET_S
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:_MAX_PDF_PAGES]:
                if time.monotonic() > deadline or len(rows) >= _MAX_OUTPUT_ROWS:
                    logger.warning('PDF parse budget reached; truncating at %d rows.', len(rows))
                    break
                page_start = time.monotonic()
                tables = []
                # Fast path: single best-guess table per page.
                try:
                    tbl = page.extract_table(_PDF_TABLE_SETTINGS)
                    if tbl:
                        tables = [tbl]
                except Exception:
                    tables = []
                # Fallback: multiple tables (slower) only if fast path failed
                # and we still have time.
                if not tables and time.monotonic() - page_start < _PDF_PAGE_BUDGET_S:
                    try:
                        tables = page.extract_tables(_PDF_TABLE_SETTINGS) or []
                    except Exception:                                # pragma: no cover
                        tables = []
                for table in tables:
                    if not table or sum(len(r or []) for r in table) < _MIN_PDF_TABLE_CELLS:
                        continue
                    rows.extend(_normalise_table(table, header))
                    if not header:
                        header = _first_non_empty_row(table)
                    if len(rows) >= _MAX_OUTPUT_ROWS:
                        break
    except Exception:                                                # pragma: no cover
        logger.exception('PDF parse failed; returning what we have.')

    rows = [r for r in rows if any((str(v) or '').strip() for v in r.values())]
    return rows[:_MAX_OUTPUT_ROWS]


def _first_non_empty_row(table: list[list]) -> list[str]:
    for raw in table:
        cells = [(str(c).strip() if c is not None else '') for c in raw]
        if any(cells):
            return cells
    return []


def _normalise_table(table: list[list], existing_header: list[str]) -> list[dict]:
    header = existing_header or _first_non_empty_row(table)
    if not header:
        return []
    # Skip every row whose stripped value matches the header (some PDFs repeat
    # the header on every page).
    out: list[dict] = []
    header_key = tuple(header)
    saw_header = False
    for raw in table:
        cells = [(str(c).strip() if c is not None else '') for c in raw]
        if not any(cells):
            continue
        if tuple(cells[: len(header)]) == header_key:
            saw_header = True
            continue
        if not saw_header and not existing_header:
            # First non-empty row in the very first table IS the header.
            saw_header = True
            continue
        out.append({header[i]: (cells[i] if i < len(cells) else '')
                    for i in range(len(header)) if header[i]})
    return out


def _parse_json(data: bytes) -> list[dict]:
    try:
        payload = json.loads(data.decode('utf-8-sig', errors='replace'))
    except Exception:
        return []
    # Accept either a top-level list[dict] or a dict with a 'rows'/'data' key.
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ('rows', 'data', 'items', 'records'):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def _parse_html(data: bytes) -> list[dict]:
    """Use stdlib HTMLParser to grab the first table that looks like data."""
    from html.parser import HTMLParser

    class _TableGrabber(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables: list[list[list[str]]] = []
            self._cur_table: Optional[list[list[str]]] = None
            self._cur_row: Optional[list[str]] = None
            self._buf: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == 'table':
                self._cur_table = []
            elif tag == 'tr' and self._cur_table is not None:
                self._cur_row = []
            elif tag in ('td', 'th') and self._cur_row is not None:
                self._buf = []

        def handle_endtag(self, tag):
            if tag in ('td', 'th') and self._cur_row is not None:
                self._cur_row.append(''.join(self._buf).strip())
                self._buf = []
            elif tag == 'tr' and self._cur_table is not None and self._cur_row is not None:
                self._cur_table.append(self._cur_row)
                self._cur_row = None
            elif tag == 'table' and self._cur_table is not None:
                self.tables.append(self._cur_table)
                self._cur_table = None

        def handle_data(self, data):
            if self._cur_row is not None:
                self._buf.append(data)

    try:
        p = _TableGrabber()
        p.feed(data.decode('utf-8', errors='replace'))
    except Exception:
        return []
    if not p.tables:
        return []
    # Pick the widest table.
    table = max(p.tables, key=lambda t: max((len(r) for r in t), default=0))
    return _normalise_table(table, [])


def _parse_text(data: bytes) -> list[dict]:
    """Detect tab or whitespace delimiter and parse like CSV."""
    text = data.decode('utf-8-sig', errors='replace')
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    delim = '\t' if '\t' in lines[0] else None     # None → splitlines on whitespace
    header_cells = lines[0].split(delim) if delim else re.split(r'\s{2,}', lines[0])
    header = [c.strip() for c in header_cells]
    rows: list[dict] = []
    for ln in lines[1:]:
        cells = ln.split(delim) if delim else re.split(r'\s{2,}', ln)
        rows.append({header[i]: (cells[i].strip() if i < len(cells) else '')
                     for i in range(len(header)) if header[i]})
    return rows


def _parse_image(data: bytes, name: str) -> list[dict]:
    """Image → table via OpenAI Vision. Silent no-op without API key.

    Bounded by `INSTRUMENT_TOOLS_LLM_TIMEOUT_SEC` (default 6s) so a slow
    Vision call cannot stall a Django request beyond the client timeout.
    """
    _VISION_TIMEOUT = float(os.environ.get('INSTRUMENT_TOOLS_LLM_TIMEOUT_SEC', '6'))
    _VISION_MODEL   = os.environ.get('INSTRUMENT_TOOLS_LLM_MODEL', 'gpt-4o-mini')
    if not os.getenv('OPENAI_API_KEY'):
        return []
    try:                                                             # pragma: no cover -- network path
        import base64
        from openai import OpenAI                                    # noqa: WPS433
        client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=_VISION_TIMEOUT)
        b64 = base64.b64encode(data).decode('ascii')
        ext = name.rsplit('.', 1)[-1].lower()
        prompt = (
            'Extract the tabular instrument / IO list data from this image. '
            'Return STRICT JSON: a list of objects, one per data row, with '
            'lower_snake_case keys derived from the column headers. '
            'Do not include any commentary.'
        )
        rsp = client.chat.completions.create(
            model=_VISION_MODEL,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url',
                     'image_url': {'url': f'data:image/{ext};base64,{b64}'}},
                ],
            }],
            max_tokens=2000,
            temperature=0.0,
            timeout=_VISION_TIMEOUT,
        )
        text = (rsp.choices[0].message.content or '').strip()
        # Strip a possible ```json fence.
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
        payload = json.loads(text)
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
    except Exception:
        logger.exception('OCR/Vision parse failed.')
    return []
