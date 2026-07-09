"""
Export all valid employee_id mappings to JSON for production sync.
"""
import json
from django.core.management.base import BaseCommand
from apps.rbac.models import UserProfile


class Command(BaseCommand):
    help = 'Export employee_id mappings to JSON for production sync'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            type=str,
            default='employee_mappings.json',
            help='Output JSON file path'
        )
        parser.add_argument(
            '--only-numeric',
            action='store_true',
            help='Only export numeric employee IDs (skip placeholders)'
        )

    def handle(self, *args, **options):
        output_file = options['output']
        only_numeric = options['only_numeric']
        
        profiles = UserProfile.objects.select_related('user').all()
        
        mappings = {}
        valid_count = 0
        placeholder_count = 0
        empty_count = 0
        
        for profile in profiles:
            email = profile.user.email
            emp_id = profile.employee_id or ''
            
            if not emp_id or emp_id == '':
                empty_count += 1
                continue
            
            # Check if placeholder format (FIRSTNAME.LASTNAME, etc.)
            is_placeholder = (
                emp_id.isupper() and '.' in emp_id
            ) or not emp_id.isdigit()
            
            if only_numeric and is_placeholder:
                placeholder_count += 1
                continue
            
            # Valid numeric or keeping placeholders
            mappings[email] = emp_id
            if emp_id.isdigit():
                valid_count += 1
            else:
                placeholder_count += 1
        
        # Write to JSON
        with open(output_file, 'w') as f:
            json.dump(mappings, f, indent=2)
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'EMPLOYEE ID MAPPINGS EXPORT'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        self.stdout.write(self.style.SUCCESS(f'✅ Exported to: {output_file}'))
        self.stdout.write(f'   Total mappings exported: {len(mappings)}')
        self.stdout.write(f'   Numeric IDs: {valid_count}')
        
        if not only_numeric:
            self.stdout.write(f'   Placeholder IDs: {placeholder_count}')
        
        self.stdout.write(self.style.WARNING(f'   Skipped (empty): {empty_count}'))
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write('Next steps:')
        self.stdout.write('1. Copy employee_mappings.json to Railway')
        self.stdout.write('2. Run: python manage.py import_employee_mappings')
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
