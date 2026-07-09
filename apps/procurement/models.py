"""
Procurement Management Models
Smart data models for procurement tracking, vendor management, and purchasing workflows
"""

from django.db import models
from django.contrib.auth import get_user_model
from apps.core.models import TimeStampedModel
import uuid

# Import master database tables for project-based procurement
from .models_master import (
    Project, Budget, CostCenter,
    ProjectStatus, ProjectType, BudgetCategory
)

__all__ = [
    'Vendor', 'PurchaseRequisition', 'PurchaseOrder', 'Receipt', 'PODocument',
    'Project', 'Budget', 'CostCenter',  # Master tables
]

User = get_user_model()


# Soft-coded configuration for procurement categories - Oil & Gas Industry
PROCUREMENT_CATEGORIES = {
    # Core Equipment
    'rotating_equipment': {'name': 'Rotating Equipment (Pumps, Compressors)', 'icon': 'CogIcon', 'color': 'blue', 'standards': ['API 610', 'API 617', 'ASME']},
    'static_equipment': {'name': 'Static Equipment (Vessels, Tanks)', 'icon': 'CubeIcon', 'color': 'indigo', 'standards': ['ASME VIII', 'API 650', 'API 620']},
    'instrumentation': {'name': 'Instrumentation & Control', 'icon': 'CircuitBoardIcon', 'color': 'purple', 'standards': ['ISA', 'IEC 61511']},
    'valves_fittings': {'name': 'Valves & Fittings', 'icon': 'AdjustmentsIcon', 'color': 'cyan', 'standards': ['API 6D', 'ASME B16.5', 'ASME B16.34']},
    
    # Materials & Spares
    'piping_materials': {'name': 'Piping & Pipeline Materials', 'icon': 'ArrowsRightLeftIcon', 'color': 'green', 'standards': ['ASME B31.3', 'API 5L']},
    'electrical_materials': {'name': 'Electrical Materials', 'icon': 'BoltIcon', 'color': 'amber', 'standards': ['IEC', 'IEEE', 'NEC']},
    'spare_parts': {'name': 'Spare Parts & Components', 'icon': 'WrenchScrewdriverIcon', 'color': 'orange', 'standards': ['OEM Specs']},
    'chemicals': {'name': 'Chemicals & Additives', 'icon': 'BeakerIcon', 'color': 'red', 'standards': ['MSDS', 'API']},
    
    # Services
    'maintenance_services': {'name': 'Maintenance & Repair Services', 'icon': 'WrenchIcon', 'color': 'purple', 'standards': ['ISO 55000']},
    'inspection_testing': {'name': 'Inspection & Testing Services', 'icon': 'MagnifyingGlassIcon', 'color': 'blue', 'standards': ['ASNT', 'API 570', 'API 510']},
    'engineering_services': {'name': 'Engineering & Consulting', 'icon': 'AcademicCapIcon', 'color': 'indigo', 'standards': ['ISO 9001']},
    
    # Others
    'safety_equipment': {'name': 'Safety & PPE', 'icon': 'ShieldCheckIcon', 'color': 'green', 'standards': ['OSHA', 'ANSI']},
    'consumables': {'name': 'Consumables & Supplies', 'icon': 'ShoppingCartIcon', 'color': 'yellow', 'standards': []},
    'software_licenses': {'name': 'Software & Licenses', 'icon': 'ComputerDesktopIcon', 'color': 'teal', 'standards': []},
    'other': {'name': 'Other', 'icon': 'EllipsisHorizontalIcon', 'color': 'gray', 'standards': []},
}

# Material certifications required for oil & gas
MATERIAL_CERTIFICATIONS = {
    'mtc': 'Material Test Certificate (3.1)',
    'mtr': 'Material Test Report',
    'coc': 'Certificate of Conformance',
    'mds': 'Material Data Sheet',
    'msds': 'Material Safety Data Sheet',
    'pqr': 'Procedure Qualification Record',
    'wps': 'Welding Procedure Specification',
    'ndt': 'Non-Destructive Testing Report',
    'hydro': 'Hydrostatic Test Certificate',
    'pmi': 'Positive Material Identification',
}

# Quality standards for oil & gas procurement
QUALITY_STANDARDS = {
    'api': 'American Petroleum Institute',
    'asme': 'ASME Boiler & Pressure Vessel Code',
    'astm': 'ASTM International Standards',
    'iso': 'ISO Quality Standards',
    'nace': 'NACE International (Corrosion)',
    'ansi': 'American National Standards Institute',
    'iec': 'International Electrotechnical Commission',
    'ieee': 'Institute of Electrical and Electronics Engineers',
}


