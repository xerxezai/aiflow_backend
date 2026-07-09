#!/usr/bin/env python
"""Fix migration order by removing and re-fake-applying in correct order"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
django.setup()

from django.db import connection, transaction
from django.core.management import call_command

print("\n" + "="*80)
print("FIXING MIGRATION ORDER VIA FAKE-MIGRATE")
print("="*80)

with transaction.atomic():
    with connection.cursor() as cursor:
        # Step 1: Show current state
        cursor.execute("""
            SELECT id, app, name, applied
            FROM django_migrations 
            WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more') 
               OR (app = 'procurement' AND name = '0007_add_master_database_tables')
            ORDER BY id;
        """)
        before = cursor.fetchall()
        print("\nCURRENT STATE (INCORRECT ORDER):")
        for row in before:
            print(f"  ID {row[0]:4d}: {row[1]:15s} | {row[2]:50s} | {row[3]}")
        
        # Step 2: Delete both migration records
        print("\n→ Removing both migration records from django_migrations...")
        cursor.execute("""
            DELETE FROM django_migrations 
            WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more')
               OR (app = 'procurement' AND name = '0007_add_master_database_tables');
        """)
        deleted_count = cursor.rowcount
        print(f"  Deleted {deleted_count} records")

print("\n→ Fake-applying finance.0007 first (will get new sequential ID)...")
call_command('migrate', 'finance', '0007', fake=True, verbosity=1)

print("\n→ Fake-applying procurement.0007 second (will get ID after finance.0007)...")
call_command('migrate', 'procurement', '0007', fake=True, verbosity=1)

# Verify the fix
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT id, app, name, applied
        FROM django_migrations 
        WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more') 
           OR (app = 'procurement' AND name = '0007_add_master_database_tables')
        ORDER BY id;
    """)
    after = cursor.fetchall()
    
    print("\n" + "="*80)
    print("NEW STATE (CORRECT ORDER):")
    print("="*80)
    for row in after:
        print(f"  ID {row[0]:4d}: {row[1]:15s} | {row[2]:50s} | {row[3]}")
    
    # Validate
    finance_id = next((r[0] for r in after if r[1] == 'finance'), None)
    procurement_id = next((r[0] for r in after if r[1] == 'procurement'), None)
    
    if finance_id and procurement_id and finance_id < procurement_id:
        print("\n✅ SUCCESS: Migration order fixed!")
        print(f"   finance.0007 (ID {finance_id}) is now before procurement.0007 (ID {procurement_id})")
        print("   Backend should now start without migration errors.")
    else:
        print("\n❌ FAILED: Order still incorrect")
        sys.exit(1)

print("="*80 + "\n")
