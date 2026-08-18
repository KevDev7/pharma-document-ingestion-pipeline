import shutil
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

import fitz
import pytesseract
from PIL import Image

from .text import classify_document_type, clean_text


class OcrConflictError(ValueError):
    pass


class PdfValidationError(ValueError):
    pass


CRITICAL_FIELD_LABELS = {
    "lot_number": re.compile(r"^\s*lot\s+(?:number|no\.?)\b", re.I),
    "batch_number": re.compile(r"^\s*batch\s+(?:number|no\.?)\b", re.I),
    "article_number": re.compile(
        r"^\s*(?:product\s+)?(?:article|part)\s+(?:number|no\.?)\b", re.I
    ),
    "expiration_date": re.compile(r"^\s*(?:expiration|expiry)\s+date\b", re.I),
    "release_status": re.compile(r"^\s*release\s+status\b", re.I),
}


def extract_critical_fields(text: str) -> dict:
    fields = {}
    lines = text.splitlines()
    for line_index, line in enumerate(lines):
        for name, pattern in CRITICAL_FIELD_LABELS.items():
            match = pattern.match(line)
            if not match:
                continue
            value = line[match.end() :].strip(" \t:#-")
            if not value and line_index + 1 < len(lines):
                value = lines[line_index + 1].strip()
            canonical_value = re.sub(r"[^a-z0-9]", "", value.lower())
            if canonical_value:
                fields[name] = canonical_value
            break
    return fields


def page_has_full_page_image(page: fitz.Page, min_coverage: float = 0.80) -> bool:
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    for image in page.get_image_info():
        bounds = fitz.Rect(image["bbox"])
        if bounds.width * bounds.height / page_area >= min_coverage:
            return True
    return False


def text_has_fragmentation(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    if len(tokens) < 20:
        return False
    return sum(len(token) == 1 for token in tokens) / len(tokens) > 0.45


def should_use_ocr(
    text: str,
    min_chars: int = 50,
    has_full_page_image: bool = False,
) -> bool:
    """Route empty, scan-backed, or clearly fragmented text through OCR."""
    return len(text) < min_chars or has_full_page_image or text_has_fragmentation(text)


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    extraction_method: str
    document_type: str
    character_count: int
    word_count: int


class PdfExtractor:
    def __init__(self, ocr_min_chars: int = 50, render_scale: float = 2.0) -> None:
        self.ocr_min_chars = ocr_min_chars
        self.render_scale = render_scale

    @property
    def ocr_available(self) -> bool:
        return shutil.which("tesseract") is not None

    def extract(self, pdf_path: Path) -> List[ExtractedPage]:
        pages = []
        with fitz.open(pdf_path) as document:
            if document.page_count == 0:
                raise PdfValidationError("PDF contains no pages")

            for page_index, page in enumerate(document):
                direct_text = clean_text(page.get_text("text"))
                page_text = direct_text
                method = "digital"

                full_page_image = page_has_full_page_image(page)
                route_to_ocr = should_use_ocr(
                    direct_text,
                    self.ocr_min_chars,
                    has_full_page_image=full_page_image,
                )
                if route_to_ocr and self.ocr_available:
                    ocr_text = clean_text(self._ocr_page(page))
                    ocr_has_content = len(re.findall(r"[A-Za-z0-9]+", ocr_text)) >= 2
                    direct_fields = extract_critical_fields(direct_text)
                    ocr_fields = extract_critical_fields(ocr_text)
                    conflicts = {
                        field: (direct_fields[field], ocr_fields[field])
                        for field in direct_fields.keys() & ocr_fields.keys()
                        if direct_fields[field] != ocr_fields[field]
                    }
                    if full_page_image and conflicts:
                        names = ", ".join(sorted(conflicts))
                        raise OcrConflictError(
                            f"OCR conflicts with embedded critical fields: {names}"
                        )
                    replace_direct_text = len(ocr_text) > len(direct_text)
                    if text_has_fragmentation(direct_text) and ocr_has_content:
                        replace_direct_text = True
                    elif full_page_image and len(direct_text) >= self.ocr_min_chars:
                        replace_direct_text = len(ocr_text) > len(direct_text) * 1.25
                    if replace_direct_text:
                        page_text = ocr_text
                        method = "ocr"

                pages.append(
                    ExtractedPage(
                        page_number=page_index + 1,
                        text=page_text,
                        extraction_method=method,
                        document_type=classify_document_type(page_text),
                        character_count=len(page_text),
                        word_count=len(page_text.split()),
                    )
                )
        return pages

    def _ocr_page(self, page: fitz.Page) -> str:
        matrix = fitz.Matrix(self.render_scale, self.render_scale)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return pytesseract.image_to_string(image, config="--psm 6")
