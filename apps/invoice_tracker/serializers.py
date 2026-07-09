from rest_framework import serializers
from .models import CustomerInvoice, InvoiceAttachment


class InvoiceAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    uploaded_by_email = serializers.CharField(source='uploaded_by.email', read_only=True)

    class Meta:
        model = InvoiceAttachment
        fields = [
            'id', 'file', 'file_url', 'original_filename', 'content_type',
            'size_bytes', 'uploaded_at', 'uploaded_by_email',
        ]
        read_only_fields = ['id', 'file_url', 'uploaded_at', 'uploaded_by_email',
                            'content_type', 'size_bytes']

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except Exception:
            return None


class CustomerInvoiceSerializer(serializers.ModelSerializer):
    attachments = InvoiceAttachmentSerializer(many=True, read_only=True)
    attachments_count = serializers.IntegerField(source='attachments.count', read_only=True)
    payment_status_label = serializers.CharField(source='get_payment_status_display', read_only=True)
    category_label = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = CustomerInvoice
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by',
                            'days_overdue', 'attachments', 'attachments_count',
                            'payment_status_label', 'category_label']
