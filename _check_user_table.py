import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection

User = get_user_model()
print(f'Model: {User.__name__}')
print(f'Table: {User._meta.db_table}')

with connection.cursor() as cursor:
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema='public' AND table_name LIKE '%user%' 
        ORDER BY table_name
    """)
    print('\nUser-related tables:')
    for row in cursor.fetchall():
        print(f'  {row[0]}')
