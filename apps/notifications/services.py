"""
Notification Service - Central notification management
Handles creation, delivery, and tracking of notifications
"""
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.utils import timezone
from .models import Notification, NotificationCategory, NotificationPreference, NotificationLog
from celery import shared_task
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Centralized notification service with multi-channel support
    """
    
    # Soft-coded notification templates for all modules
    TEMPLATES = {
        # QHSE Templates
        'QHSE_SPOT_CHECK_CREATED': {
            'title': '🔍 New Spot Check Created',
            'message': 'A new spot check has been created for project {project_no}. Category: {category}',
            'priority': 'NORMAL',
            'category': 'QHSE',
            'action_label': 'View Spot Check',
        },
        'QHSE_CAR_URGENT': {
            'title': '🚨 Urgent CAR Requires Attention',
            'message': 'Critical CAR #{car_id} needs immediate action. Project: {project_no}',
            'priority': 'URGENT',
            'category': 'QHSE',
            'send_email': True,
            'action_label': 'View CAR',
        },
        
        # Project Templates
        'PROJECT_CREATED': {
            'title': '📁 New Project Created',
            'message': 'Project {project_name} ({project_no}) has been created and assigned to you.',
            'priority': 'NORMAL',
            'category': 'PROJECT',
            'send_email': True,
            'action_label': 'View Project',
        },
        'PROJECT_DEADLINE_APPROACHING': {
            'title': '⏰ Project Deadline Approaching',
            'message': 'Project {project_no} deadline is in {days} days. Completion: {completion}%',
            'priority': 'HIGH',
            'category': 'PROJECT',
            'send_email': True,
        },
        
        # Document Templates
        'DOCUMENT_APPROVAL_NEEDED': {
            'title': '📄 Document Approval Required',
            'message': '{document_type} "{document_number}" is pending your approval.',
            'priority': 'HIGH',
            'category': 'APPROVAL',
            'send_email': True,
            'action_label': 'Review Document',
        },
        
        # AI Templates
        'AI_MODEL_ALERT': {
            'title': '🤖 AI Model Alert',
            'message': 'AI model "{model_name}" detected an anomaly: {details}',
            'priority': 'HIGH',
            'category': 'AI',
            'action_label': 'View Details',
        },
        'AI_ANALYSIS_COMPLETE': {
            'title': '✅ AI Analysis Complete',
            'message': 'Analysis of {document_type} is complete. {result_summary}',
            'priority': 'NORMAL',
            'category': 'AI',
            'send_email': True,
            'action_label': 'View Results',
        },
        
        # Payroll Templates
        'PAYROLL_FROZEN': {
            'title': '\U0001f9ca Payroll File Frozen — Awaiting HR Approval',
            'message': (
                'The {period} master payroll file has been frozen by {frozen_by}. '
                '{total_rows} employee records are locked. '
                'Please review and proceed with HR approval.'
            ),
            'priority': 'HIGH',
            'category': 'APPROVAL',
            'send_email': True,
            'action_label': 'Open Payroll Engine',
            'action_url': '/hr/payroll',
        },

        # System Templates
        'SYSTEM_MAINTENANCE': {
            'title': '🔧 System Maintenance Scheduled',
            'message': 'System maintenance scheduled for {date}. Expected downtime: {duration}',
            'priority': 'NORMAL',
            'category': 'SYSTEM',
            'send_email': True,
        },
        
        # User Templates
        'USER_WELCOME': {
            'title': '👋 Welcome to RAD AI Platform!',
            'message': 'Your account has been created. Please update your profile and change your default password.',
            'priority': 'NORMAL',
            'category': 'USER',
            'send_email': True,
            'action_label': 'Update Profile',
        },

        # Enquiry / Password Reset Templates
        'ENQUIRY_PASSWORD_RESET_REQUEST': {
            'title': '🔐 Password Reset Request',
            'message': (
                '{user_email} has requested a password reset. '
                'Please verify identity and reset via User Management.'
            ),
            'priority': 'HIGH',
            'category': 'USER',
            'action_label': 'Open Enquiries',
            'action_url': '/admin/enquiries',
        },
    }
    
    @classmethod
    def create_notification(cls, recipient, template_key=None, **kwargs):
        """
        Create a notification using a template or custom parameters
        
        Args:
            recipient: User object
            template_key: Key from TEMPLATES dict
            **kwargs: Additional parameters to override template or custom notification
        
        Returns:
            Notification object
        """
        try:
            # Get or create user preferences
            prefs, _ = NotificationPreference.objects.get_or_create(user=recipient)
            
            # Start with template if provided
            if template_key and template_key in cls.TEMPLATES:
                template = cls.TEMPLATES[template_key].copy()
                
                # Format message with provided context
                if 'message' in template:
                    template['message'] = template['message'].format(**kwargs)
                
                # Merge kwargs
                notification_data = {**template, **kwargs}
            else:
                notification_data = kwargs
            
            # Get or create category
            category_name = notification_data.get('category', 'INFO')
            category, _ = NotificationCategory.objects.get_or_create(
                name=category_name,
                defaults={
                    'description': f'{category_name} notifications',
                    'icon': cls._get_category_icon(category_name)
                }
            )
            
            # Determine channels based on priority and preferences
            priority = notification_data.get('priority', 'NORMAL')
            send_email = notification_data.get('send_email', False)
            
            # Auto-enable email for urgent/critical
            if priority in ['URGENT', 'CRITICAL']:
                send_email = send_email or prefs.enable_email
            
            # Create notification
            notification = Notification.objects.create(
                recipient=recipient,
                sender=notification_data.get('sender'),
                title=notification_data.get('title', 'Notification'),
                message=notification_data.get('message', ''),
                category=category,
                priority=priority,
                action_url=notification_data.get('action_url'),
                action_label=notification_data.get('action_label'),
                send_in_app=prefs.enable_in_app,
                send_email=send_email and prefs.enable_email,
                send_sms=notification_data.get('send_sms', False) and prefs.enable_sms,
                metadata=notification_data.get('metadata', {}),
            )
            
            # Log creation
            NotificationLog.objects.create(
                notification=notification,
                action='created',
                details={'template': template_key or 'custom'}
            )
            
            # Send email asynchronously if enabled
            if notification.send_email:
                send_notification_email.delay(notification.id)
            
            return notification
            
        except Exception as e:
            logger.error(f"Error creating notification: {str(e)}")
            return None
    
    @classmethod
    def bulk_notify(cls, recipients, template_key=None, **kwargs):
        """
        Create notifications for multiple recipients
        """
        notifications = []
        for recipient in recipients:
            notif = cls.create_notification(recipient, template_key, **kwargs)
            if notif:
                notifications.append(notif)
        return notifications
    
    @classmethod
    def _get_category_icon(cls, category):
        """Get icon for category"""
        icons = {
            'SYSTEM': '⚙️',
            'PROJECT': '📊',
            'QHSE': '🛡️',
            'DOCUMENT': '📄',
            'USER': '👤',
            'ADMIN': '🔐',
            'AI': '🤖',
            'APPROVAL': '✅',
            'ALERT': '🚨',
            'INFO': '📢',
        }
        return icons.get(category, '📢')


@shared_task(bind=True, max_retries=3)
def send_notification_email(self, notification_id):
    """
    Celery task to send notification email
    """
    try:
        notification = Notification.objects.get(id=notification_id)
        
        # Build email
        subject = f"[RAD AI] {notification.title}"
        
        # HTML content
        html_content = render_to_string('notifications/email.html', {
            'notification': notification,
            'recipient': notification.recipient,
            'action_url': notification.action_url,
            'action_label': notification.action_label,
            'priority_color': {
                'LOW': '#10B981',
                'NORMAL': '#3B82F6',
                'HIGH': '#F59E0B',
                'URGENT': '#EF4444',
                'CRITICAL': '#DC2626',
            }.get(notification.priority, '#3B82F6')
        })
        
        # Plain text fallback
        text_content = strip_tags(html_content)
        
        # Send email
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[notification.recipient.email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        
        # Mark as sent
        notification.mark_email_sent(success=True)
        
        # Log
        NotificationLog.objects.create(
            notification=notification,
            action='email_sent',
            details={'to': notification.recipient.email}
        )
        
        logger.info(f"Email sent for notification {notification_id}")
        
    except Notification.DoesNotExist:
        logger.error(f"Notification {notification_id} not found")
    except Exception as e:
        logger.error(f"Error sending email for notification {notification_id}: {str(e)}")
        try:
            notification = Notification.objects.get(id=notification_id)
            notification.mark_email_sent(success=False, error_message=str(e))
        except:
            pass
        
        # Retry
        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))


def create_qhse_spot_check_notification(spot_check, users):
    """Helper to create spot check notifications"""
    for user in users:
        NotificationService.create_notification(
            recipient=user,
            template_key='QHSE_SPOT_CHECK_CREATED',
            project_no=spot_check.project_no,
            category=spot_check.category or 'N/A',
            action_url=f'/qhse/general/spotcheck?id={spot_check.id}',
        )


def create_car_urgent_notification(car, users):
    """Helper to create urgent CAR notifications"""
    for user in users:
        NotificationService.create_notification(
            recipient=user,
            template_key='QHSE_CAR_URGENT',
            car_id=car.id,
            project_no=car.project_no,
            action_url=f'/qhse/cars/{car.id}',
            send_email=True,
        )


def create_ai_alert_notification(model_name, details, users):
    """Helper to create AI alert notifications"""
    for user in users:
        NotificationService.create_notification(
            recipient=user,
            template_key='AI_MODEL_ALERT',
            model_name=model_name,
            details=details,
            action_url='/admin/dashboard?tab=predictions',
        )
