"""
Non-TEFF Project CRUD — DRF endpoints.

Endpoints (all under /api/v1/non-teff/projects/)
------------------------------------------------
GET    projects/                      list_projects        (RBAC-filtered)
POST   projects/                      create_project
GET    projects/<project_id>/         get_project
PATCH  projects/<project_id>/         update_project
DELETE projects/<project_id>/         delete_project
GET    projects/<project_id>/items/   list_project_items   (jobs + batches)

RBAC rules (soft-coded in PROJECT_RBAC):
  - Admins (resolve_user_role == 'admin') see / modify any project
  - Regular users see / modify projects they created
"""

import logging
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import NonTeffProject, NonTeffExtractionJob, NonTeffBatch
from .services import history_archive

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded configuration
# ---------------------------------------------------------------------------

PROJECT_RBAC = {
    # Roles that always see every project regardless of created_by.
    'admin_roles':   {'admin', 'super_admin', 'tenant_admin'},
    # Roles allowed to delete projects they own.
    'delete_roles':  {'admin', 'super_admin', 'tenant_admin', 'engineer', 'lead'},
    # Max projects returned in one list call.
    'list_limit':    500,
    # Max items returned by list_project_items (jobs + batches combined).
    'items_limit':   1000,
}

# Whitelisted fields a user may set on create / update — protects against
# accidental injection of created_by / project_id from request payloads.
ALLOWED_CREATE_FIELDS = {
    'name', 'code', 'client', 'plant', 'discipline',
    'description', 'status', 'tags', 'metadata',
}
ALLOWED_UPDATE_FIELDS = ALLOWED_CREATE_FIELDS  # same whitelist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(user) -> bool:
    return history_archive.resolve_user_role(user) in PROJECT_RBAC['admin_roles']


def _user_can_modify(user, project: NonTeffProject) -> bool:
    if _is_admin(user):
        return True
    return project.created_by_id == getattr(user, 'id', None)


def _serialize_project(p: NonTeffProject, *, counts: dict | None = None) -> dict:
    c = counts or {}
    return {
        'project_id':   str(p.project_id),
        'name':         p.name,
        'code':         p.code,
        'client':       p.client,
        'plant':        p.plant,
        'discipline':   p.discipline,
        'description':  p.description,
        'status':       p.status,
        'tags':         p.tags or [],
        'metadata':     p.metadata or {},
        'created_at':   p.created_at.isoformat() if p.created_at else None,
        'updated_at':   p.updated_at.isoformat() if p.updated_at else None,
        'created_by_id': p.created_by_id,
        'created_by':   getattr(p.created_by, 'username', '') if p.created_by_id else '',
        'job_count':    c.get('job_count', 0),
        'batch_count':  c.get('batch_count', 0),
    }


def _filtered_queryset(user):
    qs = NonTeffProject.objects.all()
    if not _is_admin(user):
        qs = qs.filter(created_by=user)
    return qs


def _sanitize_payload(data: dict, whitelist: set) -> dict:
    out = {}
    for k in whitelist:
        if k in data:
            out[k] = data[k]
    # Coerce status to a known choice.
    if 'status' in out:
        valid = {c[0] for c in NonTeffProject.STATUS_CHOICES}
        if out['status'] not in valid:
            out['status'] = NonTeffProject.STATUS_ACTIVE
    # Tags must be a list of strings.
    if 'tags' in out and not isinstance(out['tags'], list):
        out['tags'] = []
    if 'metadata' in out and not isinstance(out['metadata'], dict):
        out['metadata'] = {}
    return out


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
    # Optional filters
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

    qs = qs.annotate(
        _job_count=Count('jobs', distinct=True),
        _batch_count=Count('batches', distinct=True),
    )[:PROJECT_RBAC['list_limit']]

    items = [
        _serialize_project(p, counts={
            'job_count':   getattr(p, '_job_count', 0),
            'batch_count': getattr(p, '_batch_count', 0),
        })
        for p in qs
    ]
    return Response({
        'role':  history_archive.resolve_user_role(request.user),
        'total': len(items),
        'items': items,
    })


def _create_project(request):
    payload = _sanitize_payload(request.data or {}, ALLOWED_CREATE_FIELDS)
    if not (payload.get('name') or '').strip():
        return Response({'error': 'Project name is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        p = NonTeffProject.objects.create(created_by=request.user, **payload)
    except Exception as exc:
        logger.exception('NonTeffProject create failed')
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize_project(p), status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    try:
        p = NonTeffProject.objects.get(project_id=project_id)
    except NonTeffProject.DoesNotExist:
        return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

    # Read access: admin OR creator
    if not _is_admin(request.user) and p.created_by_id != request.user.id:
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        counts = {
            'job_count':   p.jobs.count(),
            'batch_count': p.batches.count(),
        }
        return Response(_serialize_project(p, counts=counts))

    # Write access: admin OR creator (already verified above)
    if not _user_can_modify(request.user, p):
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PATCH':
        updates = _sanitize_payload(request.data or {}, ALLOWED_UPDATE_FIELDS)
        for k, v in updates.items():
            setattr(p, k, v)
        p.save()
        return Response(_serialize_project(p))

    # DELETE
    role = history_archive.resolve_user_role(request.user)
    if role not in PROJECT_RBAC['delete_roles']:
        return Response({'error': 'Your role cannot delete projects.'},
                        status=status.HTTP_403_FORBIDDEN)
    p.delete()
    return Response({'deleted': True, 'project_id': str(project_id)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_project_items(request, project_id):
    """
    Return all extractions (single-file jobs + bulk batches) belonging to
    the project, ordered by most recent first. RBAC-filtered.
    """
    try:
        p = NonTeffProject.objects.get(project_id=project_id)
    except NonTeffProject.DoesNotExist:
        return Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

    if not _is_admin(request.user) and p.created_by_id != request.user.id:
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    limit = PROJECT_RBAC['items_limit']
    jobs = NonTeffExtractionJob.objects.filter(project=p).order_by('-created_at')[:limit]
    batches = NonTeffBatch.objects.filter(project=p).order_by('-created_at')[:limit]

    job_items = [{
        'kind':           'job',
        'id':             str(j.job_id),
        'name':           j.file_name,
        'status':         j.status,
        'progress':       j.progress,
        'file_format':    j.file_format,
        'status_message': j.status_message,
        'created_at':     j.created_at.isoformat() if j.created_at else None,
    } for j in jobs]

    batch_items = [{
        'kind':         'batch',
        'id':           str(b.batch_id),
        'name':         b.name,
        'status':       b.status,
        'plant':        b.plant,
        'total_files':  b.total_files,
        'ready_files':  b.ready_files,
        'failed_files': b.failed_files,
        'created_at':   b.created_at.isoformat() if b.created_at else None,
    } for b in batches]

    combined = sorted(
        job_items + batch_items,
        key=lambda x: x.get('created_at') or '',
        reverse=True,
    )

    return Response({
        'project':     _serialize_project(p),
        'total':       len(combined),
        'job_count':   len(job_items),
        'batch_count': len(batch_items),
        'items':       combined,
    })
