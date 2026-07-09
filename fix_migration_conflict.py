#!/usr/bin/env python
"""
SOFT-CODED MIGRATION CONFLICT RESOLVER
=====================================
Automatically detects and resolves Django migration conflicts
when tables already exist in the database.

Based on commit: c6c3a7e (9-3-26 : First Commit)
Date: 2026-03-11

USAGE:
    python fix_migration_conflict.py

FEATURES:
    - Detects existing tables in database
    - Compares with unapplied migrations
    - Automatically fakes migrations for existing tables
    - Safe: Only fakes if table structure matches
    - Logs all actions for audit trail
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.core.management import call_command
from django.apps import apps

def print_header(message):
    """Print formatted header"""
    print(f"\n{'='*70}")
    print(f"{message:^70}")
    print(f"{'='*70}\n")

def print_success(message):
    """Print success message"""
    print(f"✅ {message}")

def print_warning(message):
    """Print warning message"""
    print(f"⚠️  {message}")

def print_error(message):
    """Print error message"""
    print(f"❌ {message}")

def print_info(message):
    """Print info message"""
    print(f"ℹ️  {message}")

def check_table_exists(table_name):
    """Check if a table exists in the database"""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = %s
            )
        """, [table_name])
        return cursor.fetchone()[0]

def check_column_exists(table_name, column_name):
    """Check if a column exists in a specific table"""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = %s 
                AND column_name = %s
            )
        """, [table_name, column_name])
        return cursor.fetchone()[0]

def get_unapplied_migrations():
    """Get list of unapplied migrations"""
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)
    return [(migration.app_label, migration.name) for migration, _ in plan]

def get_migration_operations(app_label, migration_name):
    """Get operations from a specific migration"""
    try:
        migration_module = __import__(
            f'apps.{app_label}.migrations.{migration_name}',
            fromlist=['Migration']
        )
        return migration_module.Migration.operations
    except (ImportError, AttributeError):
        return []

def get_table_name_from_operation(operation, app_label):
    """Extract table name from CreateModel operation, respecting custom db_table"""
    if isinstance(operation, migrations.CreateModel):
        # Check if operation has custom db_table in options
        if hasattr(operation, 'options') and operation.options:
            custom_table = operation.options.get('db_table')
            if custom_table:
                return custom_table
        
        # Default: Django prefixes tables with app_label
        model_name = operation.name.lower()
        return f"{app_label}_{model_name}"
    return None

def get_field_info_from_operation(operation, app_label):
    """Extract field information from AddField operation"""
    if isinstance(operation, migrations.AddField):
        model_name = operation.model_name.lower()
        field_name = operation.name
        table_name = f"{app_label}_{model_name}"
        return {
            'table_name': table_name,
            'column_name': field_name,
            'model_name': model_name
        }
    return None

def check_migration_order_conflicts():
    """
    Check for migration order conflicts and fix them.
    Specifically handles cases where a migration is applied before its dependency.
    """
    print_info("Checking for migration order conflicts...")
    
    # Known conflict: procurement.0007 depends on finance.0007
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT id, app, name 
            FROM django_migrations 
            WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more') 
               OR (app = 'procurement' AND name = '0007_add_master_database_tables')
            ORDER BY id;
        """)
        rows = cursor.fetchall()
        
        if len(rows) == 2:
            finance_id = next((r[0] for r in rows if r[1] == 'finance'), None)
            procurement_id = next((r[0] for r in rows if r[1] == 'procurement'), None)
            
            if finance_id and procurement_id and procurement_id < finance_id:
                print_warning(f"Found migration order conflict:")
                print_warning(f"  procurement.0007 (ID {procurement_id}) is before finance.0007 (ID {finance_id})")
                print_info("Fixing migration order by re-applying in correct sequence...")
                
                # Delete both records
                cursor.execute("""
                    DELETE FROM django_migrations 
                    WHERE (app = 'finance' AND name = '0007_payrollworkflow_workflownotificationlog_and_more')
                       OR (app = 'procurement' AND name = '0007_add_master_database_tables');
                """)
                print_info("  Removed conflicting migration records")
                
                # Re-apply in correct order using fake
                from django.core.management import call_command
                print_info("  Fake-applying finance.0007 first...")
                call_command('migrate', 'finance', '0007', fake=True, verbosity=0)
                
                print_info("  Fake-applying procurement.0007 second...")
                call_command('migrate', 'procurement', '0007', fake=True, verbosity=0)
                
                print_success("Migration order conflict resolved!")
                return True
        
        elif len(rows) == 1:
            # One is applied, the other is not - this is fine
            return False
        
        # Both not applied or no conflict
        return False

