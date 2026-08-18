"""Transactional state transitions for Purchase Requisitions."""

from decimal import Decimal
import re
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import PurchaseRequisition
from .requisition_status import canonicalize_pr_status
from .requisition_validation import line_items_total, normalize_line_items


class RequisitionWorkflowService:
    """Single source of truth for PR submission, approval, and rejection."""

    ACTIVE_REVIEW_STATUSES = {'submitted', 'in_review'}

    STAGE_CONFIG = {
        'pm': {
            'labels': ('level 1 approver', 'project manager', 'department manager', 'technical review'),
            'name_field': 'pm_name',
            'signature_field': 'pm_signature',
            'status_field': 'pm_approval_status',
            'timestamp_field': 'pm_approved_at',
        },
        'eng_manager': {
            'labels': ('engineering manager', 'engineering review'),
            'name_field': 'eng_manager_name',
            'signature_field': 'eng_manager_signature',
            'status_field': 'eng_manager_approval_status',
            'timestamp_field': 'eng_manager_approved_at',
        },
        'manager_projects': {
            'labels': ('manager of projects', 'projects manager'),
            'name_field': 'manager_projects_name',
            'signature_field': 'manager_projects_signature',
            'status_field': 'manager_projects_approval_status',
            'timestamp_field': 'manager_projects_approved_at',
        },
        'vp': {
            'labels': ('vp operations', 'vice president', 'procurement manager'),
            'name_field': 'vp_op_name',
            'signature_field': 'vp_op_signature',
            'status_field': 'vp_op_approval_status',
            'timestamp_field': 'vp_op_approved_at',
        },
    }

    @classmethod
    def _is_super_admin(cls, user):
        if getattr(user, 'is_superuser', False):
            return True

        try:
            return user.rbac_profile.roles.filter(
                code='super_admin',
                is_active=True,
            ).exists()
        except (AttributeError, ObjectDoesNotExist):
            return False

    @classmethod
    def _workflow(cls, pr):
        workflow = pr.approval_workflow_config
        if not isinstance(workflow, list) or not workflow:
            raise ValidationError({'error': 'A configured approval workflow is required.'})
        if any(not isinstance(stage, dict) for stage in workflow):
            raise ValidationError({'error': 'The approval workflow contains an invalid stage.'})
        return workflow

    @classmethod
    def _stage_key(cls, stage):
        role_text = f"{stage.get('role', '')} {stage.get('stage', '')}".strip().lower()
        for stage_key, config in cls.STAGE_CONFIG.items():
            if any(label in role_text for label in config['labels']):
                return stage_key
        return None

    @classmethod
    def _current_stage(cls, pr, workflow):
        for index, stage in enumerate(workflow):
            stage_status = str(stage.get('status', 'pending')).lower()
            if stage_status in ('pending', 'in_review'):
                pr.current_approval_step = index
                return index, stage
        raise ValidationError({'error': 'No active approval stage awaiting action.'})

    @classmethod
    def _stage_level(cls, stage, index):
        try:
            explicit_level = stage.get('level')
            if explicit_level not in (None, ''):
                return max(1, int(explicit_level))
            # Compatibility for records created while the API was stripping
            # the explicit level field (for example "Level 1 - Approver 2").
            label = f"{stage.get('stage', '')} {stage.get('role', '')}"
            match = re.search(r'\blevel\s*(\d+)\b', label, re.IGNORECASE)
            if match:
                return max(1, int(match.group(1)))
            return index + 1
        except (TypeError, ValueError):
            return index + 1

    @classmethod
    def _active_level_stages(cls, pr, workflow):
        pending = [
            (index, stage)
            for index, stage in enumerate(workflow)
            if str(stage.get('status', 'pending')).lower() in ('pending', 'in_review')
        ]
        if not pending:
            raise ValidationError({'error': 'No active approval stage awaiting action.'})
        active_level = min(cls._stage_level(stage, index) for index, stage in pending)
        active = [
            (index, stage) for index, stage in pending
            if cls._stage_level(stage, index) == active_level
        ]
        pr.current_approval_step = active[0][0]
        return active_level, active

    @classmethod
    def _actor_stage(cls, active_stages, actor, expected_stage_key=None):
        candidates = [
            entry for entry in active_stages
            if not expected_stage_key or cls._stage_key(entry[1]) == expected_stage_key
        ]
        if cls._is_super_admin(actor):
            if candidates:
                return candidates[0]
        else:
            for entry in candidates:
                assigned_id = entry[1].get('user_id') or entry[1].get('approver_id')
                if str(assigned_id) == str(actor.id):
                    return entry

        stage_name = active_stages[0][1].get('stage') or active_stages[0][1].get('role') or 'current approval level'
        if expected_stage_key and not candidates:
            raise ValidationError({'error': f'{stage_name} must be completed next.'})
        raise PermissionDenied(f'Only an assigned approver may act on {stage_name}.')

    @classmethod
    def _notify_level(cls, pr, workflow, level):
        if not getattr(pr, 'pk', None):
            return
        recipient_ids = {
            stage.get('user_id') or stage.get('approver_id')
            for index, stage in enumerate(workflow)
            if cls._stage_level(stage, index) == level
        }
        recipient_ids.discard(None)
        pr_id = pr.pk
        pr_number = pr.pr_number

        def send_notifications():
            from apps.notifications.models import Notification
            from apps.notifications.services import NotificationService
            already_notified_ids = set(
                Notification.objects.filter(
                    recipient_id__in=recipient_ids,
                    metadata__pr_id=str(pr_id),
                    metadata__approval_level=level,
                ).values_list('recipient_id', flat=True)
            )
            users = get_user_model().objects.filter(
                pk__in=recipient_ids, is_active=True,
            ).exclude(pk__in=already_notified_ids)
            for recipient in users:
                NotificationService.create_notification(
                    recipient=recipient,
                    title=f'PR {pr_number} requires your approval',
                    message=f'You have been assigned as a Level {level} approver for Purchase Requisition {pr_number}.',
                    category='APPROVAL',
                    priority='HIGH',
                    action_url=f'/procurement/requisitions/{pr_id}',
                    action_label='Review requisition',
                    metadata={'pr_id': str(pr_id), 'pr_number': pr_number, 'approval_level': level},
                )

        transaction.on_commit(send_notifications)

    @classmethod
    def _enforce_assigned_approver(cls, stage, actor):
        if cls._is_super_admin(actor):
            return

        assigned_user_id = stage.get('user_id') or stage.get('approver_id')
        stage_name = stage.get('stage') or stage.get('role') or 'current approval stage'

        if not assigned_user_id:
            raise PermissionDenied(f'No approver is assigned to {stage_name}.')
        if str(assigned_user_id) != str(actor.id):
            raise PermissionDenied(f'Only the assigned approver may act on {stage_name}.')

    @classmethod
    def _enforce_expected_stage(cls, stage, expected_stage_key):
        if expected_stage_key and cls._stage_key(stage) != expected_stage_key:
            stage_name = stage.get('stage') or stage.get('role') or 'the current stage'
            raise ValidationError({'error': f'{stage_name} must be completed next.'})

    @classmethod
    def _mirror_fixed_approval(cls, pr, stage, actor, signature, approved_at):
        stage_key = cls._stage_key(stage)
        if not stage_key:
            return

        config = cls.STAGE_CONFIG[stage_key]
        setattr(pr, config['name_field'], actor)
        setattr(pr, config['signature_field'], signature)
        setattr(pr, config['status_field'], 'approved')
        setattr(pr, config['timestamp_field'], approved_at)

    @classmethod
    def _mirror_fixed_rejection(cls, pr, stage):
        stage_key = cls._stage_key(stage)
        if stage_key:
            setattr(pr, cls.STAGE_CONFIG[stage_key]['status_field'], 'not_approved')

    @classmethod
    @transaction.atomic
    def submit(cls, pr_id, actor):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._submit_locked(pr, actor)

    @classmethod
    def _submit_locked(cls, pr, actor):
        if str(pr.issued_by_id) != str(actor.id) and not cls._is_super_admin(actor):
            raise PermissionDenied('Only the requisition issuer may submit this draft.')
        
        current_status = canonicalize_pr_status(pr.status)
        if current_status in cls.ACTIVE_REVIEW_STATUSES:
            return pr
        if current_status != 'draft':
            raise ValidationError({'error': 'Only draft requisitions can be submitted.'})

        normalized_items = normalize_line_items(pr.items)
        if normalized_items:
            calculated_total = Decimal(str(line_items_total(normalized_items) or 0)).quantize(Decimal('0.01'))
            pr_total = Decimal(str(pr.total_price or 0)).quantize(Decimal('0.01'))
            
            if pr_total != calculated_total:
                raise ValidationError({
                    'error': f'Total price ({pr_total}) must equal the sum of line items ({calculated_total}) before submission.'
                })
            pr.items = normalized_items

        workflow = cls._workflow(pr)

        # Pass 1: Validate all stages before mutating memory
        assigned_ids = []
        for index, stage in enumerate(workflow):
            if not (stage.get('user_id') or stage.get('approver_id')):
                raise ValidationError({'error': f'Approval stage {index + 1} has no assigned approver.'})
            assigned_ids.append(str(stage.get('user_id') or stage.get('approver_id')))
        if len(assigned_ids) != len(set(assigned_ids)):
            raise ValidationError({'error': 'Each employee may only be assigned once in an approval workflow.'})

        # Pass 2: Clean and initialize
        for index, stage in enumerate(workflow):
            stage['step'] = index + 1
            stage['status'] = 'pending'
            stage['approved_at'] = None
            stage.pop('approved_by_id', None)
            stage.pop('approved_by_name', None)
            stage.pop('rejected_at', None)
            stage.pop('rejected_by_id', None)
            stage.pop('rejected_by_name', None)
            stage.pop('rejection_reason', None)

        pr.approval_workflow_config = workflow
        pr.current_approval_step = 0
        pr.status = 'submitted'
        pr.rejection_reason = ''
        pr.save()
        first_level = min(cls._stage_level(stage, index) for index, stage in enumerate(workflow))
        cls._notify_level(pr, workflow, first_level)
        return pr

    @classmethod
    @transaction.atomic
    def approve(cls, pr_id, actor, signature='', expected_stage_key=None):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._approve_locked(pr, actor, signature, expected_stage_key)

    @classmethod
    def _approve_locked(cls, pr, actor, signature='', expected_stage_key=None):
        if canonicalize_pr_status(pr.status) not in cls.ACTIVE_REVIEW_STATUSES:
            raise ValidationError({'error': 'This requisition is not awaiting approval.'})
        if len(signature or '') > 500:
            raise ValidationError({'error': 'Signature cannot exceed 500 characters.'})

        workflow = cls._workflow(pr)
        active_level, active_stages = cls._active_level_stages(pr, workflow)
        current_index, stage = cls._actor_stage(active_stages, actor, expected_stage_key)

        approved_at = timezone.now()
        actor_name = actor.get_full_name() or getattr(actor, 'username', '') or getattr(actor, 'email', '')
        stage['status'] = 'approved'
        stage['approved_at'] = approved_at.isoformat()
        stage['approved_by_id'] = str(actor.id)
        stage['approved_by_name'] = actor_name
        cls._mirror_fixed_approval(pr, stage, actor, signature or '', approved_at)

        remaining_current_level = [
            (index, candidate) for index, candidate in enumerate(workflow)
            if cls._stage_level(candidate, index) == active_level
            and str(candidate.get('status', 'pending')).lower() in ('pending', 'in_review')
        ]
        next_pending = [
            (index, candidate) for index, candidate in enumerate(workflow)
            if str(candidate.get('status', 'pending')).lower() in ('pending', 'in_review')
        ]

        if not next_pending:
            pr.current_approval_step = len(workflow)
            pr.status = 'approved'
            pr.approved_by = actor
            pr.approved_at = approved_at
        else:
            next_index, next_stage = (remaining_current_level or next_pending)[0]
            pr.current_approval_step = next_index
            pr.status = 'in_review'
            workflow[next_index]['status'] = 'pending'
            next_level = cls._stage_level(next_stage, next_index)
            if not remaining_current_level and next_level != active_level:
                cls._notify_level(pr, workflow, next_level)

        pr.approval_workflow_config = workflow
        pr.save()
        return pr

    @classmethod
    @transaction.atomic
    def reject(cls, pr_id, actor, reason, expected_stage_key=None):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._reject_locked(pr, actor, reason, expected_stage_key)

    @classmethod
    def _reject_locked(cls, pr, actor, reason, expected_stage_key=None):
        if canonicalize_pr_status(pr.status) not in cls.ACTIVE_REVIEW_STATUSES:
            raise ValidationError({'error': 'This requisition is not awaiting approval.'})

        trimmed_reason = str(reason or '').strip()
        if len(trimmed_reason) < 10:
            raise ValidationError({'error': 'Rejection reason must be at least 10 characters long.'})
        if len(trimmed_reason) > 1000:
            raise ValidationError({'error': 'Rejection reason cannot exceed 1000 characters.'})

        workflow = cls._workflow(pr)
        _, active_stages = cls._active_level_stages(pr, workflow)
        _, stage = cls._actor_stage(active_stages, actor, expected_stage_key)

        rejected_at = timezone.now()
        actor_name = actor.get_full_name() or getattr(actor, 'username', '') or getattr(actor, 'email', '')
        stage['status'] = 'rejected'
        stage['rejected_at'] = rejected_at.isoformat()
        stage['rejected_by_id'] = str(actor.id)
        stage['rejected_by_name'] = actor_name
        stage['rejection_reason'] = trimmed_reason
        cls._mirror_fixed_rejection(pr, stage)

        pr.approval_workflow_config = workflow
        pr.status = 'rejected'
        pr.rejection_reason = trimmed_reason
        pr.save()
        return pr
