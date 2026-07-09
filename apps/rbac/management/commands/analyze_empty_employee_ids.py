"""
Analyze and categorize users with EMPTY employee_id values.
Helps identify which users need employee IDs vs deleted/test accounts.
"""
import re
from django.core.management.base import BaseCommand
from apps.rbac.models import UserProfile


class Command(BaseCommand):
    help = 'Analyze users with EMPTY employee_id and categorize them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix-deleted',
            action='store_true',
            help='Set employee_id to "DELETED" for deleted accounts (soft-delete marker)'
        )
        parser.add_argument(
            '--fix-test',
            action='store_true',
            help='Set employee_id to "TEST_ACCOUNT" for test accounts'
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually apply the fixes (dry run by default)'
        )

    def handle(self, *args, **options):
        fix_deleted = options['fix_deleted']
        fix_test = options['fix_test']
        apply_changes = options['apply']
        
        # Get all users with EMPTY employee_id
        profiles = UserProfile.objects.select_related('user').filter(
            employee_id__isnull=True
        ) | UserProfile.objects.select_related('user').filter(
            employee_id=''
        )
        
        # Categorize users
        deleted_accounts = []
        test_accounts = []
        active_users = []
        
        for profile in profiles:
            email = profile.user.email
            
            # Check if deleted (email contains .deleted_XXX)
            if re.search(r'\.deleted_\d+$', email):
                deleted_accounts.append(profile)
            # Check if test account
            elif any(test_marker in email.lower() for test_marker in [
                'test@', '@test.', 'demo.', 'newuser@', 'admin@rejlers.com',
                'hello@', 'info@rejlers.com', 'tameem@', 'shareeq@'
            ]):
                test_accounts.append(profile)
            # Otherwise, assume active user needing employee_id
            else:
                active_users.append(profile)
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'EMPTY EMPLOYEE_ID ANALYSIS'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        # Summary
        total_empty = len(deleted_accounts) + len(test_accounts) + len(active_users)
        self.stdout.write(f'Total EMPTY employee_id: {total_empty}')
        self.stdout.write(self.style.ERROR(f'  Deleted accounts: {len(deleted_accounts)}'))
        self.stdout.write(self.style.WARNING(f'  Test/System accounts: {len(test_accounts)}'))
        self.stdout.write(self.style.WARNING(f'  Active users needing IDs: {len(active_users)}\n'))
        
        # Show deleted accounts (sample)
        if deleted_accounts:
            self.stdout.write(self.style.ERROR(f'❌ DELETED ACCOUNTS ({len(deleted_accounts)}):'))
            for profile in deleted_accounts[:10]:
                self.stdout.write(f'  {profile.user.email}')
            if len(deleted_accounts) > 10:
                self.stdout.write(f'  ... and {len(deleted_accounts) - 10} more')
            self.stdout.write('')
        
        # Show test accounts
        if test_accounts:
            self.stdout.write(self.style.WARNING(f'⚠️  TEST/SYSTEM ACCOUNTS ({len(test_accounts)}):'))
            for profile in test_accounts[:15]:
                self.stdout.write(f'  {profile.user.email}')
            if len(test_accounts) > 15:
                self.stdout.write(f'  ... and {len(test_accounts) - 15} more')
            self.stdout.write('')
        
        # Show active users needing employee_id
        if active_users:
            self.stdout.write(self.style.WARNING(f'🔍 ACTIVE USERS NEEDING EMPLOYEE_ID ({len(active_users)}):'))
            for profile in active_users[:20]:
                name = f"{profile.user.first_name} {profile.user.last_name}".strip() or profile.user.username
                self.stdout.write(f'  {profile.user.email:50} | Name: {name}')
            if len(active_users) > 20:
                self.stdout.write(f'  ... and {len(active_users) - 20} more')
            self.stdout.write('')
        
        # Apply fixes if requested
        if fix_deleted and deleted_accounts:
            marker = "DELETED"
            self.stdout.write(self.style.WARNING(f'\n{"="*80}'))
            if apply_changes:
                self.stdout.write(self.style.WARNING(f'APPLYING FIX: Setting deleted accounts to "{marker}"'))
            else:
                self.stdout.write(self.style.WARNING(f'DRY RUN: Would set deleted accounts to "{marker}"'))
            self.stdout.write(self.style.WARNING(f'{"="*80}\n'))
            
            updated = 0
            for profile in deleted_accounts:
                if apply_changes:
                    profile.employee_id = marker
                    profile.save(update_fields=['employee_id'])
                self.stdout.write(f'  ✅ {profile.user.email} → {marker}')
                updated += 1
            
            if apply_changes:
                self.stdout.write(self.style.SUCCESS(f'\n✅ Updated {updated} deleted accounts'))
            else:
                self.stdout.write(self.style.WARNING(f'\n⚠️  DRY RUN - Add --apply to save changes'))
        
        if fix_test and test_accounts:
            marker = "TEST_ACCOUNT"
            self.stdout.write(self.style.WARNING(f'\n{"="*80}'))
            if apply_changes:
                self.stdout.write(self.style.WARNING(f'APPLYING FIX: Setting test accounts to "{marker}"'))
            else:
                self.stdout.write(self.style.WARNING(f'DRY RUN: Would set test accounts to "{marker}"'))
            self.stdout.write(self.style.WARNING(f'{"="*80}\n'))
            
            updated = 0
            for profile in test_accounts:
                if apply_changes:
                    profile.employee_id = marker
                    profile.save(update_fields=['employee_id'])
                self.stdout.write(f'  ✅ {profile.user.email} → {marker}')
                updated += 1
            
            if apply_changes:
                self.stdout.write(self.style.SUCCESS(f'\n✅ Updated {updated} test accounts'))
            else:
                self.stdout.write(self.style.WARNING(f'\n⚠️  DRY RUN - Add --apply to save changes'))
        
        # Recommendations
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'RECOMMENDATIONS'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        if deleted_accounts:
            self.stdout.write(f'1. Deleted accounts ({len(deleted_accounts)}):')
            self.stdout.write(f'   Run: python manage.py analyze_empty_employee_ids --fix-deleted --apply')
            self.stdout.write(f'   This marks them as "DELETED" so they\'re not confused with active users\n')
        
        if test_accounts:
            self.stdout.write(f'2. Test/System accounts ({len(test_accounts)}):')
            self.stdout.write(f'   Run: python manage.py analyze_empty_employee_ids --fix-test --apply')
            self.stdout.write(f'   This marks them as "TEST_ACCOUNT" to distinguish from real employees\n')
        
        if active_users:
            self.stdout.write(f'3. Active users needing employee_id ({len(active_users)}):')
            self.stdout.write(f'   Option A: Run fuzzy sync with lower threshold (may get more matches):')
            self.stdout.write(f'     python manage.py sync_employee_ids --threshold 60 --dry-run')
            self.stdout.write(f'   Option B: Manually map these users to biometric employee codes')
            self.stdout.write(f'   Option C: These users might not exist in biometric system (contractors, etc.)\n')
        
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
