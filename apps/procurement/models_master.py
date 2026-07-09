"""
Master Database Tables for Professional Project-Based Procurement System

Architecture:
  - Project: Central project registry (master table)
  - Budget: Project budget allocations and tracking
  - CostCenter: Organizational cost centers for financial reporting
  - PurchaseOrder → Project (FK linkage)
  - PurchaseOrder → finance.Invoice (A/P tracking)
  
Soft-coded approach: All thresholds, categories, and business rules 
configurable via settings or config files.
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from decimal import Decimal
import uuid

from apps.core.models import TimeStampedModel

User = get_user_model()


# ══════════════════════════════════════════════════════════════════════════════
# MASTER DATABASE TABLES
# ══════════════════════════════════════════════════════════════════════════════

class ProjectStatus(models.TextChoices):
    """Soft-coded project lifecycle states"""
    PLANNING = 'planning', 'Planning'
    ACTIVE = 'active', 'Active'
    ON_HOLD = 'on_hold', 'On Hold'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'
    ARCHIVED = 'archived', 'Archived'


class ProjectType(models.TextChoices):
    """Oil & Gas project categories (soft-coded)"""
    ENGINEERING = 'engineering', 'Engineering Services'
    CONSTRUCTION = 'construction', 'Construction'
    MAINTENANCE = 'maintenance', 'Maintenance & Operations'
    PMC = 'pmc', 'Project Management Consultancy'
    FEASIBILITY = 'feasibility', 'Feasibility Study'
    FEED = 'feed', 'Front-End Engineering Design (FEED)'
    DETAILED_DESIGN = 'detailed_design', 'Detailed Engineering'
    COMMISSIONING = 'commissioning', 'Commissioning & Startup'
    SHUTDOWN = 'shutdown', 'Shutdown & Turnaround'
    BROWNFIELD = 'brownfield', 'Brownfield Modification'
    GREENFIELD = 'greenfield', 'Greenfield Development'
    INTERNAL = 'internal', 'Internal Project'


class CostCenter(TimeStampedModel):
    """
    Organizational cost centers for financial reporting and budget control.
    Master table for departmental/divisional financial tracking.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True, help_text='CC-XXX format')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    
    # Hierarchy (soft-coded for organizational structure)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    department = models.CharField(max_length=100, blank=True, help_text='Process, Piping, Electrical, etc.')
    division = models.CharField(max_length=100, blank=True, help_text='Engineering, Finance, QHSE, etc.')
    
    # Control
    is_active = models.BooleanField(default=True, db_index=True)
    manager = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='managed_cost_centers')
    
    class Meta:
        ordering = ['code']
        verbose_name = 'Cost Center'
        verbose_name_plural = 'Cost Centers'
        indexes = [
            models.Index(fields=['code', 'is_active']),
            models.Index(fields=['department', 'division']),
        ]
    
    def __str__(self):
        return f"{self.code} - {self.name}"


