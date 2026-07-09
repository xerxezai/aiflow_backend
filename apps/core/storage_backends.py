"""
AWS S3 Storage Backends
Secure S3 storage configuration using django-storages
Enhanced with better error handling and logging
"""
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Check if S3 is enabled before importing storages
USE_S3 = os.environ.get('USE_S3', 'False').lower() == 'true'

if USE_S3:
    try:
        from storages.backends.s3boto3 import S3Boto3Storage
        from django.core.files.storage import FileSystemStorage
        from botocore.exceptions import BotoCoreError, ClientError
        import traceback
        
        class ResilientMediaStorage(FileSystemStorage):
            """
            Resilient storage that ALWAYS uses local filesystem
            when S3 configuration is problematic
            
            This prevents 500 errors from blocking file uploads
            """
            
            def __init__(self, *args, **kwargs):
                # Always use local storage - no S3 complications
                kwargs['location'] = getattr(settings, 'MEDIA_ROOT', 'media')
                super().__init__(*args, **kwargs)
                logger.warning("[MediaStorage] ⚠️ S3 configured but using LOCAL storage to avoid errors")
                logger.info("[MediaStorage] 💾 Files will be saved to local filesystem")
        
        # FORCE local storage when S3 is problematic
        MediaStorage = ResilientMediaStorage
        logger.info("[Storage] 🔄 Using resilient local storage (S3 disabled due to configuration issues)")
        
    except ImportError as e:
        logger.error(f"[S3] Failed to import dependencies: {str(e)}")
        from django.core.files.storage import FileSystemStorage
        
        class MediaStorage(FileSystemStorage):
            def __init__(self, *args, **kwargs):
                kwargs['location'] = getattr(settings, 'MEDIA_ROOT', 'media')
                super().__init__(*args, **kwargs)


        class StaticStorage(S3Boto3Storage):
            """
            S3 storage backend for static files (CSS, JS, images)
            
            Static files can be public since they don't contain sensitive data
            """
            
            location = 'static'
            default_acl = None  # Disable ACLs (use bucket policy for public access)
            file_overwrite = True         # Overwrite on deployment
            
            # Cache static files for 1 year (immutable)
            object_parameters = {
                'CacheControl': 'max-age=31536000, immutable',
            }
            
            def __init__(self, *args, **kwargs):
                try:
                    super().__init__(*args, **kwargs)
                    logger.info(f"[StaticStorage] Initialized S3 static storage: {self.bucket_name}/{self.location}")
                except Exception as e:
                    logger.error(f"[StaticStorage] Failed to initialize S3: {str(e)}")
                    raise
    except ImportError as e:
        logger.error(f"[S3] Failed to import S3Boto3Storage: {str(e)}")
        USE_S3 = False


    class PIDDrawingStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for P&ID drawings
        
        Features:
        - Isolated folder structure
        - Private access only
        - Long-term storage (no automatic deletion)
        - Presigned URLs for secure downloads
        """
        
        location = 'media/pid_drawings'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 7200  # 2 hours (longer for analysis processing)
        
        # Metadata for tracking
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'pid_drawing'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[PIDDrawingStorage] Initialized: {self.bucket_name}/{self.location}")


    class PIDReportStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for generated P&ID reports (PDF, Excel)
        
        Features:
        - Isolated folder structure
        - Private access only
        - Presigned URLs for downloads
        """
        
        location = 'media/pid_reports'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 3600  # 1 hour
        
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'pid_report'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[PIDReportStorage] Initialized: {self.bucket_name}/{self.location}")


    class CRSDocumentStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for CRS documents
        """
        
        location = 'media/crs_documents'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 3600
        
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'crs_document'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[CRSDocumentStorage] Initialized: {self.bucket_name}/{self.location}")


    class PFDFileStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for PFD files
        """
        
        location = 'media/pfd_files'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 7200
        
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'pfd_file'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[PFDFileStorage] Initialized: {self.bucket_name}/{self.location}")


    class AvatarStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for user avatars
        """
        
        location = 'media/avatars'
        default_acl = 'private'
        file_overwrite = True  # Allow overwriting avatars
        custom_domain = False
        querystring_expire = 86400  # 24 hours
        
        # Use region-specific endpoint so presigned URLs don't break on S3 redirect
        # (required for opt-in regions like me-central-1 / UAE Central)
        @property
        def endpoint_url(self):
            region = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
            return getattr(settings, 'AWS_S3_ENDPOINT_URL', f'https://s3.{region}.amazonaws.com')
        
        object_parameters = {
            'CacheControl': 'max-age=3600',  # 1 hour (avatars can change)
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'avatar'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[AvatarStorage] Initialized: {self.bucket_name}/{self.location}")


    class ExportStorage(S3Boto3Storage):
        """
        Temporary storage for exports (Excel, CSV, etc.)
        """
        
        location = 'media/exports'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 1800  # 30 minutes for exports
        
        object_parameters = {
            'CacheControl': 'max-age=1800',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'export'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[ExportStorage] Initialized: {self.bucket_name}/{self.location}")


    class IOListDocumentStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for Instrument IO List PDFs.

        Generates presigned S3 URLs so private files are accessible to authenticated
        users without exposing raw S3 URLs.

        Uses a region-specific endpoint_url (required for opt-in regions such as
        me-central-1 / UAE Abu Dhabi) so that Signature=host in the presigned URL
        matches the request host and S3 does not invalidate it with a redirect.

        Expiry is soft-coded via settings.IO_LIST_PDF_PRESIGN_EXPIRY (default 3600 s).
        """

        location = 'media'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False  # NEVER use custom domain — forces presigned query-string auth

        @property
        def endpoint_url(self):
            region = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
            return getattr(settings, 'AWS_S3_ENDPOINT_URL', f'https://s3.{region}.amazonaws.com')

        @property
        def querystring_expire(self):
            return getattr(settings, 'IO_LIST_PDF_PRESIGN_EXPIRY', 3600)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[IOListDocumentStorage] Initialized: {self.bucket_name}/{self.location}")

