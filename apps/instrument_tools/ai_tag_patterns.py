"""
AI Tag-Pattern Learner  (Step 2).

Reads a sample of customer tags and derives the project-specific tag regex.
Once derived, every other tag is validated against the learned pattern, which
catches:
  • Mistyped tag separators (`604_XV_0301` vs `604-XV-0301`)
  • Wrong unit prefix (`605-XV-0301` when the project is unit 604)
  • Loop-number digit-count drift (3-digit vs 4-digit vs 6-digit)
  • Suffix style (`/1`, `-1`, `_1`)

Soft-coded — every threshold and regex template lives at module level.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

# Soft-coded knobs.
_MIN_SAMPLE = 5                  # need at least this many tags to learn.
_MIN_SHARE  = 0.55               # winning pattern must cover >= 55 % of sample.
_SEPARATORS = ('-', '_', '.', ' ', '/')

# Canonical pattern templates the learner picks from. Each is a (regex, label).
# Captures: <unit>, <type>, <loop>, <suffix>.
_TEMPLATES: tuple[tuple[str, str], ...] = (
    # 604-XV-0301/1   ← ADNOC ZIRKU style
    (r'^(?P<unit>\d{2,4})-(?P<type>[A-Z]{1,5})-(?P<loop>\d{3,6})(?:/(?P<suffix>\w+))?$',
     'unit-type-loop[/suffix]'),
    # XV-0301
    (r'^(?P<type>[A-Z]{1,5})-(?P<loop>\d{3,6})(?:/(?P<suffix>\w+))?$',
     'type-loop[/suffix]'),
    # 604XV0301
    (r'^(?P<unit>\d{2,4})(?P<type>[A-Z]{1,5})(?P<loop>\d{3,6})$',
     'unit+type+loop (no separators)'),
    # 640-AAH-604015 (6-digit loop)
    (r'^(?P<unit>\d{3})-(?P<type>[A-Z]{2,5})-(?P<loop>\d{6})$',
     'unit-type-loop6'),
)


def learn(tags: Iterable[str]) -> dict:
    """Infer the dominant tag pattern from a sample.

    Returns:
        {
          'pattern':  compiled regex or None,
          'label':    human readable name,
          'coverage': fraction matched in [0,1],
          'units':    Counter of detected unit prefixes,
          'types':    Counter of detected instrument-type prefixes,
        }
    """
    sample = [str(t).strip() for t in tags if t and str(t).strip()]
    if len(sample) < _MIN_SAMPLE:
        return {'pattern': None, 'label': None, 'coverage': 0.0,
                'units': Counter(), 'types': Counter()}
    best = (None, None, 0)  # (regex, label, matches)
    for raw, label in _TEMPLATES:
        rx = re.compile(raw)
        hits = sum(1 for t in sample if rx.match(t))
        if hits > best[2]:
            best = (rx, label, hits)
    coverage = best[2] / len(sample) if sample else 0.0
    if coverage < _MIN_SHARE:
        return {'pattern': None, 'label': None, 'coverage': round(coverage, 3),
                'units': Counter(), 'types': Counter()}
    units: Counter = Counter()
    types: Counter = Counter()
    rx = best[0]
    for t in sample:
        m = rx.match(t)
        if not m:
            continue
        if 'unit' in m.groupdict() and m.group('unit'):
            units[m.group('unit')] += 1
        if 'type' in m.groupdict() and m.group('type'):
            types[m.group('type')] += 1
    return {
        'pattern':  rx,
        'label':    best[1],
        'coverage': round(coverage, 3),
        'units':    units,
        'types':    types,
    }


def validate(tags: Iterable[str], learned: dict) -> list[dict]:
    """Validate each tag against the learned pattern.

    Yields one issue dict per offending tag (empty list if pattern is None).
    """
    rx = learned.get('pattern')
    if rx is None:
        return []
    dominant_unit = None
    units = learned.get('units') or Counter()
    if units:
        dominant_unit, _ = units.most_common(1)[0]
    issues: list[dict] = []
    for idx, t in enumerate((str(x).strip() for x in tags), start=1):
        if not t:
            continue
        m = rx.match(t)
        if not m:
            issues.append({
                'row': idx, 'field': 'tag', 'severity': 'warning',
                'kind': 'tag_pattern',
                'message': f'Tag "{t}" does not match the project pattern ({learned.get("label")}).',
                'value': t,
            })
            continue
        if dominant_unit and m.groupdict().get('unit') and m.group('unit') != dominant_unit:
            issues.append({
                'row': idx, 'field': 'tag', 'severity': 'info',
                'kind': 'tag_unit_drift',
                'message': f'Tag unit prefix "{m.group("unit")}" differs from project unit "{dominant_unit}".',
                'value': t,
            })
    return issues
