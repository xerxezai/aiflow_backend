"""Diagnostic script — run inside backend container to debug equipment extraction."""
import sys, os, importlib
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

import apps.pid_analysis.equipment_analysis_views as ea_mod
importlib.reload(ea_mod)  # clear lru_cache

cfg = ea_mod._load_config()
print('tag_pattern:', cfg.get('extraction', {}).get('tag_pattern'))
print()

sample_text = """PJ6-EXD-MRI-BQDA-0023 PIPING AND INSTRUMENTATION DIAGRAM
NOW OIL SLUG CATCHER (V-308-TF)
V-308-TF NOMINAL CAPACITY 327 M3 DIAMETER 5.0 M LENGTH 15.0 M
OPERATING PRESS 155 psig OPERATING TEMP 105/45 F
FROM PIG RECEIVER V-805-TF SOUR GAS TO HAIL FLARE HEADER
V-805-TF OIL PIG RECEIVER 20 DIAMETER
FROM PIG RECEIVER V-805-1F
V-805-1F GAS PIG RECEIVER
V-804-TF THREE PHASE SEPARATOR
V-904 MEA INLET SCRUBBER
E-301 HEAT EXCHANGER
P-101A FEED PUMP
D-501 DRUM SEPARATOR
DRAIN FROM MRD OIL PIG RECEIVER V-805-TF
"""

items = ea_mod._extract_equipment_items(sample_text, 'TEST-P-001', cfg)
print(f'Found {len(items)} equipment items:')
for it in items:
    print(f'  TAG={it["tag"]}  TYPE={it["type_label"]}')
