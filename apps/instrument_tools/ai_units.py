"""
AI Unit Normaliser  (Step 4).

Soft-coded unit conversion table covering the common pressure / flow /
temperature / length / voltage families found in instrument datasheets.

Public helpers:
  • normalise_unit(text)              -> canonical unit code or original
  • convert(value, from_u, to_u)      -> float or None
  • sanity_check_range(row)           -> list[issue]  (LL ≤ L ≤ H ≤ HH etc.)
"""
from __future__ import annotations

import re
from typing import Optional

# ─── Soft-coded canonical unit codes ─────────────────────────────────────────
# Each entry: canonical_code -> (family, list of aliases, factor_to_si, offset)
# Pressure SI = Pa, Temperature SI = degC (with offset handling), Length SI = m
_UNITS: dict[str, dict] = {
    # Pressure (factor relative to Pa)
    'pa':     {'family': 'pressure',    'aliases': ['pa', 'pascal'],            'factor': 1.0,        'offset': 0.0},
    'kpa':    {'family': 'pressure',    'aliases': ['kpa'],                     'factor': 1e3,        'offset': 0.0},
    'mpa':    {'family': 'pressure',    'aliases': ['mpa'],                     'factor': 1e6,        'offset': 0.0},
    'bar':    {'family': 'pressure',    'aliases': ['bar'],                     'factor': 1e5,        'offset': 0.0},
    'barg':   {'family': 'pressure',    'aliases': ['barg', 'bar g'],           'factor': 1e5,        'offset': 0.0},
    'bara':   {'family': 'pressure',    'aliases': ['bara', 'bar a'],           'factor': 1e5,        'offset': 0.0},
    'psi':    {'family': 'pressure',    'aliases': ['psi', 'lbf/in2'],          'factor': 6894.757,   'offset': 0.0},
    'psig':   {'family': 'pressure',    'aliases': ['psig'],                    'factor': 6894.757,   'offset': 0.0},
    'mmh2o':  {'family': 'pressure',    'aliases': ['mmh2o', 'mm h2o', 'mmwc'], 'factor': 9.80665,    'offset': 0.0},
    'mmhg':   {'family': 'pressure',    'aliases': ['mmhg', 'torr'],            'factor': 133.322,    'offset': 0.0},
    # Flow (factor relative to m3/s)
    'm3/h':   {'family': 'flow',        'aliases': ['m3/h', 'm3h', 'm3/hr'],    'factor': 1 / 3600.0, 'offset': 0.0},
    'm3/s':   {'family': 'flow',        'aliases': ['m3/s', 'm3s'],             'factor': 1.0,        'offset': 0.0},
    'l/min':  {'family': 'flow',        'aliases': ['l/min', 'lpm'],            'factor': 1.0 / 60000.0, 'offset': 0.0},
    'kg/h':   {'family': 'mass_flow',   'aliases': ['kg/h', 'kgh', 'kg/hr'],    'factor': 1 / 3600.0, 'offset': 0.0},
    'kg/s':   {'family': 'mass_flow',   'aliases': ['kg/s', 'kgs'],             'factor': 1.0,        'offset': 0.0},
    # Temperature
    'degc':   {'family': 'temperature', 'aliases': ['degc', 'c', '°c', 'celsius'], 'factor': 1.0,     'offset': 0.0},
    'degf':   {'family': 'temperature', 'aliases': ['degf', 'f', '°f', 'fahrenheit'], 'factor': 5/9,  'offset': -32.0 * 5/9},
    'k':      {'family': 'temperature', 'aliases': ['k', 'kelvin'],             'factor': 1.0,        'offset': -273.15},
    # Length
    'mm':     {'family': 'length',      'aliases': ['mm'],                      'factor': 1e-3,       'offset': 0.0},
    'cm':     {'family': 'length',      'aliases': ['cm'],                      'factor': 1e-2,       'offset': 0.0},
    'm':      {'family': 'length',      'aliases': ['m', 'meter'],              'factor': 1.0,        'offset': 0.0},
    'km':     {'family': 'length',      'aliases': ['km'],                      'factor': 1e3,        'offset': 0.0},
    'in':     {'family': 'length',      'aliases': ['in', 'inch', '"'],         'factor': 0.0254,     'offset': 0.0},
    # Voltage
    'v':      {'family': 'voltage',     'aliases': ['v', 'vdc', 'vac', 'volt'], 'factor': 1.0,        'offset': 0.0},
    'kv':     {'family': 'voltage',     'aliases': ['kv'],                      'factor': 1e3,        'offset': 0.0},
    # Dimensionless
    'ph':     {'family': 'ph',          'aliases': ['ph'],                      'factor': 1.0,        'offset': 0.0},
    '%':      {'family': 'percent',     'aliases': ['%', 'pct', 'percent'],     'factor': 1.0,        'offset': 0.0},
}


