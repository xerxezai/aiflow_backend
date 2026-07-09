"""
Payroll Multi-Stage Approval Workflow with Smart Notifications
================================================================
Implements intelligent workflow: Draft → HR Approval → Finance/Accounting Approval → Final Release

SOFT-CODED Configuration for all stakeholders and stages.
All workflow logic centralized in this service for easy maintenance.

Workflow Stages:
1. DRAFT → Michelle.Dehoedt@rejlers.ae (Payroll Admin) creates/finalizes
2. HR_REVIEW → Sanglin.Samuel@rejlers.ae (HR Manager) reviews & approves
3. ACCOUNTING_REVIEW → Aneef.Thadikkarantavida@rejlers.ae (Accounting) reviews
4. FINANCE_REVIEW → Aleksi.Murtomaki@rejlers.ae (Finance) reviews
5. APPROVED → Final release to employees

All transactions visible to Super Administrators.
"""
from decimal import Decimal
from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.conf import settings
import uuid
import logging

from .salary_models import PayrollRun, SalarySlip, SalarySlipApproval, SalarySlipAuditLog

# Import RADAI notification service for smart notifications
from apps.notifications.models import Notification, NotificationCategory
from apps.notifications.services import NotificationService

User = get_user_model()
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SOFT-CODED WORKFLOW CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowStage(models.TextChoices):
    """Soft-coded workflow stages"""
    DRAFT = 'draft', 'Draft'
    HR_REVIEW = 'hr_review', 'HR Review'
    ACCOUNTING_REVIEW = 'accounting_review', 'Accounting Review'
    FINANCE_REVIEW = 'finance_review', 'Finance Review'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'
    RELEASED = 'released', 'Released to Employees'


# Soft-coded stakeholder configuration
WORKFLOW_STAKEHOLDERS = {
    'payroll_admin': {
        'email': 'Michelle.Dehoedt@rejlers.ae',
        'name': 'Michelle Dehoedt',
        'role': 'Payroll Administrator',
        'permissions': ['draft', 'edit', 'submit_for_review'],
    },
    'hr_manager': {
        'email': 'Sanglin.Samuel@rejlers.ae',
        'name': 'Sanglin Samuel',
        'role': 'HR Manager',
        'permissions': ['review', 'approve_hr', 'reject'],
    },
    'accounting': {
        'email': 'Aneef.Thadikkarantavida@rejlers.ae',
        'name': 'Aneef Thadikkarantavida',
        'role': 'Accounting Department',
        'permissions': ['review', 'approve_accounting', 'reject'],
    },
    'finance': {
        'email': 'Aleksi.Murtomaki@rejlers.ae',
        'name': 'Aleksi Murtomaki',
        'role': 'Finance Department',
        'permissions': ['review', 'approve_finance', 'final_release'],
    },
}

# Workflow stage progression (soft-coded sequence)
STAGE_PROGRESSION = {
    WorkflowStage.DRAFT: WorkflowStage.HR_REVIEW,
    WorkflowStage.HR_REVIEW: WorkflowStage.ACCOUNTING_REVIEW,
    WorkflowStage.ACCOUNTING_REVIEW: WorkflowStage.FINANCE_REVIEW,
    WorkflowStage.FINANCE_REVIEW: WorkflowStage.APPROVED,
}

