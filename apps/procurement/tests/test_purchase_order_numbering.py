from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.procurement.services.purchase_order_numbering import PurchaseOrderNumberService


class PurchaseOrderNumberServiceTests(SimpleTestCase):
    @patch('apps.procurement.services.purchase_order_numbering.PurchaseOrder.objects')
    @patch('apps.procurement.services.purchase_order_numbering.ProcurementNumberSequence.objects')
    def test_allocation_uses_locked_company_sequence(self, sequences, orders):
        locked = MagicMock()
        sequences.select_for_update.return_value = locked
        sequence = SimpleNamespace(last_value=4, save=MagicMock())
        locked.get_or_create.return_value = (sequence, False)
        orders.filter.return_value.values_list.return_value = [
            'RAD-PRJ-PUR-0003_2026',
            'RAD-PRJ-PUR-0007_2026',
        ]

        number = PurchaseOrderNumberService.next_number.__wrapped__(
            PurchaseOrderNumberService,
            'project',
            2026,
        )

        self.assertEqual(number, 'RAD-PRJ-PUR-0008_2026')
        sequences.select_for_update.assert_called_once_with()
        locked.get_or_create.assert_called_once_with(
            document_type='PO',
            prefix='PRJ',
            year=2026,
            defaults={'last_value': 0},
        )
        sequence.save.assert_called_once_with(update_fields=['last_value', 'updated_at'])

    def test_conversion_preserves_pr_scope_sequence_and_year(self):
        number = PurchaseOrderNumberService.from_requisition('RAD-GEN-PR-0042_2026')

        self.assertEqual(number, 'RAD-GEN-PUR-0042_2026')

    def test_conversion_rejects_nonstandard_pr_identifier(self):
        with self.assertRaisesMessage(ValueError, 'company numbering standard'):
            PurchaseOrderNumberService.from_requisition('PR-42')

    def test_verification_checks_pr_scope_and_year_but_allows_independent_sequence(self):
        verified, _ = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-0042_2026',
            'RAD-PRJ-PR-0042_2026',
        )
        independent_sequence, _ = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-0043_2026',
            'RAD-PRJ-PR-0042_2026',
        )

        mismatched, message = PurchaseOrderNumberService.verify(
            'RAD-GEN-PUR-0043_2026',
            'RAD-PRJ-PR-0042_2026',
        )

        self.assertTrue(verified)
        self.assertTrue(independent_sequence)
        self.assertFalse(mismatched)
        self.assertIn('same GEN/PRJ scope and year', message)
