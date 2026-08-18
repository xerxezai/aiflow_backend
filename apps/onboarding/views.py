"""
Onboarding & Offboarding Views
Provides REST API endpoints for managing employee lifecycle

✅ MIGRATED: Now uses EmployeeMaster instead of UserProfile
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Count, Prefetch
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction
from datetime import date
import uuid
import mimetypes

from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist,
    ONBOARDING_ACTIVE_STATUSES, OFFBOARDING_ACTIVE_STATUSES,
    CHECKLIST_STAGE_PRE_HIRE, CHECKLIST_STAGE_IT_PROVISIONING,
    CHECKLIST_STAGE_FIRST_DAY, CHECKLIST_STAGE_FINAL_VALIDATION,
    CHECKLIST_STAGE_EXIT_INITIATION, CHECKLIST_STAGE_ACCESS_REVOCATION,
    CHECKLIST_STAGE_ASSET_RETURN, CHECKLIST_STAGE_EXIT_CLEARANCE,
    CHECKLIST_STAGE_FINAL_SETTLEMENT,
)
from .serializers import (
    OnboardingRecordSerializer, OnboardingRecordListSerializer,
    OffboardingRecordSerializer, OffboardingRecordListSerializer,
    EquipmentSerializer, DocumentSerializer,
    AccessProvisioningSerializer, ChecklistSerializer
)
from apps.core.s3_service import S3Service
from apps.notifications.services import NotificationService
from apps.notifications.models import Notification

# Employee management - using new EmployeeMaster system
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.services import EmployeeService
from apps.rbac.models import UserProfile as RBACUserProfile, Organization
from .rbac import (
    can_manage_offboarding, can_manage_onboarding_stage,
    can_start_onboarding_stage, can_manage_offboarding_stage,
    can_start_offboarding_stage,
)
from .project_assignments import get_active_project_assignments, get_profile_project_manager

User = get_user_model()


def _resolve_exit_reporting_manager(user, employee=None):
    """Use the latest active project PoM, falling back to the line manager."""
    project_manager, _assignment = get_profile_project_manager(user)
    if project_manager:
        return project_manager.get_full_name() or project_manager.email or project_manager.username
    if employee is None and user:
        employee = EmployeeMaster.objects.filter(user=user).select_related('manager').first()
    return employee.manager.get_full_name() if employee and employee.manager else ''


def _notify_project_managers_of_exit(record):
    """Notify each PoM responsible for an active project assigned to the employee."""
    projects = get_active_project_assignments(record.user)
    recipients = {}
    recipient_projects = {}
    for project in projects:
        for manager in project['managers']:
            if not manager or manager.id == record.user_id:
                continue
            recipients[manager.id] = manager
            recipient_projects.setdefault(manager.id, []).append(project)

    if recipients and record.project_manager_approval_status != 'pending':
        record.project_manager_approval_status = 'pending'
        record.save(update_fields=['project_manager_approval_status', 'updated_at'])

    for user_id, recipient in recipients.items():
        assigned_projects = recipient_projects[user_id]
        project_labels = ', '.join(
            f"{project['code']} - {project['name']}" for project in assigned_projects
        )
        notification = NotificationService.create_notification(
            recipient=recipient,
            sender=record.created_by,
            title='Employee initiated an exit process',
            message=(
                f'{record.employee_name} ({record.employee_id or record.employee_email}) '
                f'has initiated an exit process and is assigned to your active '
                f'project(s): {project_labels}. Last working day: {record.last_working_day}.'
            ),
            category='APPROVAL',
            priority='HIGH',
            action_url=f'/hr/onboarding?tab=offboarding&record_id={record.id}',
            action_label='Review Offboarding',
            metadata={
                'offboarding_id': record.id,
                'employee_id': record.employee_id,
                'project_ids': [project['id'] for project in assigned_projects],
                'event': 'employee_exit_initiated',
                'action_type': 'offboarding_project_manager_decision',
                'decision_status': 'pending',
            },
        )
        if notification and notification.send_in_app and notification.status == 'PENDING':
            notification.status = 'SENT'
            notification.save(update_fields=['status', 'updated_at'])

    return projects

ONBOARDING_CHECKLIST_TEMPLATES = {
    CHECKLIST_STAGE_PRE_HIRE: (
        ('Verify approved hiring request and employee profile', 'high'),
        ('Collect and validate required identity documents', 'critical'),
        ('Issue and record the signed offer or employment contract', 'critical'),
        ('Confirm joining date, position, department, and branch', 'high'),
        ('Assign reporting manager and onboarding owner', 'high'),
        ('Prepare the employee master profile and base RBAC role', 'high'),
    ),
    CHECKLIST_STAGE_IT_PROVISIONING: (
        ('Prepare and assign workstation or laptop', 'high'),
        ('Create corporate email and Microsoft 365 account', 'high'),
        ('Create Active Directory or domain account', 'high'),
        ('Configure MFA and security policies', 'critical'),
        ('Configure VPN and remote access', 'medium'),
        ('Grant role-based application and shared-drive access', 'high'),
        ('Issue building access card or ID badge', 'medium'),
        ('Complete IT handover and employee acknowledgement', 'medium'),
    ),
    CHECKLIST_STAGE_FIRST_DAY: (
        ('Complete HR welcome and company induction', 'high'),
        ('Introduce reporting manager, team, and key contacts', 'medium'),
        ('Complete office, safety, and emergency orientation', 'high'),
        ('Review policies, code of conduct, and confidentiality', 'critical'),
        ('Confirm workstation, accounts, and required access are operational', 'high'),
    ),
    CHECKLIST_STAGE_FINAL_VALIDATION: (
        ('Validate completion of all onboarding stage checklists', 'critical'),
        ('Confirm employee documents and master data are complete', 'critical'),
        ('Confirm equipment and system access records', 'high'),
        ('Confirm payroll and benefits enrollment', 'high'),
        ('Obtain employee and reporting-manager acknowledgement', 'high'),
        ('Complete final HR review and close onboarding', 'critical'),
    ),
}

IT_ONBOARDING_CHECKLIST_TEMPLATE = ONBOARDING_CHECKLIST_TEMPLATES[CHECKLIST_STAGE_IT_PROVISIONING]

ONBOARDING_STAGE_START_STATE = {
    CHECKLIST_STAGE_PRE_HIRE: ('documentation', 20),
    CHECKLIST_STAGE_IT_PROVISIONING: ('equipment', 40),
    CHECKLIST_STAGE_FIRST_DAY: ('training', 70),
    CHECKLIST_STAGE_FINAL_VALIDATION: ('training', 90),
}

OFFBOARDING_CHECKLIST_TEMPLATES = {
    CHECKLIST_STAGE_EXIT_INITIATION: (
        ('Confirm resignation or termination approval', 'critical'),
        ('Verify last working day and notice period', 'high'),
        ('Notify HR, reporting manager, ICT, and Finance', 'high'),
        ('Confirm handover owner and transition plan', 'high'),
    ),
    CHECKLIST_STAGE_ACCESS_REVOCATION: (
        ('Schedule email and directory account deactivation', 'critical'),
        ('Revoke VPN, MFA, and remote access', 'critical'),
        ('Remove application and shared-folder permissions', 'critical'),
        ('Transfer mailbox, files, and service ownership', 'high'),
        ('Confirm access revocation evidence', 'high'),
    ),
    CHECKLIST_STAGE_ASSET_RETURN: (
        ('Collect laptop, desktop, and monitors', 'critical'),
        ('Collect mobile phone, SIM, and accessories', 'high'),
        ('Collect access card, keys, and identification badge', 'critical'),
        ('Inspect returned assets and record condition', 'high'),
        ('Update asset register and custody records', 'high'),
    ),
    CHECKLIST_STAGE_EXIT_CLEARANCE: (
        ('Complete knowledge and document handover', 'critical'),
        ('Conduct exit interview', 'medium'),
        ('Obtain department and project clearance', 'high'),
        ('Confirm confidentiality and data obligations', 'critical'),
    ),
    CHECKLIST_STAGE_FINAL_SETTLEMENT: (
        ('Confirm attendance, leave, and payroll inputs', 'critical'),
        ('Calculate and approve final settlement', 'critical'),
        ('Confirm benefits and insurance closure', 'high'),
        ('Issue service and employment documents', 'medium'),
        ('Complete final HR review and close offboarding', 'critical'),
    ),
}

OFFBOARDING_STAGE_START_STATE = {
    CHECKLIST_STAGE_EXIT_INITIATION: ('initiated', 10),
    CHECKLIST_STAGE_ACCESS_REVOCATION: ('access_revocation', 30),
    CHECKLIST_STAGE_ASSET_RETURN: ('equipment_return', 50),
    CHECKLIST_STAGE_EXIT_CLEARANCE: ('exit_interview', 70),
    CHECKLIST_STAGE_FINAL_SETTLEMENT: ('final_settlement', 90),
}


def complete_onboarding_if_ready(record):
    """Close an onboarding workflow as soon as every required checklist is complete."""
    if not record or record.status in {'completed', 'cancelled'}:
        return False

    required_stages = set(ONBOARDING_CHECKLIST_TEMPLATES)
    stage_rows = record.checklist_items.filter(stage__in=required_stages)
    present_stages = set(stage_rows.values_list('stage', flat=True).distinct())
    if present_stages != required_stages or stage_rows.filter(completed=False).exists():
        return False

    record.status = 'completed'
    record.progress_percentage = 100
    record.actual_completion_date = timezone.now()
    record.save(update_fields=[
        'status', 'progress_percentage', 'actual_completion_date', 'updated_at',
    ])
    return True


def complete_offboarding_if_ready(record):
    """Close an offboarding workflow when every required stage is complete."""
    if not record or record.status in {'completed', 'cancelled', 'rejected'}:
        return False

    required_stages = set(OFFBOARDING_CHECKLIST_TEMPLATES)
    stage_rows = record.checklist_items.filter(stage__in=required_stages)
    present_stages = set(stage_rows.values_list('stage', flat=True).distinct())
    if present_stages != required_stages or stage_rows.filter(completed=False).exists():
        return False

    record.status = 'completed'
    record.progress_percentage = 100
    record.actual_completion_date = timezone.now()
    record.save(update_fields=[
        'status', 'progress_percentage', 'actual_completion_date', 'updated_at',
    ])
    return True


def ensure_onboarding_record(employee, created_by=None):
    """Return an employee's onboarding record, creating an initiated cycle if absent."""
    identity = Q(user_id=employee.user_id)
    if employee.email:
        identity |= Q(employee_email__iexact=employee.email.strip())
    if employee.employee_number:
        identity |= Q(employee_id=employee.employee_number)

    existing = OnboardingRecord.objects.filter(identity).order_by('-created_at').first()
    if existing:
        return existing, False

    joining_date = employee.join_date or date.today()
    employee_name = (
        employee.get_full_name()
        or employee.user.get_full_name()
        or employee.email.split('@')[0]
    ).strip()
    position = employee.job_title_uae or employee.job_title_finland or employee.designation or 'Not assigned'
    department = employee.division or employee.department or 'Not assigned'
    manager_name = employee.manager.get_full_name() if employee.manager else ''
    branch = employee.branch if employee.branch in {'RAD', 'RIN'} else 'RAD'

    return OnboardingRecord.objects.get_or_create(
        employee_email=employee.email.strip().lower(),
        defaults={
            'employee_name': employee_name,
            'employee_id': employee.employee_number,
            'user': employee.user,
            'position': position,
            'department': department,
            'reporting_manager': manager_name,
            'branch': branch,
            'joining_date': joining_date,
            'initiated_date': timezone.now(),
            'target_completion_date': joining_date,
            'status': 'initiated',
            'progress_percentage': 0,
            'created_by': created_by,
            'notes': 'Onboarding cycle automatically initiated because no workflow record existed.',
        },
    )


