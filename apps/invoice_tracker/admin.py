from django.contrib import admin
from .models import CustomerInvoice, InvoiceAttachment


class InvoiceAttachmentInline(admin.TabularInline):
    model = InvoiceAttachment
    extra = 0
    readonly_fields = ('uploaded_at', 'uploaded_by', 'original_filename',
                       'content_type', 'size_bytes')


@admin.register(CustomerInvoice)
class CustomerInvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'category', 'account', 'rad_project_no',
                    'invoice_date', 'currency', 'grand_total', 'payment_status',
                    'days_overdue')
    list_filter = ('category', 'payment_status', 'currency', 'icv_applicable')
    search_fields = ('invoice_number', 'account', 'company', 'rad_project_no',
                     'project_name', 'customer_inv_reference', 'bank_reference_code')
    date_hierarchy = 'invoice_date'
    inlines = [InvoiceAttachmentInline]
    readonly_fields = ('created_at', 'updated_at', 'days_overdue')


@admin.register(InvoiceAttachment)
class InvoiceAttachmentAdmin(admin.ModelAdmin):
    list_display = ('original_filename', 'invoice', 'size_bytes', 'uploaded_at')
    search_fields = ('original_filename', 'invoice__invoice_number')
    readonly_fields = ('uploaded_at',)
