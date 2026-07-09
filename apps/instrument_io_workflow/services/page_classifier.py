"""
Page classifier — heuristic, regex-based, FREE.

Reads the text of each PDF page and returns one of:
    'cover' | 'index' | 'notes' | 'comments_sheet' | 'io_table' | 'unknown'

No LLM, no vision. Used to route pages to the cheapest possible extractor.
"""

from __future__ import annotations

import logging
from typing import List, Dict

import fitz  # PyMuPDF

from .config import PAGE_TYPES, PAGE_TYPE_MIN_HITS

logger = logging.getLogger(__name__)


def classify_pages(pdf_bytes: bytes) -> List[Dict]:
    """
    Returns a list of {page_index, page_type, text, hits} dicts.
    Page indices are 0-based.
    """
    out: List[Dict] = []
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text('text') or ''
            lower = text.lower()
            best_type, best_hits = 'unknown', 0
            for ptype, keywords in PAGE_TYPES.items():
                hits = sum(1 for kw in keywords if kw in lower)
                if hits > best_hits:
                    best_type, best_hits = ptype, hits
            if best_hits < PAGE_TYPE_MIN_HITS:
                best_type = 'unknown'
            out.append({
                'page_index': i,
                'page_type':  best_type,
                'text':       text,
                'hits':       best_hits,
            })
        logger.info(
            "[IOWF] Classified %d pages: %s",
            len(out),
            {p['page_index']: p['page_type'] for p in out},
        )
    finally:
        doc.close()
    return out
