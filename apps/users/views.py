"""
User and UserProfile management views
Handles listing active employees and updating profile fields

⚠️ MIGRATION IN PROGRESS: Migrating from UserProfile to EmployeeMaster
"""
from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import UserProfile  # ⚠️ DEPRECATED - keeping for backward compatibility
from .serializers import UserProfileSerializer

# New employee management
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.services import EmployeeService

User = get_user_model()

# ── Soft-coded Profile Field Configuration ────────────────────────────────
# Defines all editable profile fields with metadata for frontend rendering
PROFILE_FIELD_GROUPS = [
    {
        'group': 'Contact Information',
        'fields': [
            {'key': 'first_name', 'label': 'First Name', 'type': 'text', 'source': 'user', 'required': True},
            {'key': 'last_name', 'label': 'Surname', 'type': 'text', 'source': 'user', 'required': True},
            {'key': 'preferred_given_name', 'label': 'Preferred Given Name', 'type': 'text', 'source': 'profile'},
            {'key': 'manager_id', 'label': 'Manager', 'type': 'select', 'source': 'profile'},
            {'key': 'country', 'label': 'Country', 'type': 'select', 'source': 'profile'},
        ]
    },
    {
        'group': 'Other Information',
        'fields': [
            {'key': 'email', 'label': 'Email', 'type': 'email', 'source': 'user', 'required': True},
            {'key': 'phone_number', 'label': 'Mobile Phone', 'type': 'tel', 'source': 'user'},
            {'key': 'initials', 'label': 'Initials', 'type': 'text', 'source': 'profile'},
            {'key': 'employee_number', 'label': 'Employee Number', 'type': 'text', 'source': 'profile'},
            {'key': 'account_name', 'label': 'Account Name', 'type': 'text', 'source': 'profile'},
            {'key': 'employment_id', 'label': 'Employment ID', 'type': 'text', 'source': 'profile'},
            {'key': 'candidate_id', 'label': 'Candidate ID', 'type': 'text', 'source': 'profile'},
        ]
    },
    {
        'group': 'Organisation Information',
        'fields': [
            {'key': 'company', 'label': 'Company', 'type': 'text', 'source': 'profile'},
            {'key': 'business_unit', 'label': 'Business Unit', 'type': 'text', 'source': 'profile'},
            {'key': 'division', 'label': 'Division', 'type': 'text', 'source': 'profile'},
            {'key': 'business_area', 'label': 'Business Area', 'type': 'text', 'source': 'profile'},
            {'key': 'office', 'label': 'Office', 'type': 'text', 'source': 'profile'},
            {'key': 'job_title_finland', 'label': 'Job Title (Finland)', 'type': 'text', 'source': 'profile'},
            {'key': 'job_title_uae', 'label': 'Job Title (UAE)', 'type': 'text', 'source': 'profile'},
        ]
    },
    {
        'group': 'Address',
        'fields': [
            {'key': 'address', 'label': 'Street Address', 'type': 'textarea', 'source': 'profile'},
            {'key': 'city', 'label': 'City', 'type': 'text', 'source': 'profile'},
            {'key': 'postal_code', 'label': 'Postal Code', 'type': 'text', 'source': 'profile'},
        ]
    },
    {
        'group': 'Flags & Testing',
        'fields': [
            {'key': 'protected_identity', 'label': 'Protected Identity', 'type': 'boolean', 'source': 'profile'},
            {'key': 'is_test_person', 'label': 'Test Person', 'type': 'boolean', 'source': 'profile'},
            {'key': 'not_signed', 'label': 'Not Signed', 'type': 'boolean', 'source': 'profile'},
            {'key': 'implementation_test', 'label': 'Implementation Test', 'type': 'text', 'source': 'profile'},
            {'key': 'hrm_test', 'label': 'HRM Test', 'type': 'text', 'source': 'profile'},
            {'key': 'process_testing', 'label': 'Process Testing', 'type': 'text', 'source': 'profile'},
        ]
    },
]


