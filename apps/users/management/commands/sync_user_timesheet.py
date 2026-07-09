"""
Smart User-Timesheet Synchronization Command

Synchronizes data between:
  - User Management (apps.rbac.UserProfile)
  - Timesheet/Biometric System (apps.timesheet.BiometricUserMaster)

Features:
  • Bidirectional sync (users ↔ biometric)
  • Email-based matching
  • Auto-generate employee_id if missing
  • Soft-coded configuration
  • Dry-run mode
  • Conflict resolution strategies

Usage:
    python manage.py sync_user_timesheet --help
    python manage.py sync_user_timesheet --direction both --dry-run
    python manage.py sync_user_timesheet --direction both  # Live sync
    python manage.py sync_user_timesheet --direction users-to-biometric
    python manage.py sync_user_timesheet --direction biometric-to-users
"""

import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import UserProfile
from apps.timesheet.models import BiometricUserMaster
from apps.timesheet.identity import norm_code, norm_email, norm_name

User = get_user_model()
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════
SYNC_CONFIG = {
    # How to generate employee_id for users who don't have one
    'employee_id_strategy': 'email_prefix',  # 'email_prefix' | 'sequential' | 'uuid'
    
    # Email matching: how strict should email matching be?
    'email_matching_strict': False,  # True = exact match only, False = case-insensitive
    
    # Conflict resolution: what to do when data conflicts exist?
    'conflict_resolution': {
        'email': 'userprofile',      # 'userprofile' | 'biometric' | 'skip'
        'name': 'biometric',          # Biometric names are usually more accurate (from HR system)
        'department': 'biometric',    # Departments from biometric are canonical
    },
    
    # Auto-create missing records?
    'auto_create_biometric': True,   # Create BiometricUserMaster for users without one
    'auto_create_userprofile': False, # Create UserProfile for biometric records without one (risky)
    
    # Default values for new BiometricUserMaster records
    'defaults': {
        'designation': 'Staff',
        'department': 'General',
    },
}


