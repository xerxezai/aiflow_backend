#!/bin/sh
cd /app
. /opt/venv/bin/activate
python manage.py shell << 'PYEOF'
from django.contrib.auth import get_user_model
U = get_user_model()
print("TOTAL_USERS:", U.objects.count())
for u in U.objects.all()[:30]:
    print("USER:", u.email, "| active:", u.is_active, "| staff:", u.is_staff)
PYEOF
