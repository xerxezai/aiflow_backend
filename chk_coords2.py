import sys, os
sys.path.insert(0, '/app')
os.chdir('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

import json
from apps.pid_verification.models import PIDVDocument, PIDVDrawing

docs = list(PIDVDocument.objects.filter(status='completed').order_by('-created_at')[:1])
if not docs:
    print('NO DOCS')
else:
    d = docs[0]
    print('Doc:', str(d.document_id))
    dr = PIDVDrawing.objects.filter(document=d).first()
    if not dr:
        print('NO DRAWING')
    else:
        tp = (dr.metadata or {}).get('tag_positions', {})
        print('tag_positions count:', len(tp))
        v = tp.get('V-3115')
        print('V-3115:', json.dumps(v))
        print('---ALL---')
        for k in sorted(tp.keys()):
            val = tp[k]
            if isinstance(val, dict):
                x = val.get('x_pct', 0)
                y = val.get('y_pct', 0)
                occ = val.get('all', [])
                ostr = '; '.join(['x='+str(round(o.get('x_pct',0),1))+' y='+str(round(o.get('y_pct',0),1)) for o in occ])
                print(k + ' | x=' + str(round(x,1)) + ' y=' + str(round(y,1)) + ' | occ=' + str(len(occ)) + ' [' + ostr + ']')
