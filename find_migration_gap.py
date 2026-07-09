#!/usr/bin/env python
"""Find available migration ID slot before procurement.0007"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    # Find IDs around procurement.0007 (ID 158)
    cursor.execute("""
        SELECT id, app, name 
        FROM django_migrations 
        WHERE id BETWEEN 150 AND 165
        ORDER BY id;
    """)
    rows = cursor.fetchall()
    
    print("\n" + "="*80)
    print("MIGRATION IDs 150-165")
    print("="*80)
    for row in rows:
        print(f"  ID {row[0]:4d}: {row[1]:20s} | {row[2]}")
    print("="*80)
    
    # Find gaps
    ids = [r[0] for r in rows]
    all_ids = range(150, 166)
    gaps = [i for i in all_ids if i not in ids and i < 158]
    
    if gaps:
        print(f"\n✅ Available ID slots before 158: {gaps}")
        print(f"   Recommended: Use ID {gaps[-1]} for finance.0007")
    else:
        print("\n⚠️ No gaps found - all IDs from 150-157 are taken")