# Notification templates (soft-coded)
NOTIFICATION_TEMPLATES = {
    'hr_review': {
        'subject': 'Payroll Awaiting Your Review - {run_code}',
        'template': '''Dear {recipient_name},

A new payroll run has been finalized and is ready for your review.

Payroll Details:
- Run Code: {run_code}
- Period: {month}/{year}
- Total Employees: {total_employees}
- Total Net Salary: {currency} {total_net_salary}
- Submitted By: {submitted_by}
- Submitted At: {submitted_at}

Please review and approve at:
{review_url}

Note: This payroll will proceed to Accounting & Finance departments upon your approval.

Best regards,
RAD AI Payroll System
''',
    },
    'accounting_review': {
        'subject': 'Payroll for Accounting Review - {run_code}',
        'template': '''Dear {recipient_name},

Payroll run {run_code} has been approved by HR and is now ready for accounting review.

Payroll Details:
- Run Code: {run_code}
- Period: {month}/{year}
- Total Employees: {total_employees}
- Total Gross Salary: {currency} {total_gross_salary}
- Total Deductions: {currency} {total_deductions}
- Total Net Salary: {currency} {total_net_salary}
- HR Approved By: {hr_approver}
- HR Approved At: {hr_approved_at}

Please verify accounting entries and approve at:
{review_url}

Next Step: Finance Department Review

Best regards,
RAD AI Payroll System
''',
    },
    'finance_review': {
        'subject': 'Final Finance Review Required - {run_code}',
        'template': '''Dear {recipient_name},

Payroll run {run_code} has been approved by HR and Accounting departments and is ready for final finance review.

Payroll Summary:
- Run Code: {run_code}
- Period: {month}/{year}
- Total Employees: {total_employees}
- Total Net Salary: {currency} {total_net_salary}
- HR Approved: ✓
- Accounting Approved: ✓

This is the final approval stage before release to employees.

Please conduct final review and approve at:
{review_url}

Best regards,
RAD AI Payroll System
''',
    },
    'approved_notification': {
        'subject': 'Payroll Approved and Released - {run_code}',
        'template': '''Dear {recipient_name},

Payroll run {run_code} has been fully approved and is now released.

Final Summary:
- Run Code: {run_code}
- Period: {month}/{year}
- Total Employees: {total_employees}
- Total Net Salary: {currency} {total_net_salary}

Approval Chain:
✓ HR Manager: {hr_approver} ({hr_approved_at})
✓ Accounting: {accounting_approver} ({accounting_approved_at})
✓ Finance: {finance_approver} ({finance_approved_at})

Salary slips are now being distributed to employees.

Best regards,
RAD AI Payroll System
''',
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW MODELS
# ══════════════════════════════════════════════════════════════════════════════

class PayrollWorkflow(models.Model):
    """
    Tracks multi-stage approval workflow for each PayrollRun.
    Manages progression through stages and notifies stakeholders.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll_run = models.OneToOneField(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='workflow'
    )
    
    # Current workflow state
    current_stage = models.CharField(
        max_length=30,
        choices=WorkflowStage.choices,
        default=WorkflowStage.DRAFT
    )
    
    # Tracking
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submitted_payroll_workflows'
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    
    # HR Approval
    hr_reviewer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hr_reviewed_payrolls'
    )
    hr_reviewed_at = models.DateTimeField(null=True, blank=True)
    hr_comments = models.TextField(blank=True)
    
    # Accounting Approval
    accounting_reviewer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accounting_reviewed_payrolls'
    )
    accounting_reviewed_at = models.DateTimeField(null=True, blank=True)
    accounting_comments = models.TextField(blank=True)
    
    # Finance Approval
    finance_reviewer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='finance_reviewed_payrolls'
    )
    finance_reviewed_at = models.DateTimeField(null=True, blank=True)
    finance_comments = models.TextField(blank=True)
    
    # Rejection tracking
    rejected_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rejected_payroll_workflows'
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    rejection_stage = models.CharField(max_length=30, blank=True)
    
    # Final release
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='released_payroll_workflows'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'finance_payroll_workflow'
        verbose_name = 'Payroll Workflow'
        verbose_name_plural = 'Payroll Workflows'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['payroll_run']),
            models.Index(fields=['current_stage']),
            models.Index(fields=['submitted_at']),
        ]
    
    def __str__(self):
        return f"Workflow for {self.payroll_run.run_code} - {self.get_current_stage_display()}"


class WorkflowNotificationLog(models.Model):
    """
    Audit trail for all workflow notifications sent.
    Ensures full visibility for super admins.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        PayrollWorkflow,
        on_delete=models.CASCADE,
        related_name='notification_logs'
    )
    
    # Notification details
    notification_type = models.CharField(max_length=50)  # e.g., 'hr_review', 'accounting_review'
    recipient_email = models.EmailField()
    recipient_name = models.CharField(max_length=200)
    subject = models.CharField(max_length=500)
    message_body = models.TextField()
    
    # Delivery tracking
    sent_at = models.DateTimeField(auto_now_add=True)
    delivery_status = models.CharField(
        max_length=20,
        choices=[
            ('sent', 'Sent'),
            ('failed', 'Failed'),
            ('pending', 'Pending'),
        ],
        default='pending'
    )
    error_message = models.TextField(blank=True)
    
    # Audit
    triggered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='triggered_workflow_notifications'
    )
    
    class Meta:
        db_table = 'finance_workflow_notification_log'
        verbose_name = 'Workflow Notification Log'
        verbose_name_plural = 'Workflow Notification Logs'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['workflow']),
            models.Index(fields=['notification_type']),
            models.Index(fields=['delivery_status']),
        ]
    
    def __str__(self):
        return f"{self.notification_type} → {self.recipient_email} ({self.delivery_status})"


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW SERVICE
# ══════════════════════════════════════════════════════════════════════════════