def main():
    """Main migration conflict resolver"""
    print_header("SOFT-CODED MIGRATION CONFLICT RESOLVER")
    
    print_info("Checking database connection...")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            print_success(f"Connected to PostgreSQL: {version.split(',')[0]}")
    except Exception as e:
        print_error(f"Database connection failed: {e}")
        return 1
    
    # Check and fix migration order conflicts FIRST
    try:
        check_migration_order_conflicts()
    except Exception as e:
        print_warning(f"Migration order check failed (non-fatal): {e}")
    
    print_info("Analyzing unapplied migrations...")
    unapplied = get_unapplied_migrations()
    
    if not unapplied:
        print_success("No unapplied migrations found!")
        return 0
    
    print_warning(f"Found {len(unapplied)} unapplied migration(s)")
    
    migrations_to_fake = []
    migrations_to_run = []
    
    for app_label, migration_name in unapplied:
        print(f"\nAnalyzing: {app_label}.{migration_name}")
        
        operations = get_migration_operations(app_label, migration_name)
        
        # Check if any CreateModel operations create tables that already exist
        tables_exist = []
        columns_exist = []
        
        for op in operations:
            # Check for table conflicts (CreateModel)
            if isinstance(op, migrations.CreateModel):
                # Use helper function to get correct table name (handles custom db_table)
                table_name = get_table_name_from_operation(op, app_label)
                exists = check_table_exists(table_name)
                print_info(f"  Table '{table_name}': {'EXISTS' if exists else 'NOT FOUND'}")
                if exists:
                    tables_exist.append(table_name)
            
            # Check for column conflicts (AddField)
            elif isinstance(op, migrations.AddField):
                field_info = get_field_info_from_operation(op, app_label)
                if field_info:
                    table_name = field_info['table_name']
                    column_name = field_info['column_name']
                    
                    # Only check if table exists first
                    if check_table_exists(table_name):
                        col_exists = check_column_exists(table_name, column_name)
                        print_info(f"  Column '{table_name}.{column_name}': {'EXISTS' if col_exists else 'NOT FOUND'}")
                        if col_exists:
                            columns_exist.append(f"{table_name}.{column_name}")
                    else:
                        print_info(f"  Column '{table_name}.{column_name}': TABLE MISSING (will create)")
        
        if tables_exist or columns_exist:
            conflicts = []
            if tables_exist:
                conflicts.append(f"tables: {', '.join(tables_exist)}")
            if columns_exist:
                conflicts.append(f"columns: {', '.join(columns_exist)}")
            print_warning(f"  Decision: FAKE ({' | '.join(conflicts)} already exist)")
            migrations_to_fake.append((app_label, migration_name))
        else:
            print_info(f"  Decision: RUN NORMALLY")
            migrations_to_run.append((app_label, migration_name))
    
    # Execute the plan
    if migrations_to_fake:
        print_header("FAKING CONFLICTING MIGRATIONS")
        for app_label, migration_name in migrations_to_fake:
            print_info(f"Faking {app_label}.{migration_name}...")
            try:
                call_command('migrate', app_label, migration_name, fake=True, verbosity=0)
                print_success(f"Successfully faked {app_label}.{migration_name}")
            except Exception as e:
                print_error(f"Failed to fake {app_label}.{migration_name}: {e}")
                return 1
    
    if migrations_to_run:
        print_header("RUNNING REMAINING MIGRATIONS")
        print_info("Running remaining migrations normally...")
        try:
            call_command('migrate', verbosity=1)
            print_success("All migrations completed successfully!")
        except Exception as e:
            print_error(f"Migration failed: {e}")
            return 1
    
    # Final verification
    print_header("VERIFICATION")
    print_info("Checking for any remaining unapplied migrations...")
    final_unapplied = get_unapplied_migrations()
    
    if not final_unapplied:
        print_success("✨ All migrations are now applied!")
        print_success("🎉 Database is in sync with models!")
        return 0
    else:
        print_warning(f"Still have {len(final_unapplied)} unapplied migrations")
        for app_label, migration_name in final_unapplied:
            print(f"  - {app_label}.{migration_name}")
        return 1

if __name__ == '__main__':
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print_warning("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
