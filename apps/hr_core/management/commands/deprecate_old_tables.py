"""
Django management command to deprecate old tables (Phase 5)

Renames old tables to *_deprecated_YYYYMMDD and updates Django models.

Usage:
    python manage.py deprecate_old_tables --dry-run
    python manage.py deprecate_old_tables --confirm
"""

import os
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Deprecate old employee tables (Phase 5 of migration)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Actually perform the deprecation (REQUIRED to make changes)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        confirm = options.get('confirm', False)
        
        if not dry_run and not confirm:
            self.stdout.write(self.style.ERROR('\n❌ Error: Must specify --dry-run or --confirm'))
            self.stdout.write('  Use --dry-run to preview changes')
            self.stdout.write('  Use --confirm to actually deprecate tables\n')
            return

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('  PHASE 5: DEPRECATE OLD TABLES'))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made\n'))
        else:
            self.stdout.write(self.style.ERROR('⚠️  LIVE MODE - Tables will be renamed!\n'))
            self.stdout.write('   Press Ctrl+C within 5 seconds to cancel...\n')
            import time
            time.sleep(5)

        # Tables to deprecate
        date_suffix = datetime.now().strftime('%Y%m%d')
        tables_to_deprecate = [
            ('user_profiles', f'user_profiles_deprecated_{date_suffix}'),
            ('finance_employee_salary_info', f'finance_employee_salary_info_deprecated_{date_suffix}'),
            ('onboarding_record', f'onboarding_record_deprecated_{date_suffix}'),
        ]

        # Step 1: Check table existence
        self.stdout.write(self.style.WARNING('\nStep 1: Checking table existence...'))
        existing_tables = []
        
        with connection.cursor() as cursor:
            for old_name, new_name in tables_to_deprecate:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = %s
                    )
                """, [old_name])
                
                exists = cursor.fetchone()[0]
                if exists:
                    # Check record count
                    cursor.execute(f"SELECT COUNT(*) FROM {old_name}")
                    count = cursor.fetchone()[0]
                    
                    existing_tables.append((old_name, new_name, count))
                    self.stdout.write(f'  ✅ {old_name} exists ({count} records)')
                else:
                    self.stdout.write(f'  ⚠️  {old_name} does not exist (already deprecated?)')

        if not existing_tables:
            self.stdout.write(self.style.ERROR('\n❌ No tables to deprecate. Exiting.\n'))
            return

        # Step 2: Check for foreign key dependencies
        self.stdout.write(self.style.WARNING('\nStep 2: Checking foreign key dependencies...'))
        
        has_dependencies = False
        with connection.cursor() as cursor:
            for old_name, new_name, count in existing_tables:
                cursor.execute("""
                    SELECT 
                        tc.table_name as referencing_table,
                        kcu.column_name as referencing_column
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON ccu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND ccu.table_name = %s
                """, [old_name])
                
                dependencies = cursor.fetchall()
                if dependencies:
                    has_dependencies = True
                    self.stdout.write(self.style.ERROR(f'\n  ❌ {old_name} has FK dependencies:'))
                    for dep in dependencies[:5]:
                        self.stdout.write(f'     - {dep[0]}.{dep[1]}')
                    if len(dependencies) > 5:
                        self.stdout.write(f'     ... and {len(dependencies) - 5} more')
                else:
                    self.stdout.write(f'  ✅ {old_name} has no FK dependencies')

        if has_dependencies:
            self.stdout.write(self.style.ERROR('\n❌ Cannot deprecate: Tables have foreign key dependencies!'))
            self.stdout.write('   Run Phase 4 first to migrate foreign keys.\n')
            return

        # Step 3: Rename tables
        self.stdout.write(self.style.WARNING('\nStep 3: Renaming tables...'))
        
        if dry_run:
            for old_name, new_name, count in existing_tables:
                self.stdout.write(f'  [DRY RUN] Would rename: {old_name} → {new_name}')
        else:
            with connection.cursor() as cursor:
                for old_name, new_name, count in existing_tables:
                    try:
                        cursor.execute(f'ALTER TABLE {old_name} RENAME TO {new_name}')
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Renamed: {old_name} → {new_name}'))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  ❌ Failed to rename {old_name}: {e}'))

        # Step 4: Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('  SUMMARY'))
        self.stdout.write(self.style.SUCCESS('='*80))
        
        if dry_run:
            self.stdout.write(f'\n  Would deprecate {len(existing_tables)} table(s):')
            for old_name, new_name, count in existing_tables:
                self.stdout.write(f'    - {old_name} ({count} records)')
        else:
            self.stdout.write(f'\n  ✅ Deprecated {len(existing_tables)} table(s)')
            for old_name, new_name, count in existing_tables:
                self.stdout.write(f'    - {new_name} ({count} records)')

        # Step 5: Next steps
        self.stdout.write(self.style.WARNING('\n' + '='*80))
        self.stdout.write(self.style.WARNING('  NEXT STEPS'))
        self.stdout.write(self.style.WARNING('='*80))
        self.stdout.write('\n  1. Update Django models to mark as unmanaged:')
        self.stdout.write('     class Meta:')
        self.stdout.write(f"         managed = False")
        self.stdout.write(f"         db_table = '{new_name}'")
        self.stdout.write('\n  2. Run makemigrations to capture model changes')
        self.stdout.write('\n  3. Monitor application for 30-90 days')
        self.stdout.write('\n  4. Check logs for any queries to deprecated tables')
        self.stdout.write('\n  5. After monitoring period, run Phase 6 (final cleanup)\n')

        # Step 6: Create rollback script
        if not dry_run and confirm:
            rollback_path = os.path.join(
                os.path.dirname(__file__),
                '..',
                '..',
                'sql',
                f'ROLLBACK_PHASE5_{date_suffix}.sql'
            )
            
            with open(rollback_path, 'w') as f:
                f.write('-- ROLLBACK SCRIPT FOR PHASE 5\n')
                f.write(f'-- Generated: {datetime.now().isoformat()}\n')
                f.write('-- Use this to undo table deprecation\n\n')
                f.write('BEGIN;\n\n')
                
                for old_name, new_name, count in existing_tables:
                    f.write(f'ALTER TABLE {new_name} RENAME TO {old_name};\n')
                
                f.write('\nCOMMIT;\n')
            
            self.stdout.write(self.style.SUCCESS(f'  ✅ Rollback script created: {rollback_path}\n'))
