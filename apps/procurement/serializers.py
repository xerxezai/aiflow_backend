"""
Procurement Management Serializers
API data serialization for procurement workflows
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from .models import Vendor, PurchaseRequisition, PurchaseOrder, Receipt, PODocument, PROCUREMENT_CATEGORIES
from .services.purchase_order_numbering import PurchaseOrderNumberService
from .services.receipt_numbering import ReceiptNumberService
from .services.requisition_numbering import RequisitionNumberService
from .services.requisition_status import canonicalize_pr_status
from .services.requisition_validation import (
    line_items_total,
    normalize_line_items,
    validate_attachments,
)


PR_SERVER_CONTROLLED_FIELDS = {
    'issued_by',
    'status',
    'current_approval_step',
    'pm_name',
    'pm_signature',
    'pm_approval_status',
    'pm_approved_at',
    'eng_manager_name',
    'eng_manager_signature',
    'eng_manager_approval_status',
    'eng_manager_approved_at',
    'manager_projects_name',
    'manager_projects_signature',
    'manager_projects_approval_status',
    'manager_projects_approved_at',
    'vp_op_name',
    'vp_op_signature',
    'vp_op_approval_status',
    'vp_op_approved_at',
    'requested_by',
    'approved_by',
    'approved_at',
    'rejection_reason',
    'approval_hierarchy',
}


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for Vendor model"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    rating_display = serializers.CharField(source='get_rating_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = Vendor
        fields = [
            # Core Information
            'id', 'vendor_code', 'name', 'contact_person', 'email', 'phone', 'address',
            'country',
            
            # Financial & Legal
            'tax_id', 'trade_license_number', 'vat_number', 'payment_terms', 'credit_limit',
            
            # Status & Performance
            'status', 'status_display', 'rating', 'rating_display', 'performance_notes',
            
            # Categories & Services
            'categories',
            
            # Oil & Gas Specific
            'certifications', 'quality_standards', 'approved_materials', 'inspection_authority',
            
            # HSE Compliance
            'hse_rating', 'safety_certifications', 'last_audit_date', 'audit_status',
            
            # ICV (In-Country Value) - Abu Dhabi Market
            'icv_percentage', 'icv_certificate', 'icv_expiry_date', 'icv_issuing_authority', 'is_icv_certified',
            
            # ADNOC & Industry
            'adnoc_approved', 'vendor_tenure_years',
            
            # Metadata
            'created_by', 'created_by_name', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    """
    Serializer for Purchase Requisition
    Aligned with RAD-OM-PRC-0001 FRM -1 Rev 0 template (23 fields)
    """
    
    # Display fields
    requisition_type_display = serializers.CharField(source='get_requisition_type_display', read_only=True)
    status_display = serializers.SerializerMethodField()
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    pm_approval_status_display = serializers.CharField(source='get_pm_approval_status_display', read_only=True)
    vp_op_approval_status_display = serializers.CharField(source='get_vp_op_approval_status_display', read_only=True)
    
    # User relationship fields
    issued_by_name = serializers.CharField(source='issued_by.get_full_name', read_only=True, allow_null=True)
    pm_name_display = serializers.CharField(source='pm_name.get_full_name', read_only=True, allow_null=True)
    eng_manager_name_display = serializers.CharField(source='eng_manager_name.get_full_name', read_only=True, allow_null=True)
    manager_projects_name_display = serializers.CharField(source='manager_projects_name.get_full_name', read_only=True, allow_null=True)
    vp_op_name_display = serializers.CharField(source='vp_op_name.get_full_name', read_only=True, allow_null=True)
    
    # Vendor relationship fields
    vendor_details = VendorSerializer(source='vendor', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True, allow_null=True)
    
    # Legacy fields
    requested_by_name = serializers.CharField(source='requested_by.get_full_name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    category_display = serializers.SerializerMethodField()
    
    # File upload fields
    attachments_files = serializers.ListField(
        child=serializers.FileField(),
        write_only=True,
        required=False,
        help_text='Upload multiple files (will be stored in S3)'
    )

    # API alias for frontend compatibility
    approval_hierarchy = serializers.JSONField(source='approval_workflow_config', read_only=True)

    SERVER_CONTROLLED_FIELDS = PR_SERVER_CONTROLLED_FIELDS
    
    class Meta:
        model = PurchaseRequisition
        fields = [
            # Header Section (Fields 1-3)
            'id', 'pr_number', 'issued_by', 'issued_by_name', 'issued_date',
            
            # Supplier Section (Fields 4-5)
            'supplier_name', 'supplier_business_id',
            
            # Vendor Integration (Smart linking)
            'vendor', 'vendor_details', 'vendor_name', 'vendor_selection_reason', 'ai_vendor_recommendations',
            
            # Enhanced Vendor Selection (Feedback: Multiple vendors with ICV)
            'selected_vendors', 'single_source_justification',
            
            # Project/Product Section (Fields 6-7)
            'product_service', 'project_department',
            
            # Enhanced Project Selection (Feedback: Multiple projects)
            'project_details',
            
            # Description Section (Field 8)
            'description_reason',
            
            # Preferred Supplier Section (Field 9)
            'preferred_supplier_if_any',
            
            # Pricing Section (Fields 10-13)
            'price_description', 'total_price', 'currency', 'price_remarks', 'net_total_excl_vat', 'price_remarks_data',
            
            # Management Approval (Feedback: For PR > AED 100k)
            'management_approval', 'management_approval_remarks', 'management_approval_evidence',
            
            # Reference Section (Field 14) - Enhanced with PO Applicable
            'po_applicable', 'po_number_reference',
            
            # Purchase Recommendation Section (Field 15) - RENAMED from special_notes
            'purchase_recommendation',
            
            # Dynamic Approval Workflow
            'approval_workflow_config', 'approval_hierarchy', 'current_approval_step',
            
            # Approvals Section (Fields 16-21) - Enhanced with new tiers
            'pm_name', 'pm_name_display', 'pm_signature', 'pm_approval_status', 'pm_approval_status_display', 'pm_approved_at',
            'eng_manager_name', 'eng_manager_name_display', 'eng_manager_signature', 'eng_manager_approval_status', 'eng_manager_approved_at',
            'manager_projects_name', 'manager_projects_name_display', 'manager_projects_signature', 'manager_projects_approval_status', 'manager_projects_approved_at',
            'vp_op_name', 'vp_op_name_display', 'vp_op_signature', 'vp_op_approval_status', 'vp_op_approval_status_display', 'vp_op_approved_at',
            
            # Footer/Metadata (Fields 22-23)
            'form_reference', 'page_number',
            
            # Legacy fields (backward compatibility)
            'requisition_type', 'requisition_type_display', 'title', 'category', 'category_display',
            'requested_by', 'requested_by_name', 'department', 'project', 'status',
            'status_display', 'priority', 'priority_display', 'required_date',
            'estimated_budget', 'items', 'approved_by', 'approved_by_name',
            'approved_at', 'rejection_reason', 'notes',
            
            # Review Deadline & Resolution (Feedback fields)
            'review_due_at', 'resolution_referral', 
            
            # Attachments
            'attachments', 'attachments_files',
            
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'pr_number', 'created_at', 'updated_at', 'attachments',
            *PR_SERVER_CONTROLLED_FIELDS,
        ]

    def _is_super_admin(self, user):
        if getattr(user, 'is_superuser', False):
            return True

        try:
            return user.rbac_profile.roles.filter(
                code='super_admin',
                is_active=True,
            ).exists()
        except (AttributeError, ObjectDoesNotExist):
            return False

    def validate_approval_workflow_config(self, value):
        """Validate approver assignments and discard client-supplied approval state."""
        if not isinstance(value, list):
            raise serializers.ValidationError('Approval workflow must be a list of stages.')

        if len(value) > 20:
            raise serializers.ValidationError('Approval workflow cannot contain more than 20 stages.')

        User = get_user_model()
        normalized_workflow = []
        assigned_user_ids = set()

        for index, stage in enumerate(value):
            if not isinstance(stage, dict):
                raise serializers.ValidationError(f'Approval stage {index + 1} must be an object.')

            role = str(stage.get('role') or '').strip()
            if not role:
                raise serializers.ValidationError(f'Approval stage {index + 1} requires a role.')
            if len(role) > 100:
                raise serializers.ValidationError(f'Approval stage {index + 1} role is too long.')

            assigned_user_id = stage.get('user_id') or stage.get('approver_id')
            if not assigned_user_id:
                raise serializers.ValidationError(f'Approval stage {index + 1} requires an approver.')

            try:
                approver = User.objects.get(pk=assigned_user_id, is_active=True)
            except (User.DoesNotExist, ValueError, TypeError):
                raise serializers.ValidationError(
                    f'Approval stage {index + 1} must reference an active user.'
                )

            try:
                level = max(1, int(stage.get('level', index + 1)))
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'Approval stage {index + 1} has an invalid level.')

            approver_id = str(approver.pk)
            if approver_id in assigned_user_ids:
                raise serializers.ValidationError(
                    f'Approval stage {index + 1} duplicates an approver already selected in this workflow.'
                )
            assigned_user_ids.add(approver_id)

            # Level 1 is intentionally open to every active employee. Later
            # levels retain module-access validation for operational safety.
            if level != 1:
                try:
                    can_review_requisitions = (
                        approver.is_superuser
                        or approver.rbac_profile.has_module_access('procurement_requisitions')
                    )
                except ObjectDoesNotExist:
                    can_review_requisitions = False
                if not can_review_requisitions:
                    raise serializers.ValidationError(
                        f'Approval stage {index + 1} approver requires Purchase Requisitions module access.'
                    )

            normalized_stage = {
                'step': index + 1,
                'level': level,
                'role': role,
                'user_id': str(approver.pk),
                'user_name': approver.get_full_name() or approver.email,
                'status': 'pending',
                'approved_at': None,
            }

            stage_name = str(stage.get('stage') or '').strip()
            if stage_name:
                normalized_stage['stage'] = stage_name[:150]

            if level == 1:
                normalized_stage['approval_group'] = 'level_1'
                normalized_stage['group_mode'] = 'all'

            normalized_workflow.append(normalized_stage)

        return normalized_workflow

    def validate_items(self, value):
        return normalize_line_items(value)

    def validate_attachments_files(self, value):
        existing = self.instance.attachments if self.instance else []
        return validate_attachments(value, existing)

    def validate(self, attrs):
        attempted_server_fields = self.SERVER_CONTROLLED_FIELDS.intersection(self.initial_data.keys())
        if attempted_server_fields:
            raise serializers.ValidationError({
                field: 'This field is controlled by the requisition workflow.'
                for field in sorted(attempted_server_fields)
            })

        if self.instance and 'approval_workflow_config' in attrs:
            if self.instance.status != 'draft':
                raise serializers.ValidationError({
                    'approval_workflow_config': 'Approval assignments can only be changed while the requisition is a draft.'
                })

            request = self.context.get('request')
            user = getattr(request, 'user', None)
            if (
                not user
                or (
                    str(self.instance.issued_by_id) != str(user.id)
                    and not self._is_super_admin(user)
                )
            ):
                raise serializers.ValidationError({
                    'approval_workflow_config': 'Only the requisition issuer may change draft approval assignments.'
                })

        if 'items' in attrs and attrs['items']:
            calculated_total = line_items_total(attrs['items'])
            requested_total = attrs.get(
                'total_price',
                getattr(self.instance, 'total_price', None) if self.instance else None,
            )
            if requested_total is None:
                attrs['total_price'] = calculated_total
                attrs.setdefault('net_total_excl_vat', calculated_total)
            elif requested_total != calculated_total:
                raise serializers.ValidationError({
                    'total_price': 'Total price must equal the sum of the line items.'
                })

        return attrs
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)

    def get_status_display(self, obj):
        return canonicalize_pr_status(obj.status).replace('_', ' ').title()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['status'] = canonicalize_pr_status(instance.status)
        return data
    
    @transaction.atomic
    def create(self, validated_data):
        # Extract files if present
        files = validated_data.pop('attachments_files', [])
        management_evidence = validated_data.pop('management_approval_evidence_file', None)
        # This JSON column is NOT NULL. Normalize omitted/null multipart data
        # before constructing the model instance.
        validated_data['management_approval_evidence'] = (
            validated_data.get('management_approval_evidence') or []
        )
        
        # Set issued_by to current user if not provided
        if not validated_data.get('issued_by'):
            validated_data['issued_by'] = self.context['request'].user
        
        # Set issued_date to today if not provided
        if not validated_data.get('issued_date'):
            from datetime import date
            validated_data['issued_date'] = date.today()
        
        # Auto-generate PR number if not provided
        if not validated_data.get('pr_number'):
            validated_data['pr_number'] = RequisitionNumberService.next_number(
                validated_data.get('requisition_type', 'project')
            )
        
        # Auto-generate title from product_service if not provided
        if not validated_data.get('title') and validated_data.get('product_service'):
            validated_data['title'] = validated_data['product_service'][:300]
        
        # Create the PR instance
        instance = super().create(validated_data)
        
        # Upload files to S3 if any
        if files:
            self._upload_attachments(instance, files)
        if management_evidence:
            instance.management_approval_evidence = self._upload_attachments(instance, [management_evidence])
            instance.save(update_fields=['management_approval_evidence'])
        
        return instance
    
    @transaction.atomic
    def update(self, instance, validated_data):
        # Extract files if present
        files = validated_data.pop('attachments_files', [])
        management_evidence = validated_data.pop('management_approval_evidence_file', None)
        if 'management_approval_evidence' in validated_data:
            validated_data['management_approval_evidence'] = (
                validated_data.get('management_approval_evidence') or []
            )
        
        # Update the instance
        instance = super().update(instance, validated_data)
        
        # Upload files to S3 if any
        if files:
            self._upload_attachments(instance, files)
        if management_evidence:
            instance.management_approval_evidence = self._upload_attachments(instance, [management_evidence])
            instance.save(update_fields=['management_approval_evidence'])
        
        return instance
    
    def _upload_attachments(self, instance, files):
        """Upload files to S3 and update attachments field"""
        from apps.core.s3_utils import S3Client
        from django.utils import timezone
        import logging
        import uuid
        
        logger = logging.getLogger(__name__)
        s3_client = S3Client()
        
        attachments = list(instance.attachments or [])
        validated_files = validate_attachments(files, attachments)
        uploaded = []
        
        for file in validated_files:
            try:
                safe_name = file.safe_name
                object_id = uuid.uuid4().hex
                s3_key = f"procurement/requisitions/{instance.pr_number}/{object_id}_{safe_name}"
                
                # Upload to S3
                success = s3_client.upload_file(
                    file_obj=file,
                    s3_key=s3_key,
                    content_type=file.verified_content_type,
                    metadata={
                        'pr_number': instance.pr_number,
                        'uploaded_by': self.context['request'].user.email,
                        'original_filename': safe_name,
                    }
                )
                
                if success:
                    # Get S3 URL
                    s3_url = f"https://{s3_client.bucket_name}.s3.{s3_client.s3_client.meta.region_name}.amazonaws.com/{s3_key}"
                    
                    # Add to attachments
                    attachments.append({
                        'filename': safe_name,
                        's3_key': s3_key,
                        's3_url': s3_url,
                        'uploaded_at': timezone.now().isoformat(),
                        'uploaded_by': self.context['request'].user.email,
                        'file_size': file.size,
                        'content_type': file.verified_content_type,
                    })
                    uploaded.append(attachments[-1])
                    logger.info(f"Uploaded {safe_name} to S3: {s3_key}")
                else:
                    raise serializers.ValidationError(
                        {'attachments_files': f'Failed to store {safe_name}.'}
                    )
            except serializers.ValidationError:
                raise
            except Exception as e:
                logger.error(f"Error uploading attachment: {type(e).__name__}")
                raise serializers.ValidationError(
                    {'attachments_files': f'Failed to store {file.safe_name}.'}
                ) from e
        
        # Save updated attachments
        instance.attachments = attachments
        instance.save(update_fields=['attachments'])
        return uploaded


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Order"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    category_display = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    approved_by_user_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    pr_number = serializers.CharField(source='pr_reference.pr_number', read_only=True, allow_null=True)
    po_number_verified = serializers.SerializerMethodField()
    po_number_verification_message = serializers.SerializerMethodField()
    
    # Project linkage fields (soft-coded relationship)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    project_display = serializers.SerializerMethodField()
    budget_allocation_display = serializers.CharField(source='budget_allocation.description', read_only=True, allow_null=True)
    
    class Meta:
        model = PurchaseOrder
        fields = [
            # Core PO fields
            'id', 'po_number', 'po_number_verified', 'po_number_verification_message',
            'pr_reference', 'pr_number', 'pr_requester_name',
            'vendor', 'vendor_name', 'title', 'description', 
            'status', 'status_display', 'category', 'category_display', 'form_note',
            
            # Seller/Vendor contact details
            'seller_reference', 'quote_ref', 'seller_license_no',
            
            # Buyer/Invoicing contact details
            'invoicing_attn', 'invoicing_emails', 'company_fax',
            
            # Buyer reference contacts
            'buyer_reference_pm', 'buyer_reference_pe',
            
            # Financial
            'total_amount', 'currency', 'tax_amount', 'vat_percentage', 'discount_amount', 
            
            # Payment & delivery
            'payment_terms', 'payment_mode', 'delivery_terms', 'marking', 
            'payment_milestones', 'workshop_rates',
            
            # Items & pricing
            'items',
            
            # Dates
            'po_date', 'start_date', 'end_date', 'expected_delivery', 'actual_delivery',
            
            # Project linkage
            'project', 'project_name', 'project_display', 'project_number', 'project_manager', 
            'budget_allocation', 'budget_allocation_display', 'budget',
            
            # Detailed project information
            'end_client', 'contractor', 'subcontractor', 'company_agreement_no', 'rad_project_no',
            
            # Approval section
            'approved_by', 'approved_by_user_name', 'approved_by_name', 'approved_by_title', 'approved_date',
            'approval_signature', 'approval_stamp',
            'technical_approver', 'financial_approver', 'management_approver',
            'approval_log', 'final_approver_notes',
            
            # Order confirmation (vendor response)
            'confirmation_date', 'seller_contact_person', 'seller_phone', 'seller_fax', 'seller_email',
            
            # Contract sections
            'scope_of_services', 'safety_requirements', 'variations_clause', 
            'time_schedule', 'reporting_meetings', 'performance_requirements', 'contact_persons',
            
            # People & metadata
            'created_by', 'created_by_name', 'terms_and_conditions', 'notes', 'attachments', 
            
            # Timestamps
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'po_number', 'po_date', 'created_at', 'updated_at']
    
    def get_project_display(self, obj):
        """Get formatted project display string"""
        if obj.project:
            return f"{obj.project.project_number} - {obj.project.project_name}"
        return None
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)

    def _po_number_verification(self, obj):
        pr_number = obj.pr_reference.pr_number if obj.pr_reference_id else None
        return PurchaseOrderNumberService.verify(obj.po_number, pr_number)

    def get_po_number_verified(self, obj):
        return self._po_number_verification(obj)[0]

    def get_po_number_verification_message(self, obj):
        return self._po_number_verification(obj)[1]
    
    @transaction.atomic
    def create(self, validated_data):
        # Official PO identifiers are assigned only by the locked server-side sequence.
        order_type = 'project' if any(
            validated_data.get(field)
            for field in ('project', 'project_number', 'rad_project_no')
        ) else 'general'
        validated_data['po_number'] = PurchaseOrderNumberService.next_number(order_type)
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class ReceiptSerializer(serializers.ModelSerializer):
    """Serializer for Goods Receipt"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True, allow_null=True)
    po_number = serializers.CharField(source='purchase_order.po_number', read_only=True)
    
    class Meta:
        model = Receipt
        fields = [
            'id', 'receipt_number', 'purchase_order', 'po_number', 'receipt_date',
            'received_by', 'received_by_name', 'status', 'status_display',
            'items_received', 'quality_check_passed', 'inspection_notes',
            'certificates_received', 'heat_numbers', 'inspector_name',
            'inspection_agency', 'inspection_report_number', 'ndt_performed',
            'ndt_results', 'dimensional_check_passed',
            'visual_inspection_passed', 'material_verification_passed',
            'delivery_note_number', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'receipt_number', 'receipt_date', 'received_by', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        validated_data['receipt_number'] = ReceiptNumberService.next_number()
        validated_data['received_by'] = self.context['request'].user
        return super().create(validated_data)


class ProcurementCategorySerializer(serializers.Serializer):
    """Serializer for procurement category configuration"""
    
    code = serializers.CharField()
    name = serializers.CharField()
    icon = serializers.CharField()
    color = serializers.CharField()


class PODocumentSerializer(serializers.ModelSerializer):
    """Serializer for uploaded PO/PR documents and their AI-extracted data."""

    uploaded_by_name = serializers.CharField(source='uploaded_by.get_full_name', read_only=True, allow_null=True)
    extraction_status_display = serializers.CharField(source='get_extraction_status_display', read_only=True)
    document_type_display = serializers.CharField(source='get_document_type_display', read_only=True)

    class Meta:
        model = PODocument
        fields = [
            'id', 'original_filename', 's3_key', 's3_url', 'file_size_bytes',
            'document_type', 'document_type_display', 'extraction_status',
            'extraction_status_display', 'extraction_error', 'extracted_data',
            'uploaded_by', 'uploaded_by_name', 'confirmed_po',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


# ============================================================================
# MASTER DATABASE SERIALIZERS - Professional Project-Based Procurement
# ============================================================================

class CostCenterSerializer(serializers.ModelSerializer):
    """Cost Center master table serializer"""
    
    manager_name = serializers.CharField(source='manager.get_full_name', read_only=True, allow_null=True)
    parent_name = serializers.CharField(source='parent.name', read_only=True, allow_null=True)
    
    class Meta:
        from .models import CostCenter
        model = CostCenter
        fields = [
            'id', 'code', 'name', 'description', 'parent', 'parent_name',
            'department', 'division', 'is_active', 'manager', 'manager_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class BudgetSerializer(serializers.ModelSerializer):
    """Budget allocation serializer with computed spend tracking"""
    
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    project_number = serializers.CharField(source='project.project_number', read_only=True, allow_null=True)
    cost_center_name = serializers.CharField(source='cost_center.name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    
    # Computed fields (soft-coded)
    spent_amount = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    utilization_percentage = serializers.SerializerMethodField()
    is_over_budget = serializers.SerializerMethodField()
    
    class Meta:
        from .models import Budget
        model = Budget
        fields = [
            'id', 'project', 'project_name', 'project_number',
            'cost_center', 'cost_center_name', 'category', 'category_display',
            'sub_category', 'description', 'allocated_amount', 'currency',
            'fiscal_year', 'period_start', 'period_end',
            'is_approved', 'approved_by', 'approved_by_name', 'approved_at',
            'spent_amount', 'remaining_amount', 'utilization_percentage', 'is_over_budget',
            'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_spent_amount(self, obj):
        return float(obj.get_spent_amount())
    
    def get_remaining_amount(self, obj):
        return float(obj.get_remaining_amount())
    
    def get_utilization_percentage(self, obj):
        return float(obj.get_utilization_percentage())
    
    def get_is_over_budget(self, obj):
        return obj.is_over_budget()


class ProjectListSerializer(serializers.ModelSerializer):
    """Lightweight project serializer for list views"""
    
    project_manager_display = serializers.SerializerMethodField()
    cost_center_name = serializers.CharField(source='cost_center.name', read_only=True, allow_null=True)
    project_type_display = serializers.CharField(source='get_project_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    # Key metrics (soft-coded computations)
    total_budget = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    budget_utilization = serializers.SerializerMethodField()
    
    class Meta:
        from .models import Project
        model = Project
        fields = [
            'id', 'project_number', 'project_name', 'client_name',
            'project_type', 'project_type_display', 'status', 'status_display',
            'cost_center', 'cost_center_name', 'project_manager', 'project_manager_display',
            'start_date', 'planned_end_date', 'contract_value', 'contract_currency',
            'progress_percentage', 'health_status', 'is_active', 'is_billable',
            'total_budget', 'total_spent', 'budget_utilization',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_project_manager_display(self, obj):
        if obj.project_manager:
            return obj.project_manager.get_full_name()
        return obj.project_manager_name or 'N/A'
    
    def get_total_budget(self, obj):
        return float(obj.get_total_budget())
    
    def get_total_spent(self, obj):
        return float(obj.get_total_spent())
    
    def get_budget_utilization(self, obj):
        return float(obj.get_budget_utilization())


class ProjectDetailSerializer(ProjectListSerializer):
    """Full project serializer with all relationships"""
    
    lead_engineer_name = serializers.CharField(source='lead_engineer.get_full_name', read_only=True, allow_null=True)
    team_member_names = serializers.SerializerMethodField()
    budgets = BudgetSerializer(many=True, read_only=True)
    purchase_order_count = serializers.SerializerMethodField()
    
    class Meta(ProjectListSerializer.Meta):
        fields = ProjectListSerializer.Meta.fields + [
            'client_reference', 'lead_engineer', 'lead_engineer_name',
            'team_members', 'team_member_names', 'description', 'scope_of_work',
            'deliverables', 'actual_end_date', 'site_location', 'country',
            'region', 'payment_terms', 'notes', 'tags', 'is_internal',
            'budgets', 'purchase_order_count'
        ]
    
    def get_team_member_names(self, obj):
        return [m.get_full_name() for m in obj.team_members.all()]
    
    def get_purchase_order_count(self, obj):
        return obj.purchase_orders.count()

