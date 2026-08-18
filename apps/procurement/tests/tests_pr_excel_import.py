from io import BytesIO
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.pr_excel_import import (
    PRExcelImportError,
    _match_projects,
    _match_vendor,
    import_pr_workbook,
    parse_pr_workbook,
)


HEADERS = [
    'SN',
    'PR Number ',
    'PR Accepted Date ',
    'PO Number ',
    'Suppl.Name',
    'Summary of Purchase /Activity',
    'Project short name/  Code',
    'Delivery/ Completion Date',
    'Payment terms',
    'PO Amount w/o VAT',
    'PO Currency',
    'Budget in AED',
    'Country (of Vendor/SC)',
    'PO Status',
    'ICV',
    'Remarks',
]


def workbook_upload(rows, name='PR Module.xlsx'):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'PUR_PRJ_2026'
    sheet.append(["PROCUREMENT REGISTER"])
    sheet.append([])
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    content = BytesIO()
    workbook.save(content)
    return SimpleUploadedFile(
        name,
        content.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


class PRExcelParserTests(SimpleTestCase):
    def test_maps_register_columns_to_requisition_fields(self):
        upload = workbook_upload([[
            1,
            'RAD-PRJ-PR-0001_2026 ',
            '17-08-2026',
            'RAD-PRJ-PUR-0001_AUG2026',
            'Example Supplier',
            'Inspection services',
            '5901055',
            '30-08-2026',
            '30 days net',
            1250,
            'AED',
            1500,
            'UAE',
            'Ongoing',
            0.42,
            'Imported register note',
        ]])

        rows, errors = parse_pr_workbook(upload)

        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        parsed = rows[0]
        self.assertEqual(parsed.pr_number, 'RAD-PRJ-PR-0001_2026')
        self.assertEqual(parsed.values['supplier_name'], 'Example Supplier')
        self.assertEqual(parsed.values['product_service'], 'Inspection services')
        self.assertEqual(parsed.values['project'], '5901055')
        self.assertEqual(str(parsed.values['total_price']), '1250.00')
        self.assertEqual(parsed.values['currency'], 'AED')
        self.assertTrue(parsed.values['po_applicable'])
        self.assertEqual(parsed.values['status'], 'draft')
        self.assertTrue(parsed.values['price_remarks_data']['source_authoritative'])
        self.assertEqual(parsed.values['price_remarks_data']['source_authority'], 'Procurement Department')
        register = parsed.values['price_remarks_data']['procurement_register']
        self.assertEqual(len(register), 24)
        self.assertEqual(register['SN'], 1)
        self.assertEqual(register['PR Number'], 'RAD-PRJ-PR-0001_2026')
        self.assertEqual(register['Ord.Date'], '')
        self.assertEqual(register['PO Amount w/o VAT'], 1250)
        self.assertIn('% Negotiated', register)

    def test_hold_date_is_reported_as_warning(self):
        upload = workbook_upload([[
            1, 'RAD-PRJ-PR-0002_2026', 'HOLD', '', '', 'Pending scope', '',
            '', '', '', 'AED', '', '', '', '', '',
        ]])

        rows, _ = parse_pr_workbook(upload)

        self.assertIsNone(rows[0].values['issued_date'])
        self.assertTrue(any('HOLD' in warning for warning in rows[0].warnings))

    def test_rejects_non_excel_extension(self):
        upload = SimpleUploadedFile('register.csv', b'not excel')

        with self.assertRaisesMessage(PRExcelImportError, 'Only .xlsx'):
            parse_pr_workbook(upload)

    def test_matches_procurement_supplier_suffix_to_company_vendor(self):
        vendors = [
            SimpleNamespace(id='vendor-1', vendor_code='VEN-001', name='Noveltech Surveys'),
            SimpleNamespace(id='vendor-2', vendor_code='VEN-002', name='Another Supplier'),
        ]

        match = _match_vendor('Noveltech Surveys_Anewa', vendors)

        self.assertTrue(match['matched'])
        self.assertEqual(match['vendor_code'], 'VEN-001')
        self.assertEqual(match['method'], 'company-name prefix')

    def test_matches_project_code_to_company_project_master(self):
        matches = _match_projects(
            [{'project_name': '5901055'}],
            [{
                'id': 'project-1',
                'project_number': '5901055',
                'project_name': 'Company Project',
                'database': 'company_project_master',
            }],
        )

        self.assertTrue(matches[0]['matched'])
        self.assertEqual(matches[0]['database'], 'company_project_master')


class PRExcelImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='pr-importer',
            email='pr-importer@example.com',
            password='test-password',
        )

    def test_preview_does_not_create_records(self):
        upload = workbook_upload([[
            1, 'RAD-PRJ-PR-0100_2026', '17-08-2026', '', 'Supplier',
            'Testing service', '5901055', '', '', 100, 'AED', 120, '', '', '', '',
        ]])

        result = import_pr_workbook(upload, user=self.user, dry_run=True)

        self.assertEqual(result['ready_rows'], 1)
        self.assertEqual(result['created_count'], 0)
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_import_creates_draft_and_skips_existing_pr_number(self):
        PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0100_2026',
            issued_by=self.user,
            product_service='Existing',
        )
        upload = workbook_upload([
            [1, 'RAD-PRJ-PR-0100_2026', '17-08-2026', '', 'Supplier', 'Duplicate', '5901055', '', '', 100, 'AED', 120, '', '', '', ''],
            [2, 'RAD-PRJ-PR-0101_2026', '17-08-2026', 'PO-101', 'Supplier', 'New service', '5901055', '', '30 days', 200, 'USD', 900, 'UAE', 'Ongoing', 'NA', 'Note'],
        ])

        result = import_pr_workbook(upload, user=self.user, dry_run=False)

        self.assertEqual(result['created_count'], 1)
        self.assertEqual(result['skipped_count'], 1)
        imported = PurchaseRequisition.objects.get(pr_number='RAD-PRJ-PR-0101_2026')
        self.assertEqual(imported.status, 'draft')
        self.assertEqual(imported.issued_by, self.user)
        self.assertEqual(imported.po_number_reference, 'PO-101')
        self.assertEqual(imported.price_remarks_data['payment_terms'], '30 days')
