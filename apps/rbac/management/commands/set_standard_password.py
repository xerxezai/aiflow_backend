"""
Set standard password for all users (for testing/development).
Useful for manual testing when you need to login as different users.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()


class Command(BaseCommand):
    help = 'Set standard password for all users (for testing)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            type=str,
            default='Password123',
            help='Password to set for all users (default: Password123)'
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually update passwords (dry run by default)'
        )
        parser.add_argument(
            '--exclude-superusers',
            action='store_true',
            help='Skip superuser accounts'
        )
        parser.add_argument(
            '--only-active',
            action='store_true',
            help='Only update active users'
        )
        parser.add_argument(
            '--emails',
            nargs='+',
            help='Only update specific email addresses'
        )

    def handle(self, *args, **options):
        password = options['password']
        apply_changes = options['apply']
        exclude_superusers = options['exclude_superusers']
        only_active = options['only_active']
        specific_emails = options['emails']
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        if apply_changes:
            self.stdout.write(self.style.SUCCESS('SETTING STANDARD PASSWORD FOR USERS'))
        else:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be saved'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        # Build queryset
        if specific_emails:
            users = User.objects.filter(email__in=specific_emails)
        else:
            users = User.objects.all()
        
        if only_active:
            users = users.filter(is_active=True)
        
        if exclude_superusers:
            users = users.filter(is_superuser=False)
        
        users = users.order_by('email')
        
        # Show summary
        total_users = users.count()
        self.stdout.write(f'Password: {password}')
        self.stdout.write(f'Total users to update: {total_users}')
        if exclude_superusers:
            self.stdout.write(self.style.WARNING('  Excluding superusers'))
        if only_active:
            self.stdout.write(self.style.WARNING('  Only active users'))
        if specific_emails:
            self.stdout.write(self.style.WARNING(f'  Only specific emails: {len(specific_emails)}'))
        self.stdout.write('')
        
        # Show users
        self.stdout.write(self.style.SUCCESS('USERS TO UPDATE:'))
        for user in users[:20]:  # Show first 20
            status = '✅ Active' if user.is_active else '❌ Inactive'
            superuser = ' 🔑 SUPERUSER' if user.is_superuser else ''
            self.stdout.write(f'  {user.email:50} | {status}{superuser}')
        
        if total_users > 20:
            self.stdout.write(f'  ... and {total_users - 20} more')
        self.stdout.write('')
        
        # Update passwords
        if apply_changes:
            self.stdout.write(self.style.ERROR(f'\n{"="*80}'))
            self.stdout.write(self.style.ERROR('UPDATING PASSWORDS...'))
            self.stdout.write(self.style.ERROR(f'{"="*80}\n'))
            
            updated_count = 0
            failed = []
            
            with transaction.atomic():
                for user in users:
                    try:
                        user.set_password(password)
                        user.save(update_fields=['password'])
                        self.stdout.write(self.style.SUCCESS(f'  ✅ {user.email}'))
                        updated_count += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  ❌ {user.email}: {str(e)[:80]}'))
                        failed.append((user.email, str(e)))
            
            self.stdout.write(self.style.SUCCESS(f'\n✅ Successfully updated {updated_count} passwords'))
            if failed:
                self.stdout.write(self.style.ERROR(f'❌ Failed to update {len(failed)} passwords'))
        
        else:
            self.stdout.write(self.style.WARNING(f'\n⚠️  DRY RUN - Add --apply to update {total_users} passwords'))
        
        # Show usage examples
        if not apply_changes:
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('USAGE EXAMPLES'))
            self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
            self.stdout.write(f'Update all users:')
            self.stdout.write(f'  python manage.py set_standard_password --apply')
            self.stdout.write(f'\nUpdate only active users:')
            self.stdout.write(f'  python manage.py set_standard_password --only-active --apply')
            self.stdout.write(f'\nUpdate specific users:')
            self.stdout.write(f'  python manage.py set_standard_password --emails lira.viaga@rejlers.ae tanzeem.agra@rejlers.ae --apply')
            self.stdout.write(f'\nExclude superusers:')
            self.stdout.write(f'  python manage.py set_standard_password --exclude-superusers --apply')
            self.stdout.write(f'\nCustom password:')
            self.stdout.write(f'  python manage.py set_standard_password --password "TestPass123" --apply')
        
        # Final summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
        self.stdout.write(f'Total users: {total_users}')
        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f'Passwords updated: {updated_count}'))
            self.stdout.write(f'\nAll users can now login with:')
            self.stdout.write(f'  Email: <their_email>')
            self.stdout.write(f'  Password: {password}')
        else:
            self.stdout.write(self.style.WARNING(f'No changes made (dry run)'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
