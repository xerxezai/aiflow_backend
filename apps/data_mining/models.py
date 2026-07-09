"""
Data Mining Models
Soft-coded transformation pipeline architecture
"""
import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


# ─── Soft-coded transformation operation types ───────────────────────────────
TRANSFORMATION_OPERATIONS = [
    ('join', 'Join - Merge datasets by common key'),
    ('filter', 'Filter - Remove unwanted rows'),
    ('aggregate', 'Aggregate - Sum, avg, count, min, max'),
    ('clean', 'Clean - Remove duplicates, handle nulls'),
    ('derive', 'Derive - Create calculated fields'),
    ('pivot', 'Pivot - Convert rows to columns'),
    ('unpivot', 'Unpivot - Convert columns to rows'),
    ('union', 'Union - Stack datasets vertically'),
    ('rename', 'Rename - Rename columns'),
    ('select', 'Select - Choose specific columns'),
    ('sort', 'Sort - Order by columns'),
    ('sample', 'Sample - Take random subset'),
]

# ─── Soft-coded join types ───────────────────────────────────────────────────
JOIN_TYPES = [
    ('inner', 'Inner Join - Keep only matching rows'),
    ('left', 'Left Join - Keep all from left dataset'),
    ('right', 'Right Join - Keep all from right dataset'),
    ('outer', 'Full Outer Join - Keep all rows'),
]

# ─── Soft-coded aggregation functions ────────────────────────────────────────
AGGREGATION_FUNCTIONS = [
    ('sum', 'Sum'),
    ('avg', 'Average'),
    ('count', 'Count'),
    ('min', 'Minimum'),
    ('max', 'Maximum'),
    ('median', 'Median'),
    ('std', 'Standard Deviation'),
    ('var', 'Variance'),
]