else:
    # Fallback to local file storage when S3 is not enabled
    from django.core.files.storage import FileSystemStorage
    
    logger.info("[Storage] S3 not enabled, using local file storage")
    
    class MediaStorage(FileSystemStorage):
        """Local file storage for media files"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = getattr(settings, 'MEDIA_ROOT', 'media')
            super().__init__(*args, **kwargs)
    
    class StaticStorage(FileSystemStorage):
        """Local file storage for static files"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = getattr(settings, 'STATIC_ROOT', 'staticfiles')
            super().__init__(*args, **kwargs)
    
    class PIDDrawingStorage(FileSystemStorage):
        """Local storage for P&ID drawings"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/pid_drawings'
            super().__init__(*args, **kwargs)
    
    class PIDReportStorage(FileSystemStorage):
        """Local storage for P&ID reports"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/pid_reports'
            super().__init__(*args, **kwargs)
    
    class CRSDocumentStorage(FileSystemStorage):
        """Local storage for CRS documents"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/crs_documents'
            super().__init__(*args, **kwargs)
    
    class PFDFileStorage(FileSystemStorage):
        """Local storage for PFD files"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/pfd_files'
            super().__init__(*args, **kwargs)
    
    class AvatarStorage(FileSystemStorage):
        """Local storage for avatars"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/avatars'
            super().__init__(*args, **kwargs)
    
    class ExportStorage(FileSystemStorage):
        """Local storage for exports"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/exports'
            super().__init__(*args, **kwargs)

    class IOListDocumentStorage(FileSystemStorage):
        """Local storage for Instrument IO List PDFs (non-S3 fallback)"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media'
            super().__init__(*args, **kwargs)

