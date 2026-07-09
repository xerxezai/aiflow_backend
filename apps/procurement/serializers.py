"""
Procurement Management Serializers
API data serialization for procurement workflows
"""

from rest_framework import serializers
from .models import Vendor, PurchaseRequisition, PurchaseOrder, Receipt, PODocument, PROCUREMENT_CATEGORIES


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for Vendor model"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    rating_display = serializers.CharField(source='get_rating_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = Vendor
        fields = [
            'id', 'vendor_code', 'name', 'contact_person', 'email', 'phone', 'address',
            'country', 'tax_id', 'payment_terms', 'credit_limit', 'status', 'status_display',
            'rating', 'rating_display', 'performance_notes', 'categories', 'created_by',
            'created_by_name', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Requisition"""
    
    requisition_type_display = serializers.CharField(source='get_requisition_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    requested_by_name = serializers.CharField(source='requested_by.get_full_name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    category_display = serializers.SerializerMethodField()
    
    class Meta:
        model = PurchaseRequisition
        fields = [
            'id', 'pr_number', 'requisition_type', 'requisition_type_display', 'title', 'description', 'category', 'category_display',
            'requested_by', 'requested_by_name', 'department', 'project', 'status',
            'status_display', 'priority', 'priority_display', 'required_date',
            'estimated_budget', 'items', 'approved_by', 'approved_by_name',
            'approved_at', 'rejection_reason', 'notes', 'attachments',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)
    
    def create(self, validated_data):
        validated_data['requested_by'] = self.context['request'].user
        return super().create(validated_data)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Order"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    category_display = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    
    # Project linkage fields (soft-coded relationship)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    project_display = serializers.SerializerMethodField()
    budget_allocation_display = serializers.CharField(source='budget_allocation.description', read_only=True, allow_null=True)
    
    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'po_number', 'pr_reference', 'pr_requester_name',
            'vendor', 'vendor_name', 'title', 'description', 
            'status', 'status_display', 'category', 'category_display',
            'total_amount', 'currency', 'tax_amount', 'discount_amount', 'items',
            'po_date', 'start_date', 'end_date', 'expected_delivery', 'actual_delivery',
            'project', 'project_name', 'project_display', 'project_number', 'project_manager', 
            'budget_allocation', 'budget_allocation_display', 'budget',
            'payment_terms', 'delivery_terms', 'payment_milestones',
            'created_by', 'created_by_name', 'approved_by', 'approved_by_name', 
            'terms_and_conditions', 'notes', 'attachments', 
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'po_date', 'created_at', 'updated_at']
    
    def get_project_display(self, obj):
        """Get formatted project display string"""
        if obj.project:
            return f"{obj.project.project_number} - {obj.project.project_name}"
        return None
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)
    
    def create(self, validated_data):
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
            'delivery_note_number', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'receipt_date', 'created_at', 'updated_at']
    
    def create(self, validated_data):
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


# ══════════════════════════════════════════════════════════════════════════════
# MASTER DATABASE SERIALIZERS - Professional Project-Based Procurement
# ══════════════════════════════════════════════════════════════════════════════

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
        return obj.project_manager_name or '—'
    
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

