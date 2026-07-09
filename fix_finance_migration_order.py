#!/usr/bin/env python
"""Fix migration order by re-ordering finance.0007 before procurement.0007"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
django.setup()

from django.db import connection, transaction

print("\n" + "="*80)
print("FIXING MIGRATION ORDER")
print("="*80)

with transaction.atomic():
    with connection.cursor() as cursor:
        # Step 1: Check current state
        cursor.execute("""
            SELECT id, app, name 
            FROM django_migrations 
            WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more') 
               OR (app = 'procurement' AND name = '0007_add_master_database_tables')
            ORDER BY id;
        """)
        before = cursor.fetchall()
        print("\nBEFORE:")
        for row in before:
            print(f"  ID {row[0]:4d}: {row[1]}.{row[2]}")
        
        # Step 2: Delete finance.0007 record
        print("\n→ Deleting finance.0007 record (ID 169)...")
        cursor.execute("""
            DELETE FROM django_migrations 
            WHERE app = 'finance' 
              AND name = '0007_payrollworkflow_workflownotificationlog_and_more';
        """)
        
        # Step 3: Re-insert with ID 157 (before procurement.0007's ID 158)
        print("→ Re-inserting finance.0007 with ID 157 (before procurement.0007)...")
        cursor.execute("""
            INSERT INTO django_migrations (id, app, name, applied)
            VALUES (157, 'finance', '0007_payrollworkflow_workflownotificationlog_and_more', NOW());
        """)
        
        # Step 4: Update the sequence to avoid conflicts
        print("→ Updating django_migrations_id_seq...")
        cursor.execute("""
            SELECT setval('django_migrations_id_seq', (SELECT MAX(id) FROM django_migrations));
        """)
        
        # Step 5: Verify fix
        cursor.execute("""
            SELECT id, app, name 
            FROM django_migrations 
            WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more') 
               OR (app = 'procurement' AND name = '0007_add_master_database_tables')
            ORDER BY id;
        """)
        after = cursor.fetchall()
        print("\nAFTER:")
        for row in after:
            print(f"  ID {row[0]:4d}: {row[1]}.{row[2]}")
        
        # Validate order
        finance_id = next((r[0] for r in after if r[1] == 'finance'), None)
        procurement_id = next((r[0] for r in after if r[1] == 'procurement'), None)
        
        if finance_id and procurement_id and finance_id < procurement_id:
            print("\n✅ SUCCESS: Migration order fixed!")
            print(f"   finance.0007 (ID {finance_id}) is now before procurement.0007 (ID {procurement_id})")
        else:
            print("\n❌ FAILED: Order still incorrect")
            raise Exception("Migration order not fixed")

print("="*80)
print("Transaction committed. Backend should now start successfully.")
print("="*80 + "\n")
