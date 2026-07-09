#!/usr/bin/env python
"""Check migration order in django_migrations table"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("""
        SELECT id, app, name, applied 
        FROM django_migrations 
        WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more') 
           OR (app = 'procurement' AND name = '0007_add_master_database_tables')
        ORDER BY id;
    """)
    rows = cursor.fetchall()
    
    print("\n" + "="*80)
    print("MIGRATION ORDER CHECK")
    print("="*80)
    for row in rows:
        print(f"ID: {row[0]:4d} | App: {row[1]:15s} | Migration: {row[2]:50s} | Applied: {row[3]}")
    print("="*80)
    
    if len(rows) == 2:
        finance_id = next((r[0] for r in rows if r[1] == 'finance'), None)
        procurement_id = next((r[0] for r in rows if r[1] == 'procurement'), None)
        
        if finance_id and procurement_id:
            if procurement_id < finance_id:
                print("\n❌ PROBLEM DETECTED:")
                print(f"   procurement.0007 (ID {procurement_id}) was applied BEFORE finance.0007 (ID {finance_id})")
                print("   This violates the dependency order!")
            else:
                print("\n✅ Migration order is correct")