class OnboardingRecordViewSet(viewsets.ModelViewSet):
    """
    API endpoint for onboarding records
    Supports CRUD + custom actions: statistics, mark_completed
    Includes passport photo upload to S3
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = OnboardingRecord.objects.all()
    
    def get_serializer_class(self):
        """Use lightweight serializer for list, full serializer for detail"""
        if self.action == 'list':
            return OnboardingRecordListSerializer
        return OnboardingRecordSerializer
    
    def get_queryset(self):
        """
        Filter by status, branch, department, search query
        Annotate with counts for list view
        """
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by branch
        branch = self.request.query_params.get('branch')
        if branch:
            queryset = queryset.filter(branch=branch)
        
        # Filter by department
        department = self.request.query_params.get('department')
        if department:
            queryset = queryset.filter(department__icontains=department)

        # Scope employee detail views to the selected user. This avoids relying
        # on names or email addresses, which can change or be duplicated.
        user_id = self.request.query_params.get('user_id')
        if user_id:
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                from rest_framework.exceptions import ValidationError
                raise ValidationError({'user_id': 'A valid employee user ID is required.'})
            queryset = queryset.filter(user_id=user_id)
        
        # Search by employee name or email
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_name__icontains=search) |
                Q(employee_email__icontains=search) |
                Q(employee_id__icontains=search)
            )
        
        # Annotate counts for list view
        if self.action == 'list':
            queryset = queryset.annotate(
                equipment_count=Count('equipment', distinct=True),
                documents_count=Count('documents', distinct=True),
                access_count=Count('access_records', distinct=True),
                checklist_count=Count('checklist_items', distinct=True),
                checklist_completed_count=Count('checklist_items', filter=Q(checklist_items__completed=True), distinct=True)
            )
        else:
            # Prefetch related for detail view
            queryset = queryset.prefetch_related(
                'equipment', 'documents', 'access_records', 'checklist_items'
            )
        
        return queryset.select_related('created_by', 'assigned_to', 'user')
    
    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['post'], url_path='ensure-employee-workflow')
    def ensure_employee_workflow(self, request):
        """Create an initiated onboarding cycle when the selected user has none."""
        user_id = request.data.get('user_id')
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return Response(
                {'user_id': 'A valid employee user ID is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee = EmployeeMaster.objects.filter(
            user_id=user_id,
            user__is_active=True,
        ).select_related('user', 'manager').first()
        if not employee:
            return Response(
                {'detail': 'Active employee profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        record, created = ensure_onboarding_record(employee, request.user)
        return Response(
            OnboardingRecordSerializer(record, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='sync-missing')
    def sync_missing(self, request):
        """Initiate onboarding for every active employee without a workflow."""
        active_employees = EmployeeMaster.objects.filter(user__is_active=True)
        checked_count = active_employees.count()
        onboarded_user_ids = OnboardingRecord.objects.filter(
            user_id__isnull=False,
        ).values('user_id')
        employees = active_employees.exclude(
            user_id__in=onboarded_user_ids,
        ).select_related('user', 'manager')
        created_ids = []

        for employee in employees.iterator():
            record, created = ensure_onboarding_record(employee, request.user)
            if created:
                created_ids.append(record.id)

        return Response({
            'checked_count': checked_count,
            'created_count': len(created_ids),
            'created_record_ids': created_ids,
        })

    @action(detail=False, methods=['get'], url_path='command-center-pending')
    def command_center_pending(self, request):
        """Return active onboarding and offboarding requests for HR Command Center."""
        onboarding_requests = OnboardingRecord.objects.filter(
            status__in=ONBOARDING_ACTIVE_STATUSES
        ).values(
            'id', 'user_id', 'employee_name', 'employee_email', 'employee_id',
            'department', 'status', 'joining_date', 'initiated_date', 'created_at',
        )
        offboarding_requests = OffboardingRecord.objects.filter(
            status__in=OFFBOARDING_ACTIVE_STATUSES
        ).values(
            'id', 'user_id', 'employee_name', 'employee_email', 'employee_id',
            'department', 'status', 'last_working_day', 'initiated_date', 'created_at',
        )

        rows = [
            {
                **record,
                'id': f"onboarding-{record['id']}",
                'request_id': record['id'],
                'request_type': 'Onboarding',
                'effective_date': record.get('joining_date'),
                'display_status': 'Initiated' if record['status'] == 'initiated' else 'In Progress',
            }
            for record in onboarding_requests
        ]
        rows.extend([
            {
                **record,
                'id': f"offboarding-{record['id']}",
                'request_id': record['id'],
                'request_type': 'Offboarding',
                'effective_date': record.get('last_working_day'),
                'display_status': 'Initiated' if record['status'] == 'initiated' else 'In Progress',
            }
            for record in offboarding_requests
        ])
        rows.sort(key=lambda row: row.get('created_at') or row.get('initiated_date'), reverse=True)

        return Response(rows)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Get onboarding statistics
        Returns counts by status, upcoming joiners, overdue, etc.
        """
        queryset = self.get_queryset()
        
        total = queryset.count()
        by_status = {}
        for record in queryset.values('status').annotate(count=Count('id')):
            by_status[record['status']] = record['count']
        
        # Upcoming joiners (next 30 days)
        from datetime import timedelta
        upcoming_threshold = date.today() + timedelta(days=30)
        upcoming = queryset.filter(
            joining_date__gte=date.today(),
            joining_date__lte=upcoming_threshold,
            status__in=['initiated', 'documentation', 'equipment', 'access_provisioning', 'training']
        ).count()
        
        # Overdue (joining date passed but not completed)
        overdue = queryset.filter(
            joining_date__lt=date.today(),
            status__in=['initiated', 'documentation', 'equipment', 'access_provisioning', 'training']
        ).count()
        
        # Completed this month
        now = timezone.now()
        completed_this_month = queryset.filter(
            status='completed',
            actual_completion_date__year=now.year,
            actual_completion_date__month=now.month
        ).count()
        
        # By branch
        by_branch = {}
        for record in queryset.values('branch').annotate(count=Count('id')):
            by_branch[record['branch']] = record['count']
        
        return Response({
            'total': total,
            'by_status': by_status,
            'upcoming_joiners': upcoming,
            'overdue': overdue,
            'completed_this_month': completed_this_month,
            'by_branch': by_branch
        })
    
    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark onboarding as completed"""
        record = self.get_object()
        if not can_manage_onboarding_stage(request.user, CHECKLIST_STAGE_FINAL_VALIDATION, record):
            raise PermissionDenied('Only HR may complete final onboarding validation.')

        stage_rows = record.checklist_items.filter(
            stage__in=ONBOARDING_CHECKLIST_TEMPLATES.keys(),
        )
        present_stages = set(stage_rows.values_list('stage', flat=True))
        missing_stages = set(ONBOARDING_CHECKLIST_TEMPLATES) - present_stages
        if missing_stages or stage_rows.filter(completed=False).exists():
            return Response(
                {'detail': 'Every onboarding checklist stage must be started and completed before final validation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record.status = 'completed'
        record.progress_percentage = 100
        record.actual_completion_date = timezone.now()
        record.save()
        
        serializer = self.get_serializer(record)
        return Response(serializer.data)

    def _start_checklist_stage(self, request, record, stage):
        if stage not in ONBOARDING_CHECKLIST_TEMPLATES:
            raise ValidationError({'stage': 'A valid onboarding checklist stage is required.'})
        if not can_start_onboarding_stage(request.user, stage, record):
            raise PermissionDenied('Your RBAC role cannot start this onboarding checklist stage.')

        existing_names = set(
            record.checklist_items.filter(stage=stage).values_list('task_name', flat=True)
        )
        due_date = record.target_completion_date or record.joining_date
        new_items = [
            Checklist(
                onboarding_record=record,
                task_name=task_name,
                description=f'{stage} onboarding checklist task',
                stage=stage,
                due_date=due_date,
                priority=priority,
            )
            for task_name, priority in ONBOARDING_CHECKLIST_TEMPLATES[stage]
            if task_name not in existing_names
        ]
        Checklist.objects.bulk_create(new_items)

        next_status, minimum_progress = ONBOARDING_STAGE_START_STATE[stage]
        if record.status not in {'completed', 'cancelled'} and record.progress_percentage < minimum_progress:
            record.status = next_status
            record.progress_percentage = minimum_progress
            record.save(update_fields=['status', 'progress_percentage', 'updated_at'])

        record.refresh_from_db()
        serializer = self.get_serializer(record)
        return Response({**serializer.data, 'created_checklist_count': len(new_items)})

    @action(detail=True, methods=['post'], url_path='start-checklist-stage')
    def start_checklist_stage(self, request, pk=None):
        """Start one RBAC-controlled onboarding checklist stage."""
        return self._start_checklist_stage(request, self.get_object(), request.data.get('stage'))

    @action(detail=True, methods=['post'], url_path='start-it-checklist')
    def start_it_checklist(self, request, pk=None):
        """Backward-compatible shortcut for the IT provisioning checklist."""
        return self._start_checklist_stage(
            request,
            self.get_object(),
            CHECKLIST_STAGE_IT_PROVISIONING,
        )
    
    @action(detail=False, methods=['post'])
    def create_employee(self, request):
        """
        Comprehensive employee creation endpoint
        Creates User + EmployeeMaster + OnboardingRecord in one atomic transaction
        Based on Sympa HR onboarding form
        
        ✅ MIGRATED: Now uses EmployeeService instead of UserProfile
        """
        with transaction.atomic():
            try:
                # Extract data from request
                data = request.data
                
                # Required fields
                first_name = data.get('first_name', '').strip()
                last_name = data.get('surname', '').strip()
                email = data.get('email', '').strip().lower()
                
                if not all([first_name, last_name, email]):
                    return Response(
                        {'error': 'First name, surname, and email are required'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                # Check if user already exists
                if User.objects.filter(email=email).exists():
                    return Response(
                        {'error': f'User with email {email} already exists'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                # Generate username from email
                username = email.split('@')[0]
                base_username = username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1
                
                # Create User
                user = User.objects.create(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    phone_number=data.get('mobile_phone', ''),
                    is_active=True,
                    is_first_login=True,
                    must_reset_password=True  # Will need to set password
                )
                
                # ✅ Create RBAC UserProfile (required for role assignment)
                # This will trigger the signal that auto-assigns the Default role
                default_org = Organization.objects.filter(is_active=True).first()
                if not default_org:
                    default_org = Organization.objects.create(
                        name='Default Organization',
                        code='DEFAULT',
                        is_active=True
                    )
                
                rbac_profile = RBACUserProfile.objects.create(
                    user=user,
                    organization=default_org,
                    status='active',
                    department=data.get('division', ''),
                    job_title=data.get('job_title_uae') or data.get('job_title_finland', ''),
                    phone=data.get('mobile_phone', '')
                )
                print(f"[Onboarding] ✅ Created RBAC UserProfile for {email} — Default role will be auto-assigned")
                
                # Create EmployeeMaster record with all Sympa HR fields
                manager_id = data.get('manager_id')
                manager_employee = None
                if manager_id:
                    try:
                        # Find EmployeeMaster record for manager
                        manager_employee = EmployeeMaster.objects.get(user_id=manager_id)
                    except EmployeeMaster.DoesNotExist:
                        # Fallback: manager might not have EmployeeMaster yet
                        pass
                
                # Use EmployeeService to create employee record
                employee = EmployeeService.create_employee(
                    user=user,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    preferred_given_name=data.get('preferred_given_name', ''),
                    manager=manager_employee,
                    phone_number=data.get('mobile_phone', ''),
                    initials=data.get('initials', ''),
                    employee_number=data.get('employee_number') or None,  # Auto-generate if empty
                    employee_code=data.get('employee_code') or None,  # Auto-generate if empty
                    account_name=data.get('account_name', username),
                    employment_id=data.get('employment_id', ''),
                    candidate_id=data.get('candidate_id', ''),
                    department=data.get('division', ''),  # Note: using division as department
                    division=data.get('division', ''),
                    business_unit=data.get('business_unit', ''),
                    business_area=data.get('business_area', ''),
                    office=data.get('office', ''),
                    designation=data.get('job_title_uae') or data.get('job_title_finland', ''),
                    branch=data.get('branch', 'RAD'),  # RAD or RIN
                    # Note: company, job_title_finland, job_title_uae, protected_identity, etc.
                    # are in old UserProfile but not in EmployeeMaster schema
                    # These fields are being phased out or will be added if needed
                )
                
                # Create OnboardingRecord
                joining_date = data.get('joining_date')
                if joining_date:
                    from datetime import datetime
                    if isinstance(joining_date, str):
                        joining_date = datetime.strptime(joining_date, '%Y-%m-%d').date()
                else:
                    joining_date = date.today()
                
                onboarding_record = OnboardingRecord.objects.create(
                    employee_name=f"{first_name} {last_name}",
                    employee_email=email,
                    employee_id=employee.employee_number,  # Use generated employee_number from EmployeeMaster
                    user=user,
                    position=data.get('job_title_uae') or data.get('job_title_finland', ''),
                    department=data.get('division', ''),
                    reporting_manager=manager_employee.get_full_name() if manager_employee else '',
                    branch=data.get('branch', 'RAD'),
                    joining_date=joining_date,
                    initiated_date=timezone.now(),
                    target_completion_date=joining_date,
                    status='initiated',
                    progress_percentage=0,
                    created_by=request.user,
                    notes=data.get('notes', '')
                )

                # ── Sync Reporting Manager → UserProfile.manager ──────────────
                # When a new employee is created via Onboarding with a manager,
                # also set UserProfile.manager so the Profile page stays aligned.
                if manager_id and manager_employee:
                    try:
                        from apps.rbac.models import UserProfile as _UP
                        mgr_profile = _UP.objects.filter(
                            user=manager_employee.user, is_deleted=False
                        ).first()
                        _UP.objects.filter(user=user).update(manager=mgr_profile)
                    except Exception:
                        pass  # non-fatal — manager field is still on EmployeeMaster
                
                # Handle passport photo upload to S3
                photo = request.FILES.get('photo')
                if photo:
                    try:
                        # Validate file type (only images)
                        allowed_types = ['image/jpeg', 'image/jpg', 'image/png']
                        content_type = photo.content_type
                        if content_type not in allowed_types:
                            return Response(
                                {'error': 'Invalid photo format. Only JPG and PNG are allowed.'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                        
                        # Validate file size (max 5MB)
                        max_size = 5 * 1024 * 1024  # 5MB
                        if photo.size > max_size:
                            return Response(
                                {'error': 'Photo size exceeds 5MB limit.'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                        
                        # Generate unique filename
                        file_extension = photo.name.split('.')[-1] if '.' in photo.name else 'jpg'
                        photo_filename = f"{user.id}_{uuid.uuid4().hex[:8]}.{file_extension}"

                        # Upload to S3
                        s3_service = S3Service()
                        upload_result = s3_service.upload_file(
                            file_obj=photo.file,
                            folder_type='avatars',
                            filename=photo_filename,
                            content_type=content_type
                        )
                        s3_key = upload_result['key']

                        # Generate presigned URL (valid for 7 days)
                        photo_url = s3_service.get_presigned_url(s3_key, expiration=7*24*3600)
                        
                        # Update onboarding record with photo info
                        onboarding_record.photo_file_path = s3_key
                        onboarding_record.photo_url = photo_url
                        onboarding_record.photo_file_size = photo.size
                        onboarding_record.photo_mime_type = content_type
                        onboarding_record.photo_original_filename = photo.name
                        onboarding_record.save()
                        
                        # ✅ SYNC: Also update EmployeeMaster photo_url for bidirectional sync
                        employee.photo_url = photo_url
                        employee.save(update_fields=['photo_url'])
                        
                    except Exception as e:
                        # Log error but don't fail the whole request
                        print(f"Error uploading photo: {str(e)}")
                
                return Response({
                    'success': True,
                    'message': 'Employee created successfully',
                    'user_id': user.id,
                    'employee_master_id': str(employee.id),  # UUID of EmployeeMaster record
                    'onboarding_id': onboarding_record.id,
                    'employee_number': employee.employee_number,  # Auto-generated employee number
                    'employee_code': employee.employee_code,  # Auto-generated employee code
                    'emp_code': employee.emp_code,  # Biometric system code
                    'email': email,
                    'reporting_manager': employee.manager.get_full_name() if employee.manager else None,
                    'branch': employee.branch
                }, status=status.HTTP_201_CREATED)
                
            except Exception as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
    
    def update(self, request, *args, **kwargs):
        """
        Override update to sync changes to EmployeeMaster
        Ensures bidirectional data synchronization
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        
        # Save onboarding record
        self.perform_update(serializer)
        
        # ✅ SYNC: Update EmployeeMaster if employee data changed
        try:
            if instance.user:
                employee = EmployeeMaster.objects.filter(user=instance.user).first()
                if employee:
                    sync_fields = {}
                    
                    # Sync name if changed
                    if 'employee_name' in request.data:
                        name_parts = instance.employee_name.split(' ', 1)
                        if len(name_parts) == 2:
                            sync_fields['first_name'] = name_parts[0]
                            sync_fields['last_name'] = name_parts[1]
                        elif len(name_parts) == 1:
                            sync_fields['first_name'] = name_parts[0]
                    
                    # Sync email if changed
                    if 'employee_email' in request.data:
                        sync_fields['email'] = instance.employee_email
                        instance.user.email = instance.employee_email
                        instance.user.save(update_fields=['email'])
                    
                    # Sync department if changed
                    if 'department' in request.data:
                        sync_fields['department'] = instance.department
                    
                    # Sync position/designation if changed
                    if 'position' in request.data:
                        sync_fields['designation'] = instance.position
                    
                    # Sync branch if changed
                    if 'branch' in request.data:
                        sync_fields['branch'] = instance.branch
                    
                    # Apply synced fields
                    if sync_fields:
                        for field, value in sync_fields.items():
                            setattr(employee, field, value)
                        employee.save(update_fields=list(sync_fields.keys()))
                        print(f"[SYNC] Updated EmployeeMaster fields: {list(sync_fields.keys())}")
        except Exception as sync_error:
            # Log but don't fail the update
            print(f"[WARNING] Failed to sync to EmployeeMaster: {str(sync_error)}")
        
        return Response(serializer.data)
    
    def partial_update(self, request, *args, **kwargs):
        """Override partial_update to use the same sync logic"""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)


