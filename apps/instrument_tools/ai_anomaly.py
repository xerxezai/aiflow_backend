"""
Cross-row Anomaly Detection  (Step 8).

Runs statistical / structural checks across the *whole* table that the
per-row rule engine cannot see:

  • loop_completeness      — missing siblings (XV without XY/ZSO/ZSC, etc.)
  • channel_collision      — two rows mapped to same Cab/Node/Slot/Channel
  • hazardous_consistency  — IS instrument routed to NIS card or vice-versa
  • softtag_no_address     — soft tag wrongly carries a physical address
  • pid_loop_binding       — loop crosses multiple P&IDs (likely an error)
  • fail_action_valve      — control / on-off valve missing fail-action
  • cabinet_capacity       — cabinet utilisation above 80 %
  • revision_drift         — rev field present but blank on > N % of rows
  • tbd_density            — > N % of rows still contain TBD

All thresholds are soft-coded module-level constants.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

_TBD_RX            = re.compile(r'\btbd\b', re.IGNORECASE)
_TBD_DENSITY_LIMIT = 0.10
_REV_BLANK_LIMIT   = 0.20
_CABINET_UTIL_LIMIT = 0.80

# Soft-coded family → expected sibling type codes.
_EXPECTED_SIBLINGS: dict[str, tuple[str, ...]] = {
    'XV':  ('XY', 'ZSO', 'ZSC'),
    'FV':  ('FY',),
    'PV':  ('PY',),
    'TV':  ('TY',),
    'LV':  ('LY',),
}

_TAG_RX = re.compile(
    r'^(?P<unit>\d{2,4})-(?P<type>[A-Z]{1,5})-(?P<loop>\d{3,6})',
    re.IGNORECASE,
)


def _parse_tag(tag: str):
    m = _TAG_RX.match(str(tag or ''))
    if not m:
        return None
    return m.group('unit'), m.group('type').upper(), m.group('loop')


def _issue(kind, severity, message, **extra):
    out = {'kind': kind, 'severity': severity, 'message': message}
    out.update(extra)
    return out


def detect(rows: Iterable[dict]) -> list[dict]:
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    issues: list[dict] = []
    if not rows:
        return issues

    # ── loop_completeness ─────────────────────────────────────────────────
    by_loop: dict[tuple, set] = defaultdict(set)
    sample_for_loop: dict[tuple, dict] = {}
    for r in rows:
        parsed = _parse_tag(r.get('tag'))
        if not parsed:
            continue
        unit, type_, loop = parsed
        key = (unit, loop)
        by_loop[key].add(type_)
        sample_for_loop.setdefault(key, r)
    for (unit, loop), types in by_loop.items():
        for parent, expected in _EXPECTED_SIBLINGS.items():
            if parent not in types:
                continue
            missing = [e for e in expected if e not in types]
            if missing:
                issues.append(_issue(
                    'loop_completeness', 'warning',
                    f'Loop {unit}-{parent}-{loop} is missing sibling(s): {", ".join(missing)}.',
                    field='tag', tag=f'{unit}-{parent}-{loop}',
                    missing_siblings=missing,
                ))

    # ── channel_collision ─────────────────────────────────────────────────
    addr_map: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        addr = (r.get('cabinet'), r.get('node'), r.get('slot'), r.get('channel'))
        if all(a not in (None, '', 0) for a in addr):
            addr_map[addr].append(str(r.get('tag') or ''))
    for addr, tags in addr_map.items():
        if len(tags) > 1:
            issues.append(_issue(
                'channel_collision', 'error',
                f'Channel {addr[0]}/N{addr[1]}/S{addr[2]}/C{addr[3]} is shared by {len(tags)} tags.',
                field='channel', tags=tags,
            ))

    # ── softtag_no_address ────────────────────────────────────────────────
    for r in rows:
        if not (r.get('is_soft') or str(r.get('io_type') or '').upper() == 'SOFT'):
            continue
        if any(r.get(k) not in (None, '', 0) for k in ('cabinet', 'channel')):
            issues.append(_issue(
                'softtag_no_address', 'info',
                f'Soft tag {r.get("tag")} should not have a hardware address.',
                field='channel', tag=r.get('tag'),
            ))

    # ── hazardous_consistency (basic heuristic) ───────────────────────────
    for r in rows:
        eex = str(r.get('eex_certification') or r.get('hazardous') or '').lower()
        cab = str(r.get('cabinet') or '')
        if not eex or not cab:
            continue
        # Soft-coded suffix convention: IS cabinets contain 'IS', NIS contain 'NIS'.
        if "ex'i'" in eex and 'nis' in cab.lower():
            issues.append(_issue(
                'hazardous_consistency', 'error',
                f'IS instrument {r.get("tag")} routed to a NIS cabinet ({cab}).',
                field='cabinet', tag=r.get('tag'),
            ))

    # ── pid_loop_binding ──────────────────────────────────────────────────
    pid_by_loop: dict[tuple, set] = defaultdict(set)
    for r in rows:
        parsed = _parse_tag(r.get('tag'))
        pid = str(r.get('pid') or '').strip()
        if not parsed or not pid:
            continue
        pid_by_loop[(parsed[0], parsed[2])].add(pid)
    for (unit, loop), pids in pid_by_loop.items():
        if len(pids) > 1:
            issues.append(_issue(
                'pid_loop_binding', 'warning',
                f'Loop {unit}-*-{loop} appears on multiple P&IDs: {sorted(pids)}.',
                field='pid', loop=f'{unit}-*-{loop}',
            ))

    # ── fail_action_valve ─────────────────────────────────────────────────
    for r in rows:
        type_ = ''
        parsed = _parse_tag(r.get('tag'))
        if parsed:
            type_ = parsed[1]
        if type_ in ('XV', 'FV', 'PV', 'TV', 'LV'):
            fa = str(r.get('fail_action') or r.get('fail_position') or '').strip()
            if not fa:
                issues.append(_issue(
                    'fail_action_valve', 'warning',
                    f'Valve {r.get("tag")} is missing fail-action (FO/FC/FL).',
                    field='fail_action', tag=r.get('tag'),
                ))

    # ── cabinet_capacity ──────────────────────────────────────────────────
    cab_count: dict[str, int] = defaultdict(int)
    for r in rows:
        cab = str(r.get('cabinet') or '').strip()
        if cab:
            cab_count[cab] += 1
    # Assume nominal 64 channels/cabinet (soft-coded).
    nominal = 64
    for cab, n in cab_count.items():
        util = n / nominal
        if util > _CABINET_UTIL_LIMIT:
            issues.append(_issue(
                'cabinet_capacity', 'warning',
                f'Cabinet {cab} utilisation at {round(util*100)} % ({n}/{nominal}).',
                field='cabinet', cabinet=cab, utilisation=round(util, 3),
            ))

    # ── revision_drift / tbd_density (table-wide) ─────────────────────────
    if any('rev' in r for r in rows):
        blanks = sum(1 for r in rows if not str(r.get('rev') or '').strip())
        if blanks / len(rows) > _REV_BLANK_LIMIT:
            issues.append(_issue(
                'revision_drift', 'info',
                f'{blanks} of {len(rows)} rows are missing a revision tag.',
                field='rev', count=blanks, total=len(rows),
            ))
    tbd_rows = sum(
        1 for r in rows
        if any(_TBD_RX.search(str(v)) for v in r.values() if v is not None)
    )
    if tbd_rows / len(rows) > _TBD_DENSITY_LIMIT:
        issues.append(_issue(
            'tbd_density', 'info',
            f'{tbd_rows} of {len(rows)} rows still contain "TBD".',
            field=None, count=tbd_rows, total=len(rows),
        ))

    return issues
