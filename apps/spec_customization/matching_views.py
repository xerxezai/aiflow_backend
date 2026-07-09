"""
Spec Customization — Component Matching Views
==============================================

API endpoints for uploading and managing Match/SPEC/CAT workbooks.

Endpoints (mounted at /api/v1/spec-customization/):
  POST   matching/upload/               → upload Match.xlsx + SPEC.xlsx + CAT.xlsx
  GET    matching/sets/                 → list workbook sets for a project
  GET    matching/sets/<id>/            → workbook set detail
  DELETE matching/sets/<id>/            → delete workbook set
  POST   matching/sets/<id>/activate/   → activate workbook set
  POST   matching/sets/<id>/parse/      → parse Match.xlsx (create rules)
  GET    matching/sets/<id>/rules/      → list matching rules
  POST   matching/match/                → match a component
  GET    matching/results/              → list matching results
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .matching_models import (
    MatchingWorkbookSet,
    MatchingRule,
    ComponentMatchingResult,
)
from .project_models import SpecProject
from .services.component_matcher import parse_match_xlsx, match_component

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Validate Excel file
# ─────────────────────────────────────────────────────────────────────────────
def _validate_excel_file(uploaded_file, field_name: str) -> tuple[bool, str]:
    """
    Validate uploaded Excel file.
    
    Returns:
        (is_valid, error_message)
    """
    if not uploaded_file:
        return False, f"{field_name} is required"
    
    # Check extension
    filename = uploaded_file.name.lower()
    if not (filename.endswith('.xlsx') or filename.endswith('.xls')):
        return False, f"{field_name} must be an Excel file (.xlsx or .xls)"
    
    # Check size (max 50MB)
    MAX_SIZE = 50 * 1024 * 1024  # 50MB
    if uploaded_file.size > MAX_SIZE:
        return False, f"{field_name} exceeds maximum size of 50MB"
    
    return True, ''


# ─────────────────────────────────────────────────────────────────────────────
# Upload Match/SPEC/CAT workbooks
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
def upload_matching_workbooks(request):
    """
    Upload Match.xlsx, SPEC.xlsx, and CAT.xlsx for a project.
    
    Required fields:
        - project_id: UUID of SpecProject
        - match_file: Match.xlsx file
        - spec_file: SPEC.xlsx file
        - cat_file: CAT.xlsx file
    
    Optional fields:
        - version_label: Version label (default: auto-generated)
        - auto_parse: Whether to auto-parse Match.xlsx (default: true)
    """
    # Get project
    project_id = request.data.get('project_id')
    if not project_id:
        return Response(
            {'error': 'project_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    project = get_object_or_404(SpecProject, project_id=project_id)
    
    # Get files
    match_file = request.FILES.get('match_file')
    spec_file = request.FILES.get('spec_file')
    cat_file = request.FILES.get('cat_file')
    
    # Validate files
    is_valid, error = _validate_excel_file(match_file, 'match_file')
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    
    is_valid, error = _validate_excel_file(spec_file, 'spec_file')
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    
    is_valid, error = _validate_excel_file(cat_file, 'cat_file')
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    
    # Deactivate existing active sets
    MatchingWorkbookSet.objects.filter(
        project=project,
        is_active=True
    ).update(is_active=False)
    
    # Create workbook set
    version_label = request.data.get('version_label', f'Upload {MatchingWorkbookSet.objects.filter(project=project).count() + 1}')
    
    workbook_set = MatchingWorkbookSet.objects.create(
        project=project,
        version_label=version_label,
        match_file=match_file,
        spec_file=spec_file,
        cat_file=cat_file,
        match_file_name=match_file.name,
        spec_file_name=spec_file.name,
        cat_file_name=cat_file.name,
        is_active=True,
        uploaded_by=request.user,
    )
    
    # Auto-parse Match.xlsx if requested
    auto_parse = request.data.get('auto_parse', 'true').lower() in ('true', '1', 'yes')
    if auto_parse:
        try:
            rules_count, error = parse_match_xlsx(
                workbook_set.match_file.path,
                workbook_set
            )
            
            if error:
                workbook_set.parse_error = error
                workbook_set.is_parsed = False
            else:
                workbook_set.is_parsed = True
                workbook_set.rules_count = rules_count
            
            workbook_set.save(update_fields=['is_parsed', 'parse_error', 'rules_count'])
            
        except Exception as e:
            logger.exception(f"[MatchingUpload] Auto-parse failed: {e}")
            workbook_set.parse_error = str(e)
            workbook_set.save(update_fields=['parse_error'])
    
    # Count sheets in SPEC and CAT
    try:
        import pandas as pd
        spec_xl = pd.ExcelFile(workbook_set.spec_file.path)
        cat_xl = pd.ExcelFile(workbook_set.cat_file.path)
        workbook_set.spec_sheets_count = len(spec_xl.sheet_names)
        workbook_set.cat_sheets_count = len(cat_xl.sheet_names)
        workbook_set.save(update_fields=['spec_sheets_count', 'cat_sheets_count'])
    except Exception as e:
        logger.warning(f"[MatchingUpload] Failed to count sheets: {e}")
    
    return Response({
        'success': True,
        'workbook_set': {
            'id': str(workbook_set.id),
            'project_id': str(project.project_id),
            'project_name': project.name,
            'version_label': workbook_set.version_label,
            'is_active': workbook_set.is_active,
            'is_parsed': workbook_set.is_parsed,
            'rules_count': workbook_set.rules_count,
            'spec_sheets_count': workbook_set.spec_sheets_count,
            'cat_sheets_count': workbook_set.cat_sheets_count,
            'parse_error': workbook_set.parse_error,
            'created_at': workbook_set.created_at.isoformat(),
        }
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# List workbook sets for a project
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_matching_workbook_sets(request):
    """
    List all matching workbook sets for a project.
    
    Query params:
        - project_id: Filter by project (required)
        - is_active: Filter by active status (optional)
    """
    project_id = request.query_params.get('project_id')
    if not project_id:
        return Response(
            {'error': 'project_id query parameter is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    queryset = MatchingWorkbookSet.objects.filter(project__project_id=project_id)
    
    # Filter by active status
    is_active = request.query_params.get('is_active')
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active.lower() in ('true', '1', 'yes'))
    
    sets = []
    for ws in queryset:
        sets.append({
            'id': str(ws.id),
            'version_label': ws.version_label,
            'is_active': ws.is_active,
            'is_parsed': ws.is_parsed,
            'rules_count': ws.rules_count,
            'spec_sheets_count': ws.spec_sheets_count,
            'cat_sheets_count': ws.cat_sheets_count,
            'match_file_name': ws.match_file_name,
            'spec_file_name': ws.spec_file_name,
            'cat_file_name': ws.cat_file_name,
            'created_at': ws.created_at.isoformat(),
        })
    
    return Response({'items': sets, 'count': len(sets)})


# ─────────────────────────────────────────────────────────────────────────────
# Get workbook set detail
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def matching_workbook_set_detail(request, set_id):
    """Get or delete a workbook set."""
    ws = get_object_or_404(MatchingWorkbookSet, id=set_id)
    
    if request.method == 'DELETE':
        ws.delete()
        return Response({'success': True}, status=status.HTTP_204_NO_CONTENT)
    
    return Response({
        'id': str(ws.id),
        'project_id': str(ws.project.project_id),
        'project_name': ws.project.name,
        'version_label': ws.version_label,
        'is_active': ws.is_active,
        'is_parsed': ws.is_parsed,
        'rules_count': ws.rules_count,
        'spec_sheets_count': ws.spec_sheets_count,
        'cat_sheets_count': ws.cat_sheets_count,
        'match_file_name': ws.match_file_name,
        'spec_file_name': ws.spec_file_name,
        'cat_file_name': ws.cat_file_name,
        'parse_error': ws.parse_error,
        'created_at': ws.created_at.isoformat(),
        'updated_at': ws.updated_at.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Activate workbook set
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def activate_matching_workbook_set(request, set_id):
    """Activate a workbook set (deactivate others in same project)."""
    ws = get_object_or_404(MatchingWorkbookSet, id=set_id)
    
    # Deactivate all sets in same project
    MatchingWorkbookSet.objects.filter(
        project=ws.project
    ).update(is_active=False)
    
    # Activate this set
    ws.is_active = True
    ws.save(update_fields=['is_active'])
    
    return Response({'success': True, 'is_active': True})


# ─────────────────────────────────────────────────────────────────────────────
# Parse Match.xlsx
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def parse_matching_workbook(request, set_id):
    """Parse Match.xlsx and create/update matching rules."""
    ws = get_object_or_404(MatchingWorkbookSet, id=set_id)
    
    if not ws.match_file:
        return Response(
            {'error': 'Match file not uploaded'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        rules_count, error = parse_match_xlsx(ws.match_file.path, ws)
        
        if error:
            ws.parse_error = error
            ws.is_parsed = False
        else:
            ws.is_parsed = True
            ws.rules_count = rules_count
            ws.parse_error = ''
        
        ws.save(update_fields=['is_parsed', 'parse_error', 'rules_count'])
        
        return Response({
            'success': not bool(error),
            'rules_count': rules_count,
            'error': error,
        })
        
    except Exception as e:
        logger.exception(f"[MatchingParse] Parse failed: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ─────────────────────────────────────────────────────────────────────────────
# List matching rules
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_matching_rules(request, set_id):
    """List all matching rules for a workbook set."""
    ws = get_object_or_404(MatchingWorkbookSet, id=set_id)
    
    rules = MatchingRule.objects.filter(workbook_set=ws).order_by('pdf_component_name')
    
    items = []
    for rule in rules:
        items.append({
            'id': str(rule.id),
            'pdf_component_name': rule.pdf_component_name,
            'catalog_component_name': rule.catalog_component_name,
            'cat_sheet_name': rule.cat_sheet_name,
            'row_number': rule.row_number,
        })
    
    return Response({'items': items, 'count': len(items)})


# ─────────────────────────────────────────────────────────────────────────────
# Match component
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def match_component_endpoint(request):
    """
    Match a component to CAT commodity code.
    
    Request body:
        {
            "workbook_set_id": "uuid",  (optional — uses active set for project)
            "project_id": "uuid",  (required if workbook_set_id not provided)
            "pdf_component_name": "GATE VALVE",
            "nominal_size": "2",
            "pressure_rating": "150#",
            "material_grade": "A105"
        }
    """
    # Get workbook set
    workbook_set_id = request.data.get('workbook_set_id')
    project_id = request.data.get('project_id')
    
    if workbook_set_id:
        ws = get_object_or_404(MatchingWorkbookSet, id=workbook_set_id)
    elif project_id:
        ws = MatchingWorkbookSet.objects.filter(
            project__project_id=project_id,
            is_active=True
        ).first()
        if not ws:
            return Response(
                {'error': 'No active workbook set found for this project'},
                status=status.HTTP_404_NOT_FOUND
            )
    else:
        return Response(
            {'error': 'Either workbook_set_id or project_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get matching criteria
    pdf_component_name = request.data.get('pdf_component_name', '')
    nominal_size = request.data.get('nominal_size', '')
    pressure_rating = request.data.get('pressure_rating', '')
    material_grade = request.data.get('material_grade', '')
    
    if not pdf_component_name:
        return Response(
            {'error': 'pdf_component_name is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Perform matching
    result = match_component(
        pdf_component_name=pdf_component_name,
        nominal_size=nominal_size,
        pressure_rating=pressure_rating,
        material_grade=material_grade,
        workbook_set=ws,
    )
    
    return Response(result)


# ─────────────────────────────────────────────────────────────────────────────
# List matching results
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_matching_results(request):
    """
    List matching results.
    
    Query params:
        - workbook_set_id: Filter by workbook set
        - project_id: Filter by project
        - limit: Max results (default: 100)
    """
    queryset = ComponentMatchingResult.objects.all()
    
    # Filter by workbook set
    workbook_set_id = request.query_params.get('workbook_set_id')
    if workbook_set_id:
        queryset = queryset.filter(workbook_set__id=workbook_set_id)
    
    # Filter by project
    project_id = request.query_params.get('project_id')
    if project_id:
        queryset = queryset.filter(workbook_set__project__project_id=project_id)
    
    # Limit
    limit = int(request.query_params.get('limit', 100))
    queryset = queryset.order_by('-created_at')[:limit]
    
    items = []
    for result in queryset:
        items.append({
            'id': str(result.id),
            'pdf_component_name': result.pdf_component_name,
            'nominal_size': result.nominal_size,
            'pressure_rating': result.pressure_rating,
            'material_grade': result.material_grade,
            'matched_commodity_code': result.matched_commodity_code,
            'matched_description': result.matched_description,
            'match_score': result.match_score,
            'match_method': result.match_method,
            'created_at': result.created_at.isoformat(),
        })
    
    return Response({'items': items, 'count': len(items)})
