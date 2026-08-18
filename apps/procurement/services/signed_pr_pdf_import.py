"""Capture an existing signed Purchase Requisition PDF into its RADAI record."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import re

import numpy as np
from PIL import Image, ImageOps
import pymupdf
import pytesseract
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.functions import Concat
from django.db.models import CharField, Value
from django.utils import timezone
from django.utils.text import get_valid_filename

from ..models import PurchaseRequisition, Vendor
from .po_tesseract_extractor import extract_text_from_pdf_tesseract
from .pr_excel_import import _match_vendor


class SignedPRImportError(ValueError):
    pass


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" |\n\r\t")


def _capture(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return _clean(match.group(1)) if match else default


def _date(value: str):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _ocr_lines(text: str) -> list[str]:
    """Return meaningful OCR lines while preserving their document order."""
    return [
        _clean(line)
        for line in (text or "").replace("\r", "\n").split("\n")
        if _clean(line) and not re.fullmatch(r"---\s*Page\s+\d+\s*---", _clean(line), re.IGNORECASE)
    ]


def _money_match(value: str):
    return re.search(
        r"(?:(?P<currency_before>USD|AED|EUR|GBP)\s*(?P<amount_after>[\d,]+\.\d{2})|"
        r"(?P<amount_before>[\d,]+\.\d{2})\s*(?P<currency_after>USD|AED|EUR|GBP))",
        value or "",
        re.IGNORECASE,
    )


def _section(text: str, start: str, end: str) -> str:
    match = re.search(start + r"(?P<body>[\s\S]*?)(?=" + end + r")", text, re.IGNORECASE)
    return _clean(match.group("body")) if match else ""


def extract_signed_pr_fields(pdf_bytes: bytes, filename: str, *, allow_missing_pr_number: bool = False) -> dict:
    if not pdf_bytes.startswith(b"%PDF"):
        raise SignedPRImportError("The uploaded file is not a valid PDF.")
    text = extract_text_from_pdf_tesseract(pdf_bytes)
    lines = _ocr_lines(text)
    pr_number = _capture(r"PR\s+No\.\s*(RAD-(?:GEN|PRJ)-PR-\d{4}[\s_-]+\d{4})", text)
    if not pr_number:
        pr_number = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    pr_number = re.sub(r"(RAD-(?:GEN|PRJ)-PR-\d{4})[\s_-]+(\d{4})", r"\1_\2", pr_number, flags=re.IGNORECASE)
    if not re.fullmatch(r"RAD-(?:GEN|PRJ)-PR-\d{4}_\d{4}", pr_number) and not allow_missing_pr_number:
        raise SignedPRImportError("The PDF does not contain a valid RAD PR number.")
    if not re.fullmatch(r"RAD-(?:GEN|PRJ)-PR-\d{4}_\d{4}", pr_number):
        pr_number = ""

    issued_by_name = _capture(r"Issued by:\s*(.+?)(?=\s+PR\s+No\.|\n|$)", text)
    product = _capture(r"Product/\s*Service:\s*(.+?)(?=\s+Supplier:|\n|$)", text)
    supplier = _capture(r"Supplier:\s*(.+?)(?=\s+Project/Department:|\n|$)", text)
    supplier_business_match = re.search(
        r"(?:Supplier\s+)?Business\s+ID(?:\s+No\.)?\s*:\s*(CN\s*-?\s*\d{5,10})|\b(CN\s*-\s*\d{5,10})\b",
        text,
        re.IGNORECASE,
    )
    supplier_business_id = ""
    if supplier_business_match:
        supplier_business_id = re.sub(
            r"\s+", "", supplier_business_match.group(1) or supplier_business_match.group(2)
        ).upper().replace("CN", "CN-").replace("--", "-")
    if not supplier_business_id:
        interleaved_business_match = re.search(r"\bCN\s*-\s*[\s\S]{0,100}?(\d{7,10})\b", text, re.IGNORECASE)
        if interleaved_business_match:
            supplier_business_id = f"CN-{interleaved_business_match.group(1)}"

    project_department = _section(
        text,
        r"Project/Department:\s*",
        r"\s+\d+\.\s*Description\s+and\s+Reason\s+for\s+Purchase",
    )
    project_department = re.sub(
        r"(?:Supplier\s+)?Business\s+ID(?:\s+No\.)?\s*:\s*", " ", project_department, flags=re.IGNORECASE
    )
    project_department = re.sub(r"\bCN\s*-?\s*\d{5,10}\b", " ", project_department, flags=re.IGNORECASE)
    project_department = re.sub(r"\bCN\s*-", " ", project_department, flags=re.IGNORECASE)
    if supplier_business_id:
        project_department = re.sub(
            rf"\b{re.escape(supplier_business_id.removeprefix('CN-'))}\b", " ", project_department
        )
    project_department = re.sub(r"\bICV\s*:\s*[\d.]+", " ", project_department, flags=re.IGNORECASE)
    project_department = _clean(project_department)
    icv = _capture(r"\bICV:\s*([\d.]+)", text)
    description = _capture(r"1\.\s*Description and Reason for Purchase:\s*(.+?)\s+2\.\s*Preferred Supplier", text)
    preferred = _capture(
        r"2\.\s*Preferred Supplier \(if any\):\s*(.+?)(?:\n\s*[—_-]+|\n\s*For M/s|\n\s*Total Price|\n\s*Supply of)",
        text,
    )
    description = _section(
        text,
        r"\d+\.\s*Description\s+and\s+Reason\s+for\s+Purchase\s*:\s*",
        r"\s+\d+\.\s*Preferred\s+Supplier",
    ) or description
    if issued_by_name:
        description = re.sub(
            rf"\bIssued\s+by\s*:\s*{re.escape(issued_by_name)}\b", " ", description, flags=re.IGNORECASE
        )
        description = _clean(description)
    for line_index, line in enumerate(lines):
        preferred_match = re.search(r"\d+\.\s*Preferred Supplier\s*\(if any\)\s*:\s*(.*)$", line, re.IGNORECASE)
        if not preferred_match:
            continue
        preferred = _clean(preferred_match.group(1))
        if preferred and preferred.count("(") > preferred.count(")") and line_index + 1 < len(lines):
            preferred = _clean(f"{preferred} {lines[line_index + 1]}")
        break
    preferred_primary = _clean(re.split(r"\(\s*To\s+M/s", preferred, flags=re.IGNORECASE)[0])
    if preferred_primary:
        supplier = preferred_primary

    po_reference = _capture(
        r"PO\s+Reference:\s*(RAD-(?:GEN|PRJ)-PUR-\d{4}[\s_-]+(?:[A-Z]{3}\d{4}|\d{4}))",
        text,
    )
    po_reference = re.sub(r"\s+", "_", po_reference)
    notes = _capture(r"4\.\s*Special Notes:\s*\(If any\)\s*(.+?)(?:Attachment\s+No\.|APPROVALS)", text).rstrip(" =➜")
    attachment_reference = _capture(r"(Attachment\s+No\.\s*\d+\s*:\s*.+?)(?:APPROVALS|\n\s*APPROVALS)", text)
    issued_date = _date(_capture(r"\bDate:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text))
    semantic_notes_match = re.search(
        r"\d+\.\s*(?:(?:Special\s+Notes)(?:\s*:\s*\(If any\))?|(?:Pu(?:r)?chase\s+Recommendation))"
        r"\s*:?\s*(?P<body>[\s\S]*?)(?=Attachment\s+No\.|APPROVALS)",
        text,
        re.IGNORECASE,
    )
    if semantic_notes_match:
        note_lines = _ocr_lines(semantic_notes_match.group("body"))
        while note_lines and len(re.sub(r"[^A-Za-z]", "", note_lines[0])) < 5:
            note_lines.pop(0)
        while note_lines and re.fullmatch(
            r"(?:(?:USD|AED|EUR|GBP)\s*)?[\d,]+\.\d{2}(?:\s*(?:USD|AED|EUR|GBP))?",
            note_lines[-1],
            re.IGNORECASE,
        ):
            note_lines.pop()
        semantic_notes = _clean(" ".join(note_lines)).rstrip(" =âžœ")
        if semantic_notes:
            notes = semantic_notes
    for line in lines:
        attachment_match = re.search(r"(Attachment\s+No\.\s*\d+\s*:\s*.+)$", line, re.IGNORECASE)
        if attachment_match:
            attachment_reference = _clean(attachment_match.group(1))
            break

    # The standard template has existed with both "USD 1,000.00" and
    # "1,000.00 USD" layouts. Limit line extraction to the Price section so
    # amounts mentioned in the purchase justification are not treated as rows.
    price_lines = []
    price_section_match = re.search(
        r"2\.\s*Preferred Supplier[^\n]*\n(?P<body>[\s\S]*?)\n\s*Net Total,?\s*excl\s*VAT",
        text,
        re.IGNORECASE,
    )
    if not price_section_match:
        price_section_match = re.search(
            r"\d+\.\s*Preferred Supplier[^\n]*\n(?P<body>[\s\S]*?)(?=\n\s*(?:PO\s+Reference|\d+\.\s*(?:Special|Pu)))",
            text,
            re.IGNORECASE,
        )
    price_section = price_section_match.group("body") if price_section_match else ""
    price_row_pattern = re.compile(
        r"^[ \t]*(?P<description>.+?)[ \t]+(?:(?P<currency_before>USD|AED|EUR|GBP)[ \t]*"
        r"(?P<amount_after>[\d,]+\.\d{2})|(?P<amount_before>[\d,]+\.\d{2})\s*"
        r"(?P<currency_after>USD|AED|EUR|GBP))"
        r"(?:[ \t]*(?:[|\]]\}?)[ \t]*(?P<remarks>[^\n]*))?[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    for row_match in price_row_pattern.finditer(price_section):
        row_currency = (row_match.group("currency_before") or row_match.group("currency_after")).upper()
        row_amount = row_match.group("amount_after") or row_match.group("amount_before")
        row_description = _clean(row_match.group("description"))
        continuation_match = re.match(
            r"[ \t]*\n[ \t]*(?P<continuation>\([^\n]+)", price_section[row_match.end():]
        )
        if continuation_match:
            row_description = _clean(f"{row_description} {continuation_match.group('continuation')}")
        price_item = {
            "description": row_description,
            "total": str(Decimal(row_amount.replace(",", ""))),
            "currency": row_currency,
        }
        if _clean(row_match.group("remarks")):
            price_item["remarks"] = _clean(row_match.group("remarks"))
        price_lines.append(price_item)

    net_match = re.search(
        r"Net Total,?\s*excl\s*VAT\s*(?:(?P<currency_before>USD|AED|EUR|GBP)\s*"
        r"(?P<amount_after>[\d,]+\.\d{2})|(?P<amount_before>[\d,]+\.\d{2})\s*"
        r"(?P<currency_after>USD|AED|EUR|GBP))",
        text,
        re.IGNORECASE,
    )
    net_amount = (net_match.group("amount_after") or net_match.group("amount_before")) if net_match else ""
    net_currency = (net_match.group("currency_before") or net_match.group("currency_after")) if net_match else ""
    net_total = Decimal(net_amount.replace(",", "")) if net_match else (
        sum((Decimal(item["total"]) for item in price_lines), Decimal("0.00")) or None
    )
    currency = net_currency.upper() if net_currency else (price_lines[0]["currency"] if price_lines else "")

    # Some scans emit the amount column after later section text. When no row
    # survived the labeled Price section, accept only a repeated currency/amount
    # pair before APPROVALS; repetition is strong evidence for row total + net total.
    if net_total is None:
        preferred_offset = re.search(r"Preferred\s+Supplier", text, re.IGNORECASE)
        approval_offset = re.search(r"APPROVALS", text, re.IGNORECASE)
        amount_scope = text[
            preferred_offset.start() if preferred_offset else 0:
            approval_offset.start() if approval_offset else len(text)
        ]
        candidates = []
        for line in _ocr_lines(amount_scope):
            if re.search(r"\b(?:Budget|original|deduction)\b", line, re.IGNORECASE):
                continue
            money = _money_match(line)
            if money:
                candidates.append((
                    (money.group("currency_before") or money.group("currency_after")).upper(),
                    money.group("amount_after") or money.group("amount_before"),
                ))
        repeated = next((item for item in candidates if candidates.count(item) >= 2), None)
        if repeated:
            currency, repeated_amount = repeated
            net_total = Decimal(repeated_amount.replace(",", ""))
            price_lines = [{
                "description": description or product,
                "total": str(net_total),
                "currency": currency,
            }]
    if not price_lines and net_total:
        price_lines = [{"description": description or product, "total": str(net_total), "currency": currency}]
    product_tokens = re.sub(r"[^a-z0-9 ]", "", product.lower()).split()
    description_tokens = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
    for tokens in (product_tokens, description_tokens):
        while tokens and tokens[0] in {"supply", "of", "the", "a", "an"}:
            tokens.pop(0)
    product_prefix = " ".join(product_tokens[:3])
    normalized_description = re.sub(r"[^a-z0-9 ]", "", description.lower())
    semantic_prefix_match = bool(
        len(product_tokens) >= 2 and len(description_tokens) >= 2
        and all(
            left.startswith(right) or right.startswith(left)
            for left, right in zip(product_tokens[:2], description_tokens[:2])
        )
    )
    if description and (
        not product or semantic_prefix_match or (product_prefix and normalized_description.startswith(product_prefix))
    ):
        product = description
    for price_item in price_lines:
        item_description = price_item.get("description", "")
        if item_description.count("(") <= item_description.count(")"):
            continue
        open_fragment = _clean(item_description.rsplit("(", 1)[-1])
        completion = re.search(
            rf"\({re.escape(open_fragment)}\s+(?P<remainder>[^)]+)\)",
            f"{description} {product}",
            re.IGNORECASE,
        )
        if completion:
            price_item["description"] = _clean(
                f"{item_description} {completion.group('remainder')})"
            )
    supplier_looks_interleaved = bool(
        preferred and (
            len(supplier) > len(preferred) * 1.15
            or any(term.lower() in supplier.lower() for term in ("SmartPlant Electrical", "Licenses (CH"))
        )
    )
    if supplier_looks_interleaved:
        supplier = preferred

    net_total_aed = _capture(r"AED\s*([\d,]+\.\d{2})\s*\n\s*PO\s+Reference", text).replace(",", "")
    if not net_total_aed and net_total:
        if currency == "AED":
            net_total_aed = str(net_total.quantize(Decimal("0.01")))
        elif currency == "USD":
            net_total_aed = str((net_total * Decimal("3.67")).quantize(Decimal("0.01")))

    project_numbers = list(dict.fromkeys(re.findall(r"\b\d{5,12}\b", project_department)))
    project_number = project_numbers[0] if project_numbers else ""
    extracted_values = {
        "issued_by": issued_by_name,
        "issued_date": issued_date,
        "product_service": product,
        "supplier": supplier,
        "project_department": project_department,
        "description": description,
        "preferred_supplier": preferred,
        "price": net_total,
        "currency": currency,
    }
    required_fields = ("issued_by", "issued_date", "product_service", "supplier", "description", "price", "currency")
    extraction_issues = [
        f"OCR could not confidently extract the labeled field: {field.replace('_', ' ')}."
        for field in required_fields
        if not extracted_values[field]
    ]
    field_confidence = {
        field: ("high" if value else "missing")
        for field, value in extracted_values.items()
    }

    return {
        "ocr_text_length": len(text),
        "ocr_schema_version": "signed-pr-labels-v2",
        "field_confidence": field_confidence,
        "extraction_issues": extraction_issues,
        "pr_number": pr_number,
        "issued_by_name": issued_by_name,
        "issued_date": issued_date,
        "product_service": product,
        "supplier_name": supplier,
        "supplier_business_id": supplier_business_id,
        "project_department": project_department,
        "project_number": project_number,
        "project_numbers": project_numbers,
        "icv": icv,
        "description_reason": description or product,
        "preferred_supplier": preferred or supplier,
        "price_lines": price_lines,
        "net_total": net_total,
        "currency": currency,
        "budget_in_aed": _capture(r"Budget\s*[>→-]+\s*AED\s*([\d,]+\.\d{2})", text).replace(",", ""),
        "net_total_aed": net_total_aed,
        "po_reference": po_reference,
        "special_notes": notes,
        "attachment_reference": attachment_reference,
    }


def _serialize_extracted_fields(fields: dict) -> dict:
    return {
        **fields,
        "issued_date": fields["issued_date"].isoformat() if fields.get("issued_date") else None,
        "net_total": str(fields["net_total"]) if fields.get("net_total") is not None else None,
    }


def _apply_manual_overrides(fields: dict, overrides: dict | None) -> dict:
    """Apply reviewed OCR corrections using a strict, field-level allow-list."""
    if not overrides:
        return fields

    corrected = dict(fields)
    text_fields = {
        "pr_number": 40,
        "issued_by_name": 200,
        "product_service": 2000,
        "supplier_name": 500,
        "supplier_business_id": 100,
        "project_department": 1000,
        "project_number": 100,
        "description_reason": 5000,
        "preferred_supplier": 500,
        "po_reference": 100,
        "special_notes": 5000,
        "attachment_reference": 500,
        "budget_in_aed": 30,
        "net_total_aed": 30,
    }
    for field, max_length in text_fields.items():
        if field in overrides:
            corrected[field] = _clean(str(overrides.get(field) or ""))[:max_length]

    if "issued_date" in overrides:
        corrected["issued_date"] = _date(str(overrides.get("issued_date") or ""))
        if overrides.get("issued_date") and not corrected["issued_date"]:
            raise SignedPRImportError("Issued date must use YYYY-MM-DD format.")

    if "currency" in overrides:
        currency = _clean(str(overrides.get("currency") or "")).upper()
        if currency not in {"AED", "USD", "EUR", "GBP"}:
            raise SignedPRImportError("Currency must be AED, USD, EUR, or GBP.")
        corrected["currency"] = currency

    if "net_total" in overrides:
        try:
            corrected["net_total"] = Decimal(str(overrides.get("net_total") or "").replace(",", ""))
        except Exception as exc:
            raise SignedPRImportError("Total price must be a valid number.") from exc
        if corrected["net_total"] < 0:
            raise SignedPRImportError("Total price cannot be negative.")

    for money_field, label in (("budget_in_aed", "Budget in AED"), ("net_total_aed", "Net total in AED")):
        if money_field not in overrides or corrected.get(money_field) in (None, ""):
            continue
        try:
            amount = Decimal(str(corrected[money_field]).replace(",", ""))
        except Exception as exc:
            raise SignedPRImportError(f"{label} must be a valid number.") from exc
        if amount < 0:
            raise SignedPRImportError(f"{label} cannot be negative.")
        corrected[money_field] = str(amount)

    corrected["pr_number"] = re.sub(
        r"(RAD-(?:GEN|PRJ)-PR-\d{4})[\s_-]+(\d{4})", r"\1_\2",
        corrected.get("pr_number", ""), flags=re.IGNORECASE,
    ).upper()
    if not re.fullmatch(r"RAD-(?:GEN|PRJ)-PR-\d{4}_\d{4}", corrected["pr_number"]):
        raise SignedPRImportError("Enter a valid PR number using RAD-{GEN|PRJ}-PR-####_YYYY.")

    required = {
        "issued_by_name": "Issued by",
        "issued_date": "Issued date",
        "product_service": "Product / Service",
        "supplier_name": "Supplier",
        "description_reason": "Description and reason",
        "net_total": "Total price",
        "currency": "Currency",
    }
    missing = [label for field, label in required.items() if corrected.get(field) in (None, "")]
    if missing:
        raise SignedPRImportError(f"Complete the required reviewed fields: {', '.join(missing)}.")

    corrected["price_lines"] = [{
        "description": corrected["description_reason"] or corrected["product_service"],
        "total": str(corrected["net_total"]),
        "currency": corrected["currency"],
    }]
    confidence = dict(corrected.get("field_confidence") or {})
    override_confidence_keys = {
        "issued_by_name": "issued_by", "issued_date": "issued_date",
        "product_service": "product_service", "supplier_name": "supplier",
        "description_reason": "description", "net_total": "price", "currency": "currency",
    }
    for override_field, confidence_field in override_confidence_keys.items():
        if override_field in overrides:
            confidence[confidence_field] = "manual"
    corrected["field_confidence"] = confidence
    corrected["extraction_issues"] = [
        issue for issue in corrected.get("extraction_issues", [])
        if not any(label.lower() in issue.lower() for label in required.values())
    ]
    corrected["manual_review_applied"] = True
    return corrected


def preview_signed_pr_pdf(pdf_bytes: bytes, *, filename: str, expected_pr_number: str = "") -> dict:
    """Extract an editable preview without modifying a requisition or storing the PDF."""
    fields = extract_signed_pr_fields(pdf_bytes, filename, allow_missing_pr_number=True)
    detected = detect_approval_evidence(pdf_bytes)
    mapping_issues = list(fields.get("extraction_issues") or [])
    if not fields.get("pr_number"):
        mapping_issues.insert(0, "OCR could not confidently read the PR number. Enter it manually.")
    elif expected_pr_number and fields["pr_number"].casefold() != expected_pr_number.strip().casefold():
        mapping_issues.insert(0, f"Detected PR {fields['pr_number']} does not match {expected_pr_number}.")

    database_match = False
    if fields.get("pr_number"):
        database_match = PurchaseRequisition.objects.filter(pr_number=fields["pr_number"]).exists()
        if not database_match:
            mapping_issues.append(f"PR {fields['pr_number']} does not exist in RADAI.")

    return {
        "success": True,
        "preview_only": True,
        "requires_manual_review": bool(mapping_issues),
        "database_match": database_match,
        "pr_number": fields.get("pr_number", ""),
        "extracted_data": _serialize_extracted_fields(fields),
        "approval_detection": detected,
        "mapping_issues": mapping_issues,
        "workflow_issues": [],
    }


def _find_user(full_name: str):
    normalized = _clean(full_name).lower().replace("-", " ")
    if not normalized:
        return None
    User = get_user_model()
    for user in User.objects.annotate(
        full_name=Concat("first_name", Value(" "), "last_name", output_field=CharField())
    ).only("id", "first_name", "last_name"):
        candidate = _clean(f"{user.first_name} {user.last_name}").lower().replace("-", " ")
        if candidate == normalized:
            return user
    return None


def _match_user(full_name: str):
    """Match OCR names to RADAI users while tolerating small scan errors."""
    exact = _find_user(full_name)
    if exact:
        return exact
    normalized = re.sub(r"[^a-z0-9 ]", "", _clean(full_name).lower().replace("-", " "))
    best_user, best_score = None, 0.0
    User = get_user_model()
    for user in User.objects.only("id", "first_name", "last_name"):
        candidate = re.sub(
            r"[^a-z0-9 ]", "", _clean(f"{user.first_name} {user.last_name}").lower().replace("-", " ")
        )
        score = SequenceMatcher(None, normalized, candidate).ratio()
        ocr_tokens = normalized.split()
        candidate_tokens = candidate.split()
        if (
            ocr_tokens and candidate_tokens
            and ocr_tokens[0] == candidate_tokens[0]
            and SequenceMatcher(None, ocr_tokens[-1], candidate_tokens[-1]).ratio() >= 0.72
        ):
            score = max(score, 0.88)
        if score > best_score:
            best_user, best_score = user, score
    return best_user if best_score >= 0.72 else None


def detect_approval_evidence(pdf_bytes: bytes) -> dict:
    """Map variable approval-table layouts into RADAI's four required stages."""
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if not document.page_count:
        raise SignedPRImportError("The PDF has no pages.")
    page = document[-1]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
    pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )[:, :, :3]
    height, width = pixels.shape[:2]

    # Find the APPROVALS title first. The table moves vertically depending on
    # the amount of text in Special Notes, so page-fixed rows are unreliable.
    page_image = Image.fromarray(pixels)
    ocr_data = pytesseract.image_to_data(
        page_image,
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )
    approval_anchors = []
    for index, token in enumerate(ocr_data.get("text", [])):
        normalized_token = re.sub(r"[^A-Z]", "", str(token).upper())
        if normalized_token == "APPROVALS":
            approval_anchors.append(ocr_data["top"][index] / height)
    if not approval_anchors:
        fallback_data = pytesseract.image_to_data(
            page_image,
            config="--psm 11",
            output_type=pytesseract.Output.DICT,
        )
        for index, token in enumerate(fallback_data.get("text", [])):
            normalized_token = re.sub(r"[^A-Z]", "", str(token).upper())
            if normalized_token == "APPROVALS":
                approval_anchors.append(fallback_data["top"][index] / height)
    approval_anchor = max(approval_anchors) if approval_anchors else None
    if approval_anchor is None:
        return {
            "template": "RAD-OM-PRC-0001 FRM -1 Rev 0",
            "table_detected": False,
            "table_anchor_y": None,
            "signatures": {key: False for key in ("pm", "moe", "mop", "vp")},
            "signature_density": {key: 0.0 for key in ("pm", "moe", "mop", "vp")},
            "all_four_signatures": False,
            "approver_names": {key: "" for key in ("pm", "moe", "mop", "vp")},
            "date_present": False,
            "date_density": 0.0,
            "approval_date": None,
            "date_ocr": [],
        }

    # Detect horizontal rules for the title, headings, any number of role rows,
    # the date row, and table bottom. Some legacy forms contain both PM and PD.
    grayscale = pixels.mean(axis=2)
    horizontal_density = (
        grayscale[:, int(0.08 * width):int(0.95 * width)] < 160
    ).mean(axis=1)
    y_positions = np.arange(height) / height
    rule_pixels = np.where(
        (y_positions >= approval_anchor - 0.012)
        & (y_positions <= approval_anchor + 0.23)
        & (horizontal_density >= 0.40)
    )[0]
    rule_groups = []
    for pixel_y in rule_pixels:
        if not rule_groups or pixel_y > rule_groups[-1][-1] + 1:
            rule_groups.append([pixel_y])
        else:
            rule_groups[-1].append(pixel_y)
    table_rules = [float(np.mean(group) / height) for group in rule_groups]
    if len(table_rules) < 6:
        return {
            "template": "RAD-OM-PRC-0001 FRM -1 Rev 0",
            "table_detected": False,
            "table_anchor_y": round(approval_anchor, 4),
            "table_rules": [round(value, 4) for value in table_rules],
            "signatures": {key: False for key in ("pm", "moe", "mop", "vp")},
            "signature_density": {key: 0.0 for key in ("pm", "moe", "mop", "vp")},
            "all_four_signatures": False,
            "approver_names": {key: "" for key in ("pm", "moe", "mop", "vp")},
            "date_present": False,
            "date_density": 0.0,
            "approval_date": None,
            "date_ocr": [],
        }
    raw_rows = []
    # Inspect every interval between horizontal rules and retain only rows with
    # a recognized role label. This supports forms where the APPROVALS title's
    # top border is shared with the preceding section, as well as PM/PD forms.
    fallback_roles_by_rule = {}
    if len(table_rules) == 9:
        fallback_roles_by_rule = dict(zip(range(2, 7), ("pm", "pm", "moe", "mop", "vp")))
    elif len(table_rules) == 8:
        fallback_roles_by_rule = dict(zip(range(2, 6), ("pm", "moe", "mop", "vp")))
    elif len(table_rules) == 7:
        fallback_roles_by_rule = dict(zip(range(1, 5), ("pm", "moe", "mop", "vp")))
    for start_rule in range(0, len(table_rules) - 1):
        y1, y2 = table_rules[start_rule] + 0.002, table_rules[start_rule + 1] - 0.002
        role_crop = pixels[int(y1 * height):int(y2 * height), int(0.07 * width):int(0.21 * width)]
        role_image = ImageOps.grayscale(Image.fromarray(role_crop)).resize(
            (role_crop.shape[1] * 4, role_crop.shape[0] * 4)
        )
        role_candidates = [
            _clean(pytesseract.image_to_string(role_image, config="--psm 7")),
            _clean(pytesseract.image_to_string(
                role_image.point(lambda value: 0 if value < 180 else 255), config="--psm 7"
            )),
        ]
        role_text = max(role_candidates, key=len, default="")
        normalized_role = re.sub(r"[^a-z]", "", role_text.lower())
        if normalized_role.startswith("vp") or "vicepresident" in normalized_role:
            role_key = "vp"
        elif "moe" in normalized_role or "engineering" in normalized_role:
            role_key = "moe"
        elif "mop" in normalized_role or "projects" in normalized_role:
            role_key = "mop"
        elif normalized_role.startswith(("pm", "pd", "dm")) or "projectmanager" in normalized_role:
            role_key = "pm"
        else:
            role_key = fallback_roles_by_rule.get(start_rule, "")

        if not role_key:
            continue

        signature_crop = pixels[int(y1 * height):int(y2 * height), int(0.465 * width):int(0.70 * width)]
        density = float((signature_crop.mean(axis=2) < 150).mean()) if signature_crop.size else 0.0
        name_crop = pixels[int(y1 * height):int(y2 * height), int(0.165 * width):int(0.455 * width)]
        name_image = Image.fromarray(name_crop).resize((name_crop.shape[1] * 2, name_crop.shape[0] * 2))
        name_text = pytesseract.image_to_string(name_image, config="--psm 6")
        name_text = re.sub(r"\bName\b", "", name_text, flags=re.IGNORECASE)
        raw_name = _clean(re.sub(r"^[|Il1\s]+|[|Il1\s]+$", "", name_text))
        # In multi-project forms the project code can extend into the Name
        # cell (for example "5900863-H2 | Pankaj Kumar Singh"). Preserve the
        # OCR evidence but use only the value after the separator for matching.
        normalized_name = _clean(raw_name.rsplit("|", 1)[-1])
        raw_rows.append({
            "source_role": role_text,
            "role_key": role_key,
            "raw_name": raw_name,
            "name": normalized_name,
            "signature_detected": density >= 0.018,
            "signature_density": round(density, 4),
            "row_y": [round(y1, 4), round(y2, 4)],
        })

    names = {key: "" for key in ("pm", "moe", "mop", "vp")}
    signatures = {key: False for key in ("pm", "moe", "mop", "vp")}
    densities = {key: 0.0 for key in ("pm", "moe", "mop", "vp")}
    for key in names:
        candidates = [row for row in raw_rows if row["role_key"] == key]
        if not candidates:
            continue
        # PM and PD are alternative first-stage rows. Prefer the signed row;
        # otherwise preserve the strongest available evidence for review.
        selected = max(
            candidates,
            key=lambda row: (row["signature_detected"], row["signature_density"]),
        )
        names[key] = selected["name"]
        signatures[key] = selected["signature_detected"]
        densities[key] = selected["signature_density"]

    date_crop = pixels[
        int((table_rules[-2] + 0.001) * height):int((table_rules[-1] - 0.001) * height),
        int(0.165 * width):int(0.455 * width),
    ]
    date_density = float((date_crop.mean(axis=2) < 150).mean()) if date_crop.size else 0.0
    date_present = date_density >= 0.008
    date_image = ImageOps.grayscale(Image.fromarray(date_crop)).resize(
        (date_crop.shape[1] * 2, date_crop.shape[0] * 2)
    )
    date_candidates = []
    for threshold in (None, 150, 190):
        candidate_image = date_image if threshold is None else date_image.point(lambda value: 0 if value < threshold else 255)
        date_candidates.append(pytesseract.image_to_string(candidate_image, config="--psm 7").strip())
    approval_date = None
    for candidate in date_candidates:
        match = re.search(r"\b([0-3]?\d)[.\-/]([01]?\d)[.\-/](20\d{2})\b", candidate)
        if match:
            approval_date = _date(".".join(match.groups()))
            if approval_date:
                break

    return {
        "template": "RAD-OM-PRC-0001 FRM -1 Rev 0",
        "table_detected": True,
        "table_anchor_y": round(approval_anchor, 4),
        "table_rules": [round(value, 4) for value in table_rules],
        "approval_rows": raw_rows,
        "signatures": signatures,
        "signature_density": densities,
        "all_four_signatures": all(signatures.values()),
        "approver_names": names,
        "date_present": date_present,
        "date_density": round(date_density, 4),
        "approval_date": approval_date,
        "date_ocr": date_candidates,
    }


