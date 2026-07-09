"""
Diagnostic command to check employee_id configuration status.
Shows which users have invalid/missing employee_id values.
"""
from django.core.management.base import BaseCommand
from apps.rbac.models import UserProfile


class Command(BaseCommand):
    help = 'Diagnose employee_id configuration issues'

    def handle(self, *args, **options):
        profiles = UserProfile.objects.select_related('user').all()
        
        valid = []
        invalid = []
        missing = []
        
        for profile in profiles:
            emp_id = profile.employee_id or ''
            email = profile.user.email
            
            # Check validity based on frontend logic:
            # needsConfig = !empId || empId === '' || empId.includes('@') || empId.startsWith('EMP')
            if not emp_id or emp_id == '':
                missing.append((email, 'EMPTY'))
            elif '@' in emp_id:
                invalid.append((email, emp_id, 'Contains @'))
            elif emp_id.startswith('EMP'):
                invalid.append((email, emp_id, 'Starts with EMP'))
            elif emp_id.isupper() and '.' in emp_id:
                # Placeholder format: FIRSTNAME.LASTNAME
                invalid.append((email, emp_id, 'Placeholder format'))
            elif not emp_id.isdigit():
                # Some invalid format
                invalid.append((email, emp_id, 'Non-numeric'))
            else:
                valid.append((email, emp_id))
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'EMPLOYEE ID DIAGNOSTIC REPORT'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        self.stdout.write(self.style.SUCCESS(f'✅ VALID ({len(valid)}):'))
        for email, emp_id in sorted(valid)[:10]:  # Show first 10
            self.stdout.write(f'  {email:45} → {emp_id}')
        if len(valid) > 10:
            self.stdout.write(f'  ... and {len(valid) - 10} more\n')
        else:
            self.stdout.write('')
        
        self.stdout.write(self.style.WARNING(f'⚠️  INVALID ({len(invalid)}):'))
        for email, emp_id, reason in sorted(invalid):
            self.stdout.write(self.style.WARNING(f'  {email:45} → {emp_id:30} ({reason})'))
        
        self.stdout.write(self.style.ERROR(f'\n❌ MISSING ({len(missing)}):'))
        for email, status in sorted(missing):
            self.stdout.write(self.style.ERROR(f'  {email:45} → {status}'))
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'SUMMARY:'))
        self.stdout.write(f'  Total Users: {len(profiles)}')
        self.stdout.write(self.style.SUCCESS(f'  Valid: {len(valid)}'))
        self.stdout.write(self.style.WARNING(f'  Invalid: {len(invalid)}'))
        self.stdout.write(self.style.ERROR(f'  Missing: {len(missing)}'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        # Specific checks for known users
        known_emails = [
            'lira.viaga@rejlers.ae',
            'tanzeem.agra@rejlers.ae',
            'mohammed.agra@rejlers.ae',
        ]
        
        self.stdout.write(self.style.SUCCESS('CHECKING SPECIFIC USERS:'))
        for email in known_emails:
            try:
                profile = UserProfile.objects.select_related('user').get(user__email=email)
                emp_id = profile.employee_id or 'MISSING'
                self.stdout.write(f'  {email:45} → {emp_id}')
            except UserProfile.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'  {email:45} → USER NOT FOUND'))
        
        return
