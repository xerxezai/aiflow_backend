"""
Data Mining Serializers
DRF serializers for API endpoints
"""
from rest_framework import serializers
from .models import (
    DataMiningProject,
    DataMiningDocument,
    TransformationPipeline,
    TransformationStep,
)


class DataMiningDocumentSerializer(serializers.ModelSerializer):
    """Serializer for Data Mining documents"""
    
    class Meta:
        model = DataMiningDocument
        fields = [
            'id', 'wrench_doc_number', 'wrench_doc_title', 'wrench_doc_revision',
            'wrench_transmittal_id', 'file_path', 'file_type', 'file_size_bytes',
            'extraction_status', 'extracted_data', 'row_count', 'column_count',
            'sequence_order', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class TransformationStepSerializer(serializers.ModelSerializer):
    """Serializer for transformation steps"""
    operation_display = serializers.CharField(source='get_operation_type_display', read_only=True)
    
    class Meta:
        model = TransformationStep
        fields = [
            'id', 'step_name', 'operation_type', 'operation_display', 'config',
            'input_source', 'output_preview', 'output_row_count', 'output_column_count',
            'sequence_order', 'status', 'error_message', 'execution_time_ms',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class TransformationPipelineSerializer(serializers.ModelSerializer):
    """Serializer for transformation pipeline"""
    steps = TransformationStepSerializer(many=True, read_only=True)
    step_count = serializers.SerializerMethodField()
    
    class Meta:
        model = TransformationPipeline
        fields = [
            'id', 'name', 'description', 'canvas_config', 'steps', 'step_count',
            'last_executed_at', 'execution_log', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_step_count(self, obj):
        return obj.steps.count()


class DataMiningProjectSerializer(serializers.ModelSerializer):
    """Serializer for Data Mining projects"""
    documents = DataMiningDocumentSerializer(many=True, read_only=True)
    pipeline = TransformationPipelineSerializer(read_only=True)
    created_by_name = serializers.CharField(source='created_by.email', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = DataMiningProject
        fields = [
            'id', 'name', 'description', 'wrench_project_number', 'wrench_project_name',
            'created_by', 'created_by_name', 'status', 'status_display',
            'master_file_path', 'master_file_format', 'total_documents',
            'total_rows_processed', 'execution_time_seconds', 'executed_at',
            'documents', 'pipeline', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        # Set created_by from request context
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
        return super().create(validated_data)


class DataMiningProjectCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for project creation"""
    
    class Meta:
        model = DataMiningProject
        fields = [
            'name', 'description', 'wrench_project_number', 'wrench_project_name',
            'master_file_format'
        ]
    
    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
        return super().create(validated_data)
