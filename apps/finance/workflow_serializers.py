"""
Payroll Workflow Serializers
DRF serializers for multi-stage approval workflow API
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.finance.payroll_workflow import (
    PayrollWorkflow,
    WorkflowNotificationLog,
    WorkflowStage,
    WORKFLOW_STAKEHOLDERS,
)
from apps.finance.salary_models import PayrollRun

User = get_user_model()


class WorkflowUserSerializer(serializers.ModelSerializer):
    """Lightweight user serializer for workflow approvers"""
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'first_name', 'last_name']
        read_only_fields = fields


class WorkflowNotificationLogSerializer(serializers.ModelSerializer):
    """Serializer for workflow notification logs (Super Admin view)"""
    triggered_by_name = serializers.CharField(source='triggered_by.get_full_name', read_only=True)
    
    class Meta:
        model = WorkflowNotificationLog
        fields = [
            'id',
            'notification_type',
            'recipient_email',
            'recipient_name',
            'subject',
            'message_body',
            'sent_at',
            'delivery_status',
            'error_message',
            'triggered_by',
            'triggered_by_name',
        ]
        read_only_fields = fields


class PayrollWorkflowSerializer(serializers.ModelSerializer):
    """Full workflow details with approver information"""
    # Approver details
    submitted_by = WorkflowUserSerializer(read_only=True)
    hr_reviewer = WorkflowUserSerializer(read_only=True)
    accounting_reviewer = WorkflowUserSerializer(read_only=True)
    finance_reviewer = WorkflowUserSerializer(read_only=True)
    rejected_by = WorkflowUserSerializer(read_only=True)
    released_by = WorkflowUserSerializer(read_only=True)
    
    # Payroll run info
    payroll_run_code = serializers.CharField(source='payroll_run.run_code', read_only=True)
    payroll_run_month = serializers.IntegerField(source='payroll_run.month', read_only=True)
    payroll_run_year = serializers.IntegerField(source='payroll_run.year', read_only=True)
    total_employees = serializers.IntegerField(source='payroll_run.total_employees', read_only=True)
    total_net_salary = serializers.DecimalField(
        source='payroll_run.total_net_salary',
        max_digits=15,
        decimal_places=2,
        read_only=True
    )
    
    # Display values
    current_stage_display = serializers.CharField(source='get_current_stage_display', read_only=True)
    
    # Notification logs (for super admin)
    notification_logs = WorkflowNotificationLogSerializer(many=True, read_only=True)
    
    # Computed fields
    can_submit = serializers.SerializerMethodField()
    can_approve_hr = serializers.SerializerMethodField()
    can_approve_accounting = serializers.SerializerMethodField()
    can_approve_finance = serializers.SerializerMethodField()
    can_reject = serializers.SerializerMethodField()
    
    class Meta:
        model = PayrollWorkflow
        fields = [
            'id',
            'payroll_run',
            'payroll_run_code',
            'payroll_run_month',
            'payroll_run_year',
            'total_employees',
            'total_net_salary',
            'current_stage',
            'current_stage_display',
            'submitted_by',
            'submitted_at',
            'hr_reviewer',
            'hr_reviewed_at',
            'hr_comments',
            'accounting_reviewer',
            'accounting_reviewed_at',
            'accounting_comments',
            'finance_reviewer',
            'finance_reviewed_at',
            'finance_comments',
            'rejected_by',
            'rejected_at',
            'rejection_reason',
            'rejection_stage',
            'released_at',
            'released_by',
            'created_at',
            'updated_at',
            'notification_logs',
            'can_submit',
            'can_approve_hr',
            'can_approve_accounting',
            'can_approve_finance',
            'can_reject',
        ]
        read_only_fields = [
            'id', 'current_stage', 'submitted_at', 'hr_reviewed_at',
            'accounting_reviewed_at', 'finance_reviewed_at', 'rejected_at',
            'released_at', 'created_at', 'updated_at',
        ]
    
    def get_can_submit(self, obj):
        """Check if current user can submit for review"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        # Only payroll admin can submit drafts
        user_email = request.user.email
        payroll_admin_email = WORKFLOW_STAKEHOLDERS['payroll_admin']['email']
        
        return (
            obj.current_stage == WorkflowStage.DRAFT and
            user_email == payroll_admin_email
        )
    
    def get_can_approve_hr(self, obj):
        """Check if current user can approve as HR"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        user_email = request.user.email
        hr_email = WORKFLOW_STAKEHOLDERS['hr_manager']['email']
        
        return (
            obj.current_stage == WorkflowStage.HR_REVIEW and
            user_email == hr_email
        )
    
    def get_can_approve_accounting(self, obj):
        """Check if current user can approve as Accounting"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        user_email = request.user.email
        accounting_email = WORKFLOW_STAKEHOLDERS['accounting']['email']
        
        return (
            obj.current_stage == WorkflowStage.ACCOUNTING_REVIEW and
            user_email == accounting_email
        )
    
    def get_can_approve_finance(self, obj):
        """Check if current user can approve as Finance"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        user_email = request.user.email
        finance_email = WORKFLOW_STAKEHOLDERS['finance']['email']
        
        return (
            obj.current_stage == WorkflowStage.FINANCE_REVIEW and
            user_email == finance_email
        )
    
    def get_can_reject(self, obj):
        """Check if current user can reject at current stage"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        # Any reviewer can reject at their stage
        user_email = request.user.email
        
        stage_reviewer_map = {
            WorkflowStage.HR_REVIEW: WORKFLOW_STAKEHOLDERS['hr_manager']['email'],
            WorkflowStage.ACCOUNTING_REVIEW: WORKFLOW_STAKEHOLDERS['accounting']['email'],
            WorkflowStage.FINANCE_REVIEW: WORKFLOW_STAKEHOLDERS['finance']['email'],
        }
        
        allowed_email = stage_reviewer_map.get(obj.current_stage)
        return user_email == allowed_email if allowed_email else False


class WorkflowActionSerializer(serializers.Serializer):
    """Serializer for workflow actions (approve/reject)"""
    comments = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)  # For rejection
    
    def validate(self, data):
        # If rejecting, require reason
        action = self.context.get('action')
        if action == 'reject' and not data.get('reason'):
            raise serializers.ValidationError({
                'reason': 'Rejection reason is required'
            })
        return data


class WorkflowStakeholderSerializer(serializers.Serializer):
    """Serializer for workflow stakeholder configuration (for frontend)"""
    email = serializers.EmailField()
    name = serializers.CharField()
    role = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())
