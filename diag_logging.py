"""Check logging config inside container."""
import sys, os
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()
from django.conf import settings
L = getattr(settings, 'LOGGING', {})
hdl = L.get('handlers', {})
for k, v in hdl.items():
    print(f"HANDLER {k}: level={v.get('level','NOTSET')} class={v.get('class','')}")
log = L.get('loggers', {})
for k, v in log.items():
    print(f"LOGGER {k}: level={v.get('level','NOTSET')} handlers={v.get('handlers','')}")
disable = L.get('disable_existing_loggers', False)
print(f"disable_existing_loggers={disable}")
print(f"root={L.get('root', L.get('root', 'NOT SET'))}")
