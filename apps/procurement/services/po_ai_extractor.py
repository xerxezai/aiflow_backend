"""
AI-Powered Purchase Order / Purchase Requisition Extractor
============================================================
Uses pdfplumber (primary) + PyMuPDF (fallback) for text extraction,
then calls GPT-4o to parse the raw text into a structured JSON payload
that maps directly onto the PurchaseOrder model fields.

EXTRACTION METHOD SELECTION (soft-coded):
- Configure via PROCUREMENT_EXTRACTION_METHOD in settings.py or env var
- 'tesseract' = Free OCR + regex (default, cost-effective)
- 'openai'    = GPT-4o structured extraction (higher accuracy, costs money)
"""

import io
import json
import logging
import os
from django.conf import settings

logger = logging.getLogger(__name__)


# ==============================================================================
# SMART EXTRACTOR ROUTER (SOFT-CODED METHOD SELECTION)
# ==============================================================================

def extract_po_from_pdf(pdf_bytes: bytes, original_filename: str, user_id: str):
    """
    Main entry point for PO/PR extraction.
    Routes to the configured extraction method (Tesseract or OpenAI).
    
    Args:
        pdf_bytes: Raw PDF file bytes
        original_filename: Original filename
        user_id: User ID for S3 path organization
        
    Returns:
        Dict with extraction results (schema matches both extractors)
    """
    method = getattr(settings, 'PROCUREMENT_EXTRACTION_METHOD', 'tesseract').lower()
    
    logger.info(f'[POExtractor] Using method: {method} (file: {original_filename})')
    
    if method == 'openai':
        return extract_po_from_pdf_openai(pdf_bytes, original_filename, user_id)
    elif method == 'tesseract':
        from .po_tesseract_extractor import extract_po_from_pdf_tesseract
        # Get vendor list for auto-matching (import here to avoid circular dependency)
        try:
            from apps.procurement.models import Vendor
            vendor_list = list(Vendor.objects.filter(status='active').values('id', 'name'))
        except Exception as e:
            logger.warning(f'[POExtractor] Could not load vendors for matching: {e}')
            vendor_list = []
        
        return extract_po_from_pdf_tesseract(pdf_bytes, original_filename, user_id, vendor_list)
    else:
        logger.error(f'[POExtractor] Unknown extraction method: {method}')
        return {
            'success': False,
            'extraction_status': 'failed',
            'error': f'Unknown extraction method: {method}. Use "tesseract" or "openai".',
            'extracted_data': {},
            's3_key': '',
            's3_url': '',
        }


# ==============================================================================
# OPENAI GPT-4o EXTRACTION (LEGACY METHOD, COSTS MONEY)
# ==============================================================================

# ── Extraction schema description sent to GPT-4o ────────────────────────────
SYSTEM_PROMPT = (
    "You are an expert procurement document analyst specialising in Oil & Gas "
    "engineering procurement (Purchase Orders, Purchase Requisitions). "
    "Extract all available data from the supplied document text and return ONLY "
    "a valid JSON object — no markdown fences, no commentary."
)

EXTRACTION_PROMPT_TEMPLATE = """
Analyse the following procurement document and extract every available field.

Return a single JSON object with EXACTLY these keys (use null for missing values,
numbers as numbers, ISO-8601 dates as strings "YYYY-MM-DD"):

{{
  "document_type":          "purchase_order" | "purchase_requisition" | "unknown",
  "po_number":              "...",
  "pr_number":              "...",
  "pr_requester_name":      "...",
  "po_date":                "YYYY-MM-DD",
  "title":                  "...",
  "description":            "...",
  "scope_of_services":      "...",
  "vendor_name":            "...",
  "vendor_address":         "...",
  "vendor_contact_person":  "...",
  "vendor_email":           "...",
  "vendor_phone":           "...",
  "vendor_license_no":      "...",
  "vendor_vat_number":      "...",
  "buyer_name":             "...",
  "buyer_reference":        "...",
  "project_number":         "...",
  "project_code":           "...",
  "project_manager":        "...",
  "project_department":     "...",
  "end_client":             "...",
  "budget":                 0.0,
  "category":               "engineering_services" | "maintenance_services" | "rotating_equipment" | "piping_materials" | "instrumentation" | "valves_fittings" | "electrical_materials" | "safety_equipment" | "chemicals" | "spare_parts" | "other",
  "items": [
    {{
      "sno":         1,
      "description": "...",
      "quantity":    1.0,
      "unit":        "day" | "pieces" | "unit" | "meters" | "set" | "lot" | "...",
      "unit_price":  0.0,
      "discount":    0.0,
      "total":       0.0
    }}
  ],
  "currency":          "USD" | "AED" | "EUR" | "GBP" | "SAR" | "QAR" | "...",
  "total_amount":      0.0,
  "tax_rate":          5.0,
  "tax_amount":        0.0,
  "total_sum":         0.0,
  "payment_terms":     "...",
  "payment_mode":      "Bank Transfer" | "Cheque" | "...",
  "payment_milestones": [
    {{"description": "Advance payment", "percentage": 30}},
    {{"description": "On delivery", "percentage": 40}},
    {{"description": "After completion", "percentage": 30}}
  ],
  "delivery_terms":    "EXW" | "FOB" | "CIF" | "DAP" | "DDP" | "...",
  "start_date":        "YYYY-MM-DD",
  "end_date":          "YYYY-MM-DD",
  "expected_delivery": "YYYY-MM-DD",
  "issued_by":         "...",
  "approved_by": [
    {{"role": "PM", "name": "..."}}
  ],
  "contact_persons": [
    {{"role": "...", "name": "...", "email": "...", "phone": "..."}}
  ],
  "special_notes":         "...",
  "terms_and_conditions":  "..."
}}

--- DOCUMENT TEXT START ---
{text}
--- DOCUMENT TEXT END ---
""".strip()

