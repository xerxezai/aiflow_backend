"""
Django Management Command: Migrate Employee Data

Migrates historical employee data from legacy tables to EmployeeMaster.

LEGACY TABLES:
- users + user_profiles
- finance_employee_salary_info  
- onboarding_record (for photo data)

NEW TABLE:
- hr_employee_master

USAGE:
    python manage.py migrate_employee_data --dry-run  # Test without changes
    python manage.py migrate_employee_data --batch-size 100  # Migrate in batches
    python manage.py migrate_employee_data --force  # Force re-migration (overwrites)
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.hr_core.models import EmployeeMaster
from apps.users.models import UserProfile

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    help = 'Migrate employee data from legacy tables to EmployeeMaster'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate migration without making changes',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of records to process per batch (default: 50)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-migration even if employee already exists',
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Migrate specific employee by email only',
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        force = options['force']
        specific_email = options.get('email')
        
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('Employee Data Migration to EmployeeMaster'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        
        # Get all users with profiles
        users_query = User.objects.select_related('profile').all()
        
        if specific_email:
            users_query = users_query.filter(email=specific_email)
            self.stdout.write(f"Filtering to user: {specific_email}")
        
        total_users = users_query.count()
        self.stdout.write(f"Total users to process: {total_users}")
        
        if total_users == 0:
            self.stdout.write(self.style.WARNING('No users found to migrate'))
            return
        
        # Statistics
        migrated_count = 0
        skipped_count = 0
        error_count = 0
        updated_count = 0
        
        # Process in batches
        for batch_start in range(0, total_users, batch_size):
            batch_end = min(batch_start + batch_size, total_users)
            batch_users = users_query[batch_start:batch_end]
            
            self.stdout.write(f"\nProcessing batch {batch_start + 1} to {batch_end}...")
            
            for user in batch_users:
                try:
                    result = self.migrate_user(user, dry_run=dry_run, force=force)
                    
                    if result == 'migrated':
                        migrated_count += 1
                        self.stdout.write(self.style.SUCCESS(f"  ✅ Migrated: {user.email}"))
                    elif result == 'updated':
                        updated_count += 1
                        self.stdout.write(self.style.SUCCESS(f"  🔄 Updated: {user.email}"))
                    elif result == 'skipped':
                        skipped_count += 1
                        self.stdout.write(f"  ⏭️  Skipped: {user.email} (already exists)")
                    
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(f"  ❌ Error: {user.email} - {str(e)}"))
                    logger.error(f"Migration error for {user.email}: {e}", exc_info=True)
        
        # Final summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 80))
        self.stdout.write(self.style.SUCCESS('Migration Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(f"Total users processed: {total_users}")
        self.stdout.write(self.style.SUCCESS(f"✅ Successfully migrated: {migrated_count}"))
        self.stdout.write(self.style.SUCCESS(f"🔄 Updated existing: {updated_count}"))
        self.stdout.write(self.style.WARNING(f"⏭️  Skipped (already exists): {skipped_count}"))
        self.stdout.write(self.style.ERROR(f"❌ Errors: {error_count}"))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 DRY RUN COMPLETE - No changes were made'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ MIGRATION COMPLETE'))
    
    @transaction.atomic
    def migrate_user(self, user, dry_run=False, force=False):
        """
        Migrate a single user to EmployeeMaster.
        
        Returns:
            'migrated': New employee created
            'updated': Existing employee updated
            'skipped': Already exists and force=False
        """
        # Check if already migrated
        existing = EmployeeMaster.objects.filter(user=user).first()
        
        if existing and not force:
            return 'skipped'
        
        # Get profile data
        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            profile = None
        
        # Get finance data if exists
        finance_data = self._get_finance_data(user)
        
        # Get onboarding data if exists
        onboarding_data = self._get_onboarding_data(user)
        
        # Prepare employee data
        employee_data = {
            'user': user,
            'email': user.email,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
        }
        
        # From UserProfile
        if profile:
            employee_data.update({
                'preferred_given_name': profile.preferred_given_name or '',
                'initials': profile.initials or '',
                'employee_number': profile.employee_number or self._generate_employee_number(user),
                'employment_id': profile.employment_id or '',
                'candidate_id': profile.candidate_id or '',
                'account_name': profile.account_name or '',
                'date_of_birth': profile.date_of_birth,
                'department': profile.business_unit or '',
                'division': profile.division or '',
                'business_unit': profile.business_unit or '',
                'business_area': profile.business_area or '',
                'office': profile.office or '',
                'job_title_uae': profile.job_title_uae or '',
                'job_title_finland': profile.job_title_finland or '',
                'country': profile.country or '',
                'city': profile.city or '',
                'address': profile.address or '',
                'postal_code': profile.postal_code or '',
                'protected_identity': profile.protected_identity,
                'is_test_person': profile.is_test_person,
                'not_signed': profile.not_signed,
            })
            
            # Manager relationship (will be linked after all employees migrated)
            if profile.manager_id:
                manager_employee = EmployeeMaster.objects.filter(user_id=profile.manager_id).first()
                if manager_employee:
                    employee_data['manager'] = manager_employee
        
        # From finance_employee_salary_info
        if finance_data:
            employee_data.update({
                'employee_code': finance_data.get('employee_id', employee_data.get('employee_number', '')),
                'current_base_salary': finance_data.get('base_salary'),
                'designation': finance_data.get('designation', ''),
                'department': finance_data.get('department', employee_data.get('department', '')),
                'join_date': finance_data.get('join_date') or timezone.now().date(),
                'bank_account_number': finance_data.get('bank_account', ''),
                'iban': finance_data.get('iban', ''),
                'swift_code': finance_data.get('swift_code', ''),
                'tax_id': finance_data.get('tax_id', ''),
                'bank_name': finance_data.get('bank_name', ''),
            })
        else:
            # Default join date if not from finance
            employee_data['join_date'] = user.date_joined.date()
        
        # From onboarding_record (additional info & photo if available)
        if onboarding_data:
            employee_data.update({
                'branch': onboarding_data.get('branch', ''),
            })
            # Photo data (if available in database)
            if onboarding_data.get('photo_file_path'):
                employee_data.update({
                    'photo_file_path': onboarding_data.get('photo_file_path', ''),
                    'photo_url': onboarding_data.get('photo_url', ''),
                    'photo_file_size': onboarding_data.get('photo_file_size'),
                    'photo_mime_type': onboarding_data.get('photo_mime_type', ''),
                })
            # Override designation if from onboarding
            if onboarding_data.get('position'):
                employee_data['designation'] = onboarding_data['position']
            # Use onboarding employee_id if available
            if onboarding_data.get('employee_id') and not employee_data.get('employee_code'):
                employee_data['employee_code'] = onboarding_data['employee_id']
        
        # Ensure employee_number is never empty
        if not employee_data.get('employee_number'):
            employee_data['employee_number'] = self._generate_employee_number(user)
        
        # Generate employee_code and emp_code if missing or empty
        if not employee_data.get('employee_code'):
            employee_data['employee_code'] = employee_data['employee_number']
        
        if not employee_data.get('emp_code'):
            employee_data['emp_code'] = employee_data['employee_code'][:10]
        
        # Set employment status
        if not employee_data.get('employment_status'):
            if finance_data and finance_data.get('status'):
                employee_data['employment_status'] = finance_data['status']
            else:
                employee_data['employment_status'] = 'active'
        
        # Create or update in database
        if dry_run:
            self.stdout.write(f"    Would create/update EmployeeMaster for {user.email}")
            self.stdout.write(f"      employee_number: {employee_data.get('employee_number')}")
            self.stdout.write(f"      employee_code: {employee_data.get('employee_code')}")
            self.stdout.write(f"      department: {employee_data.get('department')}")
            return 'migrated' if not existing else 'updated'
        
        if existing:
            # Update existing
            for key, value in employee_data.items():
                if key != 'user':  # Don't update user FK
                    setattr(existing, key, value)
            existing.save()
            return 'updated'
        else:
            # Create new
            EmployeeMaster.objects.create(**employee_data)
            return 'migrated'
    
    def _get_finance_data(self, user):
        """Get finance data if exists."""
        try:
            from apps.finance.models import EmployeeSalaryInfo
            finance_obj = EmployeeSalaryInfo.objects.filter(user_id=user.id).first()
            if finance_obj:
                return {
                    'employee_id': finance_obj.employee_id,
                    'base_salary': finance_obj.basic_salary,
                    'designation': finance_obj.designation,
                    'department': finance_obj.department,
                    'join_date': finance_obj.join_date,
                    'bank_account': finance_obj.account_number,
                    'iban': finance_obj.iban,
                    'swift_code': finance_obj.swift_code,
                    'tax_id': finance_obj.tax_id,
                    'bank_name': finance_obj.bank_name,
                    'status': 'active' if finance_obj.is_active else 'inactive',
                }
        except (ImportError, AttributeError):
            pass
        return None
    
    def _get_onboarding_data(self, user):
        """Get onboarding data if exists."""
        try:
            from apps.onboarding.models import OnboardingRecord
            from django.db import connection
            
            # Check which fields exist in database (defensive programming)
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'onboarding_record'
                """)
                existing_columns = [row[0] for row in cursor.fetchall()]
            
            # Always safe fields
            safe_fields = ['branch', 'position', 'employee_id', 'employee_email', 'user_id']
            photo_fields = ['photo_file_path', 'photo_url', 'photo_file_size', 'photo_mime_type']
            
            # Build query fields from what exists
            query_fields = [f for f in safe_fields if f in existing_columns]
            photo_fields_exist = all(f in existing_columns for f in photo_fields)
            if photo_fields_exist:
                query_fields.extend(photo_fields)
            
            # Query using only existing fields to avoid SQL errors
            onboarding_obj = None
            if 'user_id' in existing_columns:
                onboarding_obj = OnboardingRecord.objects.filter(user_id=user.id).values(*query_fields).first()
            if not onboarding_obj and 'employee_email' in existing_columns:
                onboarding_obj = OnboardingRecord.objects.filter(employee_email=user.email).values(*query_fields).first()
            
            if onboarding_obj:
                data = {
                    'branch': onboarding_obj.get('branch', ''),
                    'position': onboarding_obj.get('position', ''),
                    'employee_id': onboarding_obj.get('employee_id', ''),
                }
                
                # Add photo fields if they exist in database
                if photo_fields_exist:
                    data.update({
                        'photo_file_path': onboarding_obj.get('photo_file_path', '') or '',
                        'photo_url': onboarding_obj.get('photo_url', '') or '',
                        'photo_file_size': onboarding_obj.get('photo_file_size'),
                        'photo_mime_type': onboarding_obj.get('photo_mime_type', '') or '',
                    })
                
                return data
        except (ImportError, AttributeError) as e:
            pass
        return None
    
    def _generate_employee_number(self, user):
        """Generate unique employee number using incrementing counter."""
        from apps.hr_core.models import EmployeeMaster
        import random
        
        year = timezone.now().year
        
        # Try up to 100 times to find a unique number
        for attempt in range(100):
            # Use random 4-digit number
            random_suffix = random.randint(1000, 9999)
            candidate = f"EMP{year}{random_suffix}"
            
            # Check if this number exists
            if not EmployeeMaster.objects.filter(employee_number=candidate).exists():
                return candidate
            if not EmployeeMaster.objects.filter(employee_code=candidate).exists():
                return candidate
            if not EmployeeMaster.objects.filter(emp_code=candidate[:10]).exists():
                return candidate
        
        # Fallback: use timestamp if random fails
        import time
        timestamp_suffix = str(int(time.time() * 1000))[-4:]
        return f"EMP{year}{timestamp_suffix}"