class EmployeeProfileViewSet(viewsets.GenericViewSet):
    """
    ViewSet for managing employee profiles (User + UserProfile)
    Provides listing all active employees and updating profile fields
    """
    queryset = User.objects.filter(is_active=True)
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return UserProfileSerializer

    @action(detail=False, methods=['get'])
    def active_employees(self, request):
        """
        GET /api/v1/users/employees/active_employees/
        List all active employees with complete profile data
        Supports search query parameter
        
        ✅ MIGRATED: Now uses EmployeeMaster instead of UserProfile
        """
        search_query = request.query_params.get('search', '').strip()
        
        # Get all active employees using EmployeeMaster
        employees_query = EmployeeMaster.objects.filter(
            user__is_active=True
        ).select_related('user', 'manager')
        
        # Apply search filter if provided
        if search_query:
            employees_query = employees_query.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(employee_number__icontains=search_query) |
                Q(employment_id__icontains=search_query) |
                Q(account_name__icontains=search_query)
            )
        
        # Order by last name, first name
        employees_query = employees_query.order_by('last_name', 'first_name')
        
        # Serialize the data
        employee_data = []
        for employee in employees_query:
            user = employee.user
            
            # Build employee data object from EmployeeMaster
            employee_dict = {
                'id': user.id,
                'user_id': user.id,
                # User fields
                'first_name': employee.first_name or user.first_name or '',
                'last_name': employee.last_name or user.last_name or '',
                'email': employee.email or user.email or '',
                'phone_number': user.phone_number or '',
                'is_active': user.is_active,
                'date_joined': user.date_joined.isoformat() if user.date_joined else None,
                
                # Employee Master fields
                'profile_id': employee.id,
                'employee_master_id': str(employee.id),
                'preferred_given_name': employee.preferred_given_name or '',
                'manager_id': employee.manager_id,
                'manager_name': employee.manager.get_full_name() if employee.manager else '',
                'initials': employee.initials or '',
                'employee_number': employee.employee_number or '',
                'account_name': employee.account_name or '',
                'employment_id': employee.employment_id or '',
                'candidate_id': employee.candidate_id or '',
                
                # Organization
                'company': getattr(employee, 'company', ''),
                'business_unit': employee.business_unit or '',
                'division': employee.division or '',
                'business_area': employee.business_area or '',
                'office': employee.office or '',
                'job_title_finland': getattr(employee, 'job_title_finland', ''),
                'job_title_uae': getattr(employee, 'job_title_uae', ''),
                
                # Address
                'country': employee.country or '',
                'city': employee.city or '',
                'address': employee.address or '',
                'postal_code': employee.postal_code or '',
                'date_of_birth': employee.date_of_birth.isoformat() if employee.date_of_birth else '',
                
                # Flags
                'protected_identity': getattr(employee, 'protected_identity', False),
                'is_test_person': getattr(employee, 'is_test_person', False),
                'not_signed': getattr(employee, 'not_signed', False),
                
                # Testing fields
                'implementation_test': getattr(employee, 'implementation_test', ''),
                'hrm_test': getattr(employee, 'hrm_test', ''),
                'process_testing': getattr(employee, 'process_testing', ''),
            }
            
            employee_data.append(employee_dict)
        
        return Response({
            'count': len(employee_data),
            'results': employee_data,
            'field_groups': PROFILE_FIELD_GROUPS,  # Include field configuration for frontend
        })

    @action(detail=True, methods=['patch'])
    def update_profile_field(self, request, pk=None):
        """
        PATCH /api/v1/users/employees/{user_id}/update_profile_field/
        Update a single profile field (or user field) for an employee
        
        ✅ MIGRATED: Now uses EmployeeMaster instead of UserProfile
        
        Request body:
        {
            "field": "employee_number",
            "value": "EMP-12345",
            "source": "profile"  // or "user"
        }
        """
        user = self.get_object()
        field = request.data.get('field')
        value = request.data.get('value')
        source = request.data.get('source', 'profile')
        
        if not field:
            return Response(
                {'error': 'Field name is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            if source == 'user':
                # Update User model field
                if hasattr(user, field):
                    setattr(user, field, value)
                    user.save(update_fields=[field])
                else:
                    return Response(
                        {'error': f'Invalid user field: {field}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            else:  # source == 'profile'
                # Get or create EmployeeMaster record
                try:
                    employee = EmployeeMaster.objects.get(user=user)
                except EmployeeMaster.DoesNotExist:
                    # Create new employee record if doesn't exist
                    employee = EmployeeService.create_employee(
                        user=user,
                        email=user.email,
                        first_name=user.first_name or '',
                        last_name=user.last_name or ''
                    )
                
                # Handle manager_id specially (ForeignKey to EmployeeMaster)
                if field == 'manager_id':
                    if value:
                        try:
                            # Manager should be another EmployeeMaster record
                            manager = EmployeeMaster.objects.get(user_id=value)
                            employee.manager = manager
                        except EmployeeMaster.DoesNotExist:
                            return Response(
                                {'error': f'Manager employee record with user ID {value} not found'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                    else:
                        employee.manager = None
                    employee.save(update_fields=['manager'])
                elif hasattr(employee, field):
                    setattr(employee, field, value)
                    employee.save(update_fields=[field])
                else:
                    return Response(
                        {'error': f'Invalid employee field: {field}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            return Response({
                'success': True,
                'message': f'Field "{field}" updated successfully',
                'field': field,
                'value': value,
            })
        
        except Exception as e:
            import traceback
            print(f"[ERROR] update_profile_field: {str(e)}")
            print(traceback.format_exc())
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get', 'patch'], url_path='my-employee-profile')
    def my_employee_profile(self, request):
        """
        GET/PATCH /api/v1/users/employees/my-employee-profile/
        Get or update current user's EmployeeMaster record
        
        Individual users can view and update their own employee data
        Similar to HR onboarding but scoped to current user only
        
        GET: Returns employee data + field groups for rendering
        PATCH: Updates employee fields (similar to update_profile_field but for current user)
        
        Request body for PATCH:
        {
            "first_name": "John",
            "last_name": "Doe",
            "job_title_uae": "Senior Engineer",
            "phone_number": "+971501234567",
            ...
        }
        """
        user = request.user
        
        if request.method == 'GET':
            # Get or create EmployeeMaster record for current user
            try:
                employee = EmployeeMaster.objects.select_related('user', 'manager').get(user=user)
            except EmployeeMaster.DoesNotExist:
                # Create employee record if doesn't exist
                employee = EmployeeService.create_employee(
                    user=user,
                    email=user.email,
                    first_name=user.first_name or '',
                    last_name=user.last_name or ''
                )
            
            # Build employee data dictionary
            employee_data = {
                'user_id': str(user.id),
                'first_name': employee.first_name or user.first_name,
                'last_name': employee.last_name or user.last_name,
                'email': employee.email or user.email,
                'employee_number': employee.employee_number,
                'employee_code': employee.employee_code,
                'emp_code': employee.emp_code,
                'preferred_given_name': employee.preferred_given_name,
                'phone_number': employee.phone_number,
                'initials': employee.initials,
                'account_name': employee.account_name,
                'employment_id': employee.employment_id,
                'candidate_id': employee.candidate_id,
                'company': getattr(employee, 'company', ''),
                'business_unit': employee.business_unit,
                'division': employee.division,
                'department': getattr(employee, 'department', ''),
                'business_area': employee.business_area,
                'office': employee.office,
                'job_title_finland': getattr(employee, 'job_title_finland', ''),
                'job_title_uae': getattr(employee, 'job_title_uae', ''),
                'address': employee.address,
                'city': employee.city,
                'postal_code': employee.postal_code,
                'country': employee.country,
                'protected_identity': getattr(employee, 'protected_identity', False),
                'is_test_person': getattr(employee, 'is_test_person', False),
                'not_signed': getattr(employee, 'not_signed', False),
                'implementation_test': getattr(employee, 'implementation_test', ''),
                'hrm_test': getattr(employee, 'hrm_test', ''),
                'process_testing': getattr(employee, 'process_testing', ''),
                'manager_id': str(employee.manager.user_id) if employee.manager else None,
                'manager_name': f"{employee.manager.first_name} {employee.manager.last_name}" if employee.manager else None,
                'manager_employee_number': employee.manager.employee_number if employee.manager else None,
                'photo_url': employee.photo_url,
                'is_active': user.is_active,
                'created_at': employee.created_at.isoformat() if employee.created_at else None,
                'updated_at': employee.updated_at.isoformat() if employee.updated_at else None,
                'engineer_profile': employee.engineer_profile or {},
            }
            
            return Response({
                'employee': employee_data,
                'field_groups': PROFILE_FIELD_GROUPS,
            })
        
        elif request.method == 'PATCH':
            # Update employee fields
            try:
                employee = EmployeeMaster.objects.get(user=user)
            except EmployeeMaster.DoesNotExist:
                return Response(
                    {'error': 'Employee record not found. Please contact administrator.'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Track changes
            updated_fields = []
            
            # Update User model fields if provided
            user_fields = ['first_name', 'last_name', 'email']
            for field in user_fields:
                if field in request.data:
                    setattr(user, field, request.data[field])
                    updated_fields.append(field)
            
            if updated_fields:
                user.save(update_fields=updated_fields)
            
            # Update EmployeeMaster fields
            employee_fields = [
                'preferred_given_name', 'phone_number', 'initials', 'account_name',
                'employment_id', 'candidate_id', 'business_unit', 'division',
                'business_area', 'office', 'address', 'city', 'postal_code', 'country',
                'is_test_person', 'not_signed', 'implementation_test', 'hrm_test', 'process_testing'
            ]
            
            # Add optional fields that might not exist
            optional_fields = ['company', 'department', 'job_title_finland', 'job_title_uae', 'protected_identity']
            
            employee_update_fields = []
            for field in employee_fields + optional_fields:
                if field in request.data and hasattr(employee, field):
                    setattr(employee, field, request.data[field])
                    employee_update_fields.append(field)
                    updated_fields.append(field)
            
            # Handle engineer_profile (JSON field)
            if 'engineer_profile' in request.data:
                employee.engineer_profile = request.data['engineer_profile']
                employee_update_fields.append('engineer_profile')
                updated_fields.append('engineer_profile')
            
            # Handle manager_id specially
            if 'manager_id' in request.data:
                manager_id = request.data['manager_id']
                if manager_id:
                    try:
                        manager = EmployeeMaster.objects.get(user_id=manager_id)
                        employee.manager = manager
                        employee_update_fields.append('manager')
                        updated_fields.append('manager')
                    except EmployeeMaster.DoesNotExist:
                        return Response(
                            {'error': f'Manager with user ID {manager_id} not found'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                else:
                    employee.manager = None
                    employee_update_fields.append('manager')
                    updated_fields.append('manager')
            
            if employee_update_fields:
                employee.save(update_fields=employee_update_fields)
            
            # ✅ SYNC: Update OnboardingRecord if it exists for bidirectional sync
            try:
                from apps.onboarding.models import OnboardingRecord
                onboarding_record = OnboardingRecord.objects.filter(
                    employee_email=employee.email
                ).first()
                if onboarding_record:
                    onboarding_sync_fields = {}
                    
                    # Sync name if changed
                    if 'first_name' in request.data or 'last_name' in request.data:
                        full_name = f"{user.first_name} {user.last_name}".strip()
                        onboarding_sync_fields['employee_name'] = full_name
                    
                    # Sync email if changed
                    if 'email' in request.data:
                        onboarding_sync_fields['employee_email'] = user.email
                    
                    # Sync department if changed
                    if 'department' in request.data:
                        onboarding_sync_fields['department'] = request.data['department']
                    
                    # Sync position if job title changed
                    if 'job_title_uae' in request.data:
                        onboarding_sync_fields['position'] = request.data['job_title_uae']
                    
                    # Apply synced fields
                    if onboarding_sync_fields:
                        for field, value in onboarding_sync_fields.items():
                            setattr(onboarding_record, field, value)
                        onboarding_record.save(update_fields=list(onboarding_sync_fields.keys()))
                        print(f"[SYNC] Updated OnboardingRecord fields: {list(onboarding_sync_fields.keys())}")
            except Exception as sync_error:
                # Log but don't fail the update
                print(f"[WARNING] Failed to sync to OnboardingRecord: {str(sync_error)}")
            
            return Response({
                'success': True,
                'message': f'Updated {len(updated_fields)} field(s) successfully',
                'updated_fields': updated_fields,
            })

    @action(detail=False, methods=['post'], url_path='my-profile-photo')
    def upload_my_profile_photo(self, request):
        """
        POST /api/v1/users/employees/my-profile-photo/
        Upload profile photo for current user
        
        Handles file upload to S3 and updates EmployeeMaster photo_url
        """
        from apps.core.s3_service import S3Service
        
        user = request.user
        
        # Check if file is provided
        if 'photo' not in request.FILES:
            return Response(
                {'error': 'No photo file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        photo_file = request.FILES['photo']
        
        # Validate file
        if photo_file.size > 5 * 1024 * 1024:  # 5MB limit
            return Response(
                {'error': 'File size exceeds 5MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
        if photo_file.content_type not in allowed_types:
            return Response(
                {'error': 'Invalid file type. Allowed: JPEG, PNG, WebP'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Get or create employee record
            try:
                employee = EmployeeMaster.objects.get(user=user)
            except EmployeeMaster.DoesNotExist:
                employee = EmployeeService.create_employee(
                    user=user,
                    email=user.email,
                    first_name=user.first_name or '',
                    last_name=user.last_name or ''
                )
            
            # Upload to S3
            photo_url = EmployeeService.upload_employee_photo(
                employee_id=str(employee.id),
                photo_file=photo_file
            )
            
            # Update employee record
            employee.photo_url = photo_url
            employee.save(update_fields=['photo_url'])
            
            # ✅ SYNC: Also update OnboardingRecord photo_url for bidirectional sync
            try:
                from apps.onboarding.models import OnboardingRecord
                onboarding_record = OnboardingRecord.objects.filter(
                    employee_email=employee.email
                ).first()
                if onboarding_record:
                    # Extract S3 key from photo_url (assuming it's structured like employee_photos/)
                    # Use the same URL since it's already in S3
                    onboarding_record.photo_url = photo_url
                    onboarding_record.save(update_fields=['photo_url'])
            except Exception as sync_error:
                # Log but don't fail - sync is optional
                print(f"[WARNING] Failed to sync photo to OnboardingRecord: {str(sync_error)}")
            
            return Response({
                'success': True,
                'message': 'Profile photo uploaded successfully',
                'photo_url': photo_url,
            })
        
        except Exception as e:
            import traceback
            print(f"[ERROR] upload_my_profile_photo: {str(e)}")
            print(traceback.format_exc())
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


