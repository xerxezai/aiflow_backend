"""
Smart Employee ID Synchronization Command
==========================================
Automatically matches RADAI users to biometric system employees using fuzzy name matching.

Usage:
    python manage.py sync_employee_ids                    # Check all users, preview only
    python manage.py sync_employee_ids --apply            # Apply updates
    python manage.py sync_employee_ids --user lira.viaga@rejlers.ae  # Check specific user
    python manage.py sync_employee_ids --threshold 85     # Higher confidence threshold

Features:
- Fuzzy name matching using difflib (soft-coded, no hardcoded names)
- Configurable similarity threshold via --threshold (default: 75%)
- Preview mode by default (--apply to commit changes)
- Works for ALL users automatically
- Updates UserProfile.employee_id with matched biometric code
"""
from difflib import SequenceMatcher
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import models
from apps.rbac.models import UserProfile
from apps.timesheet.sqlserver import connect
from apps.timesheet import config as ts_config

User = get_user_model()


def normalize_name(name):
    """Normalize name for comparison (lowercase, strip whitespace)"""
    if not name:
        return ""
    return name.lower().strip()


def similarity_ratio(name1, name2):
    """Calculate similarity between two names (0.0 to 1.0)"""
    norm1 = normalize_name(name1)
    norm2 = normalize_name(name2)
    return SequenceMatcher(None, norm1, norm2).ratio()


class Command(BaseCommand):
    help = 'Smart employee ID synchronization using fuzzy name matching'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply changes (default is preview only)',
        )
        parser.add_argument(
            '--user',
            type=str,
            help='Sync specific user by email (e.g., lira.viaga@rejlers.ae)',
        )
        parser.add_argument(
            '--threshold',
            type=int,
            default=75,
            help='Minimum name similarity threshold (0-100, default: 75)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update even if employee_id already set',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        user_email = options['user']
        threshold = options['threshold'] / 100.0  # Convert to 0.0-1.0
        force_update = options['force']

        mode = "🔧 APPLYING CHANGES" if apply_changes else "👁️  PREVIEW MODE"
        self.stdout.write(self.style.SUCCESS(f'\n{mode}'))
        self.stdout.write(f'Similarity Threshold: {threshold*100:.0f}%\n')

        # Connect to biometric database using existing infrastructure
        try:
            self.stdout.write('📊 Fetching biometric employee list...')
            
            with connect() as cursor:
                # Get unique employees from biometric system
                table = ts_config.SCHEMA['table']
                col_emp_code = ts_config.SCHEMA['columns']['employee_code']
                col_emp_name = ts_config.SCHEMA['columns']['employee_name']
                col_department = ts_config.SCHEMA['columns']['department']
                
                # Build query - handle empty department column gracefully
                dept_col = col_department if col_department else "''"
                
                query = f"""
                    SELECT DISTINCT {col_emp_code}, {col_emp_name}, {dept_col} AS dept
                    FROM {table}
                    WHERE {col_emp_code} IS NOT NULL 
                      AND {col_emp_name} IS NOT NULL
                      AND LTRIM(RTRIM({col_emp_name})) != ''
                      AND {col_emp_code} != 'UserID'
                      AND {col_emp_name} NOT IN ('UserName', 'FullName')
                    ORDER BY {col_emp_code}
                """
                cursor.execute(query)
                biometric_employees = cursor.fetchall()
                self.stdout.write(f'   Found {len(biometric_employees)} biometric employees\n')

                # Build lookup dictionary
                bio_lookup = {}
                for emp_id, emp_name, dept in biometric_employees:
                    bio_lookup[str(emp_id).strip()] = {
                        'name': emp_name.strip() if emp_name else '',
                        'dept': (dept.strip() if dept else '') if dept is not None else ''
                    }

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Could not connect to biometric DB: {e}'))
            return

        # Get RADAI users to process
        if user_email:
            try:
                user_obj = User.objects.get(email=user_email)
                profiles = UserProfile.objects.filter(user=user_obj)
                self.stdout.write(f'🎯 Processing specific user: {user_email}\n')
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'❌ User {user_email} not found'))
                return
        else:
            if force_update:
                profiles = UserProfile.objects.all()
                self.stdout.write(f'🔄 Processing ALL {profiles.count()} users (force mode)\n')
            else:
                # Only process users without employee_id or with placeholder values
                profiles = UserProfile.objects.filter(
                    models.Q(employee_id__isnull=True) |
                    models.Q(employee_id='') |
                    models.Q(employee_id__icontains='@') |  # email-format
                    models.Q(employee_id__startswith='EMP')  # placeholder
                )
                self.stdout.write(f'🔍 Processing {profiles.count()} users needing employee_id\n')

        # Process each user
        stats = {
            'high_confidence': 0,
            'medium_confidence': 0,
            'no_match': 0,
            'already_set': 0,
            'updated': 0
        }

        for profile in profiles:
            user = profile.user
            full_name = f"{user.first_name} {user.last_name}".strip()
            
            if not full_name:
                full_name = user.username

            current_emp_id = profile.employee_id

            # Skip if already has valid employee_id and not forcing
            if current_emp_id and not force_update:
                if not any(x in str(current_emp_id) for x in ['@', 'EMP', 'TBD']):
                    stats['already_set'] += 1
                    continue

            # Find best match in biometric system
            best_match = None
            best_score = 0.0
            best_emp_id = None

            for emp_id, emp_data in bio_lookup.items():
                bio_name = emp_data['name']
                score = similarity_ratio(full_name, bio_name)
                
                if score > best_score:
                    best_score = score
                    best_match = bio_name
                    best_emp_id = emp_id

            # Categorize match quality
            if best_score >= threshold:
                if best_score >= 0.90:
                    confidence = 'HIGH'
                    stats['high_confidence'] += 1
                else:
                    confidence = 'MEDIUM'
                    stats['medium_confidence'] += 1

                self.stdout.write(
                    f'  ✅ {confidence:6s} ({best_score*100:5.1f}%) {user.email:35s} → '
                    f'{best_emp_id:6s} "{best_match}"'
                )

                # Apply change if requested
                if apply_changes:
                    old_id = profile.employee_id
                    profile.employee_id = best_emp_id
                    profile.save()
                    stats['updated'] += 1
                    self.stdout.write(f'      Updated: {old_id} → {best_emp_id}')

            else:
                stats['no_match'] += 1
                self.stdout.write(
                    f'  ⚠️  NO MATCH ({best_score*100:5.1f}%) {user.email:35s} | '
                    f'RAD: "{full_name}" vs Bio: "{best_match}"'
                )

        # Print summary
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS('📊 SUMMARY'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
        self.stdout.write(f'  High Confidence Matches (≥90%):  {stats["high_confidence"]:3d}')
        self.stdout.write(f'  Medium Confidence (75-89%):      {stats["medium_confidence"]:3d}')
        self.stdout.write(f'  No Match (<{threshold*100:.0f}%):              {stats["no_match"]:3d}')
        self.stdout.write(f'  Already Set (skipped):           {stats["already_set"]:3d}')
        
        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f'  ✅ UPDATED:                       {stats["updated"]:3d}'))
        else:
            self.stdout.write(self.style.WARNING(
                f'\n💡 Run with --apply to update {stats["high_confidence"] + stats["medium_confidence"]} users'
            ))
