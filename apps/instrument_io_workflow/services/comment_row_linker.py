"""
Comment ↔ IO row linker — regex-based, FREE.

For each comment, find any ADNOC-style tag numbers mentioned in the comment
text and attach the matching IO row IDs. No LLM call.
"""

from __future__ import annotations

import re
from typing import List, Dict

from .config import TAG_NUMBER_REGEX

_TAG_RE = re.compile(TAG_NUMBER_REGEX)


def link_comments_to_rows(
    comments: List[Dict], rows: List[Dict],
) -> List[Dict]:
    """
    Mutates each comment dict adding `linked_tags: List[str]`.
    Also returns the comments list for convenience.
    """
    row_tags = {(r.get('tag_number') or '').strip().upper() for r in rows}
    row_tags.discard('')

    for c in comments:
        text = ' '.join([
            c.get('company_comment', ''),
            c.get('contractor_reply', ''),
            c.get('company_decision', ''),
        ])
        found = {m.group(0).upper() for m in _TAG_RE.finditer(text)}
        # Keep only tags that exist in this revision's IO table — drops false
        # positives like "113-XV-9501" referenced but not on this list.
        c['linked_tags'] = sorted(found & row_tags) if row_tags else sorted(found)
    return comments
