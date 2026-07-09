"""
Invoice Tracker models — Accounts Receivable.

Mirrors the finance team's Excel "Customer Inv masterfile" and "Inv summary
report" schemas. Distinct from apps.finance.Invoice (A/P, AI extraction).

Storage:
  - Structured data: PostgreSQL (CustomerInvoice)
  - Original PDFs: AWS S3 via Django storages (InvoiceAttachment.file)
"""
from django.db import models
from django.conf import settings
from django.utils import timezone


class InvoiceCategory(models.TextChoices):
    """Discriminator: which sheet did this row originate from?"""
    EXTERNAL = 'external', 'External (Customer)'
    INTERNAL = 'internal', 'Internal (Rejlers Group)'


class PaymentStatus(models.TextChoices):
    """Mirrors values seen in the Excel 'Payment Status' column."""
    PENDING       = 'pending',       'Pending'
    PAID          = 'paid',          'Paid'
    PARTIAL       = 'partial',       'Partially Paid'
    OVERDUE       = 'overdue',       'Overdue'
    CANCELLED     = 'cancelled',     'Cancelled'
    CREDIT_NOTE   = 'credit_note',   'Credit Note'


class Currency(models.TextChoices):
    AED = 'AED', 'AED'
    USD = 'USD', 'USD'
    EUR = 'EUR', 'EUR'
    GBP = 'GBP', 'GBP'
    SGD = 'SGD', 'SGD'


def attachment_upload_path(instance, filename):
    """S3 key layout: invoice_tracker/<category>/<year>/<invoice_no>/<filename>."""
    year = (instance.invoice.invoice_date or timezone.now().date()).year
    return (
        f"invoice_tracker/"
        f"{instance.invoice.category}/"
        f"{year}/"
        f"{instance.invoice.invoice_number}/"
        f"{filename}"
    )


class CustomerInvoice(models.Model):
    """Unified A/R invoice row. Sheet origin recorded in `category`."""

    # ── Identification ──────────────────────────────────────────────
    invoice_number = models.CharField(max_length=64, unique=True, db_index=True)
    category = models.CharField(
        max_length=16,
        choices=InvoiceCategory.choices,
        default=InvoiceCategory.EXTERNAL,
        db_index=True,
    )
    credit_note_ref = models.CharField(max_length=128, blank=True, default='')

    # ── Customer / Project ──────────────────────────────────────────
    account = models.CharField(max_length=256, blank=True, default='', db_index=True)
    company = models.CharField(max_length=256, blank=True, default='')
    rad_project_no = models.CharField(max_length=64, blank=True, default='', db_index=True)
    project_name = models.TextField(blank=True, default='')
    project_id = models.CharField(max_length=64, blank=True, default='')

    # ── Dates ───────────────────────────────────────────────────────
    invoice_date = models.DateField(null=True, blank=True, db_index=True)
    invoice_sent_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    payment_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=64, blank=True, default='')

    # ── Money ───────────────────────────────────────────────────────
    currency = models.CharField(max_length=4, choices=Currency.choices, default=Currency.AED)
    ppc_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    retention = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    icv_applicable = models.BooleanField(default=False)
    invoice_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    invoice_amount_aed = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_excl_vat = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    grand_total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    balance_to_be_received = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    actual_payment_received = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    paid_amount_excl_vat = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    # ── Status ──────────────────────────────────────────────────────
    payment_status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    days_overdue = models.IntegerField(null=True, blank=True)

    # ── References & notes ──────────────────────────────────────────
    bank_reference_code = models.CharField(max_length=64, blank=True, default='')
    customer_inv_reference = models.CharField(max_length=128, blank=True, default='')
    contract_clause = models.CharField(max_length=256, blank=True, default='')
    finance_pm_email = models.CharField(max_length=256, blank=True, default='')
    pm = models.CharField(max_length=128, blank=True, default='')
    details = models.TextField(blank=True, default='')
    remarks = models.TextField(blank=True, default='')

    # Internal-only fields (from Customer Inv masterfile internal sheets)
    sent_by = models.CharField(max_length=128, blank=True, default='')
    sent_to_account = models.CharField(max_length=128, blank=True, default='')

    # ── Audit ───────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['-invoice_date', '-id']
        indexes = [
            models.Index(fields=['account', 'payment_status']),
            models.Index(fields=['rad_project_no', 'invoice_date']),
        ]

    def __str__(self):
        return f"{self.invoice_number} · {self.account or '—'}"

    # ── Derived fields ──────────────────────────────────────────────
    def recompute_overdue(self):
        """Recalculate days_overdue from due_date + payment_status."""
        if self.payment_status == PaymentStatus.PAID or not self.due_date:
            self.days_overdue = None
            return
        delta = (timezone.now().date() - self.due_date).days
        self.days_overdue = max(delta, 0) if delta > 0 else None

    def recompute_all(self, *, today=None) -> set[str]:
        """Apply every Excel-derived formula (PPC, retention, FX, due-date,
        balance, status). Returns the set of fields whose value changed.

        Auto-runs on save() unless caller passes _skip_recompute=True.
        """
        from .services.finance_engine import recompute
        return recompute(self, today=today)

    def save(self, *args, **kwargs):
        skip = kwargs.pop('_skip_recompute', False)
        if not skip:
            self.recompute_all()
        return super().save(*args, **kwargs)


class InvoiceAttachment(models.Model):
    """PDF / supporting document stored on AWS S3."""

    invoice = models.ForeignKey(
        CustomerInvoice,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to=attachment_upload_path, max_length=500)
    original_filename = models.CharField(max_length=255, blank=True, default='')
    content_type = models.CharField(max_length=128, blank=True, default='')
    size_bytes = models.BigIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.original_filename or self.file.name} → {self.invoice.invoice_number}"
