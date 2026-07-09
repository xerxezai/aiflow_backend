"""
Invoice Tracker DRF views.

Endpoints (registered under /api/v1/invoice-tracker/):
  GET    /invoices/                   list + filter + search + ordering + pagination
  POST   /invoices/                   create
  GET    /invoices/{id}/              retrieve
  PUT    /invoices/{id}/              full update
  PATCH  /invoices/{id}/              partial update
  DELETE /invoices/{id}/              delete
  GET    /invoices/stats/             aggregated counts/sums
  POST   /invoices/import-excel/      multipart upload — bulk upsert
  POST   /invoices/{id}/upload-attachment/   multipart — S3 attachment
  DELETE /attachments/{id}/           remove attachment
"""
import os
import tempfile

from django.db.models import Count, Sum, Q
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CustomerInvoice, InvoiceAttachment, PaymentStatus, InvoiceCategory
from .serializers import CustomerInvoiceSerializer, InvoiceAttachmentSerializer
from .services.excel_importer import import_workbook
from .services.finance_engine import FINANCE_RULES, recompute


# Soft-coded: aggregations exposed by /stats/
STATS_STATUS_KEYS = [s.value for s in PaymentStatus]


class CustomerInvoiceViewSet(viewsets.ModelViewSet):
    queryset = CustomerInvoice.objects.all().prefetch_related('attachments')
    serializer_class = CustomerInvoiceSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'invoice_number', 'account', 'company', 'rad_project_no',
        'project_name', 'project_id', 'customer_inv_reference',
        'bank_reference_code', 'pm', 'finance_pm_email',
    ]
    ordering_fields = ['invoice_date', 'due_date', 'grand_total',
                       'invoice_amount', 'payment_status', 'created_at']
    ordering = ['-invoice_date', '-id']

    # ── List filtering ─────────────────────────────────────────────
    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        category = params.get('category')
        if category:
            qs = qs.filter(category=category)

        payment_status = params.get('payment_status')
        if payment_status:
            qs = qs.filter(payment_status=payment_status)

        account = params.get('account')
        if account:
            qs = qs.filter(account__icontains=account)

        project = params.get('project')
        if project:
            qs = qs.filter(
                Q(rad_project_no__icontains=project) |
                Q(project_name__icontains=project) |
                Q(project_id__icontains=project)
            )

        currency = params.get('currency')
        if currency:
            qs = qs.filter(currency=currency)

        date_from = params.get('date_from')
        if date_from:
            qs = qs.filter(invoice_date__gte=date_from)
        date_to = params.get('date_to')
        if date_to:
            qs = qs.filter(invoice_date__lte=date_to)

        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        instance.recompute_overdue()
        instance.save(update_fields=['days_overdue'])

    def perform_update(self, serializer):
        instance = serializer.save()
        instance.recompute_overdue()
        instance.save(update_fields=['days_overdue'])

    # ── Aggregated stats ────────────────────────────────────────────
    @action(detail=False, methods=['get'])
    def stats(self, request):
        qs = self.get_queryset()
        # Per-status breakdown
        by_status = {key: 0 for key in STATS_STATUS_KEYS}
        for row in qs.values('payment_status').annotate(c=Count('id')):
            by_status[row['payment_status']] = row['c']

        # Per-currency totals
        by_currency = {
            row['currency']: float(row['total'] or 0)
            for row in qs.values('currency').annotate(total=Sum('grand_total'))
        }

        # Per-category counts
        by_category = {
            row['category']: row['c']
            for row in qs.values('category').annotate(c=Count('id'))
        }

        return Response({
            'total': qs.count(),
            'by_status':   by_status,
            'by_currency': by_currency,
            'by_category': by_category,
            'overdue_count': qs.filter(days_overdue__gt=0).count(),
            'total_aed': float(qs.aggregate(t=Sum('invoice_amount_aed'))['t'] or 0),
        })

    # ── Excel bulk import ──────────────────────────────────────────
    @action(detail=False, methods=['post'], url_path='import-excel')
    def import_excel(self, request):
        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': "No 'file' provided"},
                            status=status.HTTP_400_BAD_REQUEST)

        sheet_names_csv = request.data.get('sheets', '') or ''
        sheet_names = [s.strip() for s in sheet_names_csv.split(',') if s.strip()] or None

        # Save upload to a temp file (openpyxl reads from path)
        tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        try:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp.close()
            result = import_workbook(tmp.name, user=request.user,
                                     sheet_names=sheet_names)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        return Response(result.as_dict(), status=status.HTTP_200_OK)

    # ── Attachment upload (S3) ─────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='upload-attachment')
    def upload_attachment(self, request, pk=None):
        invoice = self.get_object()
        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': "No 'file' provided"},
                            status=status.HTTP_400_BAD_REQUEST)
        attachment = InvoiceAttachment.objects.create(
            invoice=invoice,
            file=upload,
            original_filename=upload.name[:255],
            content_type=getattr(upload, 'content_type', '')[:128],
            size_bytes=getattr(upload, 'size', None),
            uploaded_by=request.user,
        )
        return Response(InvoiceAttachmentSerializer(
            attachment, context={'request': request}).data,
            status=status.HTTP_201_CREATED)

    # ── Finance-engine config (read-only) ──────────────────────────
    @action(detail=False, methods=['get'], url_path='config',
            permission_classes=[IsAuthenticated])
    def config(self, request):
        """Return the soft-coded FINANCE_RULES so the frontend can render
        FX rates, VAT, ICV, and status labels without re-hard-coding them."""
        rules = FINANCE_RULES
        return Response({
            'vat_rate':                  float(rules['vat_rate']),
            'icv_retention_rate':        float(rules['icv_retention_rate']),
            'default_payment_terms_days': rules['default_payment_terms_days'],
            'paid_tolerance_aed':        float(rules['paid_tolerance_aed']),
            'fx_to_aed': {ccy: float(rate) for ccy, rate in rules['fx_to_aed'].items()},
            'status_auto_derive':        rules['status_auto_derive'],
            'status_labels':             rules['status'],
        })

    # ── Recompute a single invoice ─────────────────────────────────
    @action(detail=True, methods=['post'], url_path='recompute')
    def recompute(self, request, pk=None):
        """Re-apply every Excel-derived formula and persist the result.
        Useful after FX rates change, after a payment is logged, or when
        the user clicks the 'Recompute' button in the detail drawer."""
        invoice = self.get_object()
        changed = invoice.recompute_all()
        invoice.save(_skip_recompute=True)  # avoid double-recompute on save
        return Response({
            'changed_fields': sorted(changed),
            'invoice': CustomerInvoiceSerializer(invoice).data,
        })


class InvoiceAttachmentViewSet(viewsets.GenericViewSet):
    """Slim viewset — only DELETE is exposed for attachments."""
    queryset = InvoiceAttachment.objects.all()
    serializer_class = InvoiceAttachmentSerializer
    permission_classes = [IsAuthenticated]

    def destroy(self, request, pk=None):
        att = self.get_object()
        try:
            att.file.delete(save=False)
        except Exception:
            pass
        att.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
