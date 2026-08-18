from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.po_excel_import import canonical_po_number, import_po_workbook


HEADERS = [
    'PO Number ', 'PR Number ', 'PR Accepted Date ', 'Suppl.Name',
    'Summary of Purchase ', 'Project short name/  Code', 'Ord.Date', 'OA date',
    'Delivery Date', 'Payment terms', 'Amount Curr.', 'Curr.',
    'Amount including VAT', 'Amount Inc VAT in AED', 'Country', 'Remarks',
]


def workbook_upload(rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'PUR_GEN_2026'
    sheet.append(["PO REGISTER"])
    sheet.append([])
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    content = BytesIO()
    workbook.save(content)
    return SimpleUploadedFile('RAD-PO_GEN_2026.xlsx', content.getvalue())


class POExcelImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='po-importer')
        self.vendor = Vendor.objects.create(vendor_code='VEN-001', name='City Computer Company LLC')
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-GEN-PR-0012_2026', issued_by=self.user, requested_by=self.user,
        )

    def row(self, amount=6600):
        return [
            'RAD-GEN-PUR-0011_MAY2026', 'RAD-GEN-PR-0012_2026', '13.05.2026',
            'City Computer Company LLC', 'Annual software licence', 'RAD Internal',
            '14.05.2026', None, '20.05.2026 or earlier', '30 days net',
            amount, 'AED', amount * 1.05, amount * 1.05, 'UAE', 'Approved register',
        ]

    def test_normalizes_legacy_month_suffix(self):
        self.assertEqual(canonical_po_number('RAD-GEN-PUR-0011_MAY2026'), 'RAD-GEN-PUR-0011_2026')

    def test_import_links_explicit_pr_even_when_sequences_differ(self):
        result = import_po_workbook(workbook_upload([self.row()]), user=self.user, dry_run=False)
        po = PurchaseOrder.objects.get(po_number='RAD-GEN-PUR-0011_2026')
        self.assertEqual(result['created_count'], 1)
        self.assertTrue(result['database_verification']['verified'])
        self.assertEqual(result['database_verification']['verified_count'], 1)
        self.assertEqual(po.pr_reference_id, self.pr.id)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.po_number_reference, po.po_number)
        self.assertEqual(self.pr.status, 'converted')

    def test_existing_po_is_overwritten(self):
        PurchaseOrder.objects.create(
            po_number='RAD-GEN-PUR-0011_MAY2026', pr_reference=self.pr, vendor=self.vendor,
            title='Old value', category='other', total_amount=1, created_by=self.user,
        )
        result = import_po_workbook(workbook_upload([self.row(7000)]), user=self.user, dry_run=False)
        po = PurchaseOrder.objects.get(po_number='RAD-GEN-PUR-0011_2026')
        self.assertEqual(result['overwritten_count'], 1)
        self.assertTrue(result['database_verification']['verified'])
        self.assertEqual(str(po.total_amount), '7000.00')
        self.assertEqual(po.title, 'Annual software licence')
        self.assertFalse(PurchaseOrder.objects.filter(po_number='RAD-GEN-PUR-0011_MAY2026').exists())

    def test_preview_rejects_missing_pr_link(self):
        upload = workbook_upload([[*self.row()[:1], 'RAD-GEN-PR-0999_2026', *self.row()[2:]]])
        result = import_po_workbook(upload, user=self.user, dry_run=True)
        self.assertEqual(result['ready_rows'], 0)
        self.assertFalse(result['rows'][0]['pr_linked'])