class Project(TimeStampedModel):
    """
    Master Project Registry - Central repository for all projects.
    Links to Purchase Orders, Budgets, Invoices, Timesheets, etc.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Identification (soft-coded format: PRJ-YYYY-XXX)
    project_number = models.CharField(max_length=100, unique=True, db_index=True, help_text='Unique project identifier')
    project_name = models.CharField(max_length=300)
    client_name = models.CharField(max_length=200, blank=True)
    client_reference = models.CharField(max_length=100, blank=True, help_text='Client PO/Contract number')
    
    # Classification
    project_type = models.CharField(max_length=30, choices=ProjectType.choices, default=ProjectType.ENGINEERING)
    status = models.CharField(max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.PLANNING, db_index=True)
    
    # Organization
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.SET_NULL, related_name='projects')
    project_manager = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='managed_projects')
    project_manager_name = models.CharField(max_length=200, blank=True, help_text='Fallback if PM not in system')
    
    # Team
    lead_engineer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='led_projects')
    team_members = models.ManyToManyField(User, blank=True, related_name='project_memberships')
    
    # Scope
    description = models.TextField(blank=True)
    scope_of_work = models.TextField(blank=True)
    deliverables = models.JSONField(default=list, blank=True, help_text='List of expected deliverables')
    
    # Timeline (soft-coded for AI extraction from contracts)
    start_date = models.DateField(null=True, blank=True)
    planned_end_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)
    
    # Location
    site_location = models.CharField(max_length=300, blank=True)
    country = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    
    # Contract & Financial
    contract_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    contract_currency = models.CharField(max_length=10, default='AED')
    payment_terms = models.TextField(blank=True)
    
    # Tracking
    progress_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    health_status = models.CharField(
        max_length=20,
        choices=[
            ('green', 'On Track'),
            ('yellow', 'At Risk'),
            ('red', 'Critical'),
        ],
        default='green'
    )
    
    # Metadata
    notes = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True, help_text='Searchable tags')
    is_active = models.BooleanField(default=True, db_index=True)
    is_billable = models.BooleanField(default=True, help_text='Client-billable project')
    is_internal = models.BooleanField(default=False, help_text='Internal R&D/overhead project')
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'
        indexes = [
            models.Index(fields=['project_number']),
            models.Index(fields=['status', 'is_active']),
            models.Index(fields=['client_name', 'project_type']),
            models.Index(fields=['start_date', 'planned_end_date']),
        ]
    
    def __str__(self):
        return f"{self.project_number} - {self.project_name}"
    
    def get_total_budget(self):
        """Calculate total allocated budget from all budget lines"""
        return self.budgets.aggregate(
            total=models.Sum('allocated_amount')
        )['total'] or Decimal('0.00')
    
    def get_total_spent(self):
        """Calculate total spent from purchase orders"""
        return self.purchase_orders.aggregate(
            total=models.Sum('total_amount')
        )['total'] or Decimal('0.00')
    
    def get_budget_utilization(self):
        """Calculate budget utilization percentage"""
        total_budget = self.get_total_budget()
        if total_budget == 0:
            return Decimal('0.00')
        total_spent = self.get_total_spent()
        return (total_spent / total_budget * 100).quantize(Decimal('0.01'))
    
    def is_over_budget(self):
        """Check if project has exceeded budget"""
        return self.get_total_spent() > self.get_total_budget()


class BudgetCategory(models.TextChoices):
    """Soft-coded budget line item categories"""
    ENGINEERING = 'engineering', 'Engineering Services'
    PROCUREMENT = 'procurement', 'Procurement & Materials'
    EQUIPMENT = 'equipment', 'Equipment & Machinery'
    CONSTRUCTION = 'construction', 'Construction & Installation'
    MANPOWER = 'manpower', 'Manpower & Labor'
    TRAVEL = 'travel', 'Travel & Accommodation'
    TESTING = 'testing', 'Testing & Commissioning'
    CERTIFICATION = 'certification', 'Certification & Inspection'
    SOFTWARE = 'software', 'Software & Licenses'
    TRAINING = 'training', 'Training & Development'
    CONSULTANCY = 'consultancy', 'Consultancy Services'
    CONTINGENCY = 'contingency', 'Contingency Reserve'
    OVERHEAD = 'overhead', 'Overhead & Admin'
    OTHER = 'other', 'Other Expenses'


class Budget(TimeStampedModel):
    """
    Project Budget Allocation - Master budget tracking per project/category.
    Enables financial control, forecasting, and variance analysis.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Linkage
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='budgets')
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.SET_NULL)
    
    # Classification
    category = models.CharField(max_length=30, choices=BudgetCategory.choices)
    sub_category = models.CharField(max_length=100, blank=True, help_text='Detailed breakdown')
    description = models.TextField(blank=True)
    
    # Financial (soft-coded amounts)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=10, default='AED')
    
    # Period
    fiscal_year = models.IntegerField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    
    # Control
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_budgets')
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Tracking
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['project', 'category']
        verbose_name = 'Budget'
        verbose_name_plural = 'Budgets'
        indexes = [
            models.Index(fields=['project', 'category']),
            models.Index(fields=['fiscal_year', 'is_approved']),
        ]
    
    def __str__(self):
        return f"{self.project.project_number} - {self.get_category_display()} - {self.allocated_amount} {self.currency}"
    
    def get_spent_amount(self):
        """Calculate amount spent against this budget line"""
        # Sum from linked purchase orders
        from apps.procurement.models import PurchaseOrder
        total = PurchaseOrder.objects.filter(
            project=self.project,
            category=self.category,
            status__in=['sent', 'acknowledged', 'in_progress', 'completed']
        ).aggregate(total=models.Sum('total_amount'))['total'] or Decimal('0.00')
        return total
    
    def get_remaining_amount(self):
        """Calculate remaining budget"""
        return self.allocated_amount - self.get_spent_amount()
    
    def get_utilization_percentage(self):
        """Calculate budget utilization %"""
        if self.allocated_amount == 0:
            return Decimal('0.00')
        return (self.get_spent_amount() / self.allocated_amount * 100).quantize(Decimal('0.01'))
    
    def is_over_budget(self):
        """Check if over allocated budget"""
        return self.get_spent_amount() > self.allocated_amount
