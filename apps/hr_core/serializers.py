"""
HR Core Serializers - Employee Master API Serialization
"""
from rest_framework import serializers
from apps.hr_core.models import EmployeeMaster
from apps.users.serializers import UserSerializer


class EmployeeMasterListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for employee lists.
    Used in dropdowns, search results, etc.
    """
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    class Meta:
        model = EmployeeMaster
        fields = [
            'id',
            'employee_number',
            'employee_code',
            'email',
            'full_name',
            'display_name',
            'department',
            'designation',
            'employment_status',
            'photo_url',
        ]
        read_only_fields = fields


class EmployeeMasterDetailSerializer(serializers.ModelSerializer):
    """
    Full employee details serializer.
    Used for employee profile, onboarding, etc.
    """
    user = UserSerializer(read_only=True)
    manager = EmployeeMasterListSerializer(read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    # Photo upload field (write-only)
    photo = serializers.ImageField(write_only=True, required=False)
    
    class Meta:
        model = EmployeeMaster
        fields = [
            # Identity
            'id',
            'employee_number',
            'employee_code',
            'emp_code',
            'email',
            
            # Relations
            'user',
            'manager',
            
            # Personal
            'first_name',
            'last_name',
            'preferred_given_name',
            'initials',
            'full_name',
            'display_name',
            'date_of_birth',
            
            # Photo
            'photo',  # write-only
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            
            # Organization
            'department',
            'division',
            'business_unit',
            'business_area',
            'office',
            'branch',
            
            # Job
            'job_title_uae',
            'job_title_finland',
            'designation',
            
            # Employment
            'join_date',
            'probation_end_date',
            'confirmation_date',
            'exit_date',
            'employment_status',
            
            # Salary
            'current_base_salary',
            'currency',
            
            # Contact
            'phone_number',
            'country',
            'city',
            'address',
            'postal_code',
            
            # Banking
            'bank_account_number',
            'bank_name',
            'iban',
            'swift_code',
            
            # Tax
            'pan_number',
            'uan_number',
            'tax_id',
            
            # Integration
            'employment_id',
            'candidate_id',
            'account_name',
            
            # Flags
            'is_test_person',
            'protected_identity',
            'not_signed',
            
            # Metadata
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'employee_number',
            'employee_code',
            'emp_code',
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            'created_at',
            'updated_at',
        ]
    
    def create(self, validated_data):
        """Not used - employee creation goes through EmployeeService."""
        raise NotImplementedError("Use EmployeeService.create_employee() instead")
    
    def update(self, instance, validated_data):
        """Update employee, handling photo upload if present."""
        photo = validated_data.pop('photo', None)
        
        # Update all other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        
        # Handle photo upload separately through service
        if photo:
            from apps.hr_core.services import EmployeeService
            request_user = self.context.get('request').user if self.context.get('request') else None
            EmployeeService.upload_employee_photo(instance, photo, uploaded_by=request_user)
        
        return instance
