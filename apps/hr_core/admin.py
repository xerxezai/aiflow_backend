"""
HR Core Admin - Django Admin Interface
"""
from django.contrib import admin
from apps.hr_core.models import EmployeeMaster


@admin.register(EmployeeMaster)
class EmployeeMasterAdmin(admin.ModelAdmin):
    """Admin interface for Employee Master records."""
    
    list_display = [
        'employee_number',
        'get_full_name',
        'email',
        'department',
        'designation',
        'employment_status',
        'join_date',
        'branch',
    ]
    
    list_filter = [
        'employment_status',
        'branch',
        'department',
        'division',
        'join_date',
    ]
    
    search_fields = [
        'employee_number',
        'employee_code',
        'emp_code',
        'email',
        'first_name',
        'last_name',
    ]
    
    readonly_fields = [
        'id',
        'employee_number',
        'employee_code',
        'emp_code',
        'created_at',
        'updated_at',
        'photo_uploaded_at',
    ]
    
    fieldsets = (
        ('Identity', {
            'fields': (
                'id',
                'employee_number',
                'employee_code',
                'emp_code',
                'user',
                'email',
            )
        }),
        ('Personal Information', {
            'fields': (
                'first_name',
                'last_name',
                'preferred_given_name',
                'initials',
                'date_of_birth',
            )
        }),
        ('Photo', {
            'fields': (
                'photo_file_path',
                'photo_url',
                'photo_file_size',
                'photo_mime_type',
                'photo_uploaded_at',
            )
        }),
        ('Organization', {
            'fields': (
                'manager',
                'department',
                'division',
                'business_unit',
                'business_area',
                'office',
                'branch',
            )
        }),
        ('Job Information', {
            'fields': (
                'job_title_uae',
                'job_title_finland',
                'designation',
            )
        }),
        ('Employment', {
            'fields': (
                'join_date',
                'probation_end_date',
                'confirmation_date',
                'exit_date',
                'employment_status',
            )
        }),
        ('Salary', {
            'fields': (
                'current_base_salary',
                'currency',
            )
        }),
        ('Contact', {
            'fields': (
                'phone_number',
                'country',
                'city',
                'address',
                'postal_code',
            )
        }),
        ('Banking & Payroll', {
            'fields': (
                'bank_account_number',
                'bank_name',
                'iban',
                'swift_code',
            )
        }),
        ('Tax & Compliance', {
            'fields': (
                'pan_number',
                'uan_number',
                'tax_id',
            )
        }),
        ('System Integration', {
            'fields': (
                'employment_id',
                'candidate_id',
                'account_name',
            )
        }),
        ('Flags & Metadata', {
            'fields': (
                'is_test_person',
                'protected_identity',
                'not_signed',
                'created_by',
                'last_updated_by',
                'created_at',
                'updated_at',
            )
        }),
    )
    
    def get_full_name(self, obj):
        """Display full name."""
        return obj.get_full_name()
    get_full_name.short_description = 'Full Name'
