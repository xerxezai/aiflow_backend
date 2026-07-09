from rest_framework import serializers

from .models import (
    ChangeEvent,
    CostSnapshot,
    Estimate,
    EstimateLineItem,
    ProjectDocument,
    WBSNode,
)


class EstimateLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = EstimateLineItem
        fields = [
            'id', 'estimate', 'wbs_code', 'description', 'discipline', 'category',
            'unit', 'quantity', 'unit_rate', 'line_total', 'sort_order', 'source_row',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at')


class EstimateSerializer(serializers.ModelSerializer):
    line_items = EstimateLineItemSerializer(many=True, read_only=True)
    line_item_count = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_display = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model = Estimate
        fields = [
            'id', 'project', 'version', 'kind', 'kind_display',
            'source', 'source_display', 'status', 'status_display',
            'title', 'currency', 'total_amount', 'snapshot_date', 'notes',
            'source_document', 'created_by',
            'line_items', 'line_item_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at', 'created_by', 'line_items', 'line_item_count')

    def get_line_item_count(self, obj):
        return obj.line_items.filter(is_deleted=False).count()


class EstimateListSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_item_count = serializers.SerializerMethodField()

    class Meta:
        model = Estimate
        fields = [
            'id', 'project', 'version', 'kind', 'kind_display',
            'status', 'status_display', 'source',
            'title', 'currency', 'total_amount', 'snapshot_date',
            'line_item_count', 'created_at', 'updated_at',
        ]

    def get_line_item_count(self, obj):
        return obj.line_items.filter(is_deleted=False).count()


class WBSNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WBSNode
        fields = ['id', 'project', 'parent', 'code', 'name', 'level', 'sort_order',
                  'created_at', 'updated_at']
        read_only_fields = ('created_at', 'updated_at')


class ProjectDocumentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=True)
    file_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    parse_status_display = serializers.CharField(source='get_parse_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProjectDocument
        fields = [
            'id', 'project', 'kind', 'kind_display', 'title',
            'file', 'file_url', 'download_url',
            'original_filename', 'content_type', 'size_bytes',
            'parse_status', 'parse_status_display', 'parsed_data', 'parse_error',
            'uploaded_by', 'uploaded_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = (
            'created_at', 'updated_at', 'uploaded_by', 'uploaded_by_name',
            'original_filename', 'content_type', 'size_bytes',
            'parse_status', 'parsed_data', 'parse_error',
            'file_url', 'download_url',
        )

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except Exception:
            return None

    def get_download_url(self, obj):
        # Direct presigned URL goes via the dedicated `presign-download` action.
        # This field surfaces the storage URL (already presigned for S3 backends).
        return self.get_file_url(obj)

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by_id:
            return None
        u = obj.uploaded_by
        return u.get_full_name() or getattr(u, 'email', None) or getattr(u, 'username', None)


class CostSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostSnapshot
        fields = ['id', 'project', 'period_end', 'planned_value', 'earned_value', 'actual_cost',
                  'cpi', 'spi', 'eac', 'source', 'notes', 'created_at', 'updated_at']
        read_only_fields = ('created_at', 'updated_at')


class ChangeEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangeEvent
        fields = [
            'id', 'project', 'source_document', 'detected_at',
            'summary', 'description', 'severity',
            'delta_amount', 'delta_currency',
            'status', 'ai_confidence', 'reviewed_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at', 'detected_at', 'ai_confidence')
