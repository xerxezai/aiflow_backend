"""
Import employee_id mappings from JSON and apply to production database.
"""
import json
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.rbac.models import UserProfile


class Command(BaseCommand):
    help = 'Import employee_id mappings from JSON and update database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--input',
            type=str,
            default='employee_mappings.json',
            help='Input JSON file path with mappings'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying'
        )
        parser.add_argument(
            '--inline-json',
            type=str,
            help='Provide JSON mappings directly as string (for Railway shell)'
        )

    def handle(self, *args, **options):
        input_file = options['input']
        dry_run = options['dry_run']
        inline_json = options['inline_json']
        
        # Load mappings from file or inline JSON
        if inline_json:
            try:
                mappings = json.loads(inline_json)
                self.stdout.write(f'Loaded {len(mappings)} mappings from inline JSON')
            except json.JSONDecodeError as e:
                self.stdout.write(self.style.ERROR(f'Invalid JSON: {e}'))
                return
        else:
            try:
                with open(input_file, 'r') as f:
                    mappings = json.load(f)
                self.stdout.write(f'Loaded {len(mappings)} mappings from {input_file}')
            except FileNotFoundError:
                self.stdout.write(self.style.ERROR(f'File not found: {input_file}'))
                return
            except json.JSONDecodeError as e:
                self.stdout.write(self.style.ERROR(f'Invalid JSON: {e}'))
                return
        
        # Statistics
        updated = 0
        not_found = 0
        unchanged = 0
        errors = []
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'EMPLOYEE ID IMPORT'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved\n'))
        
        with transaction.atomic():
            for email, emp_id in mappings.items():
                try:
                    profile = UserProfile.objects.select_related('user').get(user__email=email)
                    old_id = profile.employee_id or 'EMPTY'
                    
                    if profile.employee_id == emp_id:
                        unchanged += 1
                        continue
                    
                    if not dry_run:
                        profile.employee_id = emp_id
                        profile.save(update_fields=['employee_id'])
                    
                    self.stdout.write(
                        f'✅ {email:45} | {old_id:20} → {emp_id}'
                    )
                    updated += 1
                    
                except UserProfile.DoesNotExist:
                    self.stdout.write(self.style.WARNING(
                        f'⚠️  {email:45} | USER NOT FOUND'
                    ))
                    not_found += 1
                except Exception as e:
                    error_msg = f'{email}: {str(e)}'
                    errors.append(error_msg)
                    self.stdout.write(self.style.ERROR(f'❌ {error_msg}'))
            
            if dry_run:
                self.stdout.write(self.style.WARNING('\n🔄 Rolling back transaction (dry run)'))
                transaction.set_rollback(True)
        
        # Summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'SUMMARY'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'✅ Updated: {updated}'))
        self.stdout.write(f'   Unchanged: {unchanged}')
        self.stdout.write(self.style.WARNING(f'⚠️  Not found: {not_found}'))
        
        if errors:
            self.stdout.write(self.style.ERROR(f'❌ Errors: {len(errors)}'))
            for err in errors[:5]:  # Show first 5 errors
                self.stdout.write(self.style.ERROR(f'   {err}'))
            if len(errors) > 5:
                self.stdout.write(self.style.ERROR(f'   ... and {len(errors) - 5} more'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n⚠️  DRY RUN - No changes saved'))
            self.stdout.write('   Remove --dry-run to apply changes')
        else:
            self.stdout.write(self.style.SUCCESS(f'\n✅ Successfully updated {updated} employee IDs'))
        
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
