"""
Spec Customization — Paper Spec Extraction Service
===================================================

Engine waterfall (soft-coded via SPEC_EXTRACTION_CONFIG['ai_engines']):

  1. pymupdf_text  — free, instant; if text-layer is rich we skip AI entirely.
  2. gemini_vision — primary AI (1M context, cheap).
  3. openai_vision — fallback AI (GPT-4o).
  4. tesseract     — last resort for scanned pages.

Mirrors the pattern used by `InstrumentIndexService` but specialised to
piping-class structured extraction.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .config import SPEC_EXTRACTION_CONFIG
from .prompts import SYSTEM_PROMPT, EXTRACT_PIPING_CLASS_PROMPT

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse model JSON output that may be wrapped in ```json fences
# ─────────────────────────────────────────────────────────────────────────────
def _extract_json_blob(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    # Strip code fences.
    s = raw.strip()
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', s, re.DOTALL | re.IGNORECASE)
    if fence_match:
        s = fence_match.group(1)
    # Find the outermost {...}.
    first = s.find('{')
    last = s.rfind('}')
    if first < 0 or last < 0 or last <= first:
        return None
    try:
        return json.loads(s[first:last + 1])
    except Exception as e:
        logger.warning("[SpecExtraction] JSON parse failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main service
# ─────────────────────────────────────────────────────────────────────────────
class PaperSpecExtractionService:
    """Stateless service — instantiate once per Celery task or per chunk."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**SPEC_EXTRACTION_CONFIG, **(config or {})}
        self._gemini_client = None
        self._openai_client = None
        self._gemini_quota_exceeded = False
        self._openai_quota_exceeded = False
        self._ai_pages_used = 0

    # ── Lazy client init ────────────────────────────────────────────────
    def _get_gemini(self):
        if self._gemini_quota_exceeded:
            return None
        if self._gemini_client is not None:
            return self._gemini_client
        try:
            from google import genai  # type: ignore
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                logger.info("[SpecExtraction] GEMINI_API_KEY not set — Gemini disabled")
                return None
            self._gemini_client = genai.Client(api_key=api_key)
            logger.info("[SpecExtraction] ✅ Gemini ready")
            return self._gemini_client
        except Exception as e:
            logger.info("[SpecExtraction] Gemini unavailable: %s", e)
            return None

    def _get_openai(self):
        if self._openai_quota_exceeded:
            return None
        if self._openai_client is not None:
            return self._openai_client
        try:
            import openai  # type: ignore
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                logger.info("[SpecExtraction] OPENAI_API_KEY not set — OpenAI disabled")
                return None
            self._openai_client = openai.OpenAI(api_key=api_key)
            logger.info("[SpecExtraction] ✅ OpenAI ready")
            return self._openai_client
        except Exception as e:
            logger.info("[SpecExtraction] OpenAI unavailable: %s", e)
            return None

    # ── PDF helpers ─────────────────────────────────────────────────────
    def get_page_count(self, pdf_path: str) -> int:
        try:
            import fitz
            with fitz.open(pdf_path) as doc:
                return doc.page_count
        except Exception as e:
            logger.warning("[SpecExtraction] page count failed: %s", e)
            try:
                import PyPDF2
                with open(pdf_path, 'rb') as f:
                    return len(PyPDF2.PdfReader(f).pages)
            except Exception:
                return 0

    def chunk_ranges(self, total_pages: int) -> List[Tuple[int, int]]:
        size = max(1, int(self.config["chunk_size_pages"]))
        out: List[Tuple[int, int]] = []
        for start in range(0, total_pages, size):
            end = min(start + size - 1, total_pages - 1)
            out.append((start, end))
        return out

    def extract_text_for_pages(self, pdf_path: str, start: int, end: int) -> List[str]:
        """Return per-page plain text from PyMuPDF."""
        texts: List[str] = []
        try:
            import fitz
            with fitz.open(pdf_path) as doc:
                for i in range(start, end + 1):
                    if i >= doc.page_count:
                        break
                    page = doc[i]
                    texts.append(page.get_text("text") or "")
        except Exception as e:
            logger.warning("[SpecExtraction] text extract failed: %s", e)
        return texts

    def render_pages_to_jpeg_b64(self, pdf_path: str, start: int, end: int) -> List[str]:
        """Render the requested pages as JPEG base64 strings."""
        out: List[str] = []
        try:
            import fitz
            with fitz.open(pdf_path) as doc:
                dpi = self.config["render_dpi"]
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                for i in range(start, end + 1):
                    if i >= doc.page_count:
                        break
                    page = doc[i]
                    pix = page.get_pixmap(matrix=mat)
                    img_bytes = pix.tobytes("jpeg", jpg_quality=self.config["jpeg_quality"])
                    out.append(base64.b64encode(img_bytes).decode("ascii"))
        except Exception as e:
            logger.warning("[SpecExtraction] render failed: %s", e)
        return out

    # ── Engine: PyMuPDF text-layer only (no AI) ─────────────────────────
    def extract_via_text_layer(self, pdf_path: str, start: int, end: int) -> Dict[str, Any]:
        """Detect piping-class headers in raw text — returns minimal classes."""
        texts = self.extract_text_for_pages(pdf_path, start, end)
        full_text = "\n".join(texts)
        header_re = re.compile(self.config["piping_class_header_regex"], re.IGNORECASE)
        classes: Dict[str, Dict[str, Any]] = {}
        for m in header_re.finditer(full_text):
            code = m.group("code").upper()
            classes.setdefault(code, {
                "class_code":          code,
                "class_full_code":     m.group(0).strip(),
                "material_grade":      "",
                "pressure_rating":     "",
                "flange_facing":       "",
                "corrosion_allowance": "",
                "service_list":        [],
                "pt_rating_table":     [],
                "components":          [],
                "raw_notes":           "",
                "confidence":          0.30,
                "_engine":             "pymupdf_text",
                "_source_pages":       [start + 1, end + 1],
            })
        return {"piping_classes": list(classes.values()), "page_text_chars": len(full_text)}

    # ── Engine: Gemini Vision ───────────────────────────────────────────
    def extract_via_gemini(self, images_b64: List[str], page_range: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        client = self._get_gemini()
        if not client or not images_b64:
            return None
        try:
            from google.genai import types  # type: ignore
            parts: List[Any] = [SYSTEM_PROMPT + "\n\n" + EXTRACT_PIPING_CLASS_PROMPT]
            for b64 in images_b64:
                parts.append(types.Part.from_bytes(
                    data=base64.b64decode(b64),
                    mime_type="image/jpeg",
                ))
            resp = client.models.generate_content(
                model=self.config["gemini_model"],
                contents=parts,
                config=types.GenerateContentConfig(
                    temperature=self.config["gemini_temperature"],
                    response_mime_type="application/json",
                ),
            )
            blob = _extract_json_blob(resp.text or "")
            if not blob:
                return None
            for c in blob.get("piping_classes", []):
                c.setdefault("_engine", "gemini_vision")
                c.setdefault("_source_pages", [page_range[0] + 1, page_range[1] + 1])
            return blob
        except Exception as e:
            msg = str(e).lower()
            if "quota" in msg or "rate" in msg or "429" in msg:
                self._gemini_quota_exceeded = True
                logger.warning("[SpecExtraction] Gemini quota — disabling for remainder of job")
            else:
                logger.warning("[SpecExtraction] Gemini failed: %s", e)
            return None

    # ── Engine: OpenAI Vision ───────────────────────────────────────────
    def extract_via_openai(self, images_b64: List[str], page_range: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        client = self._get_openai()
        if not client or not images_b64:
            return None
        try:
            content: List[Dict[str, Any]] = [
                {"type": "text", "text": EXTRACT_PIPING_CLASS_PROMPT},
            ]
            for b64 in images_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            resp = client.chat.completions.create(
                model=self.config["openai_model"],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                max_tokens=self.config["openai_max_tokens"],
                temperature=self.config["openai_temperature"],
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
            blob = _extract_json_blob(raw)
            if not blob:
                return None
            for c in blob.get("piping_classes", []):
                c.setdefault("_engine", "openai_vision")
                c.setdefault("_source_pages", [page_range[0] + 1, page_range[1] + 1])
            return blob
        except Exception as e:
            msg = str(e).lower()
            if "quota" in msg or "rate" in msg or "429" in msg or "insufficient" in msg:
                self._openai_quota_exceeded = True
                logger.warning("[SpecExtraction] OpenAI quota — disabling for remainder of job")
            else:
                logger.warning("[SpecExtraction] OpenAI failed: %s", e)
            return None

    # ── Engine: Tesseract OCR fallback (text-only header detection) ─────
    def extract_via_tesseract(self, pdf_path: str, start: int, end: int) -> Optional[Dict[str, Any]]:
        try:
            import pytesseract
            import fitz
            from PIL import Image
            with fitz.open(pdf_path) as doc:
                all_text_parts: List[str] = []
                for i in range(start, end + 1):
                    if i >= doc.page_count:
                        break
                    page = doc[i]
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    all_text_parts.append(pytesseract.image_to_string(img) or "")
            ocr_text = "\n".join(all_text_parts)
            header_re = re.compile(self.config["piping_class_header_regex"], re.IGNORECASE)
            classes: List[Dict[str, Any]] = []
            for m in header_re.finditer(ocr_text):
                classes.append({
                    "class_code":      m.group("code").upper(),
                    "class_full_code": m.group(0).strip(),
                    "material_grade":  "",
                    "pressure_rating": "",
                    "flange_facing":   "",
                    "corrosion_allowance": "",
                    "service_list":    [],
                    "pt_rating_table": [],
                    "components":      [],
                    "raw_notes":       "",
                    "confidence":      0.25,
                    "_engine":         "tesseract",
                    "_source_pages":   [start + 1, end + 1],
                })
            return {"piping_classes": classes}
        except Exception as e:
            logger.info("[SpecExtraction] Tesseract not usable: %s", e)
            return None

    # ── Orchestrator: process one chunk via the engine waterfall ────────
    def extract_chunk(self, pdf_path: str, start: int, end: int) -> Dict[str, Any]:
        """Return {"piping_classes": [...], "engine_used": str}."""
        engines = self.config["ai_engines"]
        cost_skip_chars = int(self.config["skip_ai_if_text_chars_gte"])
        max_ai_pages = int(self.config["max_ai_pages_per_job"])
        page_count_in_chunk = (end - start + 1)

        # 1) Always try text-layer first — cheap, accurate on vector PDFs.
        text_result = self.extract_via_text_layer(pdf_path, start, end)
        text_chars = text_result.get("page_text_chars", 0)
        text_classes = text_result.get("piping_classes", [])

        # If text-layer is rich AND we already found ≥1 class, accept it
        # without hitting any AI (cost guard rail).
        if text_classes and text_chars >= cost_skip_chars * max(1, page_count_in_chunk):
            return {"piping_classes": text_classes, "engine_used": "pymupdf_text"}

        # Within AI budget?
        ai_budget_ok = (self._ai_pages_used + page_count_in_chunk) <= max_ai_pages

        if ai_budget_ok:
            images: Optional[List[str]] = None
            for engine in engines:
                if engine == "pymupdf_text":
                    continue  # already done
                if engine == "gemini_vision":
                    images = images or self.render_pages_to_jpeg_b64(pdf_path, start, end)
                    blob = self.extract_via_gemini(images, (start, end))
                    if blob and blob.get("piping_classes"):
                        self._ai_pages_used += page_count_in_chunk
                        return {"piping_classes": blob["piping_classes"], "engine_used": "gemini_vision"}
                elif engine == "openai_vision":
                    images = images or self.render_pages_to_jpeg_b64(pdf_path, start, end)
                    blob = self.extract_via_openai(images, (start, end))
                    if blob and blob.get("piping_classes"):
                        self._ai_pages_used += page_count_in_chunk
                        return {"piping_classes": blob["piping_classes"], "engine_used": "openai_vision"}
                elif engine == "tesseract":
                    blob = self.extract_via_tesseract(pdf_path, start, end)
                    if blob and blob.get("piping_classes"):
                        return {"piping_classes": blob["piping_classes"], "engine_used": "tesseract"}
        else:
            logger.info("[SpecExtraction] AI budget exhausted (%d/%d) — using text/tesseract only",
                        self._ai_pages_used, max_ai_pages)

        # Fallback: text-layer classes (may be empty) + tesseract last attempt
        if text_classes:
            return {"piping_classes": text_classes, "engine_used": "pymupdf_text"}
        blob = self.extract_via_tesseract(pdf_path, start, end)
        if blob and blob.get("piping_classes"):
            return {"piping_classes": blob["piping_classes"], "engine_used": "tesseract"}
        return {"piping_classes": [], "engine_used": "none"}

    # ── Merging chunk results ───────────────────────────────────────────
    @staticmethod
    def merge_classes(all_class_lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Deduplicate by class_code, prefer the entry with most components."""
        bucket: Dict[str, Dict[str, Any]] = {}
        for lst in all_class_lists:
            for cls in lst:
                code = (cls.get("class_code") or "").strip().upper()
                if not code:
                    continue
                existing = bucket.get(code)
                if existing is None:
                    bucket[code] = cls
                    continue
                # Prefer one with more components, else higher confidence.
                if len(cls.get("components", [])) > len(existing.get("components", [])):
                    bucket[code] = cls
                elif (cls.get("confidence", 0) or 0) > (existing.get("confidence", 0) or 0):
                    bucket[code] = cls
                # Merge page ranges.
                merged_pages = sorted(set(
                    (existing.get("_source_pages") or []) + (cls.get("_source_pages") or [])
                ))
                if merged_pages:
                    bucket[code]["_source_pages"] = [merged_pages[0], merged_pages[-1]]
        return sorted(bucket.values(), key=lambda c: c.get("class_code", ""))
