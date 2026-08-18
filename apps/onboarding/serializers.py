"""
Onboarding & Offboarding Serializers
Transforms model data to/from JSON for API responses
"""
from rest_framework import serializers
from django.db.models import Q
from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist, OFFBOARDING_ACTIVE_STATUSES
)
from .project_assignments import get_active_project_assignments


def _ongoing_project_summaries(record):
    """Return active projects to which the offboarding employee is assigned."""
    cached = getattr(record, '_ongoing_project_summaries_cache', None)
    if cached is not None:
        return cached
    if not record.user_id:
        return []
    summaries = []
    for project in get_active_project_assignments(record.user):
        managers = []
        for manager in project['managers']:
            if not manager:
                continue
            name = manager.get_full_name() or manager.email or manager.username
            if name not in managers:
                managers.append(name)
        summaries.append({
            'id': project['id'],
            'code': project['code'],
            'name': project['name'],
            'project_managers': managers,
            'source': project['source'],
        })
    record._ongoing_project_summaries_cache = summaries
    return summaries


def _can_manage_offboarding_actions(serializer):
    from .rbac import can_manage_offboarding
    request = serializer.context.get('request')
    return can_manage_offboarding(getattr(request, 'user', None))


class EquipmentSerializer(serializers.ModelSerializer):
    """Equipment assignment/return tracking"""
    
    class Meta:
        model = Equipment
        fields = [
            'id', 'equipment_type', 'item_name', 'serial_number', 'asset_tag',
            'assigned_date', 'returned_date', 'condition', 'notes',
            'created_at', 'updated_at'
        ]


class DocumentSerializer(serializers.ModelSerializer):
    """Document collection tracking with file upload support"""
    verified_by_name = serializers.CharField(source='verified_by.get_full_name', read_only=True)
    file = serializers.FileField(write_only=True, required=False)
    
    class Meta:
        model = Document
        fields = [
            'id', 'document_type', 'document_name', 'file_path', 'file_url',
            'file_size', 'file_mime_type', 'original_filename',
            'submitted', 'verified', 'verified_by', 'verified_by_name', 'verified_date',
            'notes', 'created_at', 'updated_at', 'file'
        ]
        read_only_fields = ['file_path', 'file_url', 'file_size', 'file_mime_type', 'original_filename']


class AccessProvisioningSerializer(serializers.ModelSerializer):
    """System access provisioning/revocation tracking"""
    assigned_by_name = serializers.CharField(source='assigned_by.get_full_name', read_only=True)
    
    class Meta:
        model = AccessProvisioning
        fields = [
            'id', 'access_type', 'access_name', 'account_username',
            'provisioned', 'provisioned_date', 'revoked', 'revoked_date',
            'assigned_by', 'assigned_by_name', 'notes',
            'created_at', 'updated_at'
        ]


class ChecklistSerializer(serializers.ModelSerializer):
    """Checklist item tracking"""
    completed_by_name = serializers.CharField(source='completed_by.get_full_name', read_only=True)
    
    class Meta:
        model = Checklist
        fields = [
            'id', 'onboarding_record', 'offboarding_record',
            'task_name', 'description', 'stage', 'completed', 'completed_date',
            'completed_by', 'completed_by_name', 'due_date', 'priority',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['completed_by', 'completed_date']

    def validate(self, attrs):
        onboarding_record = attrs.get(
            'onboarding_record', getattr(self.instance, 'onboarding_record', None)
        )
        offboarding_record = attrs.get(
            'offboarding_record', getattr(self.instance, 'offboarding_record', None)
        )
        if bool(onboarding_record) == bool(offboarding_record):
            raise serializers.ValidationError(
                'A checklist item must belong to exactly one onboarding or offboarding record.'
            )
        if self.instance:
            if 'onboarding_record' in attrs and attrs['onboarding_record'] != self.instance.onboarding_record:
                raise serializers.ValidationError('Checklist lifecycle ownership cannot be changed.')
            if 'offboarding_record' in attrs and attrs['offboarding_record'] != self.instance.offboarding_record:
                raise serializers.ValidationError('Checklist lifecycle ownership cannot be changed.')
            if 'stage' in attrs and attrs['stage'] != self.instance.stage:
                raise serializers.ValidationError('Checklist stage cannot be changed after creation.')
        return attrs


class OnboardingRecordSerializer(serializers.ModelSerializer):
    """
    Onboarding record with nested equipment, documents, access, and checklist
    Includes passport photo upload support
    """
    equipment = EquipmentSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    access_records = AccessProvisioningSerializer(many=True, read_only=True)
    checklist_items = ChecklistSerializer(many=True, read_only=True)
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    
    # Passport photo upload
    photo = serializers.ImageField(write_only=True, required=False, help_text='Passport size photo (JPG/PNG, max 5MB)')
    
    days_until_joining = serializers.SerializerMethodField()
    days_since_initiated = serializers.SerializerMethodField()
    
    # ✅ Engineer profile from EmployeeMaster (read-only for HR visibility)
    engineer_profile = serializers.SerializerMethodField(read_only=True)
    checklist_stage_permissions = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = OnboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id', 'user',
            'position', 'department', 'reporting_manager', 'branch',
            'joining_date', 'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by', 'created_by_name', 'assigned_to', 'assigned_to_name',
            'notes', 'created_at', 'updated_at',
            'equipment', 'documents', 'access_records', 'checklist_items',
            'days_until_joining', 'days_since_initiated',
            'photo', 'photo_file_path', 'photo_url', 'photo_file_size', 'photo_mime_type', 'photo_original_filename',
            'engineer_profile', 'checklist_stage_permissions'
        ]
        read_only_fields = ['photo_file_path', 'photo_url', 'photo_file_size', 'photo_mime_type', 'photo_original_filename']
    
    def get_days_until_joining(self, obj):
        """Calculate days until joining date"""
        from datetime import date
        delta = obj.joining_date - date.today()
        return delta.days
    
    def get_days_since_initiated(self, obj):
        """Calculate days since onboarding was initiated"""
        from datetime import date
        delta = date.today() - obj.initiated_date.date()
        return delta.days
    
    def get_engineer_profile(self, obj):
        """Fetch engineer_profile from EmployeeMaster for HR visibility"""
        if obj.user:
            try:
                from apps.hr_core.models import EmployeeMaster
                employee = EmployeeMaster.objects.filter(user=obj.user).first()
                if employee and employee.engineer_profile:
                    return employee.engineer_profile
            except Exception as e:
                print(f"[WARNING] Failed to fetch engineer_profile: {str(e)}")
        return {}

    def get_checklist_stage_permissions(self, obj):
        from .rbac import onboarding_stage_permissions
        request = self.context.get('request')
        return onboarding_stage_permissions(getattr(request, 'user', None), obj)


class OnboardingRecordListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views (no nested data)
    """
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    days_until_joining = serializers.SerializerMethodField()
    
    # Counts
    equipment_count = serializers.IntegerField(read_only=True)
    documents_count = serializers.IntegerField(read_only=True)
    access_count = serializers.IntegerField(read_only=True)
    checklist_count = serializers.IntegerField(read_only=True)
    checklist_completed_count = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = OnboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id',
            'position', 'department', 'reporting_manager', 'branch',
            'joining_date', 'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by_name', 'assigned_to_name',
            'created_at', 'updated_at',
            'days_until_joining',
            'equipment_count', 'documents_count', 'access_count',
            'checklist_count', 'checklist_completed_count',
            'photo_url', 'photo_original_filename'
        ]
    
    def get_days_until_joining(self, obj):
        from datetime import date
        delta = obj.joining_date - date.today()
        return delta.days


class OffboardingRecordSerializer(serializers.ModelSerializer):
    """
    Offboarding record with nested equipment, documents, access, and checklist
    """
    equipment = EquipmentSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    access_records = AccessProvisioningSerializer(many=True, read_only=True)
    checklist_items = ChecklistSerializer(many=True, read_only=True)
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    
    # ✨ Three-step approval workflow field names
    hr_coordinator_name = serializers.CharField(source='hr_coordinator.get_full_name', read_only=True)
    hr_approver_name = serializers.CharField(source='hr_approver.get_full_name', read_only=True)
    
    days_until_exit = serializers.SerializerMethodField()
    days_since_initiated = serializers.SerializerMethodField()
    checklist_stage_permissions = serializers.SerializerMethodField(read_only=True)
    ongoing_projects = serializers.SerializerMethodField(read_only=True)
    has_ongoing_projects = serializers.SerializerMethodField(read_only=True)
    can_manage_actions = serializers.SerializerMethodField(read_only=True)
    rejected_by_name = serializers.CharField(source='rejected_by.get_full_name', read_only=True)
    project_manager_decided_by_name = serializers.CharField(
        source='project_manager_decided_by.get_full_name', read_only=True
    )
    
    # ✨ Exit approval workflow (project managers list)
    exit_approvals = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = OffboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id', 'user',
            'position', 'department', 'reporting_manager', 'branch',
            'exit_reason', 'exit_reason_detail', 'last_working_day', 'notice_period_days',
            'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by', 'created_by_name', 'assigned_to', 'assigned_to_name',
            'notes', 'rejection_reason', 'rejected_by', 'rejected_by_name',
            'rejected_at', 'created_at', 'updated_at',
            # Legacy project manager approval (deprecated)
            'project_manager_approval_status', 'project_manager_decided_by',
            'project_manager_decided_by_name', 'project_manager_decided_at',
            'project_manager_decision_note',
            # ✨ New three-step approval workflow
            'hr_coordinator', 'hr_coordinator_name', 'hr_coordinator_approval_status',
            'hr_coordinator_approved_at', 'hr_coordinator_note',
            'hr_approver', 'hr_approver_name', 'hr_approver_approval_status',
            'hr_approver_approved_at', 'hr_approver_note',
            'approval_workflow_status', 'exit_approvals',
            # Nested data
            'equipment', 'documents', 'access_records', 'checklist_items',
            'days_until_exit', 'days_since_initiated', 'checklist_stage_permissions',
            'ongoing_projects', 'has_ongoing_projects', 'can_manage_actions'
        ]
        read_only_fields = [
            'rejection_reason', 'rejected_by', 'rejected_at',
            'project_manager_approval_status', 'project_manager_decided_by',
            'project_manager_decided_at', 'project_manager_decision_note',
            'hr_coordinator_approval_status', 'hr_coordinator_approved_at',
            'hr_approver_approval_status', 'hr_approver_approved_at',
            'approval_workflow_status',
        ]

    def get_ongoing_projects(self, obj):
        return _ongoing_project_summaries(obj)

    def get_has_ongoing_projects(self, obj):
        return bool(_ongoing_project_summaries(obj))

    def get_can_manage_actions(self, obj):
        return _can_manage_offboarding_actions(self)

    def get_checklist_stage_permissions(self, obj):
        from .rbac import offboarding_stage_permissions
        request = self.context.get('request')
        return offboarding_stage_permissions(getattr(request, 'user', None), obj)
    
    def get_exit_approvals(self, obj):
        """
        ✨ Return list of exit approvals (project managers, HR coordinator, HR approver)
        Grouped by approval step with status, decision details, and project assignment
        """
        from .models import ExitApproval
        approvals = ExitApproval.objects.filter(offboarding_record=obj).select_related('approver')
        
        return [{
            'id': approval.id,
            'approver_id': approval.approver.id if approval.approver else None,
            'approver_name': approval.approver.get_full_name() if approval.approver else 'Unassigned',
            'approver_email': approval.approver.email if approval.approver else None,
            'approval_step': approval.approval_step,
            'approval_step_display': approval.get_approval_step_display(),
            'status': approval.status,
            'status_display': approval.get_status_display(),
            'decision_note': approval.decision_note,
            'decided_at': approval.decided_at,
            'created_at': approval.created_at,
            # ✨ NEW: Project assignment details for multi-project scenario
            'project_number': approval.project_number,
            'project_name': approval.project_name,
        } for approval in approvals]

    def validate(self, attrs):
        """Prevent more than one active offboarding process per employee."""
        current_status = getattr(self.instance, 'status', None)
        status = attrs.get('status', current_status or 'initiated')

        if status not in OFFBOARDING_ACTIVE_STATUSES:
            return attrs

        user = attrs.get('user', getattr(self.instance, 'user', None))
        employee_email = attrs.get(
            'employee_email', getattr(self.instance, 'employee_email', '')
        )

        identity_filter = Q()
        if user is not None:
            identity_filter |= Q(user=user)
        if employee_email:
            identity_filter |= Q(employee_email__iexact=employee_email.strip())

        if identity_filter:
            active_records = OffboardingRecord.objects.filter(
                identity_filter,
                status__in=OFFBOARDING_ACTIVE_STATUSES,
            )
            if self.instance is not None:
                active_records = active_records.exclude(pk=self.instance.pk)

            if active_records.exists():
                raise serializers.ValidationError({
                    'detail': (
                        'This employee already has an active offboarding process. '
                        'Complete or cancel it before initiating another one.'
                    )
                })

        return attrs
    
    def get_days_until_exit(self, obj):
        """Calculate days until last working day"""
        from datetime import date
        delta = obj.last_working_day - date.today()
        return delta.days
    
    def get_days_since_initiated(self, obj):
        """Calculate days since offboarding was initiated"""
        from datetime import date
        delta = date.today() - obj.initiated_date.date()
        return delta.days


class OffboardingRecordListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views (no nested data)
    """
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    days_until_exit = serializers.SerializerMethodField()
    
    # Counts
    equipment_count = serializers.IntegerField(read_only=True)
    documents_count = serializers.IntegerField(read_only=True)
    access_count = serializers.IntegerField(read_only=True)
    checklist_count = serializers.IntegerField(read_only=True)
    checklist_completed_count = serializers.IntegerField(read_only=True)
    ongoing_projects = serializers.SerializerMethodField(read_only=True)
    has_ongoing_projects = serializers.SerializerMethodField(read_only=True)
    can_manage_actions = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = OffboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id',
            'position', 'department', 'reporting_manager', 'branch',
            'exit_reason', 'last_working_day', 'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by_name', 'assigned_to_name',
            'rejection_reason', 'rejected_at',
            'project_manager_approval_status', 'project_manager_decided_at',
            'project_manager_decision_note',
            'created_at', 'updated_at',
            'days_until_exit',
            'equipment_count', 'documents_count', 'access_count',
            'checklist_count', 'checklist_completed_count',
            'ongoing_projects', 'has_ongoing_projects', 'can_manage_actions'
        ]

    def get_ongoing_projects(self, obj):
        return _ongoing_project_summaries(obj)

    def get_has_ongoing_projects(self, obj):
        return bool(_ongoing_project_summaries(obj))

    def get_can_manage_actions(self, obj):
        return _can_manage_offboarding_actions(self)
    
    def get_days_until_exit(self, obj):
        from datetime import date
        delta = obj.last_working_day - date.today()
        return delta.days
