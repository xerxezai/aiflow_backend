import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
django.setup()

from apps.pid_verification.models import PIDVLegendSheet

for s in PIDVLegendSheet.objects.all():
    d = s.extracted_data or {}
    line_rep  = d.get('line_representation', [])
    abbr      = d.get('abbreviations_process', [])
    svc       = d.get('service_codes', {})
    ins       = d.get('insulation_codes', {})
    specs     = d.get('piping_specs', {})
    instr_pfx = d.get('instrument_prefixes', [])
    valve_pfx = d.get('valve_prefixes', [])
    inline_eq = d.get('inline_equipment', [])
    raw_sec   = d.get('raw_sections', {})
    elec_abbr = d.get('electrical_abbreviations', {})
    typical_c = d.get('typical_circuits', [])
    print(f'--- Sheet {s.id} | status={s.status} ---')
    print(f'  line_representation: {len(line_rep)}')
    print(f'  abbreviations_process: {len(abbr)}')
    print(f'  service_codes: {len(svc)} | insulation_codes: {len(ins)} | piping_specs: {len(specs)}')
    print(f'  instrument_prefixes: {len(instr_pfx)} | valve_prefixes: {len(valve_pfx)}')
    print(f'  inline_equipment: {len(inline_eq)}')
    print(f'  raw_sections: {len(raw_sec)} sections')
    print(f'  electrical_abbreviations: {len(elec_abbr)} keys -> {list(elec_abbr.keys())}')
    print(f'  typical_circuits: {len(typical_c)} entries')
    if typical_c:
        print(f'  first typical: {typical_c[0].get("typical_number")} - {typical_c[0].get("title")}')
    print(f'  all keys: {list(d.keys())}')
    if s.error_message:
        print(f'  error: {s.error_message[:200]}')
