"""
HR Core Service Layer - Employee Master Service

This service abstracts all employee data operations and provides:
1. Unified employee lookups (handles all legacy identifiers)
2. Photo upload/management (single source)
3. Employee lifecycle operations
4. Backward compatibility bridge

CRITICAL: All employee operations should go through this service layer
to ensure data consistency across legacy and new tables.
"""
import logging
import uuid
import random
from typing import Optional, Dict, Any, List
from django.db import transaction, models
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.hr_core.models import EmployeeMaster

logger = logging.getLogger(__name__)

User = get_user_model()


class EmployeeService:
    """
    Centralized employee data access layer.
    
    Design Principle: Single Point of Entry
    - All employee CRUD operations go through this service
    - Handles dual-write during migration phase
    - Abstracts database structure from business logic
    """
    
    # ========================================
    # LOOKUP METHODS (Unified Employee Retrieval)
    # ========================================
    
    @staticmethod
    def get_employee_by_any_identifier(identifier: str) -> Optional[EmployeeMaster]:
        """
        Get employee by ANY legacy identifier.
        
        Searches across:
        - employee_number (from user_profiles)
        - employee_code (from finance tables)
        - emp_code (from timesheet)
        - email
        
        Args:
            identifier: Any employee identifier string
            
        Returns:
            EmployeeMaster instance or None
        """
        try:
            return EmployeeMaster.objects.get(
                models.Q(employee_number=identifier) |
                models.Q(employee_code=identifier) |
                models.Q(emp_code=identifier) |
                models.Q(email=identifier)
            )
        except EmployeeMaster.DoesNotExist:
            logger.warning(f"Employee not found with identifier: {identifier}")
            return None
        except EmployeeMaster.MultipleObjectsReturned:
            logger.error(f"Multiple employees found with identifier: {identifier}")
            # Return first match, but log for investigation
            return EmployeeMaster.objects.filter(
                models.Q(employee_number=identifier) |
                models.Q(employee_code=identifier) |
                models.Q(emp_code=identifier) |
                models.Q(email=identifier)
            ).first()
    
    @staticmethod
    def get_employee_by_email(email: str) -> Optional[EmployeeMaster]:
        """Get employee by email address."""
        try:
            return EmployeeMaster.objects.get(email=email)
        except EmployeeMaster.DoesNotExist:
            return None
    
    @staticmethod
    def get_employee_by_user(user: User) -> Optional[EmployeeMaster]:
        """Get employee by Django User instance."""
        try:
            return EmployeeMaster.objects.get(user=user)
        except EmployeeMaster.DoesNotExist:
            return None
    
    @staticmethod
    def get_active_employees(department: str = None, branch: str = None) -> List[EmployeeMaster]:
        """
        Get all active employees with optional filters.
        
        Args:
            department: Filter by department
            branch: Filter by branch (RAD/RIN)
            
        Returns:
            QuerySet of active EmployeeMaster instances
        """
        queryset = EmployeeMaster.objects.filter(employment_status='active')
        
        if department:
            queryset = queryset.filter(department=department)
        
        if branch:
            queryset = queryset.filter(branch=branch)
        
        return queryset.select_related('user', 'manager')
    
    # ========================================
    # EMPLOYEE CREATION
    # ========================================
    
    @staticmethod
    def generate_employee_number(year: int = None) -> str:
        """
        Generate unique employee number.
        
        Format: EMP{YEAR}{4-digit-random}
        Example: EMP202612345
        
        Args:
            year: Year for employee number (defaults to current year)
            
        Returns:
            Unique employee number string
        """
        year = year or timezone.now().year
        
        # Try up to 10 times to generate unique number
        for _ in range(10):
            emp_num = f"EMP{year}{random.randint(1000, 9999)}"
            
            if not EmployeeMaster.objects.filter(employee_number=emp_num).exists():
                return emp_num
        
        # Fallback to UUID-based number if random fails
        unique_suffix = str(uuid.uuid4())[:8].upper()
        return f"EMP{year}{unique_suffix}"
    
    @staticmethod
    def generate_employee_code(first_name: str = '', last_name: str = '', branch: str = 'RAD') -> str:
        """
        Generate unique employee code for finance/payroll systems.
        
        Format: {BRANCH}-{INITIALS}-{YEAR}-{3-digit-sequence}
        Example: RAD-JD-2026-001, RIN-SA-2026-042
        
        Args:
            first_name: Employee first name
            last_name: Employee last name
            branch: Branch code (RAD/RIN)
            
        Returns:
            Unique employee code string
        """
        year = timezone.now().year
        
        # Generate initials
        first_initial = first_name[:1].upper() if first_name else 'X'
        last_initial = last_name[:1].upper() if last_name else 'X'
        initials = f"{first_initial}{last_initial}"
        
        # Try up to 50 times to generate unique code
        for sequence in range(1, 51):
            emp_code = f"{branch}-{initials}-{year}-{sequence:03d}"
            
            if not EmployeeMaster.objects.filter(employee_code=emp_code).exists():
                return emp_code
        
        # Fallback to random suffix if sequence fails
        random_suffix = random.randint(100, 999)
        return f"{branch}-{initials}-{year}-{random_suffix}"
    
    @staticmethod
    @transaction.atomic
    def create_employee(
        user: User,
        email: str = None,
        first_name: str = '',
        last_name: str = '',
        employee_number: str = None,
        employee_code: str = None,
        join_date = None,
        department: str = '',
        designation: str = '',
        branch: str = 'RAD',
        manager: EmployeeMaster = None,
        **additional_fields
    ) -> EmployeeMaster:
        """
        Create new employee master record.
        
        Automatically:
        - Generates employee_number if not provided
        - Generates employee_code if not provided (smart format based on name and branch)
        - Generates emp_code (truncated for biometric system)
        - Links to User instance
        - Creates initial leave balances (if leave management enabled)
        - Syncs data to legacy tables (during migration phase)
        
        Args:
            user: Django User instance
            email: Employee email (defaults to user.email)
            first_name: Employee first name (defaults to user.first_name)
            last_name: Employee last name (defaults to user.last_name)
            employee_number: Employee number (auto-generated if not provided)
            employee_code: Employee code for finance/payroll (auto-generated if not provided)
            join_date: Date of joining (defaults to today)
            department: Department name
            designation: Job designation
            branch: Branch (RAD/RIN)
            manager: Reporting manager (EmployeeMaster instance)
            **additional_fields: Any other EmployeeMaster fields
            
        Returns:
            Created EmployeeMaster instance
        """
        # Use provided values or fall back to user data
        email = email or user.email
        first_name = first_name or user.first_name or ''
        last_name = last_name or user.last_name or ''
        join_date = join_date or timezone.now().date()
        
        # Generate unique identifiers if not provided
        if not employee_number:
            employee_number = EmployeeService.generate_employee_number()
        
        if not employee_code:
            employee_code = EmployeeService.generate_employee_code(
                first_name=first_name,
                last_name=last_name,
                branch=branch
            )
        
        # Generate emp_code (truncated for biometric system - 20 char limit)
        emp_code = employee_code[:20] if len(employee_code) <= 20 else employee_number[:10]
        
        # Create employee master record
        employee = EmployeeMaster.objects.create(
            user=user,
            email=email,
            first_name=first_name,
            last_name=last_name,
            employee_number=employee_number,
            employee_code=employee_code,
            emp_code=emp_code,
            join_date=join_date,
            department=department,
            designation=designation,
            branch=branch,
            manager=manager,
            employment_status='probation',  # Default to probation for new joiners
            **additional_fields
        )
        
        logger.info(f"Created employee: {employee.employee_number} | Code: {employee.employee_code} | Email: {employee.email}")
        
        return employee
        
        logger.info(f"Created employee master record: {employee.employee_number} ({employee.email})")
        
        # Initialize leave balances (if leave management app is enabled)
        try:
            from apps.payroll.models import LeaveType
            from apps.hr_core.models import EmployeeLeaveBalance  # Will create this model next
            
            current_year = timezone.now().year
            
            for leave_type in LeaveType.objects.filter(is_active=True):
                EmployeeLeaveBalance.objects.create(
                    employee=employee,
                    leave_type=leave_type,
                    current_balance=leave_type.initial_balance or 0,
                    year=current_year
                )
            
            logger.info(f"Initialized leave balances for {employee.employee_number}")
        except ImportError:
            logger.debug("Leave management not enabled, skipping leave balance initialization")
        
        # TODO: Dual-write to legacy tables during migration phase
        # This will be enabled in Phase 2 of migration
        # EmployeeService._sync_to_legacy_tables(employee)
        
        return employee
    
    # ========================================
    # PHOTO MANAGEMENT (Unified S3 Upload)
    # ========================================
    
    @staticmethod
    def upload_employee_photo(
        employee: EmployeeMaster,
        photo_file,
        uploaded_by: User = None
    ) -> str:
        """
        Upload employee photo to S3 and update employee record.
        
        This is the SINGLE photo upload method for the entire platform.
        Replaces:
        - User avatar upload
        - Onboarding photo upload
        - Profile photo upload
        
        Args:
            employee: EmployeeMaster instance
            photo_file: File object (InMemoryUploadedFile or TemporaryUploadedFile)
            uploaded_by: User who uploaded the photo (for audit)
            
        Returns:
            Presigned S3 URL
            
        Raises:
            ValueError: If file validation fails
        """
        from apps.core.s3_service import S3Service
        
        # Validate file type
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png']
        content_type = photo_file.content_type.lower() if hasattr(photo_file, 'content_type') else 'unknown'
        
        if content_type not in allowed_types:
            raise ValueError(f"Invalid file type: {content_type}. Allowed: {', '.join(allowed_types)}")
        
        # Validate file size (5MB max)
        max_size = 5 * 1024 * 1024  # 5MB in bytes
        if photo_file.size > max_size:
            raise ValueError(f"File too large: {photo_file.size} bytes. Maximum: {max_size} bytes (5MB)")
        
        # Generate S3 key
        file_ext = photo_file.name.split('.')[-1].lower() if hasattr(photo_file, 'name') else 'jpg'
        s3_key = f"media/employee_photos/{employee.id}.{file_ext}"
        
        logger.info(f"Uploading photo for {employee.employee_number} to S3: {s3_key}")
        
        # Upload to S3
        s3_service = S3Service()
        try:
            s3_service.upload_file(photo_file, s3_key, content_type)
        except Exception as e:
            logger.error(f"S3 upload failed for {employee.employee_number}: {e}")
            raise
        
        # Generate presigned URL (7-day expiry)
        photo_url = s3_service.generate_presigned_url(s3_key, expiration=604800)
        
        # Update employee record
        employee.photo_file_path = s3_key
        employee.photo_url = photo_url
        employee.photo_file_size = photo_file.size
        employee.photo_mime_type = content_type
        employee.photo_uploaded_at = timezone.now()
        
        if uploaded_by:
            employee.last_updated_by = uploaded_by
        
        employee.save(update_fields=[
            'photo_file_path',
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            'last_updated_by',
            'updated_at'
        ])
        
        # Also update user.avatar for /profile page compatibility
        employee.user.avatar = photo_url
        employee.user.save(update_fields=['avatar'])
        
        logger.info(f"Photo uploaded successfully for {employee.employee_number}")
        
        return photo_url
    
    @staticmethod
    def get_employee_photo_url(employee: EmployeeMaster, refresh: bool = False) -> Optional[str]:
        """
        Get employee photo URL, optionally refreshing presigned URL.
        
        Args:
            employee: EmployeeMaster instance
            refresh: If True, regenerate presigned URL
            
        Returns:
            Presigned S3 URL or None
        """
        if not employee.photo_file_path:
            return None
        
        if refresh or not employee.photo_url:
            employee.refresh_photo_url()
        
        return employee.photo_url
    
    # ========================================
    # EMPLOYMENT STATUS UPDATES
    # ========================================
    
    @staticmethod
    @transaction.atomic
    def confirm_employee(
        employee: EmployeeMaster,
        confirmation_date=None,
        updated_by: User = None
    ) -> EmployeeMaster:
        """
        Confirm employee after probation.
        
        Args:
            employee: EmployeeMaster instance
            confirmation_date: Date of confirmation (defaults to today)
            updated_by: User who confirmed
            
        Returns:
            Updated EmployeeMaster instance
        """
        employee.employment_status = 'active'
        employee.confirmation_date = confirmation_date or timezone.now().date()
        
        if updated_by:
            employee.last_updated_by = updated_by
        
        employee.save(update_fields=['employment_status', 'confirmation_date', 'last_updated_by', 'updated_at'])
        
        logger.info(f"Employee confirmed: {employee.employee_number} on {employee.confirmation_date}")
        
        return employee
    
    @staticmethod
    @transaction.atomic
    def exit_employee(
        employee: EmployeeMaster,
        exit_date,
        updated_by: User = None
    ) -> EmployeeMaster:
        """
        Mark employee as exited.
        
        Args:
            employee: EmployeeMaster instance
            exit_date: Last working day
            updated_by: User who processed exit
            
        Returns:
            Updated EmployeeMaster instance
        """
        employee.employment_status = 'exited'
        employee.exit_date = exit_date
        
        if updated_by:
            employee.last_updated_by = updated_by
        
        employee.save(update_fields=['employment_status', 'exit_date', 'last_updated_by', 'updated_at'])
        
        logger.info(f"Employee exited: {employee.employee_number} on {exit_date}")
        
        return employee
    
    # ========================================
    # LEGACY TABLE SYNC (Migration Phase Only)
    # ========================================
    
    @staticmethod
    def _sync_to_legacy_tables(employee: EmployeeMaster):
        """
        Sync employee data to legacy tables during migration phase.
        
        This method will be enabled during Phase 2 (dual-write).
        Syncs to:
        - user_profiles
        - finance_employee_salary_info
        - onboarding_record
        
        NOTE: This is temporary and will be removed after full migration.
        """
        # TODO: Implement dual-write logic
        # Will be activated in Phase 2 of migration plan
        pass
    
    # ========================================
    # BULK OPERATIONS
    # ========================================
    
    @staticmethod
    def get_employees_by_department(department: str) -> List[EmployeeMaster]:
        """Get all employees in a department."""
        return EmployeeMaster.objects.filter(
            department=department,
            employment_status__in=['active', 'probation']
        ).select_related('user', 'manager')
    
    @staticmethod
    def get_direct_reports(manager: EmployeeMaster) -> List[EmployeeMaster]:
        """Get all direct reports of a manager."""
        return EmployeeMaster.objects.filter(
            manager=manager,
            employment_status__in=['active', 'probation']
        ).select_related('user')
    
    @staticmethod
    def search_employees(query: str, limit: int = 50) -> List[EmployeeMaster]:
        """
        Search employees by name, email, or employee number.
        
        Args:
            query: Search string
            limit: Maximum results to return
            
        Returns:
            List of matching EmployeeMaster instances
        """
        return EmployeeMaster.objects.filter(
            models.Q(first_name__icontains=query) |
            models.Q(last_name__icontains=query) |
            models.Q(email__icontains=query) |
            models.Q(employee_number__icontains=query) |
            models.Q(employee_code__icontains=query)
        ).select_related('user', 'manager')[:limit]