class DataMiningProject(models.Model):
    """
    Data Mining Project - combines multiple Wrench documents into master dataset
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="Project name")
    description = models.TextField(blank=True, help_text="Project description")
    
    # Wrench integration
    wrench_project_number = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Wrench project/order number"
    )
    wrench_project_name = models.CharField(
        max_length=255, 
        blank=True,
        help_text="Wrench project name"
    )
    
    # Owner
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='data_mining_projects'
    )
    
    # Status
    status = models.CharField(
        max_length=50,
        choices=[
            ('draft', 'Draft'),
            ('configuring', 'Configuring Pipeline'),
            ('executing', 'Executing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        default='draft'
    )
    
    # Master file output
    master_file_path = models.CharField(
        max_length=500,
        blank=True,
        help_text="S3 path to generated master file"
    )
    master_file_format = models.CharField(
        max_length=50,
        choices=[
            ('csv', 'CSV'),
            ('excel', 'Excel'),
            ('json', 'JSON'),
            ('parquet', 'Parquet'),
        ],
        default='excel'
    )
    
    # Statistics
    total_documents = models.IntegerField(default=0)
    total_rows_processed = models.IntegerField(default=0)
    execution_time_seconds = models.FloatField(null=True, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'data_mining_projects'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.wrench_project_number or 'No Project'})"


class DataMiningDocument(models.Model):
    """
    Document included in Data Mining project
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        DataMiningProject,
        on_delete=models.CASCADE,
        related_name='documents'
    )
    
    # Wrench document metadata
    wrench_doc_number = models.CharField(max_length=255, help_text="Wrench document number")
    wrench_doc_title = models.CharField(max_length=500, blank=True)
    wrench_doc_revision = models.CharField(max_length=50, blank=True)
    wrench_transmittal_id = models.CharField(max_length=100, blank=True)
    
    # File info
    file_path = models.CharField(max_length=500, blank=True, help_text="S3 path to downloaded document")
    file_type = models.CharField(
        max_length=50,
        choices=[
            ('pdf', 'PDF'),
            ('excel', 'Excel'),
            ('csv', 'CSV'),
            ('word', 'Word'),
            ('dwg', 'AutoCAD'),
            ('other', 'Other'),
        ],
        default='pdf'
    )
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    
    # Extraction status
    extraction_status = models.CharField(
        max_length=50,
        choices=[
            ('pending', 'Pending'),
            ('extracting', 'Extracting'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        default='pending'
    )
    extracted_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Extracted tabular data from document"
    )
    row_count = models.IntegerField(default=0)
    column_count = models.IntegerField(default=0)
    
    # Order in pipeline
    sequence_order = models.IntegerField(default=0)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'data_mining_documents'
        ordering = ['project', 'sequence_order']
    
    def __str__(self):
        return f"{self.wrench_doc_number} - {self.wrench_doc_title[:50]}"


class TransformationPipeline(models.Model):
    """
    Transformation pipeline (Tableau Prep-style workflow)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        DataMiningProject,
        on_delete=models.CASCADE,
        related_name='pipeline'
    )
    
    name = models.CharField(max_length=255, default="Main Pipeline")
    description = models.TextField(blank=True)
    
    # Pipeline visualization config (for frontend canvas)
    canvas_config = models.JSONField(
        default=dict,
        help_text="Visual layout: node positions, connections"
    )
    
    # Execution
    last_executed_at = models.DateTimeField(null=True, blank=True)
    execution_log = models.TextField(blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'transformation_pipelines'
    
    def __str__(self):
        return f"Pipeline: {self.name} (Project: {self.project.name})"


class TransformationStep(models.Model):
    """
    Individual transformation step in the pipeline
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline = models.ForeignKey(
        TransformationPipeline,
        on_delete=models.CASCADE,
        related_name='steps'
    )
    
    # Step metadata
    step_name = models.CharField(max_length=255, help_text="User-friendly step name")
    operation_type = models.CharField(
        max_length=50,
        choices=TRANSFORMATION_OPERATIONS,
        help_text="Type of transformation"
    )
    
    # Step configuration (soft-coded)
    config = models.JSONField(
        default=dict,
        help_text="""
        Soft-coded configuration per operation type:
        
        JOIN:
            {
                "join_type": "inner|left|right|outer",
                "left_input": "step_id or document_id",
                "right_input": "step_id or document_id",
                "left_key": "column_name",
                "right_key": "column_name"
            }
        
        FILTER:
            {
                "conditions": [
                    {"column": "status", "operator": "equals", "value": "Active"},
                    {"column": "amount", "operator": "greater_than", "value": 1000}
                ],
                "logic": "and|or"
            }
        
        AGGREGATE:
            {
                "group_by": ["project", "category"],
                "aggregations": [
                    {"column": "amount", "function": "sum", "output_name": "total_amount"},
                    {"column": "quantity", "function": "avg", "output_name": "avg_quantity"}
                ]
            }
        
        CLEAN:
            {
                "remove_duplicates": true,
                "drop_null_rows": ["column1", "column2"],
                "fill_null_value": {"column3": 0, "column4": "N/A"}
            }
        
        DERIVE:
            {
                "new_columns": [
                    {
                        "name": "total_price",
                        "expression": "quantity * unit_price",
                        "data_type": "float"
                    }
                ]
            }
        
        RENAME:
            {
                "column_mapping": {
                    "old_name1": "new_name1",
                    "old_name2": "new_name2"
                }
            }
        
        SELECT:
            {
                "columns": ["col1", "col2", "col3"]
            }
        
        UNION:
            {
                "inputs": ["step_id1", "step_id2"],
                "align_columns": true
            }
        """
    )
    
    # Input/Output
    input_source = models.CharField(
        max_length=255,
        blank=True,
        help_text="UUID of previous step or document"
    )
    output_preview = models.JSONField(
        null=True,
        blank=True,
        help_text="Sample rows after transformation (first 100 rows)"
    )
    output_row_count = models.IntegerField(default=0)
    output_column_count = models.IntegerField(default=0)
    
    # Execution order
    sequence_order = models.IntegerField(default=0)
    
    # Status
    status = models.CharField(
        max_length=50,
        choices=[
            ('pending', 'Pending'),
            ('executing', 'Executing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        default='pending'
    )
    error_message = models.TextField(blank=True)
    
    # Performance
    execution_time_ms = models.FloatField(null=True, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'transformation_steps'
        ordering = ['pipeline', 'sequence_order']
    
    def __str__(self):
        return f"{self.step_name} ({self.get_operation_type_display()})"
