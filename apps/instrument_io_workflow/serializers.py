from rest_framework import serializers

from .models import (
    IOListDocument, IOListExtractedComment, IOListExtractedRow,
)


class IOListExtractedCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = IOListExtractedComment
        fields = [
            'id', 's_no', 'company_comment', 'contractor_reply',
            'company_decision', 'status_code', 'status_meaning',
            'page_number', 'linked_tags',
        ]


class IOListExtractedRowSerializer(serializers.ModelSerializer):
    class Meta:
        model  = IOListExtractedRow
        fields = ['id', 'tag_number', 'page_number', 'data']


class IOListDocumentListSerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.SerializerMethodField()

    class Meta:
        model  = IOListDocument
        fields = [
            'id', 'project_name', 'document_number', 'revision_label',
            'plant', 'unit', 'status', 'extraction_stats',
            'crs_chain_id', 'uploaded_by_email',
            'created_at', 'updated_at',
        ]

    def get_uploaded_by_email(self, obj):
        return obj.uploaded_by.email if obj.uploaded_by else None


class IOListDocumentDetailSerializer(IOListDocumentListSerializer):
    extracted_comments = IOListExtractedCommentSerializer(many=True, read_only=True)
    extracted_rows     = IOListExtractedRowSerializer(many=True, read_only=True)
    pdf_url            = serializers.SerializerMethodField()

    class Meta(IOListDocumentListSerializer.Meta):
        fields = IOListDocumentListSerializer.Meta.fields + [
            'pdf_url', 'extraction_error',
            'extracted_comments', 'extracted_rows',
        ]

    def get_pdf_url(self, obj):
        try:
            return obj.pdf_file.url
        except Exception:
            return None
