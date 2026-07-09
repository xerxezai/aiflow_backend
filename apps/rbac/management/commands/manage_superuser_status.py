"""
Django management command to manage superuser status for users.

Usage:
    # Promote user to superuser
    python manage.py manage_superuser_status --promote tanzeem.agra@rejlers.ae

    # Demote user from superuser
    python manage.py manage_superuser_status --demote debasis.sana@rejlers.ae

    # Swap superuser status between two users
    python manage.py manage_superuser_status --swap tanzeem.agra@rejlers.ae debasis.sana@rejlers.ae

    # Apply changes (dry-run by default)
    python manage.py manage_superuser_status --swap tanzeem.agra@rejlers.ae debasis.sana@rejlers.ae --apply

Author: RAD AI Engineering Team
Created: 2026-06-26
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()


class Command(BaseCommand):
    help = "Manage superuser status - promote, demote, or swap superuser privileges"

    def add_arguments(self, parser):
        # Action arguments
        parser.add_argument(
            '--promote',
            nargs='+',
            help='Email(s) of user(s) to promote to superuser'
        )
        parser.add_argument(
            '--demote',
            nargs='+',
            help='Email(s) of user(s) to demote from superuser'
        )
        parser.add_argument(
            '--swap',
            nargs=2,
            metavar=('EMAIL1', 'EMAIL2'),
            help='Swap superuser status between two users (EMAIL1 becomes super, EMAIL2 becomes regular)'
        )
        
        # Execution control
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply changes (default is dry-run)'
        )
        
        # Filters
        parser.add_argument(
            '--check-active',
            action='store_true',
            default=True,
            help='Only modify active users (default: True)'
        )

    def handle(self, *args, **options):
        promote_emails = options.get('promote') or []
        demote_emails = options.get('demote') or []
        swap_emails = options.get('swap') or []
        apply = options.get('apply', False)
        check_active = options.get('check_active', True)

        # Normalize emails (case-insensitive)
        promote_emails = [email.lower().strip() for email in promote_emails]
        demote_emails = [email.lower().strip() for email in demote_emails]
        swap_emails = [email.lower().strip() for email in swap_emails]

        # Validate input
        if not promote_emails and not demote_emails and not swap_emails:
            self.stdout.write(self.style.ERROR("❌ No action specified. Use --promote, --demote, or --swap"))
            return

        self.stdout.write("=" * 80)
        if apply:
            self.stdout.write(self.style.SUCCESS("APPLYING SUPERUSER STATUS CHANGES"))
        else:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes will be saved"))
        self.stdout.write("=" * 80)

        changes = []

        # Handle SWAP action
        if swap_emails:
            if len(swap_emails) != 2:
                self.stdout.write(self.style.ERROR("❌ --swap requires exactly 2 email addresses"))
                return
            
            promote_email, demote_email = swap_emails
            self.stdout.write(f"\n🔄 SWAP MODE:")
            self.stdout.write(f"   Promote to superuser:  {promote_email}")
            self.stdout.write(f"   Demote from superuser: {demote_email}\n")
            
            promote_emails.append(promote_email)
            demote_emails.append(demote_email)

        # Process PROMOTIONS
        if promote_emails:
            self.stdout.write(f"\n{'=' * 80}")
            self.stdout.write(self.style.SUCCESS(f"⬆️  PROMOTIONS TO SUPERUSER ({len(promote_emails)} users)"))
            self.stdout.write(f"{'=' * 80}\n")
            
            for email in promote_emails:
                try:
                    user = User.objects.get(email__iexact=email)
                    
                    # Check if already superuser
                    if user.is_superuser:
                        self.stdout.write(f"  ⚠️  {email:45} | Already superuser (no change)")
                        continue
                    
                    # Check if active
                    if check_active and not user.is_active:
                        self.stdout.write(f"  ❌ {email:45} | Inactive user (skipped)")
                        continue
                    
                    changes.append({
                        'user': user,
                        'action': 'promote',
                        'email': email,
                        'old_status': 'Regular User',
                        'new_status': 'Superuser'
                    })
                    
                    self.stdout.write(
                        f"  ✅ {email:45} | "
                        f"{self.style.WARNING('Regular User')} → {self.style.SUCCESS('SUPERUSER')}"
                    )
                    
                except User.DoesNotExist:
                    self.stdout.write(f"  ❌ {email:45} | User not found")

        # Process DEMOTIONS
        if demote_emails:
            self.stdout.write(f"\n{'=' * 80}")
            self.stdout.write(self.style.WARNING(f"⬇️  DEMOTIONS FROM SUPERUSER ({len(demote_emails)} users)"))
            self.stdout.write(f"{'=' * 80}\n")
            
            for email in demote_emails:
                try:
                    user = User.objects.get(email__iexact=email)
                    
                    # Check if not superuser
                    if not user.is_superuser:
                        self.stdout.write(f"  ⚠️  {email:45} | Not a superuser (no change)")
                        continue
                    
                    # Check if active
                    if check_active and not user.is_active:
                        self.stdout.write(f"  ❌ {email:45} | Inactive user (skipped)")
                        continue
                    
                    changes.append({
                        'user': user,
                        'action': 'demote',
                        'email': email,
                        'old_status': 'Superuser',
                        'new_status': 'Regular User'
                    })
                    
                    self.stdout.write(
                        f"  ✅ {email:45} | "
                        f"{self.style.SUCCESS('SUPERUSER')} → {self.style.WARNING('Regular User')}"
                    )
                    
                except User.DoesNotExist:
                    self.stdout.write(f"  ❌ {email:45} | User not found")

        # Summary
        self.stdout.write(f"\n{'=' * 80}")
        self.stdout.write("SUMMARY")
        self.stdout.write(f"{'=' * 80}\n")
        self.stdout.write(f"  Total changes: {len(changes)}")
        
        promotions = [c for c in changes if c['action'] == 'promote']
        demotions = [c for c in changes if c['action'] == 'demote']
        
        self.stdout.write(f"  Promotions:    {len(promotions)}")
        self.stdout.write(f"  Demotions:     {len(demotions)}\n")

        # Apply changes if requested
        if apply and changes:
            self.stdout.write(self.style.SUCCESS("\n🚀 APPLYING CHANGES...\n"))
            
            try:
                with transaction.atomic():
                    for change in changes:
                        user = change['user']
                        
                        if change['action'] == 'promote':
                            user.is_superuser = True
                            user.is_staff = True  # Superusers should also be staff
                        elif change['action'] == 'demote':
                            user.is_superuser = False
                            # Keep is_staff as is (they might still need staff access)
                        
                        user.save(update_fields=['is_superuser', 'is_staff'])
                        
                        self.stdout.write(
                            f"  ✅ {change['email']:45} | "
                            f"{change['old_status']} → {change['new_status']}"
                        )
                
                self.stdout.write(f"\n{self.style.SUCCESS(f'✅ Successfully updated {len(changes)} users')}\n")
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"\n❌ Error: {str(e)}\n"))
                raise
        
        elif not apply:
            self.stdout.write(self.style.WARNING("\n⚠️  DRY RUN - Add --apply to save changes\n"))
        
        elif not changes:
            self.stdout.write(self.style.WARNING("\n⚠️  No changes to apply\n"))

        # Usage examples
        if not apply:
            self.stdout.write(f"\n{'=' * 80}")
            self.stdout.write("USAGE EXAMPLES")
            self.stdout.write(f"{'=' * 80}\n")
            self.stdout.write("Promote single user:")
            self.stdout.write("  python manage.py manage_superuser_status --promote tanzeem.agra@rejlers.ae --apply\n")
            self.stdout.write("Demote single user:")
            self.stdout.write("  python manage.py manage_superuser_status --demote debasis.sana@rejlers.ae --apply\n")
            self.stdout.write("Swap superuser status:")
            self.stdout.write("  python manage.py manage_superuser_status --swap tanzeem.agra@rejlers.ae debasis.sana@rejlers.ae --apply\n")
            self.stdout.write("Promote multiple users:")
            self.stdout.write("  python manage.py manage_superuser_status --promote user1@example.com user2@example.com --apply\n")
            self.stdout.write(f"{'=' * 80}\n")