@transaction.atomic
def import_signed_pr_pdf(
    pdf_bytes: bytes,
    *,
    filename: str,
    uploaded_by,
    approvals: dict[str, str] | None = None,
    signatures_verified: bool | None = None,
    approval_date: str = "",
    expected_pr_number: str = "",
    manual_overrides: dict | None = None,
) -> dict:
    fields = extract_signed_pr_fields(
        pdf_bytes, filename,
        allow_missing_pr_number=bool((manual_overrides or {}).get("pr_number")),
    )
    fields = _apply_manual_overrides(fields, manual_overrides)
    if expected_pr_number and fields["pr_number"].casefold() != expected_pr_number.strip().casefold():
        raise SignedPRImportError(
            f"Uploaded PDF is {fields['pr_number']}, but the edited record is {expected_pr_number}. Nothing was changed."
        )
    detected = detect_approval_evidence(pdf_bytes)
    signatures_verified = detected["all_four_signatures"] if signatures_verified is None else (
        signatures_verified and detected["all_four_signatures"]
    )
    try:
        pr = PurchaseRequisition.objects.select_for_update().get(pr_number=fields["pr_number"])
    except PurchaseRequisition.DoesNotExist as exc:
        raise SignedPRImportError(f"PR {fields['pr_number']} does not exist in RADAI.") from exc

    mapping_issues = list(fields.get("extraction_issues") or [])
    issuer = _find_user(fields["issued_by_name"])
    if issuer:
        pr.issued_by = issuer
        pr.requested_by = issuer
    else:
        mapping_issues.append(f"Issued-by name '{fields['issued_by_name']}' was not matched to a RADAI user.")

    vendors = list(Vendor.objects.all().only("id", "vendor_code", "name"))
    vendor_match = _match_vendor(fields["supplier_name"], vendors)
    if vendor_match.get("matched"):
        pr.vendor_id = vendor_match["id"]
    else:
        mapping_issues.append("The signed PR supplier was not matched unambiguously to the vendor master.")

    pr.issued_date = fields["issued_date"] or pr.issued_date
    pr.product_service = fields["product_service"] or pr.product_service
    pr.title = pr.product_service[:300]
    pr.supplier_name = fields["supplier_name"] or pr.supplier_name
    pr.supplier_business_id = fields["supplier_business_id"] or pr.supplier_business_id
    pr.project_department = fields["project_department"] or pr.project_department
    pr.project = fields["project_number"] or pr.project
    pr.description_reason = fields["description_reason"] or pr.description_reason
    pr.preferred_supplier_if_any = fields["preferred_supplier"] or pr.preferred_supplier_if_any
    pr.price_description = pr.product_service
    if fields["price_lines"]:
        pr.items = fields["price_lines"]
        first_price_remarks = fields["price_lines"][0].get("remarks", "")
        if first_price_remarks:
            pr.price_remarks = first_price_remarks
    if fields["currency"]:
        pr.currency = fields["currency"]
    if fields["net_total"] is not None:
        pr.total_price = fields["net_total"]
        pr.net_total_excl_vat = fields["net_total"]
    if fields["budget_in_aed"]:
        pr.estimated_budget = Decimal(fields["budget_in_aed"])
    pr.po_applicable = bool(fields["po_reference"])
    pr.po_number_reference = fields["po_reference"] or pr.po_number_reference
    pr.purchase_recommendation = fields["special_notes"] or pr.purchase_recommendation
    pr.priority = "urgent" if "urgent basis" in fields["special_notes"].lower() else pr.priority

    metadata = dict(pr.price_remarks_data or {})
    metadata.update({
        "import_source": "signed_pr_pdf",
        "source_authority": "Signed Purchase Requisition",
        "icv": fields["icv"],
        "budget_in_aed": fields["budget_in_aed"],
        "net_total_aed": fields["net_total_aed"],
        "price_lines": fields["price_lines"],
        "project_numbers": fields.get("project_numbers", []),
        "attachment_reference": fields["attachment_reference"],
        "ocr_schema_version": fields.get("ocr_schema_version"),
        "ocr_field_confidence": fields.get("field_confidence", {}),
        "ocr_extraction_issues": fields.get("extraction_issues", []),
        "mapping_issues": mapping_issues,
        "signed_approval_evidence": {
            "table_detected": detected.get("table_detected", False),
            "rows": detected.get("approval_rows", []),
            "signatures": detected.get("signatures", {}),
            "approver_names": detected.get("approver_names", {}),
            "date_present": detected.get("date_present", False),
            "date_ocr": detected.get("date_ocr", []),
        },
    })
    if manual_overrides:
        metadata["manual_ocr_review"] = {
            "applied": True,
            "reviewed_at": timezone.now().isoformat(),
            "reviewed_by_id": str(uploaded_by.id),
            "reviewed_by_name": uploaded_by.get_full_name() or uploaded_by.email,
            "corrected_fields": sorted(manual_overrides.keys()),
        }
    pr.price_remarks_data = metadata

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    existing_attachment = next((
        item for item in (pr.attachments or [])
        if isinstance(item, dict) and item.get("sha256") == digest
    ), None)
    if existing_attachment:
        storage_url = existing_attachment.get("url") or existing_attachment.get("s3_url")
        existing_attachment["signature_verified"] = signatures_verified
    else:
        safe_name = get_valid_filename(filename) or f"{pr.pr_number}.pdf"
        key = default_storage.save(
            f"procurement/signed_requisitions/{fields['issued_date'].year if fields['issued_date'] else 'unknown'}/{digest[:12]}_{safe_name}",
            ContentFile(pdf_bytes),
        )
        storage_url = default_storage.url(key)
        pr.attachments = list(pr.attachments or []) + [{
            "type": "signed_purchase_requisition_pdf",
            "document_type": "signed_purchase_requisition_pdf",
            "filename": filename,
            "url": storage_url,
            "s3_url": storage_url,
            "sha256": digest,
            "signature_verified": signatures_verified,
        }]

    workflow_issues = []
    approval_names = {**detected["approver_names"], **(approvals or {})}
    approved_on = _date(approval_date) or detected["approval_date"]
    if not approved_on and detected["date_present"] and pr.approved_at:
        approved_on = pr.approved_at.date()
    approved_at = timezone.make_aware(datetime.combine(approved_on, time(12, 0))) if approved_on else None
    workflow = []
    approval_fields = (
        ("pm", "Project Manager", "pm_name", "pm_signature", "pm_approval_status", "pm_approved_at"),
        ("moe", "Manager of Engineering", "eng_manager_name", "eng_manager_signature", "eng_manager_approval_status", "eng_manager_approved_at"),
        ("mop", "Manager of Projects", "manager_projects_name", "manager_projects_signature", "manager_projects_approval_status", "manager_projects_approved_at"),
        ("vp", "VP Operations", "vp_op_name", "vp_op_signature", "vp_op_approval_status", "vp_op_approved_at"),
    )
    for index, (key, role, user_field, signature_field, status_field, date_field) in enumerate(approval_fields, 1):
        user = _match_user(approval_names.get(key, ""))
        display_name = _clean(user.get_full_name()) if user else approval_names.get(key, "")
        status = "approved" if detected["signatures"].get(key) and user and approved_on else "pending"
        setattr(pr, user_field, user)
        setattr(pr, signature_field, f"{storage_url}#page=1" if status == "approved" else "")
        setattr(pr, status_field, status)
        setattr(pr, date_field, approved_at if status == "approved" else None)
        workflow.append({
            "step": index,
            "role": role,
            "user_id": str(user.id) if user else None,
            "user_name": display_name,
            "status": status,
            "approved_at": approved_at.isoformat() if status == "approved" else None,
            "source": "signed_purchase_requisition_pdf",
        })
        if signatures_verified and not user:
            workflow_issues.append(f"{role} signer '{approval_names.get(key, '')}' was not matched to a RADAI user.")

    if not approved_on:
        if detected["date_present"]:
            workflow_issues.append(
                "An approval date is visibly present, but handwriting OCR could not read it confidently; status was not auto-approved."
            )
        else:
            workflow_issues.append("No approval date was detected in the signed PR approval table.")
    missing_signatures = [role.upper() for role, present in detected["signatures"].items() if not present]
    if missing_signatures:
        workflow_issues.append(f"Signature evidence was not detected for: {', '.join(missing_signatures)}.")

    pr.approval_workflow_config = workflow
    if detected["all_four_signatures"] and approved_on and all(item["status"] == "approved" for item in workflow):
        if pr.status != "converted":
            pr.status = "approved"
        pr.current_approval_step = len(workflow)
        pr.approved_by = getattr(pr, "vp_op_name")
        pr.approved_at = approved_at
    pr.save()

    persisted = PurchaseRequisition.objects.get(pk=pr.pk)
    return {
        "success": True,
        "pr_id": str(persisted.id),
        "pr_number": persisted.pr_number,
        "status": persisted.status,
        "database_verified": True,
        "source_document_url": storage_url,
        "signature_verified": signatures_verified,
        "approval_detection": {
            **detected,
            "approval_date": approved_on.isoformat() if approved_on else None,
        },
        "mapping_issues": mapping_issues,
        "workflow_issues": workflow_issues,
        "manual_review_applied": bool(manual_overrides),
        "extracted_data": _serialize_extracted_fields(fields),
    }
