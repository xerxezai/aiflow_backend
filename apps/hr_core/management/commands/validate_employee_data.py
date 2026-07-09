"""
Django Management Command: Validate Employee Data

Validates data consistency between EmployeeMaster and legacy tables
during migration period.

USAGE:
    python manage.py validate_employee_data
    python manage.py validate_employee_data --email user@example.com
    python manage.py validate_employee_data --fix  # Auto-fix inconsistencies
"""
import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.hr_core.models import EmployeeMaster

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    help = 'Validate employee data consistency between EmployeeMaster and legacy tables'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Validate specific employee by email',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Automatically fix inconsistencies',
        )
    
    def handle(self, *args, **options):
        specific_email = options.get('email')
        auto_fix = options['fix']
        
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('Employee Data Validation'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        # Query employees
        employees_query = EmployeeMaster.objects.all()
        
        if specific_email:
            employees_query = employees_query.filter(email=specific_email)
        
        total_employees = employees_query.count()
        self.stdout.write(f"Total employees to validate: {total_employees}\n")
        
        if total_employees == 0:
            self.stdout.write(self.style.WARNING('No employees found in EmployeeMaster'))
            return
        
        # Validation statistics
        total_checks = 0
        passed_checks = 0
        failed_checks = 0
        fixed_issues = 0
        
        for employee in employees_query:
            self.stdout.write(f"\n{'=' * 80}")
            self.stdout.write(f"Validating: {employee.email} ({employee.employee_number})")
            self.stdout.write('=' * 80)
            
            # Check 1: User relationship
            total_checks += 1
            if self.validate_user_relationship(employee):
                passed_checks += 1
                self.stdout.write(self.style.SUCCESS("  ✅ User relationship valid"))
            else:
                failed_checks += 1
                self.stdout.write(self.style.ERROR("  ❌ User relationship broken"))
            
            # Check 2: Email consistency
            total_checks += 1
            if self.validate_email_consistency(employee):
                passed_checks += 1
                self.stdout.write(self.style.SUCCESS("  ✅ Email consistency valid"))
            else:
                failed_checks += 1
                self.stdout.write(self.style.ERROR("  ❌ Email mismatch"))
            
            # Check 3: Photo URL validity
            total_checks += 1
            if self.validate_photo_url(employee, auto_fix=auto_fix):
                passed_checks += 1
                self.stdout.write(self.style.SUCCESS("  ✅ Photo URL valid"))
            else:
                failed_checks += 1
                self.stdout.write(self.style.ERROR("  ❌ Photo URL invalid/expired"))
                if auto_fix:
                    fixed_issues += 1
            
            # Check 4: Manager relationship
            total_checks += 1
            if self.validate_manager(employee):
                passed_checks += 1
                self.stdout.write(self.style.SUCCESS("  ✅ Manager relationship valid"))
            else:
                failed_checks += 1
                self.stdout.write(self.style.ERROR("  ❌ Manager relationship issue"))
            
            # Check 5: Legacy identifier uniqueness
            total_checks += 1
            if self.validate_identifier_uniqueness(employee):
                passed_checks += 1
                self.stdout.write(self.style.SUCCESS("  ✅ Identifiers unique"))
            else:
                failed_checks += 1
                self.stdout.write(self.style.ERROR("  ❌ Duplicate identifiers found"))
        
        # Final summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 80))
        self.stdout.write(self.style.SUCCESS('Validation Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(f"Total validation checks: {total_checks}")
        self.stdout.write(self.style.SUCCESS(f"✅ Passed: {passed_checks}"))
        self.stdout.write(self.style.ERROR(f"❌ Failed: {failed_checks}"))
        
        if auto_fix:
            self.stdout.write(self.style.SUCCESS(f"🔧 Fixed: {fixed_issues}"))
        
        pass_rate = (passed_checks / total_checks * 100) if total_checks > 0 else 0
        self.stdout.write(f"\nPass rate: {pass_rate:.1f}%")
        
        if pass_rate == 100:
            self.stdout.write(self.style.SUCCESS('\n🎉 ALL VALIDATIONS PASSED!'))
        elif pass_rate >= 90:
            self.stdout.write(self.style.WARNING('\n⚠️  Some minor issues found'))
        else:
            self.stdout.write(self.style.ERROR('\n🚨 CRITICAL ISSUES FOUND - REVIEW REQUIRED'))
    
    def validate_user_relationship(self, employee):
        """Check if user relationship is valid."""
        try:
            return employee.user is not None and employee.user.email == employee.email
        except Exception as e:
            logger.error(f"User validation error for {employee.email}: {e}")
            return False
    
    def validate_email_consistency(self, employee):
        """Check if email is consistent across user and employee."""
        try:
            return employee.email == employee.user.email
        except Exception:
            return False
    
    def validate_photo_url(self, employee, auto_fix=False):
        """Check if photo URL is valid (not expired)."""
        if not employee.photo_file_path:
            return True  # No photo = valid (not an error)
        
        if not employee.photo_url:
            if auto_fix:
                employee.refresh_photo_url()
                self.stdout.write(self.style.WARNING("    🔧 Fixed: Refreshed photo URL"))
                return True
            return False
        
        # Check if URL is recent (less than 7 days old)
        from django.utils import timezone
        if employee.photo_uploaded_at:
            days_old = (timezone.now() - employee.photo_uploaded_at).days
            if days_old > 6:  # URL expires in 7 days
                if auto_fix:
                    employee.refresh_photo_url()
                    self.stdout.write(self.style.WARNING(f"    🔧 Fixed: Refreshed {days_old}-day-old photo URL"))
                    return True
                return False
        
        return True
    
    def validate_manager(self, employee):
        """Check if manager relationship is valid."""
        if not employee.manager_id:
            return True  # No manager = valid (not an error)
        
        try:
            return employee.manager is not None
        except Exception:
            return False
    
    def validate_identifier_uniqueness(self, employee):
        """Check if employee identifiers are unique."""
        # Check employee_number
        dup_num = EmployeeMaster.objects.filter(
            employee_number=employee.employee_number
        ).exclude(id=employee.id).exists()
        
        # Check employee_code
        dup_code = EmployeeMaster.objects.filter(
            employee_code=employee.employee_code
        ).exclude(id=employee.id).exists()
        
        # Check emp_code
        dup_emp = EmployeeMaster.objects.filter(
            emp_code=employee.emp_code
        ).exclude(id=employee.id).exists()
        
        if dup_num or dup_code or dup_emp:
            if dup_num:
                self.stdout.write(self.style.ERROR(f"      Duplicate employee_number: {employee.employee_number}"))
            if dup_code:
                self.stdout.write(self.style.ERROR(f"      Duplicate employee_code: {employee.employee_code}"))
            if dup_emp:
                self.stdout.write(self.style.ERROR(f"      Duplicate emp_code: {employee.emp_code}"))
            return False
        
        return True