class Command(BaseCommand):
    help = 'Smart synchronization between User Management and Timesheet/Biometric systems'

    def add_arguments(self, parser):
        parser.add_argument(
            '--direction',
            type=str,
            default='both',
            choices=['both', 'users-to-biometric', 'biometric-to-users'],
            help='Sync direction (default: both)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying them',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Detailed output',
        )

    def handle(self, *args, **options):
        direction = options['direction']
        dry_run = options['dry_run']
        verbose = options['verbose']

        self.stdout.write()
        self.stdout.write('=' * 70)
        self.stdout.write('🔄 RAD AI - User ↔ Timesheet Synchronization')
        self.stdout.write('=' * 70)
        self.stdout.write(f'Direction:  {direction.upper()}')
        self.stdout.write(f'Mode:       {"DRY RUN" if dry_run else "LIVE"}')
        self.stdout.write('=' * 70)
        self.stdout.write()

        stats = {
            'users_processed': 0,
            'biometric_created': 0,
            'employee_id_assigned': 0,
            'data_updated': 0,
            'skipped': 0,
            'errors': 0,
        }

        try:
            if direction in ['both', 'users-to-biometric']:
                self.stdout.write('📊 Phase 1: Users → Biometric System')
                self.stdout.write('-' * 70)
                self._sync_users_to_biometric(stats, dry_run, verbose)
                self.stdout.write()

            if direction in ['both', 'biometric-to-users']:
                self.stdout.write('📊 Phase 2: Biometric System → Users')
                self.stdout.write('-' * 70)
                self._sync_biometric_to_users(stats, dry_run, verbose)
                self.stdout.write()

            # Summary
            self.stdout.write('=' * 70)
            self.stdout.write('📈 Synchronization Summary')
            self.stdout.write('=' * 70)
            self.stdout.write(f'  Users processed:        {stats["users_processed"]}')
            self.stdout.write(f'  Biometric records created: {stats["biometric_created"]}')
            self.stdout.write(f'  Employee IDs assigned:  {stats["employee_id_assigned"]}')
            self.stdout.write(f'  Data updates:           {stats["data_updated"]}')
            self.stdout.write(f'  Skipped:                {stats["skipped"]}')
            self.stdout.write(f'  Errors:                 {stats["errors"]}')
            self.stdout.write('=' * 70)

            if dry_run:
                self.stdout.write()
                self.stdout.write(self.style.WARNING('⚠️  This was a DRY RUN - no changes were made'))
                self.stdout.write('   Remove --dry-run to apply changes')
            else:
                self.stdout.write()
                self.stdout.write(self.style.SUCCESS('✅ Synchronization complete!'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error: {str(e)}'))
            if verbose:
                import traceback
                traceback.print_exc()
            raise

    def _sync_users_to_biometric(self, stats, dry_run, verbose):
        """Sync User Management → Biometric System"""
        
        # Get all active users
        profiles = UserProfile.objects.filter(is_deleted=False).select_related('user')
        total = profiles.count()
        self.stdout.write(f'Found {total} active user profiles')
        self.stdout.write()

        for idx, profile in enumerate(profiles, 1):
            user = profile.user
            stats['users_processed'] += 1

            if verbose and idx % 50 == 0:
                self.stdout.write(f'Processing {idx}/{total}...')

            try:
                # Step 1: Assign employee_id if missing
                if not profile.employee_id:
                    employee_id = self._generate_employee_id(user, profile)
                    if not dry_run:
                        profile.employee_id = employee_id
                        profile.save()
                    stats['employee_id_assigned'] += 1
                    if verbose:
                        self.stdout.write(f'  ✅ Assigned employee_id: {employee_id} → {user.email}')
                else:
                    employee_id = profile.employee_id

                # Step 2: Create or update BiometricUserMaster
                bio, created = BiometricUserMaster.objects.get_or_create(
                    employee_code=employee_id,
                    defaults={
                        'full_name': f'{user.first_name} {user.last_name}'.strip() or user.username,
                        'office_email': user.email,
                        'designation': profile.job_title or SYNC_CONFIG['defaults']['designation'],
                        'department': profile.department or SYNC_CONFIG['defaults']['department'],
                    }
                )

                if created:
                    if not dry_run:
                        bio.save()
                    stats['biometric_created'] += 1
                    if verbose:
                        self.stdout.write(f'  ➕ Created biometric record: {employee_id} | {bio.full_name}')
                else:
                    # Update existing record if needed
                    updated = False
                    updates = {}

                    # Sync email (based on conflict resolution)
                    if SYNC_CONFIG['conflict_resolution']['email'] == 'userprofile':
                        if bio.office_email != user.email:
                            updates['office_email'] = user.email
                            updated = True

                    # Sync name
                    full_name = f'{user.first_name} {user.last_name}'.strip()
                    if full_name and bio.full_name != full_name:
                        if SYNC_CONFIG['conflict_resolution']['name'] == 'userprofile':
                            updates['full_name'] = full_name
                            updated = True

                    # Sync department
                    if profile.department and bio.department != profile.department:
                        if SYNC_CONFIG['conflict_resolution']['department'] == 'userprofile':
                            updates['department'] = profile.department
                            updated = True

                    # Sync designation/job_title
                    if profile.job_title and bio.designation != profile.job_title:
                        updates['designation'] = profile.job_title
                        updated = True

                    if updated and not dry_run:
                        for field, value in updates.items():
                            setattr(bio, field, value)
                        bio.save()
                        stats['data_updated'] += 1
                        if verbose:
                            self.stdout.write(f'  🔄 Updated biometric: {employee_id} | {list(updates.keys())}')

            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(self.style.ERROR(f'  ❌ Error processing {user.email}: {str(e)[:100]}'))

    def _sync_biometric_to_users(self, stats, dry_run, verbose):
        """Sync Biometric System → User Management"""
        
        biometric_records = BiometricUserMaster.objects.all()
        total = biometric_records.count()
        
        if total == 0:
            self.stdout.write('No biometric records found to sync')
            return
        
        self.stdout.write(f'Found {total} biometric records')
        self.stdout.write()

        for idx, bio in enumerate(biometric_records, 1):
            if verbose and idx % 50 == 0:
                self.stdout.write(f'Processing {idx}/{total}...')

            try:
                # Try to find matching UserProfile by employee_id
                profile = UserProfile.objects.filter(
                    employee_id=bio.employee_code,
                    is_deleted=False
                ).first()

                if not profile and bio.office_email:
                    # Try to find by email
                    user = User.objects.filter(email__iexact=bio.office_email).first()
                    if user:
                        profile = UserProfile.objects.filter(
                            user=user,
                            is_deleted=False
                        ).first()

                if profile:
                    # Update existing profile with biometric data
                    updated = False
                    updates = {}

                    # Update employee_id if missing
                    if not profile.employee_id:
                        updates['employee_id'] = bio.employee_code
                        updated = True

                    # Update department (biometric is canonical)
                    if bio.department and profile.department != bio.department:
                        if SYNC_CONFIG['conflict_resolution']['department'] == 'biometric':
                            updates['department'] = bio.department
                            updated = True

                    # Update job_title/designation
                    if bio.designation and profile.job_title != bio.designation:
                        updates['job_title'] = bio.designation
                        updated = True

                    if updated and not dry_run:
                        for field, value in updates.items():
                            setattr(profile, field, value)
                        profile.save()
                        stats['data_updated'] += 1
                        if verbose:
                            self.stdout.write(f'  🔄 Updated profile: {profile.user.email} | {list(updates.keys())}')
                else:
                    # No matching profile found
                    stats['skipped'] += 1
                    if verbose:
                        self.stdout.write(f'  ⏭️  Skipped (no matching user): {bio.employee_code} | {bio.office_email}')

            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(self.style.ERROR(f'  ❌ Error processing {bio.employee_code}: {str(e)[:100]}'))

    def _generate_employee_id(self, user, profile):
        """Generate employee_id for a user"""
        strategy = SYNC_CONFIG['employee_id_strategy']

        if strategy == 'email_prefix':
            # Use email username as employee_id (e.g., john.doe@company.com → john.doe)
            return user.email.split('@')[0].upper()
        
        elif strategy == 'sequential':
            # Find highest existing employee_id that's numeric and increment
            existing_ids = UserProfile.objects.filter(
                employee_id__regex=r'^\d+$'
            ).values_list('employee_id', flat=True)
            max_id = max([int(eid) for eid in existing_ids] + [1000])
            return str(max_id + 1)
        
        elif strategy == 'uuid':
            # Use first 8 chars of user UUID
            return str(profile.id)[:8].upper()
        
        else:
            # Fallback to email prefix
            return user.email.split('@')[0].upper()
