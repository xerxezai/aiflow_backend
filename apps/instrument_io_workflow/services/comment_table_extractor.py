"""
Comments Resolution Sheet table extractor.

Uses PyMuPDF's `page.find_tables()` (FREE — no LLM) to pull the 5-column
Comments Resolution Sheet that lives on pages classified as 'comments_sheet'.

Falls back to (a) returning empty list if structure cannot be detected.
Vision fallback is intentionally NOT wired here — cost optimisation. If a
client needs it, set INSTRUMENT_IO_ENABLE_VISION_FALLBACK and extend
`_vision_fallback_for_page` (placeholder kept for future).
"""

from __future__ import annotations

import logging
from typing import List, Dict, Optional

import fitz  # PyMuPDF

from .config import (
    COMMENT_SHEET_COLUMNS,
    COMMENT_HEADER_ALIASES,
    STATUS_CODE_MEANING,
)

logger = logging.getLogger(__name__)


def _match_header_columns(header_cells: List[str]) -> Optional[Dict[int, str]]:
    """
    Given the cells of a candidate header row, return a {col_index: canonical_name}
    map if at least 3 of the 5 expected columns are recognised; else None.
    """
    mapping: Dict[int, str] = {}
    norm = [(c or '').strip().lower() for c in header_cells]
    for canonical, aliases in COMMENT_HEADER_ALIASES.items():
        for idx, cell in enumerate(norm):
            if idx in mapping:
                continue
            if any(alias in cell for alias in aliases):
                mapping[idx] = canonical
                break
    return mapping if len(mapping) >= 3 else None


def _normalise_status_code(raw: str) -> Dict[str, str]:
    raw = (raw or '').strip()
    # Extract first digit found (cells often look like "2" or "Code 2" or "2 - Noted")
    for ch in raw:
        if ch in STATUS_CODE_MEANING:
            return {'code': ch, 'meaning': STATUS_CODE_MEANING[ch]}
    return {'code': raw, 'meaning': ''}


def extract_comments_from_pages(
    pdf_bytes: bytes,
    page_indices: List[int],
) -> List[Dict]:
    """
    Returns a list of comment dicts with keys defined in COMMENT_SHEET_COLUMNS
    plus 'page_number' (1-based) and 'status_meaning'.
    """
    comments: List[Dict] = []
    if not page_indices:
        return comments

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for pidx in page_indices:
            if pidx < 0 or pidx >= len(doc):
                continue
            page = doc[pidx]
            try:
                tables = page.find_tables()
            except Exception as exc:
                logger.warning("[IOWF] find_tables failed on page %d: %s", pidx, exc)
                continue

            for tbl in tables:
                try:
                    rows = tbl.extract()
                except Exception:
                    continue
                if not rows or len(rows) < 2:
                    continue

                # Detect header row anywhere in the first 3 rows
                header_map: Optional[Dict[int, str]] = None
                header_row_idx = -1
                for hi in range(min(3, len(rows))):
                    header_map = _match_header_columns(rows[hi])
                    if header_map:
                        header_row_idx = hi
                        break
                if not header_map:
                    continue

                for r in rows[header_row_idx + 1:]:
                    record: Dict[str, str] = {c: '' for c in COMMENT_SHEET_COLUMNS}
                    for col_idx, canonical in header_map.items():
                        if col_idx < len(r):
                            record[canonical] = (r[col_idx] or '').strip()
                    # Skip empty rows
                    if not (record['company_comment'] or record['contractor_reply']
                            or record['company_decision']):
                        continue
                    status = _normalise_status_code(record.get('status_code', ''))
                    record['status_code'] = status['code']
                    record['status_meaning'] = status['meaning']
                    record['page_number'] = pidx + 1
                    comments.append(record)
        logger.info("[IOWF] Extracted %d comments from %d pages",
                    len(comments), len(page_indices))
    finally:
        doc.close()
    return comments
