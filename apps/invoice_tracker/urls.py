from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomerInvoiceViewSet, InvoiceAttachmentViewSet

app_name = 'invoice_tracker'

router = DefaultRouter()
router.register(r'invoices',    CustomerInvoiceViewSet,  basename='invoice-tracker-invoice')
router.register(r'attachments', InvoiceAttachmentViewSet, basename='invoice-tracker-attachment')

urlpatterns = [
    path('', include(router.urls)),
]
