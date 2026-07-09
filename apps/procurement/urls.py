from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    VendorViewSet,
    PurchaseRequisitionViewSet,
    PurchaseOrderViewSet,
    ReceiptViewSet,
    PODocumentViewSet,
    # Master database viewsets
    ProjectViewSet,
    BudgetViewSet,
    CostCenterViewSet,
    get_categories
)

router = DefaultRouter()
router.register(r'vendors', VendorViewSet, basename='vendor')
router.register(r'requisitions', PurchaseRequisitionViewSet, basename='requisition')
router.register(r'orders', PurchaseOrderViewSet, basename='order')
router.register(r'receipts', ReceiptViewSet, basename='receipt')
router.register(r'po-documents', PODocumentViewSet, basename='po-document')

# Master Database Routes - Professional Project-Based Procurement
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'budgets', BudgetViewSet, basename='budget')
router.register(r'cost-centers', CostCenterViewSet, basename='cost-center')

urlpatterns = [
    path('categories/', get_categories, name='categories'),
    path('', include(router.urls)),
]
