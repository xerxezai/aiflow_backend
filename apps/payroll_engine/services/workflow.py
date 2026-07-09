"""Payroll workflow state machine.

Allowed transitions live in `apps.payroll_engine.catalog.WORKFLOW_TRANSITIONS`.
Each transition writes a `PayrollWorkflowLog` entry and stamps the
appropriate timestamp/actor on the PayrollRun.
"""
from __future__ import annotations
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ..catalog import (
    Status, WORKFLOW_TRANSITIONS, WORKFLOW_ROLES, status_meta,
)
from ..models import PayrollRun, PayrollWorkflowLog
from ..config import ALLOW_REVERT_TO_DRAFT


def _user_roles(user) -> set:
    """Resolve a user's role names. Tolerates missing RBAC."""
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    roles = set()
    if getattr(user, 'is_superuser', False):
        roles.add('Admin')
    # Common RBAC patterns in this codebase
    for attr in ('role', 'user_role', 'designation'):
        v = getattr(user, attr, None)
        if v:
            roles.add(str(v))
    groups = getattr(user, 'groups', None)
    if groups is not None:
        try:
            for g in groups.all():
                roles.add(g.name)
        except Exception:
            pass
    return roles


def _check_role(user, target_status: str) -> None:
    # `user=None` means a system context (management commands, Celery tasks).
    # We trust those callers and skip role enforcement.
    if user is None:
        return
    allowed = WORKFLOW_ROLES.get(target_status, [])
    if not allowed:
        return
    if 'Admin' in _user_roles(user):
        return
    if _user_roles(user) & set(allowed):
        return
    raise PermissionDenied(
        f"User lacks required role for '{target_status}'. Need one of: {allowed}"
    )


def _check_transition(current: str, target: str) -> None:
    allowed = WORKFLOW_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise ValidationError(
            f"Invalid transition: {current} → {target}. Allowed: {allowed or ['(terminal)']}"
        )
    if target == Status.DRAFT and not ALLOW_REVERT_TO_DRAFT:
        raise ValidationError("Reverting to Draft is disabled by configuration.")


def _stamp(run: PayrollRun, target: str, user):
    now = timezone.now()
    if target == Status.HR_APPROVED:
        run.hr_approved_at = now
        run.hr_approved_by = user
    elif target == Status.FINANCE_APPROVED:
        run.finance_approved_at = now
        run.finance_approved_by = user
    elif target == Status.RELEASED:
        run.released_at = now
        run.released_by = user
    elif target == Status.DRAFT:
        # Re-opening — clear forward timestamps that are no longer valid
        if run.status == Status.HR_APPROVED:
            run.hr_approved_at = None
            run.hr_approved_by = None
        elif run.status == Status.FINANCE_APPROVED:
            run.finance_approved_at = None
            run.finance_approved_by = None


@transaction.atomic
def transition(run: PayrollRun, target_status: str, *, user=None, note: str = '') -> PayrollRun:
    """Move a run from its current status to ``target_status``.

    Validates the transition, checks role, stamps timestamps, writes an
    audit log row, persists the run. Returns the saved run.
    """
    _check_transition(run.status, target_status)
    _check_role(user, target_status)

    from_status = run.status
    _stamp(run, target_status, user)
    run.status = target_status
    run.save()

    PayrollWorkflowLog.objects.create(
        run=run,
        from_status=from_status,
        to_status=target_status,
        actor=user if (user and getattr(user, 'is_authenticated', False)) else None,
        note=note or f"{status_meta(from_status).get('label', from_status)} → {status_meta(target_status).get('label', target_status)}",
    )
    return run


# Convenience wrappers used by views ----------------------------------
def hr_approve(run, user=None, note=''):
    return transition(run, Status.HR_APPROVED, user=user, note=note)


def finance_approve(run, user=None, note=''):
    return transition(run, Status.FINANCE_APPROVED, user=user, note=note)


def release(run, user=None, note=''):
    return transition(run, Status.RELEASED, user=user, note=note)


def revert_to_draft(run, user=None, note=''):
    return transition(run, Status.DRAFT, user=user, note=note)