class OffboardingRecordViewSet(viewsets.ModelViewSet):
    """
    API endpoint for offboarding records
    Supports CRUD + custom actions: statistics, mark_completed
    """
    permission_classes = [IsAuthenticated]
    queryset = OffboardingRecord.objects.all()
    
    def get_serializer_class(self):
        """Use lightweight serializer for list, full serializer for detail"""
        if self.action == 'list':
            return OffboardingRecordListSerializer
        return OffboardingRecordSerializer
    
    def get_queryset(self):
        """
        Filter by status, branch, department, exit_reason, search query
        Annotate with counts for list view
        """
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by branch
        branch = self.request.query_params.get('branch')
        if branch:
            queryset = queryset.filter(branch=branch)
        
        # Filter by department
        department = self.request.query_params.get('department')
        if department:
            queryset = queryset.filter(department__icontains=department)
        
        # Filter by exit reason
        exit_reason = self.request.query_params.get('exit_reason')
        if exit_reason:
            queryset = queryset.filter(exit_reason=exit_reason)
        
        # Search by employee name or email
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_name__icontains=search) |
                Q(employee_email__icontains=search) |
                Q(employee_id__icontains=search)
            )
        
        # Annotate counts for list view
        if self.action == 'list':
            queryset = queryset.annotate(
                equipment_count=Count('equipment', distinct=True),
                documents_count=Count('documents', distinct=True),
                access_count=Count('access_records', distinct=True),
                checklist_count=Count('checklist_items', distinct=True),
                checklist_completed_count=Count('checklist_items', filter=Q(checklist_items__completed=True), distinct=True)
            )
        else:
            # Prefetch related for detail view
            queryset = queryset.prefetch_related(
                'equipment', 'documents', 'access_records', 'checklist_items'
            )
        
        return queryset.select_related('created_by', 'assigned_to', 'rejected_by', 'user')
    
    def perform_create(self, serializer):
        """
        Allow HR to initiate any exit while employees may initiate only their own.
        ✨ ENHANCED: Create ExitApproval records for three-step approval workflow with project-based assignments
        """
        # Extract approval workflow data from request
        project_assignments = self.request.data.get('project_assignments', [])
        hr_coordinator_id = self.request.data.get('hr_coordinator')
        hr_approver_id = self.request.data.get('hr_approver')
        
        if can_manage_offboarding(self.request.user):
            selected_user = serializer.validated_data.get('user')
            reporting_manager = _resolve_exit_reporting_manager(selected_user)
            save_values = {
                'created_by': self.request.user,
                'project_manager_approval_status': 'not_required',
            }
            if reporting_manager:
                save_values['reporting_manager'] = reporting_manager
            
            # ✨ Set HR coordinator and approver if provided
            if hr_coordinator_id:
                save_values['hr_coordinator_id'] = hr_coordinator_id
            if hr_approver_id:
                save_values['hr_approver_id'] = hr_approver_id
            
            # ✨ Set initial approval workflow status based on project assignments
            has_project_managers = any(pa.get('project_manager_ids') for pa in project_assignments)
            if has_project_managers:
                save_values['approval_workflow_status'] = 'project_managers_pending'
            elif hr_coordinator_id:
                save_values['approval_workflow_status'] = 'hr_coordinator_pending'
            elif hr_approver_id:
                save_values['approval_workflow_status'] = 'hr_approver_pending'
            else:
                save_values['approval_workflow_status'] = 'not_started'
            
            record = serializer.save(**save_values)
            
            # ✨ Create ExitApproval records for project-based assignments
            from .models import ExitApproval
            from django.contrib.auth import get_user_model
            User = get_user_model()
            
            # Process each project assignment
            for project_assignment in project_assignments:
                project_number = project_assignment.get('project_number', '')
                project_name = project_assignment.get('project_name', '')
                project_manager_ids = project_assignment.get('project_manager_ids', [])
                
                # Create ExitApproval for each project manager in this project
                for pm_id in project_manager_ids:
                    try:
                        pm_user = User.objects.get(id=pm_id)
                        ExitApproval.objects.create(
                            offboarding_record=record,
                            approver=pm_user,
                            approval_step='project_manager',
                            status='pending',
                            project_number=project_number,
                            project_name=project_name
                        )
                    except User.DoesNotExist:
                        pass
            
            # ✨ Create ExitApproval for HR Coordinator
            if hr_coordinator_id:
                try:
                    hr_coord_user = User.objects.get(id=hr_coordinator_id)
                    ExitApproval.objects.create(
                        offboarding_record=record,
                        approver=hr_coord_user,
                        approval_step='hr_coordinator',
                        status='pending'
                    )
                except User.DoesNotExist:
                    pass
            
            # ✨ Create ExitApproval for HR Approver
            if hr_approver_id:
                try:
                    hr_approver_user = User.objects.get(id=hr_approver_id)
                    ExitApproval.objects.create(
                        offboarding_record=record,
                        approver=hr_approver_user,
                        approval_step='hr_approver',
                        status='pending'
                    )
                except User.DoesNotExist:
                    pass
            
            _notify_project_managers_of_exit(record)
            return

        selected_user = serializer.validated_data.get('user')
        if not selected_user or selected_user.id != self.request.user.id:
            raise PermissionDenied('Employees may initiate an exit process only for their own profile.')

        employee = EmployeeMaster.objects.filter(user=self.request.user).select_related('manager').first()
        if not employee:
            raise PermissionDenied('Your Employee Master profile is required before initiating an exit process.')
        rbac_profile = RBACUserProfile.objects.filter(
            user=self.request.user,
            is_deleted=False,
        ).first()

        employee_name = ' '.join(filter(None, [employee.first_name, employee.last_name])).strip()
        record = serializer.save(
            created_by=self.request.user,
            project_manager_approval_status='not_required',
            user=self.request.user,
            employee_name=employee_name or self.request.user.get_full_name() or self.request.user.username,
            employee_email=employee.email or self.request.user.email,
            employee_id=employee.employee_number or employee.employment_id or getattr(rbac_profile, 'employee_id', '') or str(self.request.user.id),
            position=employee.job_title_uae or employee.job_title_finland or employee.designation or getattr(rbac_profile, 'job_title', '') or '',
            department=employee.department or employee.division or employee.business_unit or getattr(rbac_profile, 'department', '') or '',
            reporting_manager=_resolve_exit_reporting_manager(self.request.user, employee),
            branch=employee.branch or 'RAD',
        )
        _notify_project_managers_of_exit(record)

    def update(self, request, *args, **kwargs):
        if not can_manage_offboarding(request.user):
            raise PermissionDenied('Only HR or an administrator may update an offboarding process.')
        if request.data.get('status') == 'rejected':
            raise ValidationError({'detail': 'Use the Reject action to validate active project assignments.'})
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Only lifecycle HR/admin users may permanently delete an offboarding."""
        if not can_manage_offboarding(request.user):
            raise PermissionDenied('Only HR or an administrator may delete an offboarding process.')
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject an active offboarding when the employee remains on an active project."""
        if not can_manage_offboarding(request.user):
            raise PermissionDenied('Only HR or an administrator may reject an offboarding process.')
        record = self.get_object()
        if record.status not in OFFBOARDING_ACTIVE_STATUSES:
            raise ValidationError({'detail': 'Only an active offboarding process can be rejected.'})

        active_projects = get_active_project_assignments(record.user)
        if not active_projects:
            raise ValidationError({
                'detail': 'Rejection is only available while the employee is assigned to an active project.'
            })

        reason = (request.data.get('reason') or '').strip()
        if not reason:
            reason = 'Employee is assigned to an active project.'
        project_labels = ', '.join(
            f"{project['code']} - {project['name']}" for project in active_projects
        )
        record.status = 'rejected'
        record.rejection_reason = f'{reason} Active project(s): {project_labels}'
        record.rejected_by = request.user
        record.rejected_at = timezone.now()
        record.save(update_fields=[
            'status', 'rejection_reason', 'rejected_by', 'rejected_at', 'updated_at'
        ])

        if record.user_id:
            notification = NotificationService.create_notification(
                recipient=record.user,
                sender=request.user,
                title='Offboarding request rejected',
                message=record.rejection_reason,
                category='APPROVAL',
                priority='HIGH',
                action_url=f'/hr/onboarding?tab=offboarding&record_id={record.id}',
                action_label='View Request',
                metadata={'offboarding_id': record.id, 'event': 'offboarding_rejected'},
            )
            if notification and notification.send_in_app and notification.status == 'PENDING':
                notification.status = 'SENT'
                notification.save(update_fields=['status', 'updated_at'])

        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=['post'], url_path='project-manager-decision')
    def project_manager_decision(self, request, pk=None):
        """Allow an assigned active-project PoM to approve or reject an exit request."""
        decision = (request.data.get('decision') or '').strip().lower()
        if decision not in {'approved', 'rejected'}:
            raise ValidationError({'decision': 'Decision must be approved or rejected.'})

        with transaction.atomic():
            record = OffboardingRecord.objects.select_for_update().get(pk=pk)
            active_projects = get_active_project_assignments(record.user)
            manager_ids = {
                manager.id
                for project in active_projects
                for manager in project['managers']
                if manager
            }
            notified_as_project_manager = any(
                str(notification.metadata.get('offboarding_id')) == str(record.id)
                for notification in Notification.objects.filter(
                    recipient=request.user,
                    metadata__event='employee_exit_initiated',
                )
            )
            if request.user.id not in manager_ids and not notified_as_project_manager:
                raise PermissionDenied(
                    'Only a Project Manager assigned to this employee may decide the exit process.'
                )
            if record.project_manager_approval_status != 'pending':
                raise ValidationError({
                    'detail': (
                        'This exit process has already been decided by a Project Manager '
                        f'({record.project_manager_approval_status}).'
                    )
                })
            if record.status not in OFFBOARDING_ACTIVE_STATUSES:
                raise ValidationError({'detail': 'This offboarding process is no longer active.'})

            note = (request.data.get('note') or '').strip()
            now = timezone.now()
            record.project_manager_approval_status = decision
            record.project_manager_decided_by = request.user
            record.project_manager_decided_at = now
            record.project_manager_decision_note = note
            update_fields = [
                'project_manager_approval_status', 'project_manager_decided_by',
                'project_manager_decided_at', 'project_manager_decision_note', 'updated_at',
            ]
            if decision == 'rejected':
                record.status = 'rejected'
                record.rejection_reason = note or 'Project Manager rejected the exit process.'
                record.rejected_by = request.user
                record.rejected_at = now
                update_fields.extend(['status', 'rejection_reason', 'rejected_by', 'rejected_at'])
            record.save(update_fields=update_fields)

            related_notifications = Notification.objects.filter(
                metadata__event='employee_exit_initiated'
            )
            for notification in related_notifications:
                if str(notification.metadata.get('offboarding_id')) != str(record.id):
                    continue
                notification.metadata = {
                    **notification.metadata,
                    'decision_status': decision,
                    'decided_by': request.user.get_full_name() or request.user.username,
                    'decided_at': now.isoformat(),
                }
                notification.mark_as_read()
                notification.save(update_fields=['metadata', 'updated_at'])

            if record.user_id:
                manager_name = request.user.get_full_name() or request.user.username
                decision_label = 'approved' if decision == 'approved' else 'rejected'
                notification = NotificationService.create_notification(
                    recipient=record.user,
                    sender=request.user,
                    title=f'Exit process {decision_label} by Project Manager',
                    message=(
                        f'{manager_name} {decision_label} your exit process.'
                        + (f' Note: {note}' if note else '')
                    ),
                    category='APPROVAL',
                    priority='HIGH',
                    action_url=f'/hr/onboarding?tab=offboarding&record_id={record.id}',
                    action_label='View Exit Process',
                    metadata={
                        'offboarding_id': record.id,
                        'event': f'offboarding_project_manager_{decision}',
                    },
                )
                if notification and notification.send_in_app and notification.status == 'PENDING':
                    notification.status = 'SENT'
                    notification.save(update_fields=['status', 'updated_at'])

        return Response({
            'detail': f'Exit process {decision} successfully.',
            'decision': decision,
            'offboarding': self.get_serializer(record).data,
        })

    @action(detail=False, methods=['get'], url_path='active-employees')
    def active_employees(self, request):
        """Return employee identities that already have an active offboarding."""
        records = OffboardingRecord.objects.filter(
            status__in=OFFBOARDING_ACTIVE_STATUSES
        ).values(
            'id',
            'user_id',
            'employee_email',
            'status',
            'last_working_day',
        )

        return Response(list(records))
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Get offboarding statistics
        Returns counts by status, upcoming exits, exit reasons, etc.
        """
        queryset = self.get_queryset()
        
        total = queryset.count()
        by_status = {}
        for record in queryset.values('status').annotate(count=Count('id')):
            by_status[record['status']] = record['count']
        
        # Upcoming exits (next 30 days)
        from datetime import timedelta
        upcoming_threshold = date.today() + timedelta(days=30)
        upcoming = queryset.filter(
            last_working_day__gte=date.today(),
            last_working_day__lte=upcoming_threshold,
            status__in=['initiated', 'access_revocation', 'equipment_return', 'exit_interview', 'final_settlement']
        ).count()
        
        # Overdue (last working day passed but not completed)
        overdue = queryset.filter(
            last_working_day__lt=date.today(),
            status__in=['initiated', 'access_revocation', 'equipment_return', 'exit_interview', 'final_settlement']
        ).count()
        
        # Completed this month
        now = timezone.now()
        completed_this_month = queryset.filter(
            status='completed',
            actual_completion_date__year=now.year,
            actual_completion_date__month=now.month
        ).count()
        
        # By exit reason
        by_exit_reason = {}
        for record in queryset.values('exit_reason').annotate(count=Count('id')):
            by_exit_reason[record['exit_reason']] = record['count']
        
        # By branch
        by_branch = {}
        for record in queryset.values('branch').annotate(count=Count('id')):
            by_branch[record['branch']] = record['count']
        
        return Response({
            'total': total,
            'by_status': by_status,
            'upcoming_exits': upcoming,
            'overdue': overdue,
            'completed_this_month': completed_this_month,
            'by_exit_reason': by_exit_reason,
            'by_branch': by_branch
        })
    
    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark offboarding as completed"""
        record = self.get_object()
        if not can_manage_offboarding_stage(request.user, CHECKLIST_STAGE_FINAL_SETTLEMENT, record):
            raise PermissionDenied('Only HR or Finance may complete final offboarding settlement.')
        stage_rows = record.checklist_items.filter(stage__in=OFFBOARDING_CHECKLIST_TEMPLATES)
        present_stages = set(stage_rows.values_list('stage', flat=True))
        if present_stages != set(OFFBOARDING_CHECKLIST_TEMPLATES) or stage_rows.filter(completed=False).exists():
            return Response(
                {'detail': 'Every offboarding checklist stage must be started and completed before closure.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record.status = 'completed'
        record.progress_percentage = 100
        record.actual_completion_date = timezone.now()
        record.save()
        
        serializer = self.get_serializer(record)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='start-checklist-stage')
    def start_checklist_stage(self, request, pk=None):
        record = self.get_object()
        stage = request.data.get('stage')
        if stage not in OFFBOARDING_CHECKLIST_TEMPLATES:
            raise ValidationError({'stage': 'A valid offboarding checklist stage is required.'})
        if record.status in {'completed', 'cancelled', 'rejected'}:
            raise ValidationError({'detail': 'Completed, cancelled, or rejected offboarding workflows are read-only.'})
        if not can_start_offboarding_stage(request.user, stage, record):
            raise PermissionDenied('Your RBAC role cannot start this offboarding checklist stage.')

        existing_names = set(record.checklist_items.filter(stage=stage).values_list('task_name', flat=True))
        due_date = record.target_completion_date or record.last_working_day
        new_items = [
            Checklist(
                offboarding_record=record,
                task_name=task_name,
                description=f'{stage} offboarding checklist task',
                stage=stage,
                due_date=due_date,
                priority=priority,
            )
            for task_name, priority in OFFBOARDING_CHECKLIST_TEMPLATES[stage]
            if task_name not in existing_names
        ]
        Checklist.objects.bulk_create(new_items)

        next_status, minimum_progress = OFFBOARDING_STAGE_START_STATE[stage]
        if record.progress_percentage < minimum_progress:
            record.status = next_status
            record.progress_percentage = minimum_progress
            record.save(update_fields=['status', 'progress_percentage', 'updated_at'])

        record.refresh_from_db()
        return Response({
            **self.get_serializer(record).data,
            'created_checklist_count': len(new_items),
        })


class EquipmentViewSet(viewsets.ModelViewSet):
    """API endpoint for equipment tracking"""
    permission_classes = [IsAuthenticated]
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset


class DocumentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for document tracking with file upload to S3
    Supports: create, list, retrieve, update, delete, upload_file
    """
    permission_classes = [IsAuthenticated]
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    parser_classes = (MultiPartParser, FormParser)
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset.select_related('verified_by')
    
    def perform_create(self, serializer):
        """Handle document creation with optional file upload"""
        file = self.request.FILES.get('file')
        
        if file:
            # Upload file to S3
            s3_service = S3Service()
            
            # Generate unique filename
            file_ext = file.name.split('.')[-1] if '.' in file.name else ''
            unique_filename = f"{uuid.uuid4()}.{file_ext}" if file_ext else str(uuid.uuid4())
            
            # Determine folder path (onboarding_documents/)
            s3_folder = 'media/onboarding_documents/'
            s3_key = f"{s3_folder}{unique_filename}"
            
            # Get MIME type
            mime_type = file.content_type or mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            
            try:
                # Upload to S3
                s3_service.s3_client.upload_fileobj(
                    file,
                    s3_service.bucket_name,
                    s3_key,
                    ExtraArgs={
                        'ContentType': mime_type,
                        'ContentDisposition': f'inline; filename="{file.name}"'
                    }
                )
                
                # Generate presigned URL (valid for 7 days)
                file_url = s3_service.s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': s3_service.bucket_name,
                        'Key': s3_key
                    },
                    ExpiresIn=604800  # 7 days
                )
                
                # Save document with S3 metadata
                serializer.save(
                    file_path=s3_key,
                    file_url=file_url,
                    file_size=file.size,
                    file_mime_type=mime_type,
                    original_filename=file.name,
                    submitted=True
                )
            except Exception as e:
                # Handle upload error
                raise Exception(f"Failed to upload file to S3: {str(e)}")
        else:
            # No file uploaded, just save the document record
            serializer.save()
    
    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_file(self, request, pk=None):
        """
        Upload or replace file for an existing document
        POST /api/v1/onboarding/documents/{id}/upload_file/
        """
        document = self.get_object()
        file = request.FILES.get('file')
        
        if not file:
            return Response(
                {'error': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        s3_service = S3Service()
        
        # Generate unique filename
        file_ext = file.name.split('.')[-1] if '.' in file.name else ''
        unique_filename = f"{uuid.uuid4()}.{file_ext}" if file_ext else str(uuid.uuid4())
        
        # Determine folder path
        s3_folder = 'media/onboarding_documents/'
        s3_key = f"{s3_folder}{unique_filename}"
        
        # Get MIME type
        mime_type = file.content_type or mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        
        try:
            # Delete old file from S3 if exists
            if document.file_path:
                try:
                    s3_service.s3_client.delete_object(
                        Bucket=s3_service.bucket_name,
                        Key=document.file_path
                    )
                except Exception as e:
                    # Log but don't fail if old file deletion fails
                    print(f"Warning: Could not delete old file {document.file_path}: {e}")
            
            # Upload new file to S3
            s3_service.s3_client.upload_fileobj(
                file,
                s3_service.bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': mime_type,
                    'ContentDisposition': f'inline; filename="{file.name}"'
                }
            )
            
            # Generate presigned URL (valid for 7 days)
            file_url = s3_service.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': s3_service.bucket_name,
                    'Key': s3_key
                },
                ExpiresIn=604800  # 7 days
            )
            
            # Update document with new file metadata
            document.file_path = s3_key
            document.file_url = file_url
            document.file_size = file.size
            document.file_mime_type = mime_type
            document.original_filename = file.name
            document.submitted = True
            document.save()
            
            serializer = self.get_serializer(document)
            return Response(serializer.data)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to upload file: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        """Mark document as verified"""
        document = self.get_object()
        document.verified = True
        document.verified_by = request.user
        document.verified_date = timezone.now()
        document.save()
        
        serializer = self.get_serializer(document)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def download_url(self, request, pk=None):
        """
        Generate a fresh presigned URL for downloading the document
        GET /api/v1/onboarding/documents/{id}/download_url/
        """
        document = self.get_object()
        
        if not document.file_path:
            return Response(
                {'error': 'No file associated with this document'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            s3_service = S3Service()
            
            # Generate presigned URL (valid for 1 hour)
            file_url = s3_service.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': s3_service.bucket_name,
                    'Key': document.file_path,
                    'ResponseContentDisposition': f'attachment; filename="{document.original_filename or document.document_name}"'
                },
                ExpiresIn=3600  # 1 hour
            )
            
            return Response({'download_url': file_url})
            
        except Exception as e:
            return Response(
                {'error': f'Failed to generate download URL: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AccessProvisioningViewSet(viewsets.ModelViewSet):
    """API endpoint for access provisioning tracking"""
    permission_classes = [IsAuthenticated]
    queryset = AccessProvisioning.objects.all()
    serializer_class = AccessProvisioningSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset.select_related('assigned_by')


class ChecklistViewSet(viewsets.ModelViewSet):
    """API endpoint for checklist items"""
    permission_classes = [IsAuthenticated]
    queryset = Checklist.objects.all()
    serializer_class = ChecklistSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset.select_related('completed_by', 'onboarding_record', 'offboarding_record')

    def _assert_manage_permission(self, item, stage=None):
        if item.onboarding_record_id:
            if item.onboarding_record.status in {'completed', 'cancelled'}:
                raise PermissionDenied('Completed or cancelled onboarding checklists are read-only.')
            checklist_stage = stage or item.stage
            if not can_manage_onboarding_stage(
                self.request.user, checklist_stage, item.onboarding_record
            ):
                raise PermissionDenied('Your RBAC role cannot update this onboarding checklist stage.')
        elif item.offboarding_record_id:
            if item.offboarding_record.status in {'completed', 'cancelled', 'rejected'}:
                raise PermissionDenied('Completed, cancelled, or rejected offboarding checklists are read-only.')
            if not can_manage_offboarding_stage(
                self.request.user, stage or item.stage, item.offboarding_record
            ):
                raise PermissionDenied('Your RBAC role cannot update this offboarding checklist stage.')

    def perform_create(self, serializer):
        onboarding_record = serializer.validated_data.get('onboarding_record')
        offboarding_record = serializer.validated_data.get('offboarding_record')
        stage = serializer.validated_data.get('stage')
        if onboarding_record and not can_manage_onboarding_stage(
            self.request.user, stage, onboarding_record
        ):
            raise PermissionDenied('Your RBAC role cannot create this onboarding checklist item.')
        if offboarding_record and not can_manage_offboarding_stage(
            self.request.user, stage, offboarding_record
        ):
            raise PermissionDenied('Your RBAC role cannot create this offboarding checklist item.')
        serializer.save()

    def perform_update(self, serializer):
        self._assert_manage_permission(
            serializer.instance,
            serializer.validated_data.get('stage', serializer.instance.stage),
        )
        completed = serializer.validated_data.get('completed')
        if completed is True:
            item = serializer.save(completed_by=self.request.user, completed_date=timezone.now())
            if item.onboarding_record_id:
                complete_onboarding_if_ready(item.onboarding_record)
            elif item.offboarding_record_id:
                complete_offboarding_if_ready(item.offboarding_record)
        elif completed is False:
            serializer.save(completed_by=None, completed_date=None)
        else:
            serializer.save()

    def perform_destroy(self, instance):
        self._assert_manage_permission(instance)
        instance.delete()