class Vendor(TimeStampedModel):
    """
    Vendor/Supplier master data
    """
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('pending', 'Pending Approval'),
        ('blacklisted', 'Blacklisted'),
    ]
    
    RATING_CHOICES = [
        (5, 'Excellent'),
        (4, 'Good'),
        (3, 'Average'),
        (2, 'Below Average'),
        (1, 'Poor'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor_code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=300)
    contact_person = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=100, blank=True)
    
    # Financial
    tax_id = models.CharField(max_length=100, blank=True)
    payment_terms = models.CharField(max_length=200, blank=True)
    credit_limit = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    
    # Performance
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    rating = models.IntegerField(choices=RATING_CHOICES, null=True, blank=True)
    performance_notes = models.TextField(blank=True)
    
    # Categories handled
    categories = models.JSONField(default=list, blank=True)  # List of category codes
    
    # Oil & Gas Industry Specific Fields
    certifications = models.JSONField(default=list, blank=True)  # ISO, API, ASME certifications
    quality_standards = models.JSONField(default=list, blank=True)  # API, ASME, ASTM compliance
    approved_materials = models.JSONField(default=list, blank=True)  # Approved material grades
    inspection_authority = models.CharField(max_length=200, blank=True)  # Third-party inspection agency
    
    # HSE Compliance
    hse_rating = models.CharField(max_length=50, blank=True)  # Health, Safety, Environment rating
    safety_certifications = models.JSONField(default=list, blank=True)  # OSHA, ISO 45001, etc.
    last_audit_date = models.DateField(null=True, blank=True)
    audit_status = models.CharField(max_length=50, blank=True)  # Passed, Failed, Pending
    
    # ICV (In-Country Value) - Mandatory for Abu Dhabi Market
    icv_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="In-Country Value percentage (0-100)")
    icv_certificate = models.CharField(max_length=100, blank=True, help_text="ICV Certificate Number")
    icv_expiry_date = models.DateField(null=True, blank=True, help_text="ICV Certificate Expiry Date")
    icv_issuing_authority = models.CharField(max_length=200, blank=True, default="ADDED", help_text="ICV Issuing Authority (e.g., ADDED)")
    is_icv_certified = models.BooleanField(default=False, help_text="ICV Certification Status")
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='vendors_created')
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_vendors'
        ordering = ['name']
        indexes = [
            models.Index(fields=['vendor_code']),
            models.Index(fields=['status']),
            models.Index(fields=['rating']),
        ]
    
    def __str__(self):
        return f"{self.vendor_code} - {self.name}"


class PurchaseRequisition(TimeStampedModel):
    """
    Purchase Requisition (PR) - Internal request for procurement
    """
    
    TYPE_CHOICES = [
        ('general', 'General'),
        ('project', 'Project'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
        ('converted', 'Converted to PO'),
    ]
    
    PRIORITY_CHOICES = [
        ('urgent', 'Urgent'),
        ('high', 'High'),
        ('normal', 'Normal'),
        ('low', 'Low'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pr_number = models.CharField(max_length=50, unique=True, db_index=True)
    requisition_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='general')
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50)  # From PROCUREMENT_CATEGORIES
    
    # Requestor info
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='prs_requested')
    department = models.CharField(max_length=200, blank=True)
    project = models.CharField(max_length=200, blank=True)
    
    # Details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')
    required_date = models.DateField(null=True, blank=True)
    estimated_budget = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    
    # Items (JSON field for flexibility)
    items = models.JSONField(default=list, blank=True)
    # Example: [{'item': 'Laptop', 'qty': 2, 'unit': 'ea', 'estimated_price': 1500}]
    
    # Approval workflow
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='prs_approved')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    
    # Metadata
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_requisitions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['pr_number']),
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['requested_by', 'status']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"PR-{self.pr_number}: {self.title}"


