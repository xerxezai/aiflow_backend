"""
Data Mining API Views
RESTful endpoints for data mining platform
"""
import logging
import json
import pandas as pd
import io
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction

from apps.wrench_integration import service as wrench_service
from apps.wrench_integration.models import WrenchConfig
from apps.rbac.permissions import IsAdmin

from .models import (
    DataMiningProject,
    DataMiningDocument,
    TransformationPipeline,
    TransformationStep,
)
from .serializers import (
    DataMiningProjectSerializer,
    DataMiningProjectCreateSerializer,
    DataMiningDocumentSerializer,
    TransformationPipelineSerializer,
    TransformationStepSerializer,
)
from .transformation_engine import TransformationEngine

logger = logging.getLogger(__name__)


class DataMiningProjectViewSet(viewsets.ModelViewSet):
    """
    Data Mining Project management
    
    Endpoints:
        GET    /api/data-mining/projects/          - List all projects
        POST   /api/data-mining/projects/          - Create new project
        GET    /api/data-mining/projects/{id}/     - Get project details
        PATCH  /api/data-mining/projects/{id}/     - Update project
        DELETE /api/data-mining/projects/{id}/     - Delete project
        
        POST   /api/data-mining/projects/{id}/add_documents/     - Add Wrench documents
        POST   /api/data-mining/projects/{id}/extract_data/      - Extract data from documents
        POST   /api/data-mining/projects/{id}/execute_pipeline/  - Run transformation pipeline
        GET    /api/data-mining/projects/{id}/download_master/   - Download master file
    """
    permission_classes = [IsAuthenticated]
    queryset = DataMiningProject.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'create':
            return DataMiningProjectCreateSerializer
        return DataMiningProjectSerializer
    
    def get_queryset(self):
        # Users can only see their own projects unless they're admin
        user = self.request.user
        if hasattr(user, 'is_admin') and user.is_admin:
            return DataMiningProject.objects.all()
        return DataMiningProject.objects.filter(created_by=user)
    
    @action(detail=True, methods=['post'])
    def add_documents(self, request, pk=None):
        """
        Add Wrench documents to the project
        
        Request body:
            {
                "wrench_documents": [
                    {
                        "doc_number": "DOC-001",
                        "doc_title": "Equipment List",
                        "doc_revision": "A",
                        "transmittal_id": "TR-001"
                    }
                ]
            }
        """
        project = self.get_object()
        wrench_documents = request.data.get('wrench_documents', [])
        
        if not wrench_documents:
            return Response(
                {'error': 'No documents provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        created_docs = []
        with transaction.atomic():
            for idx, doc_data in enumerate(wrench_documents):
                doc = DataMiningDocument.objects.create(
                    project=project,
                    wrench_doc_number=doc_data.get('doc_number', ''),
                    wrench_doc_title=doc_data.get('doc_title', ''),
                    wrench_doc_revision=doc_data.get('doc_revision', ''),
                    wrench_transmittal_id=doc_data.get('transmittal_id', ''),
                    sequence_order=idx,
                )
                created_docs.append(doc)
            
            # Update project
            project.total_documents = project.documents.count()
            project.status = 'configuring'
            project.save()
        
        return Response({
            'message': f'Added {len(created_docs)} documents',
            'documents': DataMiningDocumentSerializer(created_docs, many=True).data
        })
    
    @action(detail=True, methods=['post'])
    def extract_data(self, request, pk=None):
        """
        Extract tabular data from uploaded documents
        Uses AI/OCR to extract tables from PDFs, Excel, etc.
        
        This is a placeholder - actual implementation would use:
        - PDF table extraction (Camelot, Tabula, Azure Form Recognizer)
        - Excel reading (openpyxl, xlrd)
        - OCR for scanned documents
        """
        project = self.get_object()
        
        # Soft-coded extraction logic placeholder
        # In production, this would:
        # 1. Download documents from Wrench/S3
        # 2. Use appropriate extraction method based on file type
        # 3. Store extracted data in document.extracted_data JSONField
        
        documents = project.documents.filter(extraction_status='pending')
        
        for doc in documents:
            # Placeholder: simulate extraction
            doc.extraction_status = 'completed'
            # Simulated extracted data (would be real table data from document)
            doc.extracted_data = {
                'columns': ['Item', 'Description', 'Quantity', 'Unit Price'],
                'rows': [
                    ['PUMP-001', 'Centrifugal Pump', 2, 15000],
                    ['VALVE-001', 'Gate Valve 6"', 10, 850],
                ]
            }
            doc.row_count = len(doc.extracted_data.get('rows', []))
            doc.column_count = len(doc.extracted_data.get('columns', []))
            doc.save()
        
        project.status = 'configuring'
        project.save()
        
        return Response({
            'message': f'Extracted data from {documents.count()} documents',
            'project': DataMiningProjectSerializer(project).data
        })
    
    @action(detail=True, methods=['post'])
    def execute_pipeline(self, request, pk=None):
        """
        Execute the transformation pipeline and generate master file
        """
        project = self.get_object()
        
        try:
            pipeline = project.pipeline
        except TransformationPipeline.DoesNotExist:
            return Response(
                {'error': 'No pipeline configured for this project'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Load source documents as DataFrames
        dataframes = {}
        for doc in project.documents.filter(extraction_status='completed'):
            if doc.extracted_data:
                # Convert JSONField data to DataFrame
                df = pd.DataFrame(
                    doc.extracted_data.get('rows', []),
                    columns=doc.extracted_data.get('columns', [])
                )
                dataframes[str(doc.id)] = df
        
        if not dataframes:
            return Response(
                {'error': 'No extracted data available. Please run extract_data first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Execute pipeline steps in order
        engine = TransformationEngine()
        step_outputs = {}
        
        with transaction.atomic():
            project.status = 'executing'
            project.save()
            
            start_time = timezone.now()
            
            for step in pipeline.steps.order_by('sequence_order'):
                try:
                    step.status = 'executing'
                    step.save()
                    
                    # Get input DataFrame
                    if step.input_source:
                        # Input from previous step or document
                        input_df = step_outputs.get(step.input_source) or dataframes.get(step.input_source)
                        if input_df is None:
                            raise ValueError(f"Input source '{step.input_source}' not found")
                    else:
                        # Use first available document as default input
                        input_df = list(dataframes.values())[0]
                    
                    # Collect additional inputs for operations like join, union
                    additional_inputs = {}
                    if step.operation_type in ['join', 'union']:
                        # Make all previous outputs and documents available
                        additional_inputs.update(step_outputs)
                        additional_inputs.update(dataframes)
                    
                    # Execute transformation
                    step_start = timezone.now()
                    output_df = engine.execute(
                        step.operation_type,
                        input_df,
                        step.config,
                        additional_inputs
                    )
                    step_end = timezone.now()
                    
                    # Store output
                    step_outputs[str(step.id)] = output_df
                    
                    # Update step with results
                    step.status = 'completed'
                    step.output_row_count = len(output_df)
                    step.output_column_count = len(output_df.columns)
                    step.execution_time_ms = (step_end - step_start).total_seconds() * 1000
                    
                    # Store preview (first 100 rows)
                    preview_df = output_df.head(100)
                    step.output_preview = {
                        'columns': preview_df.columns.tolist(),
                        'rows': preview_df.values.tolist()
                    }
                    step.save()
                    
                except Exception as e:
                    step.status = 'failed'
                    step.error_message = str(e)
                    step.save()
                    
                    project.status = 'failed'
                    project.save()
                    
                    return Response(
                        {'error': f'Step "{step.step_name}" failed: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            
            # Get final output (last step or first document if no steps)
            if step_outputs:
                final_df = list(step_outputs.values())[-1]
            else:
                final_df = list(dataframes.values())[0]
            
            # Save master file (placeholder - would upload to S3)
            project.total_rows_processed = len(final_df)
            project.master_file_path = f"s3://data-mining/{project.id}/master.{project.master_file_format}"
            project.status = 'completed'
            project.executed_at = timezone.now()
            project.execution_time_seconds = (timezone.now() - start_time).total_seconds()
            project.save()
            
            pipeline.last_executed_at = timezone.now()
            pipeline.save()
        
        return Response({
            'message': 'Pipeline executed successfully',
            'rows_processed': project.total_rows_processed,
            'execution_time': project.execution_time_seconds,
            'master_file': project.master_file_path,
            'preview': {
                'columns': final_df.columns.tolist(),
                'rows': final_df.head(20).values.tolist()
            }
        })
    
    @action(detail=True, methods=['get'])
    def download_master(self, request, pk=None):
        """
        Download the generated master file
        (Placeholder - would download from S3)
        """
        project = self.get_object()
        
        if not project.master_file_path:
            return Response(
                {'error': 'No master file generated yet. Please execute the pipeline first.'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        return Response({
            'download_url': project.master_file_path,
            'format': project.master_file_format,
            'message': 'Download functionality placeholder - implement S3 download'
        })


class TransformationPipelineViewSet(viewsets.ModelViewSet):
    """
    Transformation Pipeline management
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TransformationPipelineSerializer
    queryset = TransformationPipeline.objects.all()
    
    @action(detail=True, methods=['post'])
    def add_step(self, request, pk=None):
        """
        Add a transformation step to the pipeline
        
        Request body:
            {
                "step_name": "Join with Equipment List",
                "operation_type": "join",
                "config": {
                    "join_type": "inner",
                    "right_input": "doc_id_2",
                    "left_key": "equipment_id",
                    "right_key": "id"
                },
                "input_source": "doc_id_1",
                "sequence_order": 1
            }
        """
        pipeline = self.get_object()
        
        step_data = request.data
        step = TransformationStep.objects.create(
            pipeline=pipeline,
            step_name=step_data.get('step_name', 'Unnamed Step'),
            operation_type=step_data.get('operation_type'),
            config=step_data.get('config', {}),
            input_source=step_data.get('input_source', ''),
            sequence_order=step_data.get('sequence_order', pipeline.steps.count())
        )
        
        return Response(TransformationStepSerializer(step).data)


class WrenchDocumentSearchViewSet(viewsets.ViewSet):
    """
    Wrench document search integration for Data Mining
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Search Wrench documents
        
        Query params:
            project_number: Wrench project/order number
            search_term: Search in document title/number
        """
        try:
            config = WrenchConfig.objects.first()
            if not config:
                return Response(
                    {'error': 'Wrench integration not configured'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            project_number = request.query_params.get('project_number', '')
            search_term = request.query_params.get('search_term', '')
            
            # Use Wrench service to search documents
            results = wrench_service.search_documents(
                config,
                project_number=project_number,
                search_term=search_term,
                page=1,
                page_size=100
            )
            
            return Response(results)
            
        except Exception as e:
            logger.error(f"Wrench document search failed: {str(e)}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def projects(self, request):
        """
        Get list of Wrench projects for dropdown
        """
        try:
            config = WrenchConfig.objects.first()
            if not config:
                return Response(
                    {'error': 'Wrench integration not configured'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # This would use the wrench_service to get project list
            # Placeholder response
            projects = [
                {'project_number': 'PRJ-001', 'project_name': 'Oil Refinery Expansion'},
                {'project_number': 'PRJ-002', 'project_name': 'Gas Processing Plant'},
            ]
            
            return Response({'projects': projects})
            
        except Exception as e:
            logger.error(f"Wrench project list failed: {str(e)}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
