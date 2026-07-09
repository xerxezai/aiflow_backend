#!/bin/sh
cd /app
python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
exec(open('setup_superadmin.py').read())
"
