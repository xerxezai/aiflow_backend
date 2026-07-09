"""
AI Cabinet / Channel Auto-Allocator  (Step 6).

Allocates DCS hardware addresses (Cabinet → Node → Slot → Channel) to physical
signals using a soft-coded cabinet pool, respecting:
  • Hazardous-area separation (Ex'i' goes to IS FTAs, Ex'd' to NIS FTAs).
  • IO-type compatibility per card (AI cards take AI/AI-FF, DI cards take
    DI/DI-R, etc.).
  • A configurable max utilisation per cabinet (default 80 %).

Used by the Generator. Idempotent: rows with a pre-existing address are left
alone — the allocator only fills blanks.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

# ─── Soft-coded cabinet pool ────────────────────────────────────────────────
# Each card: (cabinet, node, slot, io_types_accepted, hazardous_class, channels)
# hazardous_class: 'IS' (intrinsically safe) | 'NIS' (non-IS) | 'ANY'
_CABINET_POOL: list[dict] = [
    # IS cards for instruments in hazardous areas (Ex'i').
    {'cabinet': '646-DCS-0107', 'node': 1, 'slot': 1, 'channels': 16,
     'io_types': ('AI-FF', 'AI', 'AI-R'), 'hz': 'IS'},
    {'cabinet': '646-DCS-0107', 'node': 1, 'slot': 2, 'channels': 16,
     'io_types': ('AO-FF', 'AO', 'AO-R'), 'hz': 'IS'},
    {'cabinet': '646-DCS-0107', 'node': 1, 'slot': 3, 'channels': 32,
     'io_types': ('DI-R', 'DI'),          'hz': 'IS'},
    {'cabinet': '646-DCS-0108', 'node': 1, 'slot': 1, 'channels': 16,
     'io_types': ('AI-FF', 'AI', 'AI-R'), 'hz': 'IS'},
    {'cabinet': '646-DCS-0108', 'node': 1, 'slot': 2, 'channels': 16,
     'io_types': ('AO-FF', 'AO', 'AO-R'), 'hz': 'IS'},
    {'cabinet': '646-DCS-0108', 'node': 1, 'slot': 3, 'channels': 32,
     'io_types': ('DI-R', 'DI'),          'hz': 'IS'},
    # NIS cards for non-IS digital signals (solenoids, etc.).
    {'cabinet': '646-DCS-0109', 'node': 2, 'slot': 1, 'channels': 32,
     'io_types': ('DO-R', 'DO'),          'hz': 'NIS'},
    {'cabinet': '646-DCS-0109', 'node': 2, 'slot': 2, 'channels': 32,
     'io_types': ('DI-R', 'DI'),          'hz': 'NIS'},
    {'cabinet': '646-DCS-0110', 'node': 2, 'slot': 1, 'channels': 32,
     'io_types': ('DO-R', 'DO'),          'hz': 'NIS'},
    {'cabinet': '646-DCS-0110', 'node': 2, 'slot': 2, 'channels': 32,
     'io_types': ('DI-R', 'DI'),          'hz': 'NIS'},
]

_MAX_UTILISATION = 0.80   # warn / refuse beyond 80 % of channels used per card.

# Soft-coded field names used to read / write addresses on a row.
_CAB_FIELD     = 'cabinet'
_NODE_FIELD    = 'node'
_SLOT_FIELD    = 'slot'
_CHANNEL_FIELD = 'channel'


def _io_compatible(io_type: str, accepted: tuple[str, ...]) -> bool:
    t = (io_type or '').upper().replace(' ', '')
    base = re.sub(r'\(.*\)', '', t)   # strip "(IS)" / "(NIS)" suffix
    return base in accepted


def _hazardous_class(row: dict) -> str:
    eex = str(row.get('eex_certification') or row.get('hazardous') or '').lower()
    if "ex'i'" in eex or eex.strip() == 'is':
        return 'IS'
    if "ex'd'" in eex or eex.strip() == 'nis':
        return 'NIS'
    # Heuristic: HART / FF analog usually IS, simple DO usually NIS.
    iot = (row.get('io_type') or row.get('signal_type') or '').upper()
    if 'FF' in iot or 'AI' in iot:
        return 'IS'
    if iot.startswith('DO'):
        return 'NIS'
    return 'ANY'


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


class CabinetAllocator:
    """Stateful allocator — instantiate per generation run."""

    def __init__(self, pool: Optional[list[dict]] = None,
                 max_util: float = _MAX_UTILISATION) -> None:
        self.pool = [dict(p) for p in (pool or _CABINET_POOL)]
        self.max_util = max_util
        self.used: dict[tuple, int] = defaultdict(int)   # (cab,node,slot) -> count

    def _capacity_left(self, card: dict) -> int:
        key = (card['cabinet'], card['node'], card['slot'])
        cap = int(card['channels'] * self.max_util)
        return max(0, cap - self.used[key])

    def _find_card(self, io_type: str, hz: str) -> Optional[dict]:
        for card in self.pool:
            if hz != 'ANY' and card['hz'] != 'ANY' and card['hz'] != hz:
                continue
            if not _io_compatible(io_type, card['io_types']):
                continue
            if self._capacity_left(card) <= 0:
                continue
            return card
        return None

    def allocate(self, row: dict) -> Optional[dict]:
        """Mutates the row in place; returns the allocation or None."""
        # Skip soft tags & rows that already have a full address.
        is_soft = bool(row.get('is_soft')) or str(row.get('io_type') or '').upper() == 'SOFT'
        if is_soft:
            return None
        if all(not _is_blank(row.get(k)) for k in (_CAB_FIELD, _NODE_FIELD, _SLOT_FIELD, _CHANNEL_FIELD)):
            return None
        io_type = row.get('io_type') or row.get('signal_type') or ''
        hz = _hazardous_class(row)
        card = self._find_card(io_type, hz)
        if card is None:
            return None
        key = (card['cabinet'], card['node'], card['slot'])
        self.used[key] += 1
        channel = self.used[key]
        row[_CAB_FIELD]     = card['cabinet']
        row[_NODE_FIELD]    = card['node']
        row[_SLOT_FIELD]    = card['slot']
        row[_CHANNEL_FIELD] = channel
        row['_ai_auto_allocated'] = True
        return {
            'cabinet': card['cabinet'], 'node': card['node'],
            'slot': card['slot'],       'channel': channel,
        }

    def utilisation_report(self) -> list[dict]:
        out: list[dict] = []
        for card in self.pool:
            key = (card['cabinet'], card['node'], card['slot'])
            used = self.used[key]
            cap  = card['channels']
            pct  = (used / cap) if cap else 0.0
            out.append({
                'cabinet': card['cabinet'], 'node': card['node'], 'slot': card['slot'],
                'used': used, 'channels': cap,
                'utilisation': round(pct, 3),
                'over_threshold': pct > self.max_util,
            })
        return out


def allocate_rows(rows: list[dict]) -> dict:
    """Convenience wrapper that returns {rows, report}."""
    alloc = CabinetAllocator()
    for r in rows or []:
        if isinstance(r, dict):
            alloc.allocate(r)
    return {'rows': rows, 'report': alloc.utilisation_report()}