class PurchaseOrder(TimeStampedModel):
    """
    Purchase Order (PO) - Official order to vendor
    Enhanced with comprehensive Oil & Gas procurement fields
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent to Vendor'),
        ('acknowledged', 'Acknowledged by Vendor'),
        ('in_progress', 'In Progress'),
        ('partially_received', 'Partially Received'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    po_number = models.CharField(max_length=50, unique=True, db_index=True)
    pr_reference = models.ForeignKey(PurchaseRequisition, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_orders')
    
    # PR/Requisition Details (soft-coded extraction fields)
    pr_requester_name = models.CharField(max_length=200, blank=True, help_text='Name of person who requested the PR')
    
    # Vendor info
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='purchase_orders')
    
    # Details
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    category = models.CharField(max_length=50)
    
    # Financial (soft-coded for AI extraction)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=10, default='USD')
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Payment Terms (soft-coded for extraction from PO documents)
    payment_terms = models.CharField(max_length=300, blank=True, help_text='e.g., Net 30, 50% advance / 50% completion')
    delivery_terms = models.CharField(max_length=200, blank=True, help_text='e.g., FOB, CIF, DAP, DDP')
    payment_milestones = models.JSONField(default=list, blank=True, help_text='Payment schedule milestones')
    # Example: [{'milestone': 'Advance', 'percentage': 30, 'amount': 10000, 'due_date': '2026-07-01'}]
    
    # ═══ PROJECT LINKAGE (Master Database Integration) ═══
    # Professional project-based procurement with master database
    project = models.ForeignKey(
        'Project',  # FK to master Project table
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='purchase_orders',
        help_text='Link to master project registry'
    )
    project_number = models.CharField(
        max_length=100, blank=True, db_index=True,
        help_text='Project code (for AI extraction / legacy compatibility)'
    )
    project_manager = models.CharField(max_length=200, blank=True, help_text='Project Manager name (soft-coded)')
    budget_allocation = models.ForeignKey(
        'Budget',  # FK to project budget line
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='purchase_orders',
        help_text='Link to specific budget line item'
    )
    budget = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Allocated budget for this PO (deprecated, use budget_allocation FK)'
    )
    
    # Items
    items = models.JSONField(default=list, blank=True)
    # Example: [{'item': 'Laptop', 'qty': 2, 'unit_price': 1500, 'total': 3000}]
    
    # Dates (soft-coded for AI extraction)
    po_date = models.DateField(auto_now_add=True)
    start_date = models.DateField(null=True, blank=True, help_text='Service/Project start date')
    end_date = models.DateField(null=True, blank=True, help_text='Service/Project end date')
    expected_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateField(null=True, blank=True)
    
    # Oil & Gas Industry Specific
    material_specifications = models.JSONField(default=dict, blank=True)  # Material grades, standards
    required_certifications = models.JSONField(default=list, blank=True)  # MTC, MTR, COC, etc.
    inspection_requirements = models.TextField(blank=True)  # Third-party inspection requirements
    witness_inspection = models.BooleanField(default=False)  # Client witness inspection required
    heat_numbers_required = models.BooleanField(default=False)  # Material traceability
    ndt_requirements = models.TextField(blank=True)  # Non-destructive testing requirements
    
    # Compliance & Standards
    applicable_standards = models.JSONField(default=list, blank=True)  # API, ASME, ASTM standards
    material_grade = models.CharField(max_length=100, blank=True)  # e.g., API 5L X65, ASTM A106 Gr. B
    pressure_rating = models.CharField(max_length=50, blank=True)  # Pressure class/rating
    temperature_rating = models.CharField(max_length=50, blank=True)  # Temperature range
    
    # ═══ INVOICE TRACKING (A/P Integration) ═══
    # Professional linkage to vendor invoices for payment tracking
    # Enables PO → Invoice reconciliation and 3-way matching
    related_invoices = models.ManyToManyField(
        'finance.Invoice',  # Link to apps.finance.Invoice (A/P invoices)
        blank=True,
        related_name='purchase_orders',
        help_text='Vendor invoices against this PO'
    )
    invoice_status = models.CharField(
        max_length=20,
        choices=[
            ('not_invoiced', 'Not Invoiced'),
            ('partially_invoiced', 'Partially Invoiced'),
            ('fully_invoiced', 'Fully Invoiced'),
            ('over_invoiced', 'Over Invoiced'),  # Invoice amount > PO amount
        ],
        default='not_invoiced',
        help_text='Invoice reconciliation status'
    )
    total_invoiced_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text='Sum of all related invoice amounts'
    )
    
    # People
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='pos_created')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_approved')
    
    # Metadata
    terms_and_conditions = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_orders'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['po_number']),
            models.Index(fields=['vendor', 'status']),
            models.Index(fields=['status']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"PO-{self.po_number}: {self.title}"
    
    # ═══ BUSINESS LOGIC METHODS (Soft-Coded) ═══
    
    def update_invoice_status(self):
        """
        Recalculate invoice status based on related invoices.
        Soft-coded 3-way matching logic: PO ↔ Invoice ↔ Receipt
        """
        from django.db.models import Sum
        total_invoiced = self.related_invoices.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        
        self.total_invoiced_amount = total_invoiced
        
        if total_invoiced == 0:
            self.invoice_status = 'not_invoiced'
        elif total_invoiced < self.total_amount:
            self.invoice_status = 'partially_invoiced'
        elif total_invoiced == self.total_amount:
            self.invoice_status = 'fully_invoiced'
        else:
            self.invoice_status = 'over_invoiced'
        
        self.save(update_fields=['invoice_status', 'total_invoiced_amount'])
    
    def get_budget_variance(self):
        """Calculate variance against allocated budget"""
        if not self.budget_allocation:
            return None
        allocated = self.budget_allocation.allocated_amount
        return self.total_amount - allocated
    
    def is_over_budget(self):
        """Check if PO exceeds allocated budget"""
        variance = self.get_budget_variance()
        return variance > 0 if variance is not None else False
    
    def get_project_display(self):
        """Get project name/number for display (smart fallback)"""
        if self.project:
            return f"{self.project.project_number} - {self.project.project_name}"
        elif self.project_number:
            return self.project_number
        return "No Project Assigned"
    
    def auto_link_project(self):
        """
        Auto-link to Project master table by matching project_number.
        Enables migration from string-based to FK-based project tracking.
        """
        if self.project or not self.project_number:
            return False
        
        try:
            project = Project.objects.get(project_number=self.project_number)
            self.project = project
            self.save(update_fields=['project'])
            return True
        except Project.DoesNotExist:
            return False
        except Project.MultipleObjectsReturned:
            # Log warning - data integrity issue
            return False


class Receipt(TimeStampedModel):
    """
    Goods Receipt - Track deliveries and receiving
    """
    
    STATUS_CHOICES = [
        ('pending', 'Pending Inspection'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('partial', 'Partially Accepted'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt_number = models.CharField(max_length=50, unique=True, db_index=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='receipts')
    
    # Receipt details
    receipt_date = models.DateField(auto_now_add=True)
    received_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='receipts_received')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Items received
    items_received = models.JSONField(default=list, blank=True)
    # Example: [{'item': 'Laptop', 'ordered_qty': 2, 'received_qty': 2, 'accepted_qty': 2}]
    
    # Quality check
    quality_check_passed = models.BooleanField(default=True)
    inspection_notes = models.TextField(blank=True)
    
    # Oil & Gas Quality & Compliance
    certificates_received = models.JSONField(default=list, blank=True)  # MTC, MTR, COC received
    heat_numbers = models.JSONField(default=list, blank=True)  # Material heat numbers for traceability
    inspector_name = models.CharField(max_length=200, blank=True)  # Third-party inspector
    inspection_agency = models.CharField(max_length=200, blank=True)  # SGS, Bureau Veritas, etc.
    inspection_report_number = models.CharField(max_length=100, blank=True)
    ndt_performed = models.BooleanField(default=False)  # Non-destructive testing performed
    ndt_results = models.TextField(blank=True)  # NDT test results
    dimensional_check_passed = models.BooleanField(default=True)
    visual_inspection_passed = models.BooleanField(default=True)
    material_verification_passed = models.BooleanField(default=True)  # PMI test passed
    
    # Metadata
    delivery_note_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'procurement_receipts'
        ordering = ['-receipt_date']
        indexes = [
            models.Index(fields=['receipt_number']),
            models.Index(fields=['purchase_order', 'status']),
            models.Index(fields=['-receipt_date']),
        ]
    
    def __str__(self):
        return f"GRN-{self.receipt_number} for {self.purchase_order.po_number}"


class PODocument(TimeStampedModel):
    """
    Uploaded PO/PR PDF document - stores S3 reference and AI-extracted data.
    One record per uploaded file; linked to a PurchaseOrder once confirmed.
    """

    EXTRACTION_STATUS_CHOICES = [
        ('pending', 'Pending Extraction'),
        ('processing', 'Processing'),
        ('completed', 'Extraction Completed'),
        ('failed', 'Extraction Failed'),
    ]

    DOC_TYPE_CHOICES = [
        ('purchase_order', 'Purchase Order'),
        ('purchase_requisition', 'Purchase Requisition'),
        ('unknown', 'Unknown'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Source file
    original_filename = models.CharField(max_length=300)
    s3_key = models.CharField(max_length=500, blank=True)
    s3_url = models.URLField(max_length=1000, blank=True)
    file_size_bytes = models.PositiveIntegerField(default=0)

    # Detection
    document_type = models.CharField(max_length=30, choices=DOC_TYPE_CHOICES, default='unknown')
    extraction_status = models.CharField(max_length=20, choices=EXTRACTION_STATUS_CHOICES, default='pending')
    extraction_error = models.TextField(blank=True)

    # AI extracted payload — mirrors all PO/PR fields
    extracted_data = models.JSONField(default=dict, blank=True)

    # Audit
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='po_documents_uploaded')
    confirmed_po = models.ForeignKey(
        PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='source_documents',
        help_text='Populated once user confirms the extracted data as a real PO'
    )

    class Meta:
        db_table = 'procurement_po_documents'
        ordering = ['-created_at']

    def __str__(self):
        return f"PODoc:{self.original_filename} [{self.extraction_status}]"
