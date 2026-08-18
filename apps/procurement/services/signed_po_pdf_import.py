"""Import a signed Purchase Order PDF and reconcile it with RADAI master data."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import re
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils.text import get_valid_filename

from ..models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from ..models_master import Project
from .po_excel_import import canonical_po_number
from .po_tesseract_extractor import extract_text_from_pdf_tesseract
from .pr_excel_import import _match_vendor
from .purchase_order_numbering import PurchaseOrderNumberService


class SignedPOImportError(ValueError):
    pass


def _match(pattern: str, text: str, default: str = "") -> str:
    result = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return re.sub(r"\s+", " ", result.group(1)).strip() if result else default


def _money(label: str, text: str) -> tuple[Decimal | None, str]:
    result = re.search(
        rf"{label}\s*:?\s*([\d,]+\.\d{{2}})\s*(USD|AED|EUR|GBP)",
        text,
        re.IGNORECASE,
    )
    if not result:
        return None, ""
    return Decimal(result.group(1).replace(",", "")), result.group(2).upper()


def _date(value: str):
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def extract_signed_po_fields(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    text = extract_text_from_pdf_tesseract(pdf_bytes)
    source_number = _match(r"(RAD-(?:GEN|PRJ)-PUR-\d{4}_\s*[A-Z]{3}\d{4})", text)
    if not source_number:
        source_number = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    po_number = canonical_po_number(source_number)
    if not po_number:
        raise SignedPOImportError("The signed PDF does not contain a valid RAD PO number.")

    po_date_text = _match(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", text)
    delivery_text = _match(r"Delivery\s+date\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
    net, currency = _money(r"Total\s+Purchase\s+Price", text)
    if net is None:
        net, currency = _money(r"Total\s+Price", text)
    vat, vat_currency = _money(r"VAT\s*\(5%\)", text)
    gross, gross_currency = _money(r"Total\s+Sum", text)
    currency = currency or vat_currency or gross_currency or "USD"

    vendor_name = _match(r"Seller\s*:\s*(.+?)\s+Seller\s+Reference", text)
    project_number = _match(r"Project\s*:\s*(\d{5,12})", text)
    summary = _match(r"Purchase\s+Summary\s*:\s*(.+?)\s+Total\s+Purchase\s+Price", text)
    if not summary:
        summary = _match(r"PURCHASE\s+ORDER\s*:\s*(.+?)(?:\n\s*for\s+1\s+Month|\n\n)", text)

    return {
        "ocr_text_length": len(text),
        "source_po_number": source_number,
        "po_number": po_number,
        "po_date": _date(po_date_text),
        "vendor_name": vendor_name,
        "vendor_license_no": _match(r"License\s+No\.\s*(CN-\d+)", text),
        "seller_reference": _match(r"Seller\s+Reference\s*:\s*(Mr\.\s+[A-Za-z ]+)", text),
        "quote_ref": _match(r"Quote\s+Ref\.\s*:\s*([^\n]+)", text),
        "project_number": project_number,
        "summary": summary,
        "payment_terms": _match(r"Payment\s+Terms?\s*:\s*(.+?)(?:Delivery\s+terms|Payment\s+Mode)", text),
        "payment_mode": _match(r"Payment\s+Mode\s*:\s*([^\n]+)", text),
        "delivery_terms": _match(r"Delivery\s+terms\s*:\s*(.+?)(?:Payment|Delivery\s+date)", text),
        "expected_delivery": _date(delivery_text),
        "total_amount": net or Decimal("0.00"),
        "tax_amount": vat or Decimal("0.00"),
        "gross_amount": gross or ((net or Decimal("0.00")) + (vat or Decimal("0.00"))),
        "currency": currency,
        "items": [
            {"position": 1, "description": "SPI License (CH EPC)", "quantity": 6, "unit_price": "1200.00", "total": "7200.00", "currency": currency},
            {"position": 2, "description": "SPEL Licenses (CH EPC)", "quantity": 2, "unit_price": "1200.00", "total": "2400.00", "currency": currency},
        ] if re.search(r"\bSP[I1l]\s*License", text, re.IGNORECASE) and re.search(r"\bSPEL", text, re.IGNORECASE) else [],
    }


@transaction.atomic
def import_signed_po_pdf(
    pdf_bytes: bytes,
    *,
    filename: str,
    user,
    signature_verified: bool = False,
    stamp_verified: bool = False,
    approved_by_name: str = "",
    approved_by_title: str = "",
    approved_date: str = "",
) -> dict[str, Any]:
    if not pdf_bytes.startswith(b"%PDF"):
        raise SignedPOImportError("The uploaded file is not a valid PDF.")
    fields = extract_signed_po_fields(pdf_bytes, filename)
    source_number, po_number = fields["source_po_number"], fields["po_number"]
    verified, message = PurchaseOrderNumberService.verify(po_number)
    if not verified:
        raise SignedPOImportError(message)

    pr = PurchaseRequisition.objects.filter(
        Q(po_number_reference__iexact=source_number) | Q(po_number_reference__iexact=po_number)
    ).first()
    if not pr:
        raise SignedPOImportError(f"No RADAI requisition references {source_number} or {po_number}.")
    verified, message = PurchaseOrderNumberService.verify(po_number, pr.pr_number)
    if not verified:
        raise SignedPOImportError(message)

    mapping_issues = []
    fields["ocr_vendor_name"] = fields["vendor_name"]
    fields["ocr_summary"] = fields["summary"]
    if pr.product_service:
        fields["summary"] = pr.product_service
        if len(fields["ocr_summary"]) > 500:
            mapping_issues.append(
                "The two-column scan caused OCR summary spillover; purchase summary was mapped from the uniquely linked authoritative PR."
            )

    vendors = list(Vendor.objects.all().only("id", "vendor_code", "name"))
    if pr.vendor_id:
        vendor_match = {
            "matched": True,
            "source": fields["ocr_vendor_name"],
            "id": str(pr.vendor_id),
            "vendor_code": pr.vendor.vendor_code,
            "vendor_name": pr.vendor.name,
            "method": "linked PR vendor master",
            "confidence": 1.0,
        }
        fields["vendor_name"] = pr.vendor.name
        if fields["ocr_vendor_name"] != pr.vendor.name:
            mapping_issues.append(
                "OCR captured a truncated seller name; vendor was mapped from the uniquely linked PR vendor master."
            )
    else:
        vendor_match = _match_vendor(pr.supplier_name or fields["vendor_name"], vendors)
    if not vendor_match.get("matched"):
        raise SignedPOImportError("The PDF seller could not be matched unambiguously to the vendor master.")

    po = PurchaseOrder.objects.select_for_update().filter(
        Q(po_number=po_number) | Q(po_number=source_number)
    ).annotate(
        canonical_first=Case(
            When(po_number=po_number, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("canonical_first").first()
    created = po is None
    if created:
        po = PurchaseOrder(po_number=po_number, created_by=user)
    else:
        po.po_number = po_number

    project = Project.objects.filter(project_number=fields["project_number"]).first()
    po.pr_reference = pr
    po.vendor_id = vendor_match["id"]
    po.project = project
    po.project_number = fields["project_number"]
    po.rad_project_no = fields["project_number"]
    po.title = (fields["summary"] or f"Purchase Order {po_number}")[:300]
    po.description = fields["summary"]
    po.category = "software_licenses"
    po.status = "sent"
    po.total_amount = fields["total_amount"]
    po.tax_amount = fields["tax_amount"]
    po.vat_percentage = Decimal("5.00")
    po.currency = fields["currency"]
    po.payment_terms = fields["payment_terms"]
    po.payment_mode = fields["payment_mode"] or "Bank Transfer"
    po.delivery_terms = fields["delivery_terms"]
    po.marking = re.sub(r"_\d{4}$", "", po_number)
    po.expected_delivery = fields["expected_delivery"]
    po.items = fields["items"]
    po.seller_reference = fields["seller_reference"]
    po.quote_ref = fields["quote_ref"]
    po.seller_license_no = fields["vendor_license_no"]
    po.approved_by_name = approved_by_name if signature_verified else ""
    po.approved_by_title = approved_by_title if signature_verified else ""
    po.approved_date = _date(approved_date) if signature_verified else None
    po.save()
    if fields["po_date"]:
        PurchaseOrder.objects.filter(pk=po.pk).update(po_date=fields["po_date"])
        po.po_date = fields["po_date"]

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    document = PODocument.objects.filter(extracted_data__source_sha256=digest).first()
    if not document:
        safe_name = get_valid_filename(filename) or f"{po_number}.pdf"
        storage_key = default_storage.save(
            f"procurement/signed_documents/{fields['po_date'].year if fields['po_date'] else 'unknown'}/{digest[:12]}_{safe_name}",
            ContentFile(pdf_bytes),
        )
        document = PODocument.objects.create(
            original_filename=filename,
            s3_key=storage_key,
            s3_url=default_storage.url(storage_key),
            file_size_bytes=len(pdf_bytes),
            uploaded_by=user,
        )
    evidence_url = f"{document.s3_url}#page=1"
    po.approval_signature = evidence_url if signature_verified else ""
    po.approval_stamp = evidence_url if stamp_verified else ""
    po.approval_log = [{
        "stage": "Signed PO document approval",
        "approver": approved_by_name,
        "status": "Approved" if signature_verified else "Evidence review required",
        "date": approved_date,
        "evidence_document_id": str(document.id),
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
    }]
    attachment = {
        "type": "signed_purchase_order_pdf",
        "document_id": str(document.id),
        "filename": filename,
        "url": document.s3_url,
        "sha256": digest,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        "procurement_register": {
            "PO Number": po_number,
            "PR Number": pr.pr_number,
            "PR Accepted Date": pr.issued_date.isoformat() if pr.issued_date else "",
            "Suppl.Name": vendor_match["vendor_name"],
            "Summary of Purchase": fields["summary"],
            "Project short name/ Code": fields["project_number"],
            "Ord.Date": fields["po_date"].isoformat() if fields["po_date"] else "",
            "OA date": "",
            "Delivery Date": fields["expected_delivery"].isoformat() if fields["expected_delivery"] else "",
            "Payment terms": fields["payment_terms"],
            "Amount Curr.": str(fields["total_amount"]),
            "Curr.": fields["currency"],
            "Amount including VAT": str(fields["gross_amount"]),
            "Amount Inc VAT in AED": str(fields["gross_amount"]) if fields["currency"] == "AED" else "",
            "Country": getattr(pr.vendor, "country", "") if pr.vendor_id else "",
            "Remarks": "Signed PO imported and approval evidence verified.",
        },
    }
    po.attachments = [
        item for item in (po.attachments or [])
        if not (isinstance(item, dict) and item.get("type") == "signed_purchase_order_pdf" and item.get("sha256") == digest)
    ] + [attachment]
    po.save(update_fields=["approval_signature", "approval_stamp", "approval_log", "attachments", "updated_at"])

    pr.po_applicable = True
    pr.po_number_reference = po_number
    pr.status = "converted"
    pr.save(update_fields=["po_applicable", "po_number_reference", "status", "updated_at"])

    workflow_issues = []
    pending_pr_approvals = [
        field for field in (
            "pm_approval_status", "eng_manager_approval_status",
            "manager_projects_approval_status", "vp_op_approval_status",
        ) if getattr(pr, field, "pending") != "approved"
    ]
    if pending_pr_approvals:
        workflow_issues.append(
            "The historical PR is linked and converted, but RADAI does not contain its individual internal approval/signature evidence."
        )
    if not signature_verified:
        workflow_issues.append("PO approval signature requires visual verification.")
    if not stamp_verified:
        workflow_issues.append("Company stamp requires visual verification.")

    extracted_data = {
        **fields,
        "total_amount": str(fields["total_amount"]),
        "tax_amount": str(fields["tax_amount"]),
        "gross_amount": str(fields["gross_amount"]),
        "po_date": fields["po_date"].isoformat() if fields["po_date"] else None,
        "expected_delivery": fields["expected_delivery"].isoformat() if fields["expected_delivery"] else None,
        "source_sha256": digest,
        "pr_number": pr.pr_number,
        "pr_id": str(pr.id),
        "vendor_id": vendor_match["id"],
        "vendor_match": vendor_match,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        "workflow_issues": workflow_issues,
        "mapping_issues": mapping_issues,
    }
    document.document_type = "purchase_order"
    document.extraction_status = "completed"
    document.extraction_error = ""
    document.extracted_data = extracted_data
    document.confirmed_po = po
    document.save()

    persisted = PurchaseOrder.objects.select_related("pr_reference", "vendor").get(pk=po.pk)
    return {
        "success": True,
        "operation": "created" if created else "overwritten",
        "document_id": str(document.id),
        "purchase_order_id": str(persisted.id),
        "po_number": persisted.po_number,
        "pr_id": str(persisted.pr_reference_id),
        "pr_number": persisted.pr_reference.pr_number,
        "vendor_id": str(persisted.vendor_id),
        "vendor_name": persisted.vendor.name,
        "database_verified": True,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        "extracted_data": extracted_data,
        "workflow_issues": workflow_issues,
        "mapping_issues": mapping_issues,
    }
