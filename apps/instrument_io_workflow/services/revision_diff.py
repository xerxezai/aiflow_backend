"""
Revision-to-revision diff — pure Python, FREE.

Given two extracted IO-row lists (older, newer) keyed by tag_number, return
{added, removed, modified, unchanged} buckets plus per-row field-level diff.
"""

from __future__ import annotations

from typing import List, Dict, Any

from .config import IO_LIST_CANONICAL_COLUMNS


def _index_by_tag(rows: List[Dict]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for r in rows:
        tag = (r.get('tag_number') or '').strip().upper()
        if tag:
            out[tag] = r
    return out


def diff_revisions(old_rows: List[Dict], new_rows: List[Dict]) -> Dict[str, Any]:
    old_idx = _index_by_tag(old_rows)
    new_idx = _index_by_tag(new_rows)

    added   = [new_idx[t] for t in new_idx.keys() - old_idx.keys()]
    removed = [old_idx[t] for t in old_idx.keys() - new_idx.keys()]
    modified, unchanged = [], []
    for t in old_idx.keys() & new_idx.keys():
        field_diff = {}
        for col in IO_LIST_CANONICAL_COLUMNS:
            ov, nv = old_idx[t].get(col, ''), new_idx[t].get(col, '')
            if (ov or '').strip() != (nv or '').strip():
                field_diff[col] = {'old': ov, 'new': nv}
        if field_diff:
            modified.append({'tag_number': t, 'changes': field_diff})
        else:
            unchanged.append(t)

    return {
        'summary': {
            'added':     len(added),
            'removed':   len(removed),
            'modified':  len(modified),
            'unchanged': len(unchanged),
        },
        'added':     added,
        'removed':   removed,
        'modified':  modified,
        'unchanged_tags': unchanged,
    }
