"""
S3 Document Service for Electrical Datasheet Validation
=======================================================
Purpose: Store and retrieve reference documents, uploaded datasheets, and validation reports
Bucket Structure:
- adnoc_standards/      - ADNOC standard specifications
- datasheets/uploaded/  - User-uploaded datasheets (PDF/Excel)
- datasheets/validated/ - Processed and validated datasheets
- validation_reports/   - AI validation reports and analysis
"""

import io
import os
import json
import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from django.conf import settings
from typing import Optional, Dict, Any, List
from datetime import datetime
import PyPDF2
import pandas as pd


class ElectricalDatasheetS3Service:
    """Enhanced S3 service for electrical datasheet document management"""
    
    def __init__(self):
        self.s3_enabled = getattr(settings, 'USE_S3', False) and getattr(settings, 'S3_READY', False)
        self.bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers')
        # Default region matches the production bucket (UAE Central). SigV4 +
        # region-specific endpoint are required so presigned URLs work for
        # opt-in regions like me-central-1 (otherwise IllegalLocationConstraintException).
        self.region = getattr(settings, 'AWS_S3_REGION_NAME', 'me-central-1')
        self.endpoint_url = getattr(settings, 'AWS_S3_ENDPOINT_URL', f'https://s3.{self.region}.amazonaws.com')
        self.signature_version = getattr(settings, 'AWS_S3_SIGNATURE_VERSION', 's3v4')
        self.addressing_style  = getattr(settings, 'AWS_S3_ADDRESSING_STYLE',  'virtual')

        if self.s3_enabled:
            self.s3_client = boto3.client(
                's3',
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                config=BotoConfig(
                    signature_version=self.signature_version,
                    s3={'addressing_style': self.addressing_style},
                ),
            )
        else:
            self.s3_client = None
            self.local_storage_path = os.path.join(settings.MEDIA_ROOT, 'electrical_datasheets')
            os.makedirs(self.local_storage_path, exist_ok=True)
    
    def upload_datasheet(self, 
                        file_obj, 
                        filename: str, 
                        equipment_type: str, 
                        metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Upload electrical datasheet to S3 or local storage.
        
        Args:
            file_obj: File object (PDF/Excel)
            filename: Original filename
            equipment_type: 'transformer' or 'switchgear'
            metadata: Additional metadata (project, tag_number, etc.)
        
        Returns:
            Dictionary with upload details and S3 key/local path
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_filename = self._sanitize_filename(filename)
        s3_key = f"datasheets/uploaded/{equipment_type}/{timestamp}_{safe_filename}"
        
        # Prepare metadata
        file_metadata = {
            'equipment-type': equipment_type,
            'upload-timestamp': timestamp,
            'original-filename': filename,
            **(metadata or {})
        }
        
        if self.s3_enabled:
            return self._upload_to_s3(file_obj, s3_key, file_metadata)
        else:
            return self._upload_to_local(file_obj, s3_key, file_metadata)
    
    def download_datasheet(self, s3_key_or_path: str) -> Optional[bytes]:
        """Download datasheet content from S3 or local storage"""
        if self.s3_enabled:
            return self._download_from_s3(s3_key_or_path)
        else:
            return self._download_from_local(s3_key_or_path)
    
    def extract_text_from_pdf(self, file_obj) -> str:
        """Extract text content from PDF datasheet"""
        try:
            pdf_reader = PyPDF2.PdfReader(file_obj)
            text_content = []
            
            for page_num in range(len(pdf_reader.pages)):
                page = pdf_reader.pages[page_num]
                text_content.append(page.extract_text())
            
            return '\n'.join(text_content)
        except Exception as e:
            print(f"[PDF Extraction] Error: {e}")
            return ""
    
    def extract_data_from_excel(self, file_obj) -> Dict[str, Any]:
        """Extract structured data from Excel datasheet"""
        try:
            # Read Excel file
            excel_data = pd.read_excel(file_obj, sheet_name=None)  # Read all sheets
            
            extracted_data = {
                'sheets': {},
                'summary': {}
            }
            
            for sheet_name, df in excel_data.items():
                # Convert to dictionary format
                extracted_data['sheets'][sheet_name] = df.to_dict('records')
                
                # Extract key-value pairs (common in datasheets)
                if df.shape[1] >= 2:
                    key_value_pairs = {}
                    for _, row in df.iterrows():
                        if pd.notna(row.iloc[0]) and pd.notna(row.iloc[1]):
                            key_value_pairs[str(row.iloc[0])] = str(row.iloc[1])
                    
                    extracted_data['summary'][sheet_name] = key_value_pairs
            
            return extracted_data
        except Exception as e:
            print(f"[Excel Extraction] Error: {e}")
            return {}
    
    def save_validation_report(self, 
                               report_data: Dict[str, Any], 
                               datasheet_id: int,
                               equipment_type: str) -> str:
        """
        Save validation report to S3 or local storage.
        
        Args:
            report_data: Validation report dictionary
            datasheet_id: Database ID of the datasheet
            equipment_type: 'transformer' or 'switchgear'
        
        Returns:
            S3 key or local path of saved report
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"validation_reports/{equipment_type}/{datasheet_id}_{timestamp}.json"
        
        # Convert report to JSON
        report_json = json.dumps(report_data, indent=2, default=str)
        file_obj = io.BytesIO(report_json.encode('utf-8'))
        
        metadata = {
            'datasheet-id': str(datasheet_id),
            'equipment-type': equipment_type,
            'report-timestamp': timestamp,
            'content-type': 'application/json'
        }
        
        if self.s3_enabled:
            result = self._upload_to_s3(file_obj, s3_key, metadata)
        else:
            result = self._upload_to_local(file_obj, s3_key, metadata)
        
        return result.get('key') or result.get('path')
    
    def upload_adnoc_standard(self, 
                             file_obj, 
                             standard_type: str, 
                             voltage_class: str,
                             metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Upload ADNOC standard specification document.
        
        Args:
            file_obj: PDF/Excel file of ADNOC standard
            standard_type: 'transformer' or 'switchgear'
            voltage_class: '11kv', '33kv', etc.
            metadata: Additional metadata
        
        Returns:
            Upload result with S3 key
        """
        s3_key = f"adnoc_standards/{standard_type}/{voltage_class}.pdf"
        
        file_metadata = {
            'standard-type': standard_type,
            'voltage-class': voltage_class,
            'upload-date': datetime.now().isoformat(),
            **(metadata or {})
        }
        
        if self.s3_enabled:
            return self._upload_to_s3(file_obj, s3_key, file_metadata)
        else:
            return self._upload_to_local(file_obj, s3_key, file_metadata)
    
    def list_datasheets(self, equipment_type: str = None, prefix: str = 'datasheets/uploaded/') -> List[Dict]:
        """List all uploaded datasheets"""
        if equipment_type:
            prefix = f"{prefix}{equipment_type}/"
        
        if self.s3_enabled:
            return self._list_s3_objects(prefix)
        else:
            return self._list_local_files(prefix)
    
    def _upload_to_s3(self, file_obj, s3_key: str, metadata: Dict) -> Dict[str, Any]:
        """Upload file to S3 with metadata"""
        try:
            # Convert metadata values to strings (S3 requirement)
            str_metadata = {k: str(v) for k, v in metadata.items()}
            
            file_obj.seek(0)
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs={'Metadata': str_metadata}
            )
            
            return {
                'success': True,
                'storage': 's3',
                'bucket': self.bucket_name,
                'key': s3_key,
                's3_key': s3_key,
                'file_name': metadata.get('original-filename', s3_key.split('/')[-1]),
                'url': f"s3://{self.bucket_name}/{s3_key}",
                'metadata': metadata
            }
        except ClientError as e:
            return {
                'success': False,
                'error': str(e),
                'storage': 's3'
            }
    
    def _upload_to_local(self, file_obj, path: str, metadata: Dict) -> Dict[str, Any]:
        """Upload file to local storage"""
        try:
            local_path = os.path.join(self.local_storage_path, path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            file_obj.seek(0)
            with open(local_path, 'wb') as f:
                f.write(file_obj.read())
            
            # Save metadata as JSON
            metadata_path = f"{local_path}.meta.json"
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            return {
                'success': True,
                'storage': 'local',
                'path': local_path,
                'local_path': local_path,
                'file_name': metadata.get('original-filename', os.path.basename(local_path)),
                'metadata': metadata
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'storage': 'local'
            }
    
    def _download_from_s3(self, s3_key: str) -> Optional[bytes]:
        """Download file content from S3"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            return response['Body'].read()
        except ClientError as e:
            print(f"[S3 Download] Error: {e}")
            return None
    
    def _download_from_local(self, path: str) -> Optional[bytes]:
        """Download file content from local storage"""
        try:
            local_path = os.path.join(self.local_storage_path, path)
            if os.path.exists(local_path):
                with open(local_path, 'rb') as f:
                    return f.read()
        except Exception as e:
            print(f"[Local Download] Error: {e}")
        return None
    
    def _list_s3_objects(self, prefix: str) -> List[Dict]:
        """List objects in S3 bucket with prefix"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            
            objects = []
            for obj in response.get('Contents', []):
                objects.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'storage': 's3'
                })
            
            return objects
        except ClientError as e:
            print(f"[S3 List] Error: {e}")
            return []
    
    def _list_local_files(self, prefix: str) -> List[Dict]:
        """List files in local storage with prefix"""
        try:
            local_dir = os.path.join(self.local_storage_path, prefix)
            if not os.path.exists(local_dir):
                return []
            
            files = []
            for root, dirs, filenames in os.walk(local_dir):
                for filename in filenames:
                    if not filename.endswith('.meta.json'):
                        filepath = os.path.join(root, filename)
                        files.append({
                            'path': os.path.relpath(filepath, self.local_storage_path),
                            'size': os.path.getsize(filepath),
                            'last_modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat(),
                            'storage': 'local'
                        })
            
            return files
        except Exception as e:
            print(f"[Local List] Error: {e}")
            return []
    
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize filename for safe storage"""
        # Remove special characters
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
        return safe_name
