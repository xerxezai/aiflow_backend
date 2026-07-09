"""
AI Loop Template Expander  (Step 5).

Given a *seed* instrument row (e.g. an on-off valve `604-XV-0301`), expand it
into the full loop's worth of sibling signals — modelled directly after the
ADNOC ZIRKU reference (XV → XY, ZSO, ZSC, ZIO, ZIC, HS×N, XA).

Soft-coded — adding a new instrument family (e.g. variable-speed motor) is a
matter of appending one entry to `_LOOP_TEMPLATES`.
"""
from __future__ import annotations

import re
from typing import Optional

# Soft-coded family → children template.
# Each child entry: (type_code, signal_type, label, hmi_only?)
_LOOP_TEMPLATES: dict[str, list[dict]] = {
    # On-off valve loop (typical 10 signals including 6 HS variants).
    'on_off_valve': [
        {'type': 'XV',  'signal_type': '',        'label': 'On-Off Valve',                 'hmi': False, 'physical': False},
        {'type': 'XY',  'signal_type': 'DO-R',    'label': 'Solenoid Valve',               'hmi': False, 'physical': True},
        {'type': 'ZSO', 'signal_type': 'DI-R',    'label': 'Position (Limit) Switch Open', 'hmi': False, 'physical': True},
        {'type': 'ZSC', 'signal_type': 'DI-R',    'label': 'Position (Limit) Switch Closed','hmi': False, 'physical': True},
        {'type': 'ZIO', 'signal_type': 'SOFT',    'label': 'Position Indicator - DCS',     'hmi': True,  'physical': False},
        {'type': 'ZIC', 'signal_type': 'SOFT',    'label': 'Position Indicator - DCS',     'hmi': True,  'physical': False},
        {'type': 'XA',  'signal_type': 'SOFT',    'label': 'Discrepancy Alarm',            'hmi': True,  'physical': False},
        {'type': 'HS',  'signal_type': 'SOFT',    'label': 'Hand Switch - DCS Open',       'hmi': True,  'physical': False, 'suffix': '1'},
        {'type': 'HS',  'signal_type': 'SOFT',    'label': 'Hand Switch - DCS Close',      'hmi': True,  'physical': False, 'suffix': '2'},
        {'type': 'HS',  'signal_type': 'DI-R',    'label': 'Local Hand Switch (Open)',     'hmi': False, 'physical': True,  'suffix': '3'},
        {'type': 'HS',  'signal_type': 'DI-R',    'label': 'Local Hand Switch (Close)',    'hmi': False, 'physical': True,  'suffix': '4'},
        {'type': 'HS',  'signal_type': 'SOFT',    'label': 'Hand Switch (Auto)',           'hmi': True,  'physical': False, 'suffix': '5'},
        {'type': 'HS',  'signal_type': 'SOFT',    'label': 'Hand Switch (Manual)',         'hmi': True,  'physical': False, 'suffix': '6'},
    ],
    # Control valve loop (transmitter→controller→positioner→valve).
    'control_valve': [
        {'type': 'FT',  'signal_type': 'AI-FF',   'label': 'D/P Type Flow Transmitter',    'hmi': False, 'physical': True},
        {'type': 'FIC', 'signal_type': 'SOFT',    'label': 'Flow Controller - DCS',        'hmi': True,  'physical': False},
        {'type': 'FAL', 'signal_type': 'SOFT',    'label': 'Flow Alarm Low',               'hmi': True,  'physical': False},
        {'type': 'FV',  'signal_type': '',        'label': 'Control Valve',                'hmi': False, 'physical': False},
        {'type': 'FY',  'signal_type': 'AO-FF',   'label': 'Control Valve Positioner',     'hmi': False, 'physical': True},
    ],
    # Pressure transmitter loop.
    'pressure_tx': [
        {'type': 'PT',  'signal_type': 'AI-FF',   'label': 'Pressure Indicating Transmitter', 'hmi': False, 'physical': True},
        {'type': 'PI',  'signal_type': 'SOFT',    'label': 'Pressure Indicator - DCS',        'hmi': True,  'physical': False},
        {'type': 'PAH', 'signal_type': 'SOFT',    'label': 'Pressure Alarm High',             'hmi': True,  'physical': False},
    ],
    # Temperature transmitter loop.
    'temperature_tx': [
        {'type': 'TT',  'signal_type': 'AI-FF',   'label': 'Temperature Transmitter',   'hmi': False, 'physical': True},
        {'type': 'TI',  'signal_type': 'SOFT',    'label': 'Temperature Indicator - DCS', 'hmi': True, 'physical': False},
        {'type': 'TAH', 'signal_type': 'SOFT',    'label': 'Temperature Alarm High',    'hmi': True,  'physical': False},
    ],
    # pH / generic analyzer loop.
    'analyzer': [
        {'type': 'AT',   'signal_type': 'AI-FF', 'label': 'Analyzer Transmitter',  'hmi': False, 'physical': True},
        {'type': 'AI',   'signal_type': 'SOFT',  'label': 'Analyzer Indicator',    'hmi': True,  'physical': False},
        {'type': 'AAH',  'signal_type': 'SOFT',  'label': 'Analyzer Alarm High',   'hmi': True,  'physical': False},
        {'type': 'AAL',  'signal_type': 'SOFT',  'label': 'Analyzer Alarm Low',    'hmi': True,  'physical': False},
        {'type': 'AXA',  'signal_type': 'SOFT',  'label': 'Analyzer Common Fault', 'hmi': True,  'physical': False},
    ],
}

