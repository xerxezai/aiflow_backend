import json, os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.pid_verification.models import PIDVDocument, PIDVDrawing
docs = list(PIDVDocument.objects.filter(status='completed').order_by('-created_at')[:1])
if not docs:
    print('No completed docs found')
else:
    d = docs[0]
    print('Doc:', d.document_id)
    dr = PIDVDrawing.objects.filter(document=d).first()
    if not dr:
        print('No drawings')
    else:
        tp = (dr.metadata or {}).get('tag_positions', {})
        print('Total tag_positions:', len(tp))
        print()
        v = tp.get('V-3115')
        print('=== V-3115 ===')
        print(json.dumps(v, indent=2))
        print()
        print('=== ALL entries ===')
        for k, val in sorted(tp.items()):
            if isinstance(val, dict):
                x = val.get('x_pct', '?')
                y = val.get('y_pct', '?')
                occ = val.get('all', [])
                occ_str = ', '.join([f"x={o.get('x_pct',0):.1f} y={o.get('y_pct',0):.1f}" for o in occ])
                print(f"  {k:20s}: primary x={x!r:6} y={y!r:6}  | {len(occ)} occ [{occ_str}]")
