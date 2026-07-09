"""Spec Customization — DRF Serializers."""
from rest_framework import serializers

from .models import (
    PaperSpecDocument,
    PaperSpecExtractionJob,
    PipingClass,
    PipingClassComponent,
)


class PaperSpecDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = PaperSpecDocument
        fields = [
            'id', 'original_filename', 'file_url', 'file_size_bytes', 'total_pages',
            'sha256_hash', 'project_id', 'title', 'document_number',
            'uploaded_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except Exception:
            return None


class PaperSpecExtractionJobSerializer(serializers.ModelSerializer):
    document_filename = serializers.CharField(source='document.original_filename', read_only=True)
    document_pages = serializers.IntegerField(source='document.total_pages', read_only=True)

    class Meta:
        model = PaperSpecExtractionJob
        fields = [
            'id', 'document', 'document_filename', 'document_pages',
            'status', 'progress_percent', 'current_phase',
            'pages_processed', 'chunks_total', 'chunks_done',
            'celery_task_id', 'error_message',
            'created_at', 'started_at', 'completed_at',
        ]
        read_only_fields = fields


class PipingClassComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipingClassComponent
        fields = [
            'id', 'component_type', 'sub_type', 'size_from', 'size_to',
            'description', 'schedule_or_rating', 'material_standard',
            'end_connection', 'notes', 'display_order',
        ]


class PipingClassSerializer(serializers.ModelSerializer):
    components = PipingClassComponentSerializer(many=True, read_only=True)
    components_count = serializers.SerializerMethodField()

    class Meta:
        model = PipingClass
        fields = [
            'id', 'job', 'class_code', 'class_full_code',
            'material_grade', 'pressure_rating', 'flange_facing',
            'corrosion_allowance', 'service_list', 'pt_rating_table',
            'source_pages', 'confidence_score', 'raw_notes',
            'extraction_engine', 'components_count', 'components',
            'created_at',
        ]
        read_only_fields = fields

    def get_components_count(self, obj):
        return obj.components.count()


class PipingClassListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer (no nested components)."""
    components_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PipingClass
        fields = [
            'id', 'job', 'class_code', 'class_full_code',
            'material_grade', 'pressure_rating', 'flange_facing',
            'service_list', 'source_pages', 'confidence_score',
            'extraction_engine', 'components_count', 'created_at',
        ]
