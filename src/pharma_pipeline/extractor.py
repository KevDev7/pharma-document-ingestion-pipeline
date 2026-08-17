import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

import fitz
import pytesseract
from PIL import Image

from .text import classify_document_type, clean_text


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
                raise ValueError("PDF contains no pages")

            for page_index, page in enumerate(document):
                direct_text = clean_text(page.get_text("text"))
                page_text = direct_text
                method = "digital"

                if len(direct_text) < self.ocr_min_chars and self.ocr_available:
                    ocr_text = clean_text(self._ocr_page(page))
                    if len(ocr_text) > len(direct_text):
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
