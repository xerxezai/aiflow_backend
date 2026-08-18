from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.procurement.services.requisition_workflow import RequisitionWorkflowService


class FakeUser(SimpleNamespace):
    def get_full_name(self):
        return self.full_name


class FakeRequisition(SimpleNamespace):
    def save(self, *args, **kwargs):
        self.save_count = getattr(self, 'save_count', 0) + 1


class RequisitionWorkflowServiceTests(SimpleTestCase):
    def setUp(self):
        self.issuer = FakeUser(
            id='issuer',
            is_superuser=False,
            full_name='PR Issuer',
            username='issuer',
            email='issuer@example.com',
        )
        self.pm = FakeUser(
            id='pm-user',
            is_superuser=False,
            full_name='Project Manager',
            username='pm',
            email='pm@example.com',
        )
        self.engineering_manager = FakeUser(
            id='eng-user',
            is_superuser=False,
            full_name='Engineering Manager',
            username='eng',
            email='eng@example.com',
        )
        self.second_level_one = FakeUser(
            id='level-one-2',
            is_superuser=False,
            full_name='Second Level One Approver',
            username='levelone2',
            email='levelone2@example.com',
        )

    def _workflow(self):
        return [
            {'step': 1, 'role': 'Project Manager', 'user_id': self.pm.id, 'status': 'pending'},
            {
                'step': 2,
                'role': 'Engineering Manager',
                'user_id': self.engineering_manager.id,
                'status': 'pending',
            },
        ]

    def _pr(self, status='draft'):
        return FakeRequisition(
            status=status,
            issued_by_id=self.issuer.id,
            approval_workflow_config=self._workflow(),
            current_approval_step=0,
            items=[],
            total_price=None,
            rejection_reason='',
            approved_by=None,
            approved_at=None,
            pm_name=None,
            pm_signature='',
            pm_approval_status='pending',
            pm_approved_at=None,
            eng_manager_name=None,
            eng_manager_signature='',
            eng_manager_approval_status='pending',
            eng_manager_approved_at=None,
            manager_projects_name=None,
            manager_projects_signature='',
            manager_projects_approval_status='pending',
            manager_projects_approved_at=None,
            vp_op_name=None,
            vp_op_signature='',
            vp_op_approval_status='pending',
            vp_op_approved_at=None,
        )

    def test_submit_initializes_workflow(self):
        pr = self._pr()
        pr.approval_workflow_config[0].update({
            'status': 'approved',
            'approved_by_id': 'forged',
        })

        result = RequisitionWorkflowService._submit_locked(pr, self.issuer)

        self.assertIs(result, pr)
        self.assertEqual(pr.status, 'submitted')
        self.assertEqual(pr.current_approval_step, 0)
        self.assertEqual([stage['status'] for stage in pr.approval_workflow_config], ['pending', 'pending'])
        self.assertNotIn('approved_by_id', pr.approval_workflow_config[0])

    def test_only_issuer_can_submit(self):
        pr = self._pr()

        with self.assertRaises(PermissionDenied):
            RequisitionWorkflowService._submit_locked(pr, self.pm)

    def test_submission_requires_configured_approvers(self):
        pr = self._pr()
        pr.approval_workflow_config = []

        with self.assertRaisesMessage(ValidationError, 'configured approval workflow is required'):
            RequisitionWorkflowService._submit_locked(pr, self.issuer)

    def test_submission_rejects_line_item_total_mismatch(self):
        pr = self._pr()
        pr.items = [{
            'description': 'Engineering review',
            'quantity': 2,
            'unit_price': 100,
        }]
        pr.total_price = 250

        with self.assertRaises(ValidationError) as caught:
            RequisitionWorkflowService._submit_locked(pr, self.issuer)
        self.assertIn('sum of line items', str(caught.exception.detail['error']))

    def test_submission_retry_is_idempotent(self):
        pr = self._pr(status='submitted')

        result = RequisitionWorkflowService._submit_locked(pr, self.issuer)

        self.assertIs(result, pr)
        self.assertFalse(hasattr(pr, 'save_count'))

    def test_stages_advance_in_order_and_finish_as_approved(self):
        pr = self._pr(status='submitted')

        RequisitionWorkflowService._approve_locked(pr, self.pm, 'pm-signature', 'pm')

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 1)
        self.assertEqual(pr.approval_workflow_config[0]['status'], 'approved')
        self.assertEqual(pr.pm_name, self.pm)
        self.assertEqual(pr.pm_signature, 'pm-signature')
        self.assertEqual(pr.pm_approval_status, 'approved')

        RequisitionWorkflowService._approve_locked(
            pr,
            self.engineering_manager,
            'eng-signature',
            'eng_manager',
        )

        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.current_approval_step, 2)
        self.assertEqual(pr.approved_by, self.engineering_manager)
        self.assertEqual(pr.eng_manager_approval_status, 'approved')

    def test_later_stage_cannot_be_approved_early(self):
        pr = self._pr(status='submitted')

        with self.assertRaisesMessage(ValidationError, 'Project Manager must be completed next'):
            RequisitionWorkflowService._approve_locked(
                pr,
                self.engineering_manager,
                expected_stage_key='eng_manager',
            )

    def test_all_level_one_approvers_can_act_in_any_order_and_must_finish(self):
        pr = self._pr(status='submitted')
        pr.approval_workflow_config = [
            {
                'step': 1, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.pm.id, 'status': 'pending',
            },
            {
                'step': 2, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.second_level_one.id, 'status': 'pending',
            },
            {
                'step': 3, 'level': 2, 'role': 'Engineering Manager',
                'user_id': self.engineering_manager.id, 'status': 'pending',
            },
        ]

        RequisitionWorkflowService._approve_locked(
            pr, self.second_level_one, 'second-signature', 'pm'
        )

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 0)
        self.assertEqual(pr.approval_workflow_config[0]['status'], 'pending')
        self.assertEqual(pr.approval_workflow_config[1]['status'], 'approved')

        with self.assertRaises(ValidationError):
            RequisitionWorkflowService._approve_locked(
                pr, self.engineering_manager, expected_stage_key='eng_manager'
            )

        RequisitionWorkflowService._approve_locked(pr, self.pm, 'first-signature', 'pm')

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 2)

    def test_level_one_rejection_by_any_assigned_member_terminates_workflow(self):
        pr = self._pr(status='submitted')
        pr.approval_workflow_config = [
            {
                'step': 1, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.pm.id, 'status': 'pending',
            },
            {
                'step': 2, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.second_level_one.id, 'status': 'pending',
            },
        ]

        RequisitionWorkflowService._reject_locked(
            pr,
            self.second_level_one,
            'The requisition needs updated technical details.',
            'pm',
        )

        self.assertEqual(pr.status, 'rejected')
        self.assertEqual(pr.approval_workflow_config[1]['status'], 'rejected')

    def test_rejection_records_stage_audit_and_terminates_workflow(self):
        pr = self._pr(status='submitted')

        RequisitionWorkflowService._reject_locked(
            pr,
            self.pm,
            'The technical specification is incomplete.',
            'pm',
        )

        stage = pr.approval_workflow_config[0]
        self.assertEqual(pr.status, 'rejected')
        self.assertEqual(pr.pm_approval_status, 'not_approved')
        self.assertEqual(stage['status'], 'rejected')
        self.assertEqual(stage['rejected_by_id'], self.pm.id)
        self.assertEqual(stage['rejection_reason'], pr.rejection_reason)

    def test_completed_requisition_cannot_be_approved_again(self):
        pr = self._pr(status='approved')

        with self.assertRaisesMessage(ValidationError, 'not awaiting approval'):
            RequisitionWorkflowService._approve_locked(pr, self.pm)
