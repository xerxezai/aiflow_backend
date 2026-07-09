from django.contrib import admin

from .models import (
    IOListDocument, IOListExtractedComment, IOListExtractedRow,
)


@admin.register(IOListDocument)
class IOListDocumentAdmin(admin.ModelAdmin):
    list_display  = ('id', 'document_number', 'revision_label', 'status',
                     'uploaded_by', 'created_at')
    list_filter   = ('status', 'created_at')
    search_fields = ('document_number', 'project_name', 'pdf_sha256')
    readonly_fields = ('pdf_sha256', 'extraction_stats', 'created_at',
                       'updated_at')


@admin.register(IOListExtractedComment)
class IOListExtractedCommentAdmin(admin.ModelAdmin):
    list_display  = ('id', 'document', 's_no', 'status_code', 'page_number')
    search_fields = ('s_no', 'company_comment')


@admin.register(IOListExtractedRow)
class IOListExtractedRowAdmin(admin.ModelAdmin):
    list_display  = ('id', 'document', 'tag_number', 'page_number')
    search_fields = ('tag_number',)