class PayrollWorkflowService:
    """
    Service layer for payroll workflow operations.
    Handles stage transitions, notifications, and audit logging.
    """
    
    @staticmethod
    @transaction.atomic
    def initialize_workflow(payroll_run, created_by):
        """
        Create workflow instance for a new payroll run.
        """
        workflow, created = PayrollWorkflow.objects.get_or_create(
            payroll_run=payroll_run,
            defaults={'submitted_by': created_by}
        )
        return workflow
    
    @staticmethod
    @transaction.atomic
    def submit_for_review(payroll_run, submitted_by):
        """
        Finalize draft and submit to HR for review.
        Sends notification to HR Manager.
        """
        workflow = PayrollWorkflow.objects.get(payroll_run=payroll_run)
        
        if workflow.current_stage != WorkflowStage.DRAFT:
            raise ValueError(f"Cannot submit: workflow is in {workflow.current_stage} stage")
        
        # Update workflow
        workflow.current_stage = WorkflowStage.HR_REVIEW
        workflow.submitted_by = submitted_by
        workflow.submitted_at = timezone.now()
        workflow.save()
        
        # Send notification to HR Manager
        PayrollWorkflowService._send_notification(
            workflow=workflow,
            notification_type='hr_review',
            recipient_config=WORKFLOW_STAKEHOLDERS['hr_manager'],
            triggered_by=submitted_by
        )
        
        # Audit log
        SalarySlipAuditLog.objects.create(
            salary_slip=payroll_run.salary_slips.first(),  # Create run-level audit
            action='submitted_for_review',
            performed_by=submitted_by,
            description=f'Payroll run {payroll_run.run_code} submitted for HR review'
        )
        
        logger.info(f"Payroll {payroll_run.run_code} submitted for HR review by {submitted_by}")
        return workflow
    
    @staticmethod
    @transaction.atomic
    def approve_hr(payroll_run, reviewer, comments=''):
        """
        HR Manager approves payroll, forwards to Accounting.
        """
        workflow = PayrollWorkflow.objects.get(payroll_run=payroll_run)
        
        if workflow.current_stage != WorkflowStage.HR_REVIEW:
            raise ValueError(f"Cannot approve: workflow is in {workflow.current_stage} stage")
        
        # Update workflow
        workflow.current_stage = WorkflowStage.ACCOUNTING_REVIEW
        workflow.hr_reviewer = reviewer
        workflow.hr_reviewed_at = timezone.now()
        workflow.hr_comments = comments
        workflow.save()
        
        # Send notification to Accounting
        PayrollWorkflowService._send_notification(
            workflow=workflow,
            notification_type='accounting_review',
            recipient_config=WORKFLOW_STAKEHOLDERS['accounting'],
            triggered_by=reviewer
        )
        
        logger.info(f"Payroll {payroll_run.run_code} approved by HR, forwarded to Accounting")
        return workflow
    
    @staticmethod
    @transaction.atomic
    def approve_accounting(payroll_run, reviewer, comments=''):
        """
        Accounting approves payroll, forwards to Finance.
        """
        workflow = PayrollWorkflow.objects.get(payroll_run=payroll_run)
        
        if workflow.current_stage != WorkflowStage.ACCOUNTING_REVIEW:
            raise ValueError(f"Cannot approve: workflow is in {workflow.current_stage} stage")
        
        # Update workflow
        workflow.current_stage = WorkflowStage.FINANCE_REVIEW
        workflow.accounting_reviewer = reviewer
        workflow.accounting_reviewed_at = timezone.now()
        workflow.accounting_comments = comments
        workflow.save()
        
        # Send notification to Finance
        PayrollWorkflowService._send_notification(
            workflow=workflow,
            notification_type='finance_review',
            recipient_config=WORKFLOW_STAKEHOLDERS['finance'],
            triggered_by=reviewer
        )
        
        logger.info(f"Payroll {payroll_run.run_code} approved by Accounting, forwarded to Finance")
        return workflow
    
    @staticmethod
    @transaction.atomic
    def approve_finance(payroll_run, reviewer, comments=''):
        """
        Finance gives final approval, releases payroll to employees.
        """
        workflow = PayrollWorkflow.objects.get(payroll_run=payroll_run)
        
        if workflow.current_stage != WorkflowStage.FINANCE_REVIEW:
            raise ValueError(f"Cannot approve: workflow is in {workflow.current_stage} stage")
        
        # Update workflow
        workflow.current_stage = WorkflowStage.APPROVED
        workflow.finance_reviewer = reviewer
        workflow.finance_reviewed_at = timezone.now()
        workflow.finance_comments = comments
        workflow.released_at = timezone.now()
        workflow.released_by = reviewer
        workflow.save()
        
        # Notify all stakeholders of final approval
        for stakeholder_key in ['payroll_admin', 'hr_manager', 'accounting', 'finance']:
            PayrollWorkflowService._send_notification(
                workflow=workflow,
                notification_type='approved_notification',
                recipient_config=WORKFLOW_STAKEHOLDERS[stakeholder_key],
                triggered_by=reviewer
            )
        
        logger.info(f"Payroll {payroll_run.run_code} FULLY APPROVED by Finance and released")
        return workflow
    
    @staticmethod
    @transaction.atomic
    def reject(payroll_run, reviewer, reason):
        """
        Reject payroll at any stage, returns to draft.
        """
        workflow = PayrollWorkflow.objects.get(payroll_run=payroll_run)
        
        # Track rejection
        workflow.rejected_by = reviewer
        workflow.rejected_at = timezone.now()
        workflow.rejection_reason = reason
        workflow.rejection_stage = workflow.current_stage
        workflow.current_stage = WorkflowStage.REJECTED
        workflow.save()
        
        # Notify payroll admin of rejection
        PayrollWorkflowService._send_notification(
            workflow=workflow,
            notification_type='rejection_notification',
            recipient_config=WORKFLOW_STAKEHOLDERS['payroll_admin'],
            triggered_by=reviewer,
            custom_message=f"Payroll rejected at {workflow.get_rejection_stage_display()} stage.\n\nReason: {reason}"
        )
        
        logger.warning(f"Payroll {payroll_run.run_code} REJECTED by {reviewer}: {reason}")
        return workflow
    
    @staticmethod
    def _send_notification(workflow, notification_type, recipient_config, triggered_by, custom_message=None):
        """
        Send notification using RADAI's smart notification system.
        Creates both in-app notification and email.
        """
        try:
            template = NOTIFICATION_TEMPLATES.get(notification_type, {})
            payroll_run = workflow.payroll_run
            
            # Build context
            context = {
                'recipient_name': recipient_config['name'],
                'run_code': payroll_run.run_code,
                'month': payroll_run.month,
                'year': payroll_run.year,
                'total_employees': payroll_run.total_employees,
                'total_gross_salary': f"{payroll_run.total_gross_salary:,.2f}",
                'total_deductions': f"{payroll_run.total_deductions:,.2f}",
                'total_net_salary': f"{payroll_run.total_net_salary:,.2f}",
                'currency': 'AED',
                'submitted_by': workflow.submitted_by.get_full_name() if workflow.submitted_by else 'System',
                'submitted_at': workflow.submitted_at.strftime('%Y-%m-%d %H:%M') if workflow.submitted_at else '',
                'hr_approver': workflow.hr_reviewer.get_full_name() if workflow.hr_reviewer else 'Pending',
                'hr_approved_at': workflow.hr_reviewed_at.strftime('%Y-%m-%d %H:%M') if workflow.hr_reviewed_at else 'Pending',
                'accounting_approver': workflow.accounting_reviewer.get_full_name() if workflow.accounting_reviewer else 'Pending',
                'accounting_approved_at': workflow.accounting_reviewed_at.strftime('%Y-%m-%d %H:%M') if workflow.accounting_reviewed_at else 'Pending',
                'finance_approver': workflow.finance_reviewer.get_full_name() if workflow.finance_reviewer else 'Pending',
                'finance_approved_at': workflow.finance_reviewed_at.strftime('%Y-%m-%d %H:%M') if workflow.finance_reviewed_at else 'Pending',
                'review_url': f"/hr/payroll?run={payroll_run.id}",
            }
            
            subject = template.get('subject', f'Payroll Notification - {payroll_run.run_code}').format(**context)
            message = (custom_message or template.get('template', '')).format(**context)
            
            # Get recipient user by email
            try:
                recipient_user = User.objects.get(email=recipient_config['email'])
            except User.DoesNotExist:
                logger.warning(f"User not found for email: {recipient_config['email']}")
                # Log failed attempt
                WorkflowNotificationLog.objects.create(
                    workflow=workflow,
                    notification_type=notification_type,
                    recipient_email=recipient_config['email'],
                    recipient_name=recipient_config['name'],
                    subject=subject,
                    message_body=message,
                    delivery_status='failed',
                    error_message=f'User not found: {recipient_config["email"]}',
                    triggered_by=triggered_by
                )
                return False
            
            # Get or create APPROVAL category
            approval_category, _ = NotificationCategory.objects.get_or_create(
                name='APPROVAL',
                defaults={
                    'description': 'Approval Required',
                    'icon': '✅',
                    'color': 'yellow',
                }
            )
            
            # Determine priority based on notification type
            priority_map = {
                'hr_review': 'HIGH',
                'accounting_review': 'HIGH',
                'finance_review': 'URGENT',
                'approved_notification': 'NORMAL',
                'rejection_notification': 'CRITICAL',
            }
            priority = priority_map.get(notification_type, 'NORMAL')
            
            # Create in-app notification using RADAI's notification system
            notification = Notification.objects.create(
                title=subject,
                message=message,
                recipient=recipient_user,
                sender=triggered_by,
                category=approval_category,
                priority=priority,
                send_in_app=True,
                send_email=True,
                action_url=context['review_url'],
                action_label='Review Payroll',
                metadata={
                    'workflow_id': str(workflow.id),
                    'payroll_run_id': str(payroll_run.id),
                    'run_code': payroll_run.run_code,
                    'notification_type': notification_type,
                    'stage': workflow.current_stage,
                }
            )
            
            # Send email using Django mail
            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient_config['email']],
                    fail_silently=False
                )
                notification.mark_email_sent(success=True)
                delivery_status = 'sent'
                error_msg = ''
            except Exception as email_error:
                logger.error(f"Email send failed: {str(email_error)}")
                notification.mark_email_sent(success=False, error_message=str(email_error))
                delivery_status = 'failed'
                error_msg = str(email_error)
            
            # Log notification in workflow log
            WorkflowNotificationLog.objects.create(
                workflow=workflow,
                notification_type=notification_type,
                recipient_email=recipient_config['email'],
                recipient_name=recipient_config['name'],
                subject=subject,
                message_body=message,
                delivery_status=delivery_status,
                error_message=error_msg,
                triggered_by=triggered_by
            )
            
            logger.info(f"Notification created: {notification_type} → {recipient_config['email']} (In-app ID: {notification.id})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create notification: {str(e)}")
            
            # Log failed attempt
            WorkflowNotificationLog.objects.create(
                workflow=workflow,
                notification_type=notification_type,
                recipient_email=recipient_config.get('email', 'unknown'),
                recipient_name=recipient_config.get('name', 'Unknown'),
                subject=f"Payroll Notification - {workflow.payroll_run.run_code}",
                message_body=custom_message or '',
                delivery_status='failed',
                error_message=str(e),
                triggered_by=triggered_by
            )
            return False
    
    @staticmethod
    def get_workflow_status(payroll_run):
        """
        Get current workflow status with full details.
        """
        try:
            workflow = PayrollWorkflow.objects.select_related(
                'submitted_by', 'hr_reviewer', 'accounting_reviewer',
                'finance_reviewer', 'rejected_by', 'released_by'
            ).get(payroll_run=payroll_run)
            
            return {
                'current_stage': workflow.current_stage,
                'current_stage_display': workflow.get_current_stage_display(),
                'submitted_by': workflow.submitted_by.get_full_name() if workflow.submitted_by else None,
                'submitted_at': workflow.submitted_at,
                'hr_reviewer': workflow.hr_reviewer.get_full_name() if workflow.hr_reviewer else None,
                'hr_reviewed_at': workflow.hr_reviewed_at,
                'hr_comments': workflow.hr_comments,
                'accounting_reviewer': workflow.accounting_reviewer.get_full_name() if workflow.accounting_reviewer else None,
                'accounting_reviewed_at': workflow.accounting_reviewed_at,
                'accounting_comments': workflow.accounting_comments,
                'finance_reviewer': workflow.finance_reviewer.get_full_name() if workflow.finance_reviewer else None,
                'finance_reviewed_at': workflow.finance_reviewed_at,
                'finance_comments': workflow.finance_comments,
                'rejected_by': workflow.rejected_by.get_full_name() if workflow.rejected_by else None,
                'rejected_at': workflow.rejected_at,
                'rejection_reason': workflow.rejection_reason,
                'rejection_stage': workflow.rejection_stage,
                'released_at': workflow.released_at,
                'released_by': workflow.released_by.get_full_name() if workflow.released_by else None,
            }
        except PayrollWorkflow.DoesNotExist:
            return None
