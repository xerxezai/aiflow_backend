from django.apps import AppConfig


class InvoiceTrackerConfig(AppConfig):
    """Invoice Tracker — Accounts Receivable register.

    Separate from apps.finance (which handles Accounts Payable invoice
    upload + AI extraction). This app stores invoices SENT to customers,
    mirroring the finance team's existing Excel master file.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.invoice_tracker'
    verbose_name = 'Invoice Tracker (A/R)'
