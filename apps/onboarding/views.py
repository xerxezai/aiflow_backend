"""
Onboarding & Offboarding Views
Provides REST API endpoints for managing employee lifecycle

✅ MIGRATED: Now uses EmployeeMaster instead of UserProfile
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q, Count, Prefetch
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction
from datetime import date
import uuid
import mimetypes

from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist
)
from .serializers import (
    OnboardingRecordSerializer, OnboardingRecordListSerializer,
    OffboardingRecordSerializer, OffboardingRecordListSerializer,
    EquipmentSerializer, DocumentSerializer,
    AccessProvisioningSerializer, ChecklistSerializer
)
from apps.core.s3_service import S3Service

# Employee management - using new EmployeeMaster system
from apps.hr_core.models import EmployeeMaster
from apps.hr_core.services import EmployeeService

User = get_user_model()


class OnboardingRecordViewSet(viewsets.ModelViewSet):
    """
    API endpoint for onboarding records
    Supports CRUD + custom actions: statistics, mark_completed
    Includes passport photo upload to S3
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
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
        record.status = 'completed'
        record.progress_percentage = 100
        record.actual_completion_date = timezone.now()
        record.save()
        
        serializer = self.get_serializer(record)
        return Response(serializer.data)
    
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
                        
                        # Generate unique S3 key
                        file_extension = photo.name.split('.')[-1] if '.' in photo.name else 'jpg'
                        s3_key = f"media/onboarding_photos/{user.id}_{uuid.uuid4().hex[:8]}.{file_extension}"
                        
                        # Upload to S3
                        s3_service = S3Service()
                        s3_service.upload_file(
                            file_obj=photo.file,
                            key=s3_key,
                            content_type=content_type
                        )
                        
                        # Generate presigned URL (valid for 7 days)
                        photo_url = s3_service.generate_presigned_url(s3_key, expiration=7*24*3600)
                        
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
        
        return queryset.select_related('created_by', 'assigned_to', 'user')
    
    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)
    
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
        record.status = 'completed'
        record.progress_percentage = 100
        record.actual_completion_date = timezone.now()
        record.save()
        
        serializer = self.get_serializer(record)
        return Response(serializer.data)


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
        
        return queryset.select_related('completed_by')