# Max characters sent to GPT-4o (≈ 30 k tokens leaves room for response)
MAX_TEXT_CHARS = 24_000


def _extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    """Extract all text from PDF bytes using pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text)
        return "\n\n".join(pages).strip()
    except Exception as exc:
        logger.warning("[POExtractor] pdfplumber failed: %s", exc)
        return ""


def _extract_text_pymupdf(pdf_bytes: bytes) -> str:
    """Fallback text extraction using PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = [doc[i].get_text() for i in range(len(doc))]
        doc.close()
        return "\n\n".join(pages).strip()
    except Exception as exc:
        logger.warning("[POExtractor] PyMuPDF fallback failed: %s", exc)
        return ""


def _extract_text_pypdf2(pdf_bytes: bytes) -> str:
    """Last-resort extraction using PyPDF2."""
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    except Exception as exc:
        logger.warning("[POExtractor] PyPDF2 fallback failed: %s", exc)
        return ""


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract raw text from PDF using the best available library.
    Tries pdfplumber → PyMuPDF → PyPDF2.
    """
    text = _extract_text_pdfplumber(pdf_bytes)
    if len(text) > 50:
        return text

    text = _extract_text_pymupdf(pdf_bytes)
    if len(text) > 50:
        return text

    return _extract_text_pypdf2(pdf_bytes)


def call_gpt4o_extraction(raw_text: str) -> dict:
    """
    Send extracted PDF text to GPT-4o and parse the structured JSON response.
    Returns the parsed dict or raises ValueError on failure.
    """
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)

    # Trim to max chars to stay within token limits
    trimmed_text = raw_text[:MAX_TEXT_CHARS]

    prompt = EXTRACTION_PROMPT_TEMPLATE.format(text=trimmed_text)

    logger.info("[POExtractor] Sending %d chars to GPT-4o", len(trimmed_text))

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )

    raw_json = response.choices[0].message.content
    logger.info("[POExtractor] GPT-4o raw response length: %d chars", len(raw_json or ""))

    try:
        return json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GPT-4o returned invalid JSON: {exc}") from exc


def upload_to_s3(pdf_bytes: bytes, original_filename: str, user_id: str) -> dict:
    """
    Upload the PDF to S3 under procurement/po_uploads/ and return
    {'s3_key': str, 's3_url': str, 'success': bool}.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
        from django.utils import timezone

        bucket = os.environ.get("AWS_STORAGE_BUCKET_NAME", "")
        region = os.environ.get("AWS_S3_REGION_NAME", "us-east-1")

        if not bucket:
            logger.warning("[POExtractor] S3 bucket not configured, skipping upload")
            return {"success": False, "s3_key": "", "s3_url": ""}

        s3 = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

        now = timezone.now()
        safe_name = original_filename.replace(" ", "_")
        s3_key = (
            f"procurement/po_uploads/{now.strftime('%Y/%m/%d')}/"
            f"{user_id}/{now.strftime('%H%M%S')}_{safe_name}"
        )

        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            Metadata={
                "original-filename": original_filename,
                "uploaded-by": str(user_id),
                "upload-date": now.isoformat(),
            },
        )

        s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"
        logger.info("[POExtractor] Uploaded to S3: %s", s3_key)
        return {"success": True, "s3_key": s3_key, "s3_url": s3_url}

    except Exception as exc:
        logger.error("[POExtractor] S3 upload failed: %s", exc)
        return {"success": False, "s3_key": "", "s3_url": "", "error": str(exc)}


def extract_po_from_pdf_openai(pdf_bytes: bytes, original_filename: str, user_id: str) -> dict:
    """
    OpenAI GPT-4o extraction pipeline (costs money - use for high-accuracy needs).
    
    Full pipeline:
      1. Upload PDF to S3
      2. Extract text from PDF
      3. Call GPT-4o for structured extraction
      4. Return combined result dict

    Returns:
        {
            'success': bool,
            'extraction_status': 'completed' | 'failed',
            'extracted_data': {...},   # structured fields
            's3_key': str,
            's3_url': str,
            'raw_text_length': int,
            'error': str | None
        }
    """
    logger.info('[OpenAI] Starting GPT-4o extraction pipeline...')
    result = {
        "success": False,
        "extraction_status": "failed",
        "extracted_data": {},
        "s3_key": "",
        "s3_url": "",
        "raw_text_length": 0,
        "error": None,
    }

    # ── 1. Upload to S3 ─────────────────────────────────────────────────────
    s3_result = upload_to_s3(pdf_bytes, original_filename, user_id)
    result["s3_key"] = s3_result.get("s3_key", "")
    result["s3_url"] = s3_result.get("s3_url", "")

    # ── 2. Extract raw text ──────────────────────────────────────────────────
    raw_text = extract_text_from_pdf(pdf_bytes)
    result["raw_text_length"] = len(raw_text)

    if len(raw_text) < 50:
        result["error"] = "Could not extract readable text from PDF. The file may be image-only or corrupted."
        logger.error("[POExtractor] Text extraction yielded < 50 chars for: %s", original_filename)
        return result

    logger.info("[POExtractor] Extracted %d chars from %s", len(raw_text), original_filename)

    # ── 3. AI structured extraction ──────────────────────────────────────────
    try:
        extracted_data = call_gpt4o_extraction(raw_text)
        result["extracted_data"] = extracted_data
        result["success"] = True
        result["extraction_status"] = "completed"
        logger.info("[POExtractor] Successfully extracted %d fields", len(extracted_data))
    except Exception as exc:
        result["error"] = f"AI extraction failed: {exc}"
        logger.error("[POExtractor] GPT-4o extraction failed: %s", exc)

    return result