# Precompute slug → canonical map.
def _slug(s) -> str:
    return re.sub(r'\s+', '', str(s or '').strip().lower())


_ALIAS_INDEX = {}
for code, meta in _UNITS.items():
    _ALIAS_INDEX[_slug(code)] = code
    for a in meta['aliases']:
        _ALIAS_INDEX[_slug(a)] = code


def normalise_unit(text) -> Optional[str]:
    if text is None:
        return None
    return _ALIAS_INDEX.get(_slug(text))


def convert(value, from_u, to_u) -> Optional[float]:
    """Convert value from `from_u` to `to_u`. Returns None on unsupported units."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    a = normalise_unit(from_u)
    b = normalise_unit(to_u)
    if not a or not b:
        return None
    fa, fb = _UNITS[a], _UNITS[b]
    if fa['family'] != fb['family']:
        return None
    # Convert to SI first, then to target.
    si = (v + fa['offset']) * fa['factor']
    out = (si / fb['factor']) - fb['offset']
    return out


# ─── Range sanity checks ────────────────────────────────────────────────────
# Order: LL <= L <= H <= HH, Calib subset of Instrument Range.
_RANGE_FIELDS = ('range_min', 'range_max', 'calib_min', 'calib_max',
                 'll', 'l', 'h', 'hh')


def sanity_check_range(row: dict) -> list[dict]:
    issues: list[dict] = []

    def f(k):
        try:
            v = row.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                return None
            return float(str(v).replace(',', ''))
        except (TypeError, ValueError):
            return None

    ll, l, h, hh = f('ll'), f('l'), f('h'), f('hh')
    rmin, rmax = f('range_min'), f('range_max')
    cmin, cmax = f('calib_min'), f('calib_max')

    pairs = [
        (ll, l,  'LL must be ≤ L',         'alarm_order_ll_l'),
        (l,  h,  'L must be ≤ H',          'alarm_order_l_h'),
        (h,  hh, 'H must be ≤ HH',         'alarm_order_h_hh'),
        (rmin, rmax, 'Range min must be ≤ Range max', 'range_order'),
        (cmin, cmax, 'Calib min must be ≤ Calib max', 'calib_order'),
    ]
    for a, b, msg, kind in pairs:
        if a is not None and b is not None and a > b:
            issues.append({'field': 'range', 'severity': 'warning',
                           'kind': kind, 'message': msg, 'value': f'{a} > {b}'})

    # Calibration range must lie inside instrument range when both defined.
    if rmin is not None and cmin is not None and cmin < rmin:
        issues.append({'field': 'calib', 'severity': 'warning',
                       'kind': 'calib_below_range',
                       'message': 'Calibration min is below instrument range min.',
                       'value': f'{cmin} < {rmin}'})
    if rmax is not None and cmax is not None and cmax > rmax:
        issues.append({'field': 'calib', 'severity': 'warning',
                       'kind': 'calib_above_range',
                       'message': 'Calibration max is above instrument range max.',
                       'value': f'{cmax} > {rmax}'})
    return issues
