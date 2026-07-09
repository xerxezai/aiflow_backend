"""
Payroll Intelligence — S3 Storage Backends
==========================================
Uses the existing RADAI AWS bucket (AWS_STORAGE_BUCKET_NAME env var)
with dedicated payroll/ sub-prefixes so payroll documents are logically
separated from other media without needing a new bucket.

Activated only when USE_S3=True in the environment. Falls back to the
default Django file storage (local media/) when S3 is not configured,
so local development works without AWS credentials.
"""
from decouple import config

# Guard — only import boto3 storage if django-storages is installed
try:
    from storages.backends.s3boto3 import S3Boto3Storage

    # Soft-coded prefix — override per-environment without touching code.
    _SLIPS_PREFIX     = config('PAYROLL_S3_PREFIX',          default='payroll/slips')
    _DOCUMENTS_PREFIX = config('PAYROLL_DOCS_S3_PREFIX',     default='payroll/documents')
    _EXPORTS_PREFIX   = config('PAYROLL_EXPORTS_S3_PREFIX',  default='payroll/exports')

    class PayrollSlipStorage(S3Boto3Storage):
        """
        Storage backend for payslip PDF files.
        Files are private (presigned URLs, expire after 1 hour).
        """
        location          = _SLIPS_PREFIX
        file_overwrite    = False
        default_acl       = 'private'
        querystring_auth  = True
        querystring_expire = 3600   # 1 hour — soft-coded; extend if needed

    class PayrollDocumentStorage(S3Boto3Storage):
        """
        Storage backend for supporting payroll documents
        (contracts, letters, bank confirmation PDFs).
        """
        location          = _DOCUMENTS_PREFIX
        file_overwrite    = False
        default_acl       = 'private'
        querystring_auth  = True
        querystring_expire = 3600

    class PayrollExportStorage(S3Boto3Storage):
        """
        Storage backend for Excel/CSV payroll exports.
        Slightly longer expiry (4 hours) since finance teams
        often share export links.
        """
        location          = _EXPORTS_PREFIX
        file_overwrite    = False
        default_acl       = 'private'
        querystring_auth  = True
        querystring_expire = 14400  # 4 hours

    S3_AVAILABLE = True

except ImportError:
    # django-storages not installed — use Django default storage
    PayrollSlipStorage     = None
    PayrollDocumentStorage = None
    PayrollExportStorage   = None
    S3_AVAILABLE           = False
