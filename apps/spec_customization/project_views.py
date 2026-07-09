"""
Spec Customization — Project CRUD endpoints
============================================

Endpoints (all under /api/v1/spec-customization/projects/)
----------------------------------------------------------
GET    projects/                      list_projects        (RBAC-filtered)
POST   projects/                      create_project
GET    projects/<project_id>/         get_project
PATCH  projects/<project_id>/         update_project
DELETE projects/<project_id>/         delete_project
GET    projects/<project_id>/items/   list_project_items   (extraction jobs)

The implementation mirrors ``apps.non_teff_metadata.project_views`` but
is fully self-contained (no cross-app imports). RBAC rules are soft-coded
in ``PROJECT_RBAC`` so they can be tuned without code changes.

Admins (Django ``is_staff``/``is_superuser``) see and modify every project;
regular users only see / modify projects they created.
"""
from __future__ import annotations

import logging

from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import PaperSpecDocument, PaperSpecExtractionJob
from .project_models import SpecProject

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded configuration
# ---------------------------------------------------------------------------
PROJECT_RBAC = {
    'list_limit':  500,
    'items_limit': 1000,
}

ALLOWED_CREATE_FIELDS = {
    'name', 'code', 'client', 'plant', 'discipline',
    'description', 'status', 'tags', 'metadata',
}
ALLOWED_UPDATE_FIELDS = ALLOWED_CREATE_FIELDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_admin(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    # Soft RBAC — recognise common admin role attributes if present.
    role = (getattr(user, 'role', '') or '').lower()
    if role in {'admin', 'super_admin', 'tenant_admin'}:
        return True
    return False


def _user_role(user) -> str:
    if _is_admin(user):
        return 'admin'
    return (getattr(user, 'role', '') or 'user').lower()


def _user_can_modify(user, project: SpecProject) -> bool:
    if _is_admin(user):
        return True
    return project.created_by_id == getattr(user, 'id', None)


def _serialize_project(p: SpecProject, *, counts: dict | None = None) -> dict:
    c = counts or {}
    return {
        'project_id':    str(p.project_id),
        'name':          p.name,
        'code':          p.code,
        'client':        p.client,
        'plant':         p.plant,
        'discipline':    p.discipline,
        'description':   p.description,
        'status':        p.status,
        'tags':          p.tags or [],
        'metadata':      p.metadata or {},
        'created_at':    p.created_at.isoformat() if p.created_at else None,
        'updated_at':    p.updated_at.isoformat() if p.updated_at else None,
        'created_by_id': p.created_by_id,
        'created_by':    getattr(p.created_by, 'username', '') if p.created_by_id else '',
        'job_count':     c.get('job_count', 0),
        'document_count': c.get('document_count', 0),
    }


def _filtered_queryset(user):
    qs = SpecProject.objects.all()
    if not _is_admin(user):
        qs = qs.filter(created_by=user)
    return qs


def _sanitize_payload(data: dict, whitelist: set) -> dict:
    out = {}
    for k in whitelist:
        if k in data:
            out[k] = data[k]
    if 'status' in out:
        valid = {c[0] for c in SpecProject.STATUS_CHOICES}
        if out['status'] not in valid:
            out['status'] = SpecProject.STATUS_ACTIVE
    if 'tags' in out and not isinstance(out['tags'], list):
        out['tags'] = []
    if 'metadata' in out and not isinstance(out['metadata'], dict):
        out['metadata'] = {}
    return out


def _count_for_project(project_id) -> dict:
    """Soft-link counts via the existing ``project_id`` CharField on documents."""
    pid_str = str(project_id)
    doc_qs = PaperSpecDocument.objects.filter(project_id=pid_str)
    doc_count = doc_qs.count()
    job_count = PaperSpecExtractionJob.objects.filter(document__in=doc_qs).count()
    return {'document_count': doc_count, 'job_count': job_count}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def projects_collection(request):
    if request.method == 'POST':
        return _create_project(request)
    return _list_projects(request)


def _list_projects(request):
    qs = _filtered_queryset(request.user)
    status_filter = request.query_params.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)
    search = (request.query_params.get('q') or '').strip()
    if search:
        qs = qs.filter(
            Q(name__icontains=search) |
            Q(code__icontains=search) |
            Q(client__icontains=search) |
            Q(plant__icontains=search)
        )

    qs = qs[:PROJECT_RBAC['list_limit']]
    items = []
    for p in qs:
        items.append(_serialize_project(p, counts=_count_for_project(p.project_id)))
    return Response({
        'role':  _user_role(request.user),
        'total': len(items),
        'items': items,
    })


def _create_project(request):
    payload = _sanitize_payload(request.data or {}, ALLOWED_CREATE_FIELDS)
    if not (payload.get('name') or '').strip():
        return Response({'error': 'Project name is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        p = SpecProject.objects.create(created_by=request.user, **payload)
    except Exception as exc:
        logger.exception('SpecProject create failed')
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize_project(p), status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    try:
        p = SpecProject.objects.get(project_id=project_id)
    except SpecProject.DoesNotExist:
        return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

    if not _is_admin(request.user) and p.created_by_id != getattr(request.user, 'id', None):
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        return Response(_serialize_project(p, counts=_count_for_project(p.project_id)))

    if not _user_can_modify(request.user, p):
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PATCH':
        updates = _sanitize_payload(request.data or {}, ALLOWED_UPDATE_FIELDS)
        for k, v in updates.items():
            setattr(p, k, v)
        p.save()
        return Response(_serialize_project(p, counts=_count_for_project(p.project_id)))

    # DELETE
    p.delete()
    return Response({'deleted': True, 'project_id': str(project_id)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_project_items(request, project_id):
    """Return all extraction jobs (and their parent documents) belonging to
    the project, ordered most-recent first."""
    try:
        p = SpecProject.objects.get(project_id=project_id)
    except SpecProject.DoesNotExist:
        return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

    if not _is_admin(request.user) and p.created_by_id != getattr(request.user, 'id', None):
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    pid_str = str(p.project_id)
    limit = PROJECT_RBAC['items_limit']

    docs = PaperSpecDocument.objects.filter(project_id=pid_str).order_by('-created_at')[:limit]
    doc_ids = [d.pk for d in docs]
    jobs = (
        PaperSpecExtractionJob.objects
        .filter(document_id__in=doc_ids)
        .order_by('-created_at')[:limit]
    )

    job_items = []
    for j in jobs:
        job_items.append({
            'kind':           'job',
            'id':             str(j.pk),
            'document_id':    str(getattr(j, 'document_id', '') or ''),
            'name':           getattr(j.document, 'file_name', '') if getattr(j, 'document_id', None) else '',
            'status':         getattr(j, 'status', ''),
            'progress':       getattr(j, 'progress', None),
            'status_message': getattr(j, 'status_message', '') or getattr(j, 'error_message', ''),
            'created_at':     j.created_at.isoformat() if getattr(j, 'created_at', None) else None,
        })

    doc_items = [{
        'kind':       'document',
        'id':         str(d.pk),
        'name':       getattr(d, 'file_name', '') or getattr(d, 'original_filename', ''),
        'created_at': d.created_at.isoformat() if getattr(d, 'created_at', None) else None,
    } for d in docs]

    combined = sorted(
        job_items + doc_items,
        key=lambda x: x.get('created_at') or '',
        reverse=True,
    )

    return Response({
        'project':        _serialize_project(p, counts={
            'document_count': len(doc_items),
            'job_count':      len(job_items),
        }),
        'total':          len(combined),
        'job_count':      len(job_items),
        'document_count': len(doc_items),
        'items':          combined,
    })
