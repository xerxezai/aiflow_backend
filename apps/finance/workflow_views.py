"""
Payroll Workflow API Views
REST endpoints for multi-stage payroll approval workflow
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db import transaction
import logging

from apps.finance.salary_models import PayrollRun
from apps.finance.payroll_workflow import (
    PayrollWorkflow,
    PayrollWorkflowService,
    WorkflowStage,
    WORKFLOW_STAKEHOLDERS,
)
from apps.finance.workflow_serializers import (
    PayrollWorkflowSerializer,
    WorkflowActionSerializer,
    WorkflowStakeholderSerializer,
    WorkflowNotificationLogSerializer,
)

logger = logging.getLogger(__name__)


class PayrollWorkflowViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Payroll Workflow API
    
    Endpoints:
    - GET /workflows/ - List all workflows (filtered by user permissions)
    - GET /workflows/{id}/ - Get workflow details
    - POST /workflows/{id}/submit/ - Submit draft for HR review
    - POST /workflows/{id}/approve_hr/ - HR approval
    - POST /workflows/{id}/approve_accounting/ - Accounting approval
    - POST /workflows/{id}/approve_finance/ - Finance final approval
    - POST /workflows/{id}/reject/ - Reject at any stage
    - GET /workflows/stakeholders/ - Get stakeholder configuration
    - GET /workflows/my_pending/ - Get workflows pending current user's action
    """
    queryset = PayrollWorkflow.objects.select_related(
        'payroll_run',
        'submitted_by',
        'hr_reviewer',
        'accounting_reviewer',
        'finance_reviewer',
        'rejected_by',
        'released_by',
    ).prefetch_related('notification_logs')
    serializer_class = PayrollWorkflowSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """
        Filter workflows based on user role.
        Super admins see all, others see workflows relevant to them.
        """
        queryset = super().get_queryset()
        user = self.request.user
        
        # Super admins see everything
        if user.is_superuser or user.is_staff:
            return queryset
        
        # Filter by user's role in workflow
        user_email = user.email
        
        # Check which stakeholder this user is
        relevant_workflows = []
        for key, config in WORKFLOW_STAKEHOLDERS.items():
            if config['email'] == user_email:
                # User is a stakeholder, show workflows at relevant stages
                if key == 'payroll_admin':
                    # Show drafts and rejected
                    queryset = queryset.filter(
                        current_stage__in=[WorkflowStage.DRAFT, WorkflowStage.REJECTED]
                    )
                elif key == 'hr_manager':
                    # Show HR review stage
                    queryset = queryset.filter(current_stage=WorkflowStage.HR_REVIEW)
                elif key == 'accounting':
                    # Show accounting review stage
                    queryset = queryset.filter(current_stage=WorkflowStage.ACCOUNTING_REVIEW)
                elif key == 'finance':
                    # Show finance review stage
                    queryset = queryset.filter(current_stage=WorkflowStage.FINANCE_REVIEW)
                break
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def stakeholders(self, request):
        """Get workflow stakeholder configuration"""
        serializer = WorkflowStakeholderSerializer(
            [{'key': k, **v} for k, v in WORKFLOW_STAKEHOLDERS.items()],
            many=True
        )
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_pending(self, request):
        """Get workflows pending current user's action"""
        user_email = request.user.email
        
        # Determine user's role
        user_role = None
        for key, config in WORKFLOW_STAKEHOLDERS.items():
            if config['email'] == user_email:
                user_role = key
                break
        
        if not user_role:
            return Response({'pending': []})
        
        # Filter workflows pending this user's action
        pending_stage_map = {
            'payroll_admin': WorkflowStage.DRAFT,  # Can submit
            'hr_manager': WorkflowStage.HR_REVIEW,
            'accounting': WorkflowStage.ACCOUNTING_REVIEW,
            'finance': WorkflowStage.FINANCE_REVIEW,
        }
        
        pending_stage = pending_stage_map.get(user_role)
        if not pending_stage:
            return Response({'pending': []})
        
        workflows = self.get_queryset().filter(current_stage=pending_stage)
        serializer = self.get_serializer(workflows, many=True)
        
        return Response({
            'pending': serializer.data,
            'count': workflows.count(),
            'role': user_role,
            'stage': pending_stage,
        })
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def submit(self, request, pk=None):
        """Submit draft payroll for HR review"""
        workflow = self.get_object()
        
        # Validate user permission
        user_email = request.user.email
        payroll_admin_email = WORKFLOW_STAKEHOLDERS['payroll_admin']['email']
        
        if user_email != payroll_admin_email:
            return Response(
                {'error': 'Only payroll admin can submit for review'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if workflow.current_stage != WorkflowStage.DRAFT:
            return Response(
                {'error': f'Cannot submit: workflow is in {workflow.get_current_stage_display()} stage'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            updated_workflow = PayrollWorkflowService.submit_for_review(
                payroll_run=workflow.payroll_run,
                submitted_by=request.user
            )
            serializer = self.get_serializer(updated_workflow)
            return Response({
                'success': True,
                'message': 'Payroll submitted for HR review successfully',
                'workflow': serializer.data
            })
        except Exception as e:
            logger.error(f"Failed to submit workflow: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def approve_hr(self, request, pk=None):
        """HR Manager approves payroll"""
        workflow = self.get_object()
        action_serializer = WorkflowActionSerializer(
            data=request.data,
            context={'action': 'approve_hr'}
        )
        action_serializer.is_valid(raise_exception=True)
        
        # Validate user permission
        user_email = request.user.email
        hr_email = WORKFLOW_STAKEHOLDERS['hr_manager']['email']
        
        if user_email != hr_email:
            return Response(
                {'error': 'Only HR Manager can approve at this stage'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if workflow.current_stage != WorkflowStage.HR_REVIEW:
            return Response(
                {'error': f'Cannot approve: workflow is in {workflow.get_current_stage_display()} stage'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            updated_workflow = PayrollWorkflowService.approve_hr(
                payroll_run=workflow.payroll_run,
                reviewer=request.user,
                comments=action_serializer.validated_data.get('comments', '')
            )
            serializer = self.get_serializer(updated_workflow)
            return Response({
                'success': True,
                'message': 'Payroll approved by HR, forwarded to Accounting',
                'workflow': serializer.data
            })
        except Exception as e:
            logger.error(f"Failed to approve (HR): {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def approve_accounting(self, request, pk=None):
        """Accounting approves payroll"""
        workflow = self.get_object()
        action_serializer = WorkflowActionSerializer(
            data=request.data,
            context={'action': 'approve_accounting'}
        )
        action_serializer.is_valid(raise_exception=True)
        
        # Validate user permission
        user_email = request.user.email
        accounting_email = WORKFLOW_STAKEHOLDERS['accounting']['email']
        
        if user_email != accounting_email:
            return Response(
                {'error': 'Only Accounting can approve at this stage'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if workflow.current_stage != WorkflowStage.ACCOUNTING_REVIEW:
            return Response(
                {'error': f'Cannot approve: workflow is in {workflow.get_current_stage_display()} stage'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            updated_workflow = PayrollWorkflowService.approve_accounting(
                payroll_run=workflow.payroll_run,
                reviewer=request.user,
                comments=action_serializer.validated_data.get('comments', '')
            )
            serializer = self.get_serializer(updated_workflow)
            return Response({
                'success': True,
                'message': 'Payroll approved by Accounting, forwarded to Finance',
                'workflow': serializer.data
            })
        except Exception as e:
            logger.error(f"Failed to approve (Accounting): {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def approve_finance(self, request, pk=None):
        """Finance gives final approval"""
        workflow = self.get_object()
        action_serializer = WorkflowActionSerializer(
            data=request.data,
            context={'action': 'approve_finance'}
        )
        action_serializer.is_valid(raise_exception=True)
        
        # Validate user permission
        user_email = request.user.email
        finance_email = WORKFLOW_STAKEHOLDERS['finance']['email']
        
        if user_email != finance_email:
            return Response(
                {'error': 'Only Finance can approve at this stage'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if workflow.current_stage != WorkflowStage.FINANCE_REVIEW:
            return Response(
                {'error': f'Cannot approve: workflow is in {workflow.get_current_stage_display()} stage'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            updated_workflow = PayrollWorkflowService.approve_finance(
                payroll_run=workflow.payroll_run,
                reviewer=request.user,
                comments=action_serializer.validated_data.get('comments', '')
            )
            serializer = self.get_serializer(updated_workflow)
            return Response({
                'success': True,
                'message': 'Payroll FULLY APPROVED and released to employees!',
                'workflow': serializer.data
            })
        except Exception as e:
            logger.error(f"Failed to approve (Finance): {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def reject(self, request, pk=None):
        """Reject payroll at any stage"""
        workflow = self.get_object()
        action_serializer = WorkflowActionSerializer(
            data=request.data,
            context={'action': 'reject'}
        )
        action_serializer.is_valid(raise_exception=True)
        
        # Validate user permission (must be reviewer at current stage)
        user_email = request.user.email
        
        stage_reviewer_map = {
            WorkflowStage.HR_REVIEW: WORKFLOW_STAKEHOLDERS['hr_manager']['email'],
            WorkflowStage.ACCOUNTING_REVIEW: WORKFLOW_STAKEHOLDERS['accounting']['email'],
            WorkflowStage.FINANCE_REVIEW: WORKFLOW_STAKEHOLDERS['finance']['email'],
        }
        
        allowed_email = stage_reviewer_map.get(workflow.current_stage)
        if user_email != allowed_email:
            return Response(
                {'error': 'You cannot reject at this stage'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            updated_workflow = PayrollWorkflowService.reject(
                payroll_run=workflow.payroll_run,
                reviewer=request.user,
                reason=action_serializer.validated_data['reason']
            )
            serializer = self.get_serializer(updated_workflow)
            return Response({
                'success': True,
                'message': 'Payroll rejected and returned to draft',
                'workflow': serializer.data
            })
        except Exception as e:
            logger.error(f"Failed to reject: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
