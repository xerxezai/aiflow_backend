"""
IO List structured-table extractor.

Cost-optimised pipeline:
  1. PyMuPDF page.find_tables() — text-based PDFs, FREE
  2. Header alias matcher → canonical 40-column row schema
  3. Vision fallback (gpt-4o-mini) — opt-in, page-targeted, hard-capped

The vision fallback function is a stub by default; turning it on requires
INSTRUMENT_IO_ENABLE_VISION_FALLBACK=true AND an OpenAI key. It is called
ONLY for pages classified as 'io_table' that returned zero rows from
PyMuPDF — never speculatively.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Optional, Tuple

import fitz  # PyMuPDF

from .config import (
    IO_LIST_CANONICAL_COLUMNS,
    IO_HEADER_ALIASES,
    ENABLE_VISION_FALLBACK,
    VISION_MAX_PAGES_PER_DOC,
)

logger = logging.getLogger(__name__)


def _build_header_map(header_cells: List[str]) -> Optional[Dict[int, str]]:
    """{col_index → canonical_name}; require ≥ 4 recognised columns."""
    norm = [(c or '').strip().lower() for c in header_cells]
    mapping: Dict[int, str] = {}
    for canonical, aliases in IO_HEADER_ALIASES.items():
        for idx, cell in enumerate(norm):
            if idx in mapping:
                continue
            if any(a == cell or a in cell for a in aliases):
                mapping[idx] = canonical
                break
    return mapping if len(mapping) >= 4 else None


def _extract_rows_with_pymupdf(
    pdf_bytes: bytes, page_indices: List[int],
) -> Tuple[List[Dict], List[int]]:
    """
    Returns (rows, pages_that_yielded_nothing).
    rows: list of dicts with keys from IO_LIST_CANONICAL_COLUMNS + 'page_number'.
    """
    rows: List[Dict] = []
    empty_pages: List[int] = []
    if not page_indices:
        return rows, empty_pages

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for pidx in page_indices:
            if pidx < 0 or pidx >= len(doc):
                continue
            page = doc[pidx]
            page_yielded = False
            try:
                tables = page.find_tables()
            except Exception as exc:
                logger.warning("[IOWF] find_tables failed on IO page %d: %s", pidx, exc)
                empty_pages.append(pidx)
                continue

            for tbl in tables:
                try:
                    raw = tbl.extract()
                except Exception:
                    continue
                if not raw or len(raw) < 2:
                    continue

                header_map: Optional[Dict[int, str]] = None
                header_row_idx = -1
                # IO sheets often have 2-3 header rows (group label + actual cols).
                for hi in range(min(4, len(raw))):
                    header_map = _build_header_map(raw[hi])
                    if header_map:
                        header_row_idx = hi
                        break
                if not header_map:
                    continue

                for r in raw[header_row_idx + 1:]:
                    record: Dict[str, str] = {c: '' for c in IO_LIST_CANONICAL_COLUMNS}
                    for col_idx, canonical in header_map.items():
                        if col_idx < len(r):
                            record[canonical] = (r[col_idx] or '').strip()
                    if not record.get('tag_number'):
                        continue  # tag number is the natural key
                    record['page_number'] = pidx + 1
                    rows.append(record)
                    page_yielded = True

            if not page_yielded:
                empty_pages.append(pidx)
    finally:
        doc.close()
    logger.info("[IOWF] PyMuPDF extracted %d IO rows from %d pages "
                "(%d pages yielded nothing)",
                len(rows), len(page_indices), len(empty_pages))
    return rows, empty_pages


def _vision_fallback_for_pages(
    pdf_bytes: bytes, page_indices: List[int],
) -> List[Dict]:
    """
    OPT-IN vision fallback. Returns [] by default to keep cost at zero.

    To activate: set INSTRUMENT_IO_ENABLE_VISION_FALLBACK=true AND extend this
    function to call OpenAI gpt-4o-mini with a soft-coded prompt that targets
    the IO_LIST_CANONICAL_COLUMNS schema. Hard-capped to
    VISION_MAX_PAGES_PER_DOC pages per document.
    """
    if not ENABLE_VISION_FALLBACK:
        return []
    if not page_indices:
        return []
    capped = page_indices[:VISION_MAX_PAGES_PER_DOC]
    logger.warning(
        "[IOWF] Vision fallback requested for %d page(s) but is not yet "
        "implemented. Capped list would be: %s", len(capped), capped,
    )
    # Implementation slot reserved — keep returning [] to guarantee $0 cost
    # until the user explicitly opts in to vision spend.
    return []


def extract_io_rows_from_pages(
    pdf_bytes: bytes, page_indices: List[int],
) -> List[Dict]:
    """Public entrypoint — combines free path + opt-in vision fallback."""
    rows, empty = _extract_rows_with_pymupdf(pdf_bytes, page_indices)
    if empty:
        rows.extend(_vision_fallback_for_pages(pdf_bytes, empty))
    return rows
