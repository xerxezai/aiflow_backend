"""
Procurement Document Extraction Service - Tesseract OCR + Pattern Matching
Extracts structured data from PO/PR PDF documents using free, local OCR.

This is a cost-effective alternative to GPT-4o that uses:
1. pdf2image + Tesseract OCR for text extraction
2. Regex pattern matching for field identification
3. Fuzzy string matching for vendor lookup

Author: AI Assistant
Created: June 24, 2026
"""

import re
import io
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional
from difflib import SequenceMatcher

import boto3
from django.conf import settings

logger = logging.getLogger(__name__)

# ==============================================================================
# SOFT-CODED CONFIGURATION
# All magic numbers and patterns are defined here for easy tuning
# ==============================================================================

# OCR Configuration
OCR_LANG = settings.PROCUREMENT_OCR_LANG  # Default: 'eng'
MAX_PDF_SIZE = settings.PROCUREMENT_MAX_PDF_SIZE  # Default: 10MB
VENDOR_MATCH_THRESHOLD = settings.PROCUREMENT_VENDOR_MATCH_THRESHOLD  # Default: 0.7

# Regex Patterns for Field Extraction (soft-coded for easy maintenance)
PATTERNS = {
    'po_number': [
        r'P\.?O\.?\s*(?:Number|No|#)[\s:]*([A-Z0-9\-/]+)',
        r'Purchase\s+Order[\s:]*([A-Z0-9\-/]+)',
        r'Order\s+(?:Number|No|#)[\s:]*([A-Z0-9\-/]+)',
    ],
    'pr_number': [
        r'P\.?R\.?\s*(?:Number|No|#)[\s:]*([A-Z0-9\-/]+)',
        r'Requisition[\s:]*([A-Z0-9\-/]+)',
        r'(?:Purchase\s+)?Req(?:uisition)?[\s:]*([A-Z0-9\-/]+)',
    ],
    'pr_requester_name': [
        r'Requester[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
        r'Requested\s+[Bb]y[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
        r'Originator[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
        r'Prepared\s+[Bb]y[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
    ],
    'vendor': [
        r'(?:Vendor|Supplier|Seller|Contractor)[\s:]+([A-Z][A-Za-z0-9\s&\.,\-]+?)(?:\n|$)',
        r'To[\s:]+([A-Z][A-Za-z0-9\s&\.,\-]+?)(?:\n|Address:|P\.?O\.?|$)',
        r'Company[\s:]+([A-Z][A-Za-z0-9\s&\.,\-]+?)(?:\n|$)',
    ],
    'date': [
        r'(?:PO\s+)?(?:Date|Dated)[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(?:PO\s+)?(?:Date|Dated)[\s:]*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
        r'Issue\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ],
    'start_date': [
        r'(?:Service\s+)?Start\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Commencement\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Begin(?:ning)?\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ],
    'end_date': [
        r'(?:Service\s+)?End\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Completion\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Expiry\s+Date[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    ],
    'delivery_date': [
        r'(?:Delivery|Expected|Required)\s+(?:Date|By)[\s:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(?:Delivery|Expected|Required)\s+(?:Date|By)[\s:]*(\d{4}[/-]\d{1,2}[/-]\d{1,2})',
    ],
    'total': [
        r'(?:Total|Grand\s+Total|Amount|PO\s+Value)[\s:]*[\$£€]?\s*([\d,]+\.?\d*)',
        r'(?:Total|Amount)\s+(?:USD|AED|EUR|GBP)[\s:]*(\d+[\d,]*\.?\d*)',
    ],
    'budget': [
        r'Budget[\s:]*[\$£€]?\s*([\d,]+\.?\d*)',
        r'Allocated\s+(?:Budget|Amount)[\s:]*[\$£€]?\s*([\d,]+\.?\d*)',
    ],
    'currency': [
        r'\b(USD|AED|EUR|GBP|SAR|QAR|KWD|INR|CNY)\b',
        r'Currency[\s:]*([A-Z]{3})',
    ],
    'payment_terms': [
        r'Payment\s+Terms[\s:]*(.{5,200}?)(?:\n\n|\n[A-Z]|$)',
        r'Terms\s+of\s+Payment[\s:]*(.{5,200}?)(?:\n\n|\n[A-Z]|$)',
        r'(?:Net|Within)\s+(\d+\s+days?)',
    ],
    'delivery_terms': [
        r'Delivery\s+Terms[\s:]*(.{3,100}?)(?:\n|$)',
        r'Incoterms?[\s:]*([A-Z]{3})',
        r'\b(EXW|FOB|CIF|DAP|DDP|FCA|CPT|CIP)\b',
    ],
    'project_number': [
        r'Project\s+(?:Number|No|Code|#|ID)[\s:]*([A-Z0-9\-/]+)',
        r'Job\s+(?:Number|No|Code|#)[\s:]*([A-Z0-9\-/]+)',
        r'Contract\s+(?:Number|No|#)[\s:]*([A-Z0-9\-/]+)',
    ],
    'project_manager': [
        r'Project\s+Manager[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
        r'PM[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
        r'Manager[\s:]+([A-Z][a-zA-Z\s\.]{2,50})',
    ],
    'service_description': [
        r'(?:Scope\s+of\s+)?(?:Service|Work|Services)[\s:]*(.{10,500}?)(?:\n\n|\n[A-Z]|Items?:|Description:)',
        r'Description[\s:]*(.{10,500}?)(?:\n\n|\n[A-Z]|Items?:|Total:)',
    ],
    'payment_milestones': [
        r'(?:Milestone|Payment\s+Schedule)[\s:]*(.{20,1000}?)(?:\n\n|Total:)',
    ],
}

# Currency symbols to currency code mapping
CURRENCY_SYMBOLS = {
    '$': 'USD',
    '£': 'GBP',
    '€': 'EUR',
    'د.إ': 'AED',
    'ر.س': 'SAR',
}


# ==============================================================================
# OCR TEXT EXTRACTION
# ==============================================================================

def extract_text_from_pdf_tesseract(pdf_bytes: bytes) -> str:
    """
    Extract text from PDF using pdf2image + Tesseract OCR.
    
    Triple-redundant fallback:
    1. pdf2image + Tesseract (best quality)
    2. PyMuPDF text extraction (faster)
    3. PyPDF2 text extraction (last resort)
    
    Args:
        pdf_bytes: Raw PDF file bytes
        
    Returns:
        Extracted text string
        
    Raises:
        Exception: If all extraction methods fail
    """
    # Method 1: pdf2image + Tesseract OCR (best for scanned PDFs)
    try:
        import pdf2image
        import pytesseract
        
        logger.info('[Tesseract] Converting PDF to images...')
        images = pdf2image.convert_from_bytes(pdf_bytes, dpi=300, fmt='png')
        
        logger.info(f'[Tesseract] Running OCR on {len(images)} page(s)...')
        full_text = ''
        for i, img in enumerate(images, 1):
            page_text = pytesseract.image_to_string(img, lang=OCR_LANG)
            full_text += f'\n--- Page {i} ---\n{page_text}\n'
            logger.debug(f'[Tesseract] Page {i}: extracted {len(page_text)} chars')
        
        logger.info(f'[Tesseract] ✅ Extracted {len(full_text)} total characters')
        return full_text
    except ImportError as e:
        logger.warning(f'[Tesseract] pdf2image or pytesseract not installed: {e}')
    except Exception as e:
        logger.warning(f'[Tesseract] OCR method failed: {e}')
    
    # Method 2: PyMuPDF text extraction (faster but worse for scanned PDFs)
    try:
        import fitz  # PyMuPDF
        logger.info('[PyMuPDF] Attempting text extraction...')
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype='pdf')
        full_text = ''
        for page_num in range(doc.page_count):
            page = doc[page_num]
            page_text = page.get_text()
            full_text += f'\n--- Page {page_num + 1} ---\n{page_text}\n'
        doc.close()
        logger.info(f'[PyMuPDF] ✅ Extracted {len(full_text)} characters')
        return full_text
    except ImportError:
        logger.warning('[PyMuPDF] PyMuPDF not installed')
    except Exception as e:
        logger.warning(f'[PyMuPDF] Extraction failed: {e}')
    
    # Method 3: PyPDF2 (last resort)
    try:
        import PyPDF2
        logger.info('[PyPDF2] Attempting text extraction (last resort)...')
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ''
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            page_text = page.extract_text() or ''
            full_text += f'\n--- Page {page_num + 1} ---\n{page_text}\n'
        logger.info(f'[PyPDF2] ✅ Extracted {len(full_text)} characters')
        return full_text
    except ImportError:
        logger.warning('[PyPDF2] PyPDF2 not installed')
    except Exception as e:
        logger.warning(f'[PyPDF2] Extraction failed: {e}')
    
    raise Exception('All PDF extraction methods failed. Install pdf2image, pytesseract, PyMuPDF, or PyPDF2.')


# ==============================================================================
# PATTERN-BASED FIELD EXTRACTION
# ==============================================================================

def extract_field(text: str, patterns: List[str]) -> Optional[str]:
    """
    Extract a field from text using multiple regex patterns.
    Tries each pattern in order until one matches.
    
    Args:
        text: Full OCR'd text
        patterns: List of regex patterns to try
        
    Returns:
        Extracted value or None
    """
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip()
            logger.debug(f'[Pattern] Matched "{pattern[:50]}..." → {value[:50]}')
            return value
    return None


def parse_date(date_str: str) -> Optional[str]:
    """
    Parse various date formats to ISO 8601 (YYYY-MM-DD).
    Handles: DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD, DD-MM-YYYY
    
    Args:
        date_str: Date string in various formats
        
    Returns:
        ISO 8601 date string or None if parsing fails
    """
    if not date_str:
        return None
    
    # Try multiple date formats
    date_formats = [
        '%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d', '%d-%m-%Y',
        '%d/%m/%y', '%m/%d/%y', '%Y/%m/%d',
    ]
    
    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    logger.warning(f'[DateParse] Could not parse date: {date_str}')
    return None


def parse_currency_amount(amount_str: str) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Parse currency amount string to Decimal and currency code.
    
    Args:
        amount_str: String like "$1,234.56", "1234.56 USD", "€500"
        
    Returns:
        Tuple of (amount as Decimal, currency code) or (None, None)
    """
    if not amount_str:
        return None, None
    
    # Extract currency symbol/code
    currency = None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in amount_str:
            currency = code
            amount_str = amount_str.replace(symbol, '').strip()
            break
    
    # Extract currency code
    currency_match = re.search(r'\b(USD|AED|EUR|GBP|SAR|QAR|KWD)\b', amount_str, re.IGNORECASE)
    if currency_match:
        currency = currency_match.group(1).upper()
        amount_str = amount_str.replace(currency, '').strip()
    
    # Clean and parse amount
    amount_str = re.sub(r'[^\d.]', '', amount_str)  # Remove everything except digits and decimal
    try:
        amount = Decimal(amount_str)
        return amount, currency
    except Exception as e:
        logger.warning(f'[CurrencyParse] Could not parse amount: {amount_str} - {e}')
        return None, currency


def fuzzy_match_vendor(vendor_name: str, vendor_list: List[Dict[str, Any]]) -> Optional[str]:
    """
    Fuzzy match extracted vendor name to database vendors using similarity ratio.
    
    Args:
        vendor_name: Extracted vendor name from PDF
        vendor_list: List of vendor dicts with 'id' and 'name' keys
        
    Returns:
        Best matching vendor ID or None if no good match
    """
    if not vendor_name or not vendor_list:
        return None
    
    best_match = None
    best_score = 0.0
    
    for vendor in vendor_list:
        db_name = vendor.get('name', '').lower()
        extracted_name = vendor_name.lower()
        
        # Calculate similarity using SequenceMatcher
        score = SequenceMatcher(None, extracted_name, db_name).ratio()
        
        if score > best_score:
            best_score = score
            best_match = vendor.get('id')
    
    if best_score >= VENDOR_MATCH_THRESHOLD:
        logger.info(f'[VendorMatch] "{vendor_name}" → {best_match} (score: {best_score:.2f})')
        return str(best_match)
    
    logger.info(f'[VendorMatch] No match for "{vendor_name}" (best score: {best_score:.2f} < {VENDOR_MATCH_THRESHOLD})')
    return None


def parse_payment_milestones(milestones_text: str) -> List[Dict[str, Any]]:
    """
    Parse payment milestones from text like:
    "30% on advance, 40% on delivery, 30% on completion"
    "50% advance, 25% after 30 days, 25% after 60 days"
    
    Args:
        milestones_text: Text describing payment schedule
        
    Returns:
        List of milestone dicts with keys: description, percentage
    """
    if not milestones_text:
        return []
    
    milestones = []
    
    # Pattern: percentage + description
    # e.g., "30% on advance", "50% advance", "25% after delivery"
    milestone_pattern = r'(\d+)%\s*(?:on\s+|after\s+|upon\s+)?([a-zA-Z\s\d]+?)(?:,|\.|;|$)'
    
    matches = re.finditer(milestone_pattern, milestones_text, re.IGNORECASE)
    for match in matches:
        percentage = int(match.group(1))
        description = match.group(2).strip()
        
        milestones.append({
            'description': description,
            'percentage': percentage,
        })
        logger.debug(f'[PaymentMilestone] {percentage}% - {description}')
    
    logger.info(f'[PaymentMilestones] Extracted {len(milestones)} milestones')
    return milestones



# ==============================================================================
# LINE ITEMS EXTRACTION (TABLE PARSING)
# ==============================================================================

def extract_line_items(text: str) -> List[Dict[str, Any]]:
    """
    Extract line items from invoice/PO table.
    Looks for patterns like:
      Item    Description           Qty    Unit Price    Total
      1       Widget A              5      $10.00        $50.00
    
    Args:
        text: Full OCR'd text
        
    Returns:
        List of item dicts with keys: description, quantity, unit_price, total
    """
    items = []
    
    # Look for table rows with item data
    # Pattern: optional item number, description, quantity, price, total
    item_pattern = r'(\d+\.?\s+)?([A-Za-z][\w\s\-,\.]+?)\s+(\d+(?:\.\d+)?)\s+(?:[\$£€]?\s*)(\d+(?:,\d{3})*(?:\.\d{2})?)\s+(?:[\$£€]?\s*)(\d+(?:,\d{3})*(?:\.\d{2})?)'
    
    matches = re.finditer(item_pattern, text, re.MULTILINE)
    for match in matches:
        item_no, desc, qty, unit_price, total = match.groups()
        
        # Clean up numeric values
        qty = float(qty)
        unit_price = float(unit_price.replace(',', ''))
        total = float(total.replace(',', ''))
        
        items.append({
            'description': desc.strip(),
            'quantity': qty,
            'unit': 'unit',
            'unit_price': unit_price,
            'total': total,
        })
        logger.debug(f'[LineItem] Extracted: {desc[:30]} | {qty} x ${unit_price} = ${total}')
    
    logger.info(f'[LineItems] Extracted {len(items)} items')
    return items


# ==============================================================================
# S3 UPLOAD
# ==============================================================================

def upload_to_s3(pdf_bytes: bytes, original_filename: str, user_id: str) -> Dict[str, str]:
    """
    Upload PDF to S3 and return storage metadata.
    Path: procurement/po_uploads/YYYY/MM/DD/<user_id>/<filename>
    
    Uses soft-coded credentials from Django settings (which loads from env vars).
    Falls back to boto3's default credential chain if settings are empty.
    
    Args:
        pdf_bytes: PDF file bytes
        original_filename: Original filename
        user_id: User ID for path organization
        
    Returns:
        Dict with keys: s3_key, s3_url
        
    Raises:
        Exception: If S3 upload fails
    """
    now = datetime.now()
    date_path = now.strftime('%Y/%m/%d')
    s3_key = f'procurement/po_uploads/{date_path}/{user_id}/{original_filename}'
    
    try:
        # Soft-coded S3 configuration from Django settings
        # Credentials loaded from environment variables, never hardcoded
        aws_access_key = getattr(settings, 'AWS_ACCESS_KEY_ID', '')
        aws_secret_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '')
        aws_region = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
        aws_bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
        
        if not aws_bucket:
            raise Exception('AWS_STORAGE_BUCKET_NAME not configured in settings')
        
        # Create S3 client with explicit credentials if available,
        # otherwise let boto3 use its default credential chain
        # (env vars, IAM role, ~/.aws/credentials)
        client_kwargs = {'region_name': aws_region}
        if aws_access_key and aws_secret_key:
            client_kwargs['aws_access_key_id'] = aws_access_key
            client_kwargs['aws_secret_access_key'] = aws_secret_key
            logger.debug('[S3] Using explicit credentials from settings')
        else:
            logger.debug('[S3] Using boto3 default credential chain')
        
        s3_client = boto3.client('s3', **client_kwargs)
        
        s3_client.put_object(
            Bucket=aws_bucket,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType='application/pdf',
            Metadata={
                'original_filename': original_filename,
                'user_id': user_id,
                'upload_date': now.isoformat(),
            }
        )
        
        s3_url = f'https://{aws_bucket}.s3.{aws_region}.amazonaws.com/{s3_key}'
        logger.info(f'[S3] ✅ Uploaded to: {s3_key}')
        
        return {'s3_key': s3_key, 's3_url': s3_url}
    except Exception as e:
        logger.error(f'[S3] ❌ Upload failed: {e}', exc_info=True)
        raise


# ==============================================================================
# MAIN EXTRACTION PIPELINE
# ==============================================================================

def extract_po_from_pdf_tesseract(
    pdf_bytes: bytes,
    original_filename: str,
    user_id: str,
    vendor_list: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Main extraction pipeline using Tesseract OCR + regex patterns.
    
    Pipeline:
    1. Upload PDF to S3
    2. Extract text using OCR
    3. Apply regex patterns to find fields
    4. Parse and normalize extracted data
    5. Fuzzy match vendor if possible
    6. Return structured result
    
    Args:
        pdf_bytes: PDF file bytes
        original_filename: Original filename
        user_id: User ID
        vendor_list: Optional list of vendor dicts for auto-matching
        
    Returns:
        Dict with keys:
            - success: bool
            - extraction_status: 'completed' | 'failed'
            - extracted_data: dict with all extracted fields
            - s3_key: S3 storage key
            - s3_url: S3 URL
            - error: error message if failed
    """
    logger.info(f'[TesseractExtractor] Starting extraction for: {original_filename}')
    
    try:
        # Step 1: Upload to S3
        s3_result = upload_to_s3(pdf_bytes, original_filename, user_id)
        
        # Step 2: Extract text
        text = extract_text_from_pdf_tesseract(pdf_bytes)
        
        if not text or len(text) < 50:
            return {
                'success': False,
                'extraction_status': 'failed',
                'error': 'OCR produced insufficient text. PDF may be empty or corrupted.',
                's3_key': s3_result['s3_key'],
                's3_url': s3_result['s3_url'],
                'extracted_data': {},
            }
        
        # Step 3: Extract fields using patterns
        extracted = {}
        
        # Document type (PO vs PR)
        has_po = bool(extract_field(text, PATTERNS['po_number']))
        has_pr = bool(extract_field(text, PATTERNS['pr_number']))
        extracted['document_type'] = 'PO' if has_po else 'PR' if has_pr else 'unknown'
        
        # Basic identification fields
        extracted['po_number'] = extract_field(text, PATTERNS['po_number']) or ''
        extracted['pr_reference'] = extract_field(text, PATTERNS['pr_number']) or ''
        extracted['pr_requester_name'] = extract_field(text, PATTERNS['pr_requester_name']) or ''
        
        # Vendor/Supplier details
        extracted['vendor_name'] = extract_field(text, PATTERNS['vendor']) or ''
        
        # Project details (soft-coded extraction)
        extracted['project_number'] = extract_field(text, PATTERNS['project_number']) or ''
        extracted['project_manager'] = extract_field(text, PATTERNS['project_manager']) or ''
        
        # Service/Work description
        service_desc = extract_field(text, PATTERNS['service_description'])
        extracted['description'] = service_desc if service_desc else ''
        extracted['title'] = service_desc[:200] if service_desc else ''  # Use first 200 chars as title
        
        # Dates (comprehensive date extraction)
        date_str = extract_field(text, PATTERNS['date'])
        extracted['po_date'] = parse_date(date_str) if date_str else None
        
        start_date_str = extract_field(text, PATTERNS['start_date'])
        extracted['start_date'] = parse_date(start_date_str) if start_date_str else None
        
        end_date_str = extract_field(text, PATTERNS['end_date'])
        extracted['end_date'] = parse_date(end_date_str) if end_date_str else None
        
        delivery_date_str = extract_field(text, PATTERNS['delivery_date'])
        extracted['expected_delivery'] = parse_date(delivery_date_str) if delivery_date_str else None
        
        # Financial fields (soft-coded currency and amount parsing)
        total_str = extract_field(text, PATTERNS['total'])
        amount, currency_from_amount = parse_currency_amount(total_str) if total_str else (None, None)
        
        budget_str = extract_field(text, PATTERNS['budget'])
        budget_amount, budget_currency = parse_currency_amount(budget_str) if budget_str else (None, None)
        
        currency_code = extract_field(text, PATTERNS['currency'])
        extracted['currency'] = currency_code or currency_from_amount or budget_currency or 'USD'
        extracted['total_amount'] = str(amount) if amount else ''
        extracted['budget'] = str(budget_amount) if budget_amount else ''
        
        # Payment & Delivery terms
        extracted['payment_terms'] = extract_field(text, PATTERNS['payment_terms']) or ''
        extracted['delivery_terms'] = extract_field(text, PATTERNS['delivery_terms']) or ''
        
        # Payment milestones (parse if found)
        milestones_text = extract_field(text, PATTERNS['payment_milestones'])
        extracted['payment_milestones'] = parse_payment_milestones(milestones_text) if milestones_text else []
        
        # Line items (table extraction)
        extracted['items'] = extract_line_items(text)
        
        # Step 4: Vendor matching (if vendor list provided)
        if vendor_list and extracted['vendor_name']:
            vendor_id = fuzzy_match_vendor(extracted['vendor_name'], vendor_list)
            if vendor_id:
                extracted['vendor'] = vendor_id
        
        logger.info(f'[TesseractExtractor] ✅ Extraction completed: {extracted.get("po_number", "Unknown")}')
        
        return {
            'success': True,
            'extraction_status': 'completed',
            'extracted_data': extracted,
            's3_key': s3_result['s3_key'],
            's3_url': s3_result['s3_url'],
        }
        
    except Exception as e:
        logger.error(f'[TesseractExtractor] ❌ Extraction failed: {e}', exc_info=True)
        return {
            'success': False,
            'extraction_status': 'failed',
            'error': str(e),
            'extracted_data': {},
            's3_key': '',
            's3_url': '',
        }
