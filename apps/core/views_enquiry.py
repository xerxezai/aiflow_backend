"""
Enquiry API Views - REJLERS RADAI
Handles customer enquiry submissions and email notifications

Features:
- Form validation
- Email notification to sales team
- Soft-coded email configuration
- Rate limiting
- Error handling
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q
import logging

from apps.core.models import Enquiry

logger = logging.getLogger(__name__)


# Soft-coded Configuration
ENQUIRY_CONFIG = {
    'recipient_email': 'tanzeem.agra@rejlers.ae',
    'cc_emails': [],  # Add CC recipients here if needed
    'from_email': settings.DEFAULT_FROM_EMAIL,
    'subject_prefix': '[RADAI Enquiry]',
    'auto_reply': True,
    'save_to_database': True  # Future: save enquiries to database
}

# Services for which the `phone` field is not required. Password reset
# requests originate from /forgot-password where the user typically only
# knows their email.
ENQUIRY_SERVICES_WITHOUT_PHONE = {'password-reset'}

# Notification config — which services generate an admin notification, and
# which notification template to use. Extend when new critical services
# need admin attention.
ENQUIRY_NOTIFICATION_TEMPLATE = {
    'password-reset': 'ENQUIRY_PASSWORD_RESET_REQUEST',
}


def _notify_admins_of_enquiry(enquiry_obj, service, user_email, user_name):
    """
    Send an in-app notification to all admin users when a critical enquiry
    (e.g. password-reset) arrives. Silent no-op for services not listed in
    ENQUIRY_NOTIFICATION_TEMPLATE.
    """
    template_key = ENQUIRY_NOTIFICATION_TEMPLATE.get(service)
    if not template_key or not enquiry_obj:
        return

    from django.contrib.auth import get_user_model
    from apps.notifications.services import NotificationService

    User = get_user_model()
    admins = User.objects.filter(
        is_active=True,
    ).filter(
        # Django admins OR RBAC admin/super_admin roles
        # We use is_staff/is_superuser as the widest safe net; role-based
        # filtering happens in the RBAC layer for finer control if needed.
        Q(is_superuser=True) | Q(is_staff=True)
    ).distinct()

    NotificationService.bulk_notify(
        recipients=admins,
        template_key=template_key,
        user_email=user_email,
        user_name=user_name,
        action_url=f'/admin/enquiries?enquiry={enquiry_obj.pk}',
        metadata={
            'enquiry_id': enquiry_obj.pk,
            'service':    service,
            'user_email': user_email,
        },
    )


@api_view(['POST'])
@permission_classes([AllowAny])  # Public endpoint - no authentication required
def submit_enquiry(request):
    """
    Submit customer enquiry and send email notification
    
    Request Body:
    {
        "name": "John Doe",
        "email": "john@example.com",
        "phone": "+971 50 123 4567",
        "company": "ABC Company",
        "subject": "Enquiry about services",
        "message": "I would like to know more about...",
        "service": "pid-analysis",
        "urgency": "normal"
    }
    """
    try:
        # Extract form data
        data = request.data
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip()
        company = data.get('company', '').strip()
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        service = data.get('service', '').strip()
        urgency = data.get('urgency', 'normal').strip()
        
        # Validation
        errors = {}
        if not name:
            errors['name'] = 'Name is required'
        if not email:
            errors['email'] = 'Email is required'
        elif '@' not in email or '.' not in email:
            errors['email'] = 'Invalid email format'
        if not phone and service not in ENQUIRY_SERVICES_WITHOUT_PHONE:
            errors['phone'] = 'Phone number is required'
        if not subject:
            errors['subject'] = 'Subject is required'
        if not message:
            errors['message'] = 'Message is required'
        elif len(message) < 10:
            errors['message'] = 'Message must be at least 10 characters'
        
        if errors:
            return Response({
                'success': False,
                'errors': errors,
                'message': 'Validation failed'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Persist to database (gated by soft-coded flag)
        enquiry_obj = None
        if ENQUIRY_CONFIG.get('save_to_database', True):
            try:
                enquiry_obj = Enquiry.objects.create(
                    name=name,
                    email=email,
                    phone=phone,
                    company=company,
                    subject=subject,
                    message=message,
                    service=service,
                    urgency=urgency if urgency in dict(Enquiry.URGENCY_CHOICES) else 'normal',
                    source_ip=(request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip() or None,
                    user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:400],
                )
            except Exception as db_err:
                logger.error(f'Enquiry DB persist failed (continuing with email): {db_err}')

        # Notify administrators via in-app notification (soft-coded per service)
        # For password-reset requests this is critical since SMTP may be down;
        # the notification bell alerts admins so they can action from 9.6 Enquiry.
        try:
            _notify_admins_of_enquiry(enquiry_obj, service, email, name)
        except Exception as notif_err:
            logger.error(f'Enquiry admin notification failed (continuing): {notif_err}')

        # Service name mapping
        service_names = {
            'pid-analysis': 'P&ID Analysis & Verification',
            'pfd-conversion': 'PFD to P&ID Conversion',
            'asset-integrity': 'Asset Integrity Management',
            'engineering-consulting': 'Engineering Consulting',
            'digital-twin': 'Digital Twin Solutions',
            'ai-ml-services': 'AI/ML Engineering Services',
            'general': 'General Enquiry',
            'other': 'Other Services'
        }
        service_label = service_names.get(service, 'General Enquiry')
        
        # Urgency level mapping
        urgency_labels = {
            'low': '⏰ Low Priority',
            'normal': '📅 Normal Priority',
            'high': '⚡ High Priority',
            'urgent': '🚨 URGENT'
        }
        urgency_label = urgency_labels.get(urgency, 'Normal Priority')
        
        # Prepare email content
        email_subject = f"{ENQUIRY_CONFIG['subject_prefix']} {subject}"
        
        # HTML Email Template
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f9f9f9;
                }}
                .header {{
                    background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%);
                    color: white;
                    padding: 30px;
                    border-radius: 10px 10px 0 0;
                    text-align: center;
                }}
                .content {{
                    background: white;
                    padding: 30px;
                    border-radius: 0 0 10px 10px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .field {{
                    margin-bottom: 20px;
                    padding: 15px;
                    background-color: #f8f9fa;
                    border-left: 4px solid #3b82f6;
                    border-radius: 4px;
                }}
                .label {{
                    font-weight: bold;
                    color: #1e40af;
                    margin-bottom: 5px;
                }}
                .value {{
                    color: #333;
                }}
                .message-box {{
                    background-color: #e3f2fd;
                    padding: 20px;
                    border-radius: 8px;
                    margin-top: 20px;
                    border: 2px solid #3b82f6;
                }}
                .footer {{
                    margin-top: 30px;
                    padding: 20px;
                    text-align: center;
                    color: #666;
                    font-size: 12px;
                    border-top: 1px solid #ddd;
                }}
                .urgency {{
                    display: inline-block;
                    padding: 8px 16px;
                    border-radius: 20px;
                    font-weight: bold;
                }}
                .urgency-urgent {{
                    background-color: #fee2e2;
                    color: #dc2626;
                }}
                .urgency-high {{
                    background-color: #fef3c7;
                    color: #d97706;
                }}
                .urgency-normal {{
                    background-color: #dbeafe;
                    color: #2563eb;
                }}
                .urgency-low {{
                    background-color: #f3f4f6;
                    color: #6b7280;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0;">📧 New Enquiry Received</h1>
                    <p style="margin: 10px 0 0 0;">RADAI Customer Enquiry System</p>
                </div>
                
                <div class="content">
                    <div class="field">
                        <div class="label">Urgency Level:</div>
                        <div class="value">
                            <span class="urgency urgency-{urgency}">{urgency_label}</span>
                        </div>
                    </div>
                    
                    <div class="field">
                        <div class="label">📝 Subject:</div>
                        <div class="value">{subject}</div>
                    </div>
                    
                    <div class="field">
                        <div class="label">👤 Customer Name:</div>
                        <div class="value">{name}</div>
                    </div>
                    
                    <div class="field">
                        <div class="label">📧 Email:</div>
                        <div class="value"><a href="mailto:{email}">{email}</a></div>
                    </div>
                    
                    <div class="field">
                        <div class="label">📞 Phone:</div>
                        <div class="value">{phone}</div>
                    </div>
                    
                    {f'<div class="field"><div class="label">🏢 Company:</div><div class="value">{company}</div></div>' if company else ''}
                    
                    <div class="field">
                        <div class="label">🔧 Service of Interest:</div>
                        <div class="value">{service_label}</div>
                    </div>
                    
                    <div class="message-box">
                        <div class="label">💬 Message:</div>
                        <div class="value" style="white-space: pre-wrap; margin-top: 10px;">{message}</div>
                    </div>
                    
                    <div class="footer">
                        <p><strong>Submitted:</strong> {timezone.now().strftime('%B %d, %Y at %I:%M %p UTC')}</p>
                        <p>This is an automated message from RADAI Enquiry System</p>
                        <p>Reply directly to this email to respond to the customer</p>
                        <hr style="margin: 20px 0; border: none; border-top: 1px solid #ddd;">
                        <p>© 2025 REJLERS AB • Engineering Excellence Since 1942</p>
                        <p><a href="https://www.radai.ae">www.radai.ae</a> | <a href="https://www.rejlers.com">www.rejlers.com</a></p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_content = f"""
        New Enquiry Received - RADAI
        =============================
        
        Urgency: {urgency_label}
        Subject: {subject}
        
        Customer Details:
        -----------------
        Name: {name}
        Email: {email}
        Phone: {phone}
        {f'Company: {company}' if company else ''}
        
        Service Interest: {service_label}
        
        Message:
        --------
        {message}
        
        Submitted: {timezone.now().strftime('%B %d, %Y at %I:%M %p UTC')}
        
        ---
        This is an automated message from RADAI Enquiry System
        Reply directly to this email to respond to the customer
        """
        
        # Send email
        email_message = EmailMultiAlternatives(
            subject=email_subject,
            body=text_content,
            from_email=ENQUIRY_CONFIG['from_email'],
            to=[ENQUIRY_CONFIG['recipient_email']],
            cc=ENQUIRY_CONFIG['cc_emails'],
            reply_to=[email]  # Set customer's email as reply-to
        )
        email_message.attach_alternative(html_content, "text/html")
        try:
            email_message.send(fail_silently=False)
            logger.info(f"✅ Enquiry submitted: {subject} from {email}")
        except Exception as mail_err:
            # Email failure must not lose the enquiry — it is already persisted.
            logger.error(f"⚠️ Enquiry email failed (DB row was saved): {mail_err}")
        
        # Optional: Send auto-reply to customer
        if ENQUIRY_CONFIG['auto_reply']:
            try:
                auto_reply_subject = "Thank you for contacting REJLERS RADAI"
                auto_reply_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                        .header {{ background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%); color: white; padding: 30px; border-radius: 10px; text-align: center; }}
                        .content {{ background: white; padding: 30px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1 style="margin: 0;">Thank You!</h1>
                        </div>
                        <div class="content">
                            <p>Dear {name},</p>
                            <p>Thank you for contacting REJLERS RADAI. We have received your enquiry and our team will review it shortly.</p>
                            <p><strong>Your Enquiry Details:</strong></p>
                            <ul>
                                <li><strong>Subject:</strong> {subject}</li>
                                <li><strong>Service:</strong> {service_label}</li>
                                <li><strong>Reference:</strong> {timezone.now().strftime('%Y%m%d-%H%M%S')}</li>
                            </ul>
                            <p>We aim to respond to all enquiries within 24 hours. For urgent matters, please call us at <strong>+971 2 639 7449</strong>.</p>
                            <p>Best regards,<br><strong>REJLERS RADAI Team</strong></p>
                            <hr style="margin: 20px 0; border: none; border-top: 1px solid #ddd;">
                            <p style="font-size: 12px; color: #666;">© 2025 REJLERS AB | <a href="https://www.radai.ae">www.radai.ae</a></p>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                auto_reply = EmailMultiAlternatives(
                    subject=auto_reply_subject,
                    body=f"Dear {name},\n\nThank you for contacting REJLERS RADAI. We have received your enquiry and will respond within 24 hours.\n\nBest regards,\nRADAI Team",
                    from_email=ENQUIRY_CONFIG['from_email'],
                    to=[email]
                )
                auto_reply.attach_alternative(auto_reply_html, "text/html")
                auto_reply.send(fail_silently=True)
                logger.info(f"✅ Auto-reply sent to: {email}")
            except Exception as e:
                logger.warning(f"⚠️ Auto-reply failed: {str(e)}")
        
        return Response({
            'success': True,
            'message': 'Your enquiry has been submitted successfully. We will get back to you within 24 hours.',
            'reference': (f'ENQ-{enquiry_obj.id:06d}' if enquiry_obj else timezone.now().strftime('%Y%m%d-%H%M%S'))
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"❌ Enquiry submission error: {str(e)}")
        return Response({
            'success': False,
            'message': 'Failed to submit enquiry. Please try again or contact us directly at tanzeem.agra@rejlers.ae',
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ============================================================================
# ADMIN ENDPOINTS — list / detail / update / delete / stats
# Used by the 9.6 Enquiry admin page (frontend/src/pages/Admin/EnquiryManagement.jsx)
# ============================================================================

def _serialize_enquiry(e: Enquiry) -> dict:
    return {
        'id':          e.id,
        'reference':   f'ENQ-{e.id:06d}',
        'name':        e.name,
        'email':       e.email,
        'phone':       e.phone,
        'company':     e.company,
        'subject':     e.subject,
        'message':     e.message,
        'service':     e.service,
        'urgency':     e.urgency,
        'status':      e.status,
        'admin_notes': e.admin_notes,
        'source_ip':   e.source_ip,
        'user_agent':  e.user_agent,
        'created_at':  e.created_at.isoformat() if e.created_at else None,
        'updated_at':  e.updated_at.isoformat() if e.updated_at else None,
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def list_enquiries(request):
    """List enquiries with optional filters: ?status=&urgency=&service=&search=&page=&page_size="""
    qs = Enquiry.objects.all()

    f_status  = request.query_params.get('status')
    f_urgency = request.query_params.get('urgency')
    f_service = request.query_params.get('service')
    f_search  = request.query_params.get('search', '').strip()

    if f_status:
        qs = qs.filter(status=f_status)
    if f_urgency:
        qs = qs.filter(urgency=f_urgency)
    if f_service:
        qs = qs.filter(service=f_service)
    if f_search:
        qs = qs.filter(
            Q(name__icontains=f_search) |
            Q(email__icontains=f_search) |
            Q(company__icontains=f_search) |
            Q(subject__icontains=f_search) |
            Q(message__icontains=f_search)
        )

    try:
        page      = max(1, int(request.query_params.get('page', 1)))
        page_size = min(100, max(1, int(request.query_params.get('page_size', 25))))
    except (TypeError, ValueError):
        page, page_size = 1, 25

    total = qs.count()
    start = (page - 1) * page_size
    items = [_serialize_enquiry(e) for e in qs[start:start + page_size]]

    return Response({
        'success':   True,
        'count':     total,
        'page':      page,
        'page_size': page_size,
        'results':   items,
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def enquiry_stats(request):
    """Aggregate counts for dashboard widgets at the top of the admin page."""
    by_status_qs  = Enquiry.objects.values('status').annotate(c=Count('id'))
    by_urgency_qs = Enquiry.objects.values('urgency').annotate(c=Count('id'))
    return Response({
        'success':   True,
        'total':     Enquiry.objects.count(),
        'new':       Enquiry.objects.filter(status='new').count(),
        'by_status': {row['status']: row['c'] for row in by_status_qs},
        'by_urgency':{row['urgency']: row['c'] for row in by_urgency_qs},
    })


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
def enquiry_detail(request, pk: int):
    """Retrieve, update (status / admin_notes), or delete a single enquiry."""
    enquiry = get_object_or_404(Enquiry, pk=pk)

    if request.method == 'GET':
        return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry)})

    if request.method == 'DELETE':
        enquiry.delete()
        return Response({'success': True, 'message': 'Enquiry deleted'})

    # PATCH — only allow safe admin-controlled fields
    allowed = {'status', 'admin_notes', 'urgency'}
    payload = {k: v for k, v in (request.data or {}).items() if k in allowed}

    if 'status' in payload and payload['status'] not in dict(Enquiry.STATUS_CHOICES):
        return Response({'success': False, 'message': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
    if 'urgency' in payload and payload['urgency'] not in dict(Enquiry.URGENCY_CHOICES):
        return Response({'success': False, 'message': 'Invalid urgency'}, status=status.HTTP_400_BAD_REQUEST)

    for k, v in payload.items():
        setattr(enquiry, k, v)
    enquiry.save(update_fields=list(payload.keys()) + ['updated_at'])

    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry)})

