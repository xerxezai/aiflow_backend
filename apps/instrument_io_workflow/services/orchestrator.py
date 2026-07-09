"""
Top-level extraction orchestrator.

extract_document(pdf_bytes) → {
    'pages': [...],          # classified
    'comments': [...],       # 5-column comments resolution sheet
    'io_rows': [...],        # canonical 40-column IO list
    'stats': {...},
    'cost_profile': {...},   # for transparency in the UI
}

All work is cost-optimised:
  - PyMuPDF for everything by default ($0)
  - Vision fallback only when explicitly enabled (opt-in)
  - Per-PDF hash cache to skip re-extraction
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from typing import Dict, Any

from .config import ENABLE_HASH_CACHE, ENABLE_VISION_FALLBACK
from .page_classifier import classify_pages
from .comment_table_extractor import extract_comments_from_pages
from .io_table_extractor import extract_io_rows_from_pages
from .comment_row_linker import link_comments_to_rows

logger = logging.getLogger(__name__)


# In-process cache (per worker). Persistent cache is the DB row itself —
# views layer checks IOListDocument.pdf_sha256 first.
_MEMO: Dict[str, Dict[str, Any]] = {}


def sha256_of(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def extract_document(pdf_bytes: bytes) -> Dict[str, Any]:
    started = time.time()
    digest = sha256_of(pdf_bytes)

    if ENABLE_HASH_CACHE and digest in _MEMO:
        cached = _MEMO[digest]
        cached['cost_profile']['cache_hit'] = True
        return cached

    pages = classify_pages(pdf_bytes)
    comment_page_idx = [p['page_index'] for p in pages
                        if p['page_type'] == 'comments_sheet']
    io_page_idx      = [p['page_index'] for p in pages
                        if p['page_type'] == 'io_table']

    comments = extract_comments_from_pages(pdf_bytes, comment_page_idx)
    io_rows  = extract_io_rows_from_pages(pdf_bytes, io_page_idx)

    # Free regex-based linker
    link_comments_to_rows(comments, io_rows)

    result: Dict[str, Any] = {
        'sha256':   digest,
        'pages':    [
            {'page_index': p['page_index'], 'page_type': p['page_type'],
             'hits': p['hits']}
            for p in pages
        ],
        'comments': comments,
        'io_rows':  io_rows,
        'stats': {
            'total_pages':          len(pages),
            'comment_pages':        len(comment_page_idx),
            'io_table_pages':       len(io_page_idx),
            'comments_found':       len(comments),
            'io_rows_found':        len(io_rows),
            'linked_comments':      sum(1 for c in comments if c.get('linked_tags')),
            'elapsed_seconds':      round(time.time() - started, 2),
            # Precomputed breakdown so the frontend chart never needs to
            # iterate extracted_comments (soft-coded: all keys come from data).
            'status_code_breakdown': dict(
                Counter(
                    (c.get('status_code') or '').strip() or 'unknown'
                    for c in comments
                )
            ),
        },
        'cost_profile': {
            'cache_hit':            False,
            'vision_fallback_used': False,  # always False until opt-in wires it
            'vision_enabled':       ENABLE_VISION_FALLBACK,
            'llm_tokens_estimated': 0,
        },
    }

    if ENABLE_HASH_CACHE:
        _MEMO[digest] = result

    logger.info(
        "[IOWF] Extraction complete: pages=%d comments=%d io_rows=%d "
        "elapsed=%.2fs",
        len(pages), len(comments), len(io_rows),
        result['stats']['elapsed_seconds'],
    )
    return result
