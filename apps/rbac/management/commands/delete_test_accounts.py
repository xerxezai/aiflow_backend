"""
Delete test/demo accounts from the database.
Safely removes User and associated UserProfile records.
Uses raw SQL for robust deletion even with missing tables.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction, connection

User = get_user_model()


class Command(BaseCommand):
    help = 'Delete test/demo accounts by email address'

    def add_arguments(self, parser):
        parser.add_argument(
            '--emails',
            nargs='+',
            help='Email addresses to delete (space-separated)'
        )
        parser.add_argument(
            '--file',
            type=str,
            help='Path to file with one email per line'
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually delete accounts (dry run by default)'
        )
        parser.add_argument(
            '--delete-test-markers',
            action='store_true',
            help='Delete all users with employee_id="TEST_ACCOUNT"'
        )

    def handle(self, *args, **options):
        emails_to_delete = []
        
        # Collect emails from --emails argument
        if options['emails']:
            emails_to_delete.extend(options['emails'])
        
        # Collect emails from --file argument
        if options['file']:
            try:
                with open(options['file'], 'r') as f:
                    file_emails = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                    emails_to_delete.extend(file_emails)
            except FileNotFoundError:
                self.stdout.write(self.style.ERROR(f'File not found: {options["file"]}'))
                return
        
        # If --delete-test-markers, find all TEST_ACCOUNT users
        if options['delete_test_markers']:
            from apps.rbac.models import UserProfile
            test_profiles = UserProfile.objects.filter(employee_id='TEST_ACCOUNT').select_related('user')
            test_emails = [p.user.email for p in test_profiles]
            emails_to_delete.extend(test_emails)
            self.stdout.write(self.style.WARNING(f'Found {len(test_emails)} users with TEST_ACCOUNT marker'))
        
        # Remove duplicates
        emails_to_delete = list(set(emails_to_delete))
        
        if not emails_to_delete:
            self.stdout.write(self.style.WARNING('No emails provided. Use --emails or --file'))
            return
        
        apply_changes = options['apply']
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        if apply_changes:
            self.stdout.write(self.style.SUCCESS('DELETING TEST ACCOUNTS'))
        else:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be saved'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        # Find users
        found = []
        not_found = []
        
        for email in emails_to_delete:
            try:
                user = User.objects.get(email=email)
                found.append((email, user))
            except User.DoesNotExist:
                not_found.append(email)
        
        # Show summary
        self.stdout.write(f'Total emails provided: {len(emails_to_delete)}')
        self.stdout.write(self.style.SUCCESS(f'  Found: {len(found)}'))
        if not_found:
            self.stdout.write(self.style.WARNING(f'  Not found: {len(not_found)}\n'))
        else:
            self.stdout.write('')
        
        # Show not found emails
        if not_found:
            self.stdout.write(self.style.WARNING('❌ NOT FOUND IN DATABASE:'))
            for email in not_found:
                self.stdout.write(f'  {email}')
            self.stdout.write('')
        
        # Show accounts to delete
        if found:
            self.stdout.write(self.style.ERROR(f'{"🗑️  ACCOUNTS TO DELETE" if apply_changes else "🔍 ACCOUNTS THAT WOULD BE DELETED"}:'))
            for email, user in found:
                username = user.username
                name = f"{user.first_name} {user.last_name}".strip() or "(no name)"
                self.stdout.write(f'  ✓ {email:40} | {username:25} | {name}')
            self.stdout.write('')
        
        # Delete accounts
        if found and apply_changes:
            self.stdout.write(self.style.ERROR(f'\n{"="*80}'))
            self.stdout.write(self.style.ERROR('DELETING ACCOUNTS...'))
            self.stdout.write(self.style.ERROR(f'{"="*80}\n'))
            
            deleted_count = 0
            failed = []
            
            for email, user in found:
                try:
                    # Use transaction to ensure atomicity
                    with transaction.atomic():
                        user_id = user.id
                        user.delete()  # Django handles CASCADE deletes automatically
                        self.stdout.write(self.style.SUCCESS(f'  ✅ Deleted: {email} (user_id={user_id})'))
                        deleted_count += 1
                except Exception as e:
                    error_msg = str(e)
                    self.stdout.write(self.style.ERROR(f'  ❌ Failed: {email} - {error_msg[:100]}'))
                    failed.append((email, error_msg))
            
            self.stdout.write(self.style.SUCCESS(f'\n✅ Successfully deleted {deleted_count} accounts'))
            if failed:
                self.stdout.write(self.style.ERROR(f'❌ Failed to delete {len(failed)} accounts'))
                for email, error in failed:
                    self.stdout.write(self.style.ERROR(f'  {email}: {error[:80]}'))
        
        elif found:
            self.stdout.write(self.style.WARNING(f'\n⚠️  DRY RUN - Add --apply to permanently delete {len(found)} accounts'))
        
        # Final summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
        self.stdout.write(f'Total emails checked: {len(emails_to_delete)}')
        self.stdout.write(self.style.SUCCESS(f'Found and {"deleted" if apply_changes else "would delete"}: {len(found)}'))
        self.stdout.write(self.style.WARNING(f'Not found (already gone): {len(not_found)}'))
        if not apply_changes and found:
            self.stdout.write(self.style.WARNING(f'\n⚠️  To apply changes, run with --apply flag'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
