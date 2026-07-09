"""
Project Management Serializers
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone
# Smart Project Collection Models
from apps.core.models import (
    ProjectCollection,
    ProjectDiscipline,
    SmartProjectDocument,
    CrossDisciplineRecommendation
)

User = get_user_model()


class UserSimpleSerializer(serializers.ModelSerializer):
    """Simple user serializer for nested representations"""
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name']


class ProjectMemberSerializer(serializers.ModelSerializer):
    """Project member serializer"""
    user = UserSimpleSerializer(read_only=True)
    user_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = ProjectMember
        fields = ['id', 'user', 'user_id', 'role', 'joined_at', 'is_active']


class ProjectTaskSerializer(serializers.ModelSerializer):
    """Project task serializer"""
    assigned_to = UserSimpleSerializer(read_only=True)
    assigned_to_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = ProjectTask
        fields = [
            'id', 'title', 'description', 'status', 'assigned_to', 'assigned_to_id',
            'due_date', 'priority', 'estimated_hours', 'actual_hours',
            'created_at', 'updated_at'
        ]


class ProjectMilestoneSerializer(serializers.ModelSerializer):
    """Project milestone serializer"""
    class Meta:
        model = ProjectMilestone
        fields = [
            'id', 'name', 'description', 'target_date', 'completed_date',
            'is_completed', 'created_at', 'updated_at'
        ]


class ProjectSerializer(serializers.ModelSerializer):
    """Project serializer"""
    owner = UserSimpleSerializer(read_only=True)
    owner_id = serializers.IntegerField(write_only=True, required=False)
    team_members_data = ProjectMemberSerializer(source='memberships', many=True, read_only=True)
    tasks_summary = serializers.SerializerMethodField()
    milestones_summary = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    budget_utilization = serializers.FloatField(read_only=True)
    team_size = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'code', 'description', 'status', 'priority', 'progress',
            'start_date', 'end_date', 'owner', 'owner_id', 'team_members_data',
            'budget', 'spent', 'client_name', 'location', 'tags', 'custom_fields',
            # Project Dashboard fields — added in migration 0002
            'contract_value', 'currency', 'scope_type',
            'tasks_summary', 'milestones_summary', 'is_overdue', 'budget_utilization',
            'team_size', 'created_at', 'updated_at',
        ]

    def get_tasks_summary(self, obj):
        """Get task counts by status"""
        tasks = obj.tasks.filter(is_deleted=False)
        return {
            'total': tasks.count(),
            'todo': tasks.filter(status='todo').count(),
            'in_progress': tasks.filter(status='in_progress').count(),
            'completed': tasks.filter(status='completed').count(),
            'blocked': tasks.filter(status='blocked').count(),
        }

    def get_milestones_summary(self, obj):
        """Get milestone summary"""
        milestones = obj.milestones.filter(is_deleted=False)
        return {
            'total': milestones.count(),
            'completed': milestones.filter(is_completed=True).count(),
            'pending': milestones.filter(is_completed=False).count(),
        }


class ProjectListSerializer(serializers.ModelSerializer):
    """Lightweight project list serializer"""
    owner_name = serializers.SerializerMethodField()
    team_size = serializers.IntegerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'code', 'status', 'priority', 'progress',
            'start_date', 'end_date', 'owner_name', 'team_size',
            'is_overdue', 'created_at'
        ]

    def get_owner_name(self, obj):
        if obj.owner:
            return f"{obj.owner.first_name} {obj.owner.last_name}".strip() or obj.owner.email
        return "Unassigned"


# ========================================
# SMART PROJECT COLLECTION SERIALIZERS
# ========================================

class ProjectDisciplineSerializer(serializers.ModelSerializer):
    """Serializer for project disciplines"""
    lead_engineer = UserSimpleSerializer(read_only=True)
    lead_engineer_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    document_count = serializers.IntegerField(read_only=True)
    size_mb = serializers.SerializerMethodField()
    
    class Meta:
        model = ProjectDiscipline
        fields = [
            'id', 'discipline_name', 'discipline_type', 'description',
            'lead_engineer', 'lead_engineer_id', 'document_count',
            'size_bytes', 'size_mb', 'document_types', 's3_discipline_path',
            'created_at', 'updated_at'
        ]
        
    def get_size_mb(self, obj):
        """Convert size from bytes to MB"""
        return round(obj.size_bytes / (1024 * 1024), 2) if obj.size_bytes else 0


class ProjectCollectionSerializer(serializers.ModelSerializer):
    """Serializer for project collections with smart organization"""
    created_by = UserSimpleSerializer(read_only=True)
    disciplines = ProjectDisciplineSerializer(many=True, read_only=True)
    discipline_count = serializers.IntegerField(read_only=True)
    total_documents = serializers.IntegerField(read_only=True)
    total_size_mb = serializers.SerializerMethodField()
    ai_insights = serializers.SerializerMethodField()
    
    class Meta:
        model = ProjectCollection
        fields = [
            'id', 'project_code', 'project_name', 'project_description',
            'client_name', 'project_manager', 'project_status', 
            'start_date', 'end_date', 'created_by',
            's3_root_path', 'auto_organize_enabled', 'ai_classification_enabled',
            'cross_discipline_recommendations', 'disciplines', 'discipline_count',
            'total_documents', 'total_size_bytes', 'total_size_mb',
            'last_document_upload', 'ai_insights',
            'created_at', 'updated_at'
        ]
        
    def get_total_size_mb(self, obj):
        """Convert total size from bytes to MB"""
        return round(obj.total_size_mb, 2) if obj.total_size_mb else 0
        
    def get_ai_insights(self, obj):
        """Get AI-powered insights for this project"""
        # This can be enhanced to include actual AI insights
        insights = {
            'document_organization_confidence': 0.85,
            'discipline_completeness': {},
            'missing_document_types': [],
            'cross_discipline_opportunities': []
        }
        
        # Calculate discipline completeness
        expected_docs_per_discipline = {
            'process': ['pid_drawing', 'process_flow', 'heat_balance'],
            'mechanical': ['equipment_datasheet', 'mechanical_drawing', 'pump_curve'],
            'electrical': ['electrical_drawing', 'motor_datasheet', 'cable_schedule'],
            'instrumentation': ['loop_diagram', 'instrument_datasheet', 'io_list'],
            'civil': ['structural_drawing', 'foundation_plan'],
            'piping': ['isometric_drawing', 'piping_spec', 'stress_analysis']
        }
        
        for discipline in obj.disciplines.all():
            expected_docs = expected_docs_per_discipline.get(discipline.discipline_name, [])
            if expected_docs:
                available_docs = discipline.document_types or []
                completeness = len(set(available_docs) & set(expected_docs)) / len(expected_docs)
                insights['discipline_completeness'][discipline.discipline_name] = round(completeness * 100, 1)
                
                missing_docs = set(expected_docs) - set(available_docs)
                if missing_docs:
                    insights['missing_document_types'].extend([
                        f"{discipline.discipline_name}: {doc}" for doc in missing_docs
                    ])
        
        return insights


class SmartProjectDocumentSerializer(serializers.ModelSerializer):
    """Serializer for smart project documents"""
    uploaded_by = UserSimpleSerializer(read_only=True)
    project = serializers.StringRelatedField(read_only=True)
    discipline = ProjectDisciplineSerializer(read_only=True)
    file_size_mb = serializers.SerializerMethodField()
    ai_classification_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = SmartProjectDocument
        fields = [
            'id', 'document_id', 'filename', 'original_filename',
            'project', 'discipline', 'document_type', 'document_subtype',
            's3_key', 'file_size', 'file_size_mb', 'file_extension',
            'content_hash', 'uploaded_by', 'upload_date',
            'ai_classification_confidence', 'ai_extracted_metadata',
            'ai_classification_summary', 'is_active', 'created_at', 'updated_at'
        ]
        
    def get_file_size_mb(self, obj):
        """Convert file size from bytes to MB"""
        return round(obj.file_size_mb, 2) if obj.file_size else 0
        
    def get_ai_classification_summary(self, obj):
        """Get human-readable AI classification summary"""
        confidence_level = "High" if obj.ai_classification_confidence >= 0.8 else \
                          "Medium" if obj.ai_classification_confidence >= 0.6 else "Low"
        
        return {
            'confidence_level': confidence_level,
            'confidence_score': round(obj.ai_classification_confidence, 3),
            'classification_details': {
                'project': obj.project.project_code,
                'discipline': obj.discipline.discipline_name,
                'document_type': obj.document_type,
                'document_subtype': obj.document_subtype
            },
            'extracted_metadata_summary': self._summarize_metadata(obj.ai_extracted_metadata or {})
        }
        
    def _summarize_metadata(self, metadata):
        """Summarize extracted metadata for display"""
        summary = {}
        
        # Key metadata fields to highlight
        key_fields = ['equipment_tag', 'drawing_number', 'revision', 'project_number', 'discipline_code']
        
        for field in key_fields:
            if field in metadata and metadata[field]:
                summary[field] = metadata[field]
                
        return summary


class CrossDisciplineRecommendationSerializer(serializers.ModelSerializer):
    """Serializer for cross-discipline recommendations"""
    project = serializers.StringRelatedField(read_only=True)
    source_document = SmartProjectDocumentSerializer(read_only=True)
    source_discipline = ProjectDisciplineSerializer(read_only=True)
    target_discipline = ProjectDisciplineSerializer(read_only=True)
    created_by = UserSimpleSerializer(read_only=True)
    
    class Meta:
        model = CrossDisciplineRecommendation
        fields = [
            'id', 'project', 'source_document', 'source_discipline',
            'target_discipline', 'recommendation_type', 'recommendation_text',
            'priority', 'status', 'ai_confidence', 'created_by',
            'reviewed_by', 'reviewed_at', 'review_notes',
            'created_at', 'updated_at'
        ]


class SmartProjectDocumentUploadSerializer(serializers.Serializer):
    """Serializer for smart document upload requests"""
    file = serializers.FileField(required=True)
    hint_project_code = serializers.CharField(required=False, max_length=100)
    hint_discipline = serializers.CharField(required=False, max_length=50)
    hint_document_type = serializers.CharField(required=False, max_length=100)
    
    def validate_file(self, value):
        """Validate uploaded file"""
        # Check file size (e.g., max 100MB)
        max_file_size = 100 * 1024 * 1024  # 100MB
        if value.size > max_file_size:
            raise serializers.ValidationError(
                f"File size ({value.size / (1024*1024):.1f}MB) exceeds maximum allowed size (100MB)."
            )
        
        # Check file extension
        allowed_extensions = [
            'pdf', 'dwg', 'dxf', 'xlsx', 'xls', 'docx', 'doc', 
            'pptx', 'ppt', 'txt', 'csv', 'png', 'jpg', 'jpeg',
            'tif', 'tiff', 'zip', 'rar', '7z'
        ]
        
        file_extension = value.name.split('.')[-1].lower() if '.' in value.name else ''
        if file_extension not in allowed_extensions:
            raise serializers.ValidationError(
                f"File type '.{file_extension}' is not supported. "
                f"Allowed types: {', '.join(allowed_extensions)}"
            )
            
        return value


class BatchUploadRequestSerializer(serializers.Serializer):
    """Serializer for batch upload requests"""
    files = serializers.ListField(
        child=serializers.FileField(),
        min_length=1,
        max_length=50  # Maximum 50 files per batch
    )
    hint_project_code = serializers.CharField(required=False, max_length=100)
    hint_discipline = serializers.CharField(required=False, max_length=50)
    file_specific_hints = serializers.JSONField(required=False)
    
    def validate_file_specific_hints(self, value):
        """Validate file-specific hints JSON structure"""
        if value:
            if not isinstance(value, dict):
                raise serializers.ValidationError("file_specific_hints must be a JSON object")
                
            # Validate structure of hints for each file
            for filename, hints in value.items():
                if not isinstance(hints, dict):
                    raise serializers.ValidationError(
                        f"Hints for file '{filename}' must be a JSON object"
                    )
                    
                allowed_hint_keys = ['project_code', 'discipline', 'document_type']
                invalid_keys = set(hints.keys()) - set(allowed_hint_keys)
                if invalid_keys:
                    raise serializers.ValidationError(
                        f"Invalid hint keys for file '{filename}': {list(invalid_keys)}"
                    )
                    
        return value