# Family aliases — what an input description can match against.
_FAMILY_ALIASES: dict[str, tuple[str, ...]] = {
    'on_off_valve':   ('on_off_valve', 'on-off valve', 'shutdown valve', 'xv'),
    'control_valve':  ('control_valve', 'control valve', 'fv', 'cv loop'),
    'pressure_tx':    ('pressure_tx', 'pt', 'pressure transmitter'),
    'temperature_tx': ('temperature_tx', 'tt', 'temperature transmitter'),
    'analyzer':       ('analyzer', 'at', 'ph analyzer', 'gas analyzer'),
}

# Pattern used to decompose a seed tag.
_TAG_RX = re.compile(
    r'^(?P<unit>\d{2,4})-(?P<type>[A-Z]{1,5})-(?P<loop>\d{3,6})(?:/(?P<suffix>\w+))?$',
    flags=re.IGNORECASE,
)


def _family_for(seed_type: str, seed_family: str) -> Optional[str]:
    """Resolve which template to use."""
    if seed_family and seed_family in _LOOP_TEMPLATES:
        return seed_family
    if seed_family:
        for fam, aliases in _FAMILY_ALIASES.items():
            if seed_family.lower() in aliases:
                return fam
    # Fallback by type code.
    t = (seed_type or '').upper()
    if t in ('XV',):
        return 'on_off_valve'
    if t in ('FV', 'LV', 'TV', 'PV'):
        return 'control_valve'
    if t in ('PT',):
        return 'pressure_tx'
    if t in ('TT',):
        return 'temperature_tx'
    if t in ('AT',):
        return 'analyzer'
    return None


def expand_loop(seed_row: dict) -> dict:
    """Expand a single seed row into a full loop.

    Returns:
        {
          'family':   resolved family name or None,
          'rows':     list[dict] of generated rows (canonical IO-list schema),
          'skipped':  reason if no expansion happened
        }
    """
    tag = str(seed_row.get('tag') or '').strip()
    if not tag:
        return {'family': None, 'rows': [], 'skipped': 'no_tag'}

    m = _TAG_RX.match(tag)
    if not m:
        return {'family': None, 'rows': [], 'skipped': 'tag_pattern_unrecognised'}

    unit  = m.group('unit')
    type_ = (m.group('type') or '').upper()
    loop  = m.group('loop')

    family = _family_for(type_, str(seed_row.get('family') or ''))
    if not family:
        return {'family': None, 'rows': [], 'skipped': 'no_template_for_family'}

    template = _LOOP_TEMPLATES[family]
    rows: list[dict] = []
    for child in template:
        suffix = child.get('suffix')
        child_tag = f'{unit}-{child["type"]}-{loop}' + (f'/{suffix}' if suffix else '')
        rows.append({
            'tag':            child_tag,
            'description':    child['label'],
            'signal_type':    child['signal_type'],
            'pid':            seed_row.get('pid', ''),
            'location':       seed_row.get('location', ''),
            'panel':          seed_row.get('panel', ''),
            'range':          seed_row.get('range', '') if child['physical'] else '',
            'units':          seed_row.get('units', '') if child['physical'] else '',
            'manufacturer':   seed_row.get('manufacturer', '') if child['physical'] else '',
            'model':          seed_row.get('model', '') if child['physical'] else '',
            'is_soft':        child['hmi'],
            'is_physical':    child['physical'],
            'family':         family,
            '_ai_expanded_from': tag,
        })
    return {'family': family, 'rows': rows, 'skipped': ''}


def expand_many(seed_rows: list[dict]) -> dict:
    """Expand multiple seed rows; deduplicates by tag."""
    expanded: list[dict] = []
    seen: set[str] = set()
    families: dict[str, int] = {}
    skipped: list[dict] = []
    for sr in seed_rows or []:
        result = expand_loop(sr)
        if result['skipped']:
            skipped.append({'seed': sr.get('tag'), 'reason': result['skipped']})
            # still keep the seed row itself if we can't expand
            tag = (sr.get('tag') or '').strip()
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                expanded.append(sr)
            continue
        families[result['family']] = families.get(result['family'], 0) + 1
        for row in result['rows']:
            key = row['tag'].lower()
            if key in seen:
                continue
            seen.add(key)
            expanded.append(row)
    return {'rows': expanded, 'families': families, 'skipped': skipped}
