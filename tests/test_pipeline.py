import shutil
from pathlib import Path
from typing import Optional

import fitz
import pytest
from PIL import Image, ImageDraw

from pharma_pipeline.config import Settings
from pharma_pipeline.extractor import extract_critical_fields
from pharma_pipeline.pipeline import IngestionPipeline


def make_digital_pdf(path: Path, page_texts) -> None:  # type: ignore[no-untyped-def]
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 540, 720), text, fontsize=11)
    document.save(path)
    document.close()


def make_scanned_pdf(path: Path, text: str) -> None:
    image = Image.new("RGB", (1400, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 150), text, fill="black", font_size=42)
    image_path = path.with_suffix(".png")
    image.save(image_path)

    document = fitz.open()
    page = document.new_page(width=700, height=250)
    page.insert_image(page.rect, filename=str(image_path))
    document.save(path)
    document.close()
    image_path.unlink()


def make_scanned_pdf_with_corrupt_text_layer(path: Path, text: str) -> None:
    image = Image.new("RGB", (1400, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 150), text, fill="black", font_size=42)
    image_path = path.with_suffix(".png")
    image.save(image_path)

    document = fitz.open()
    page = document.new_page(width=700, height=250)
    page.insert_image(page.rect, filename=str(image_path))
    page.insert_textbox(
        fitz.Rect(20, 20, 680, 80),
        "l " * 30,
        fontsize=5,
        render_mode=3,
    )
    document.save(path)
    document.close()
    image_path.unlink()


def make_scanned_pdf_with_correct_text_layer(
    path: Path, text: str, layer_text: Optional[str] = None
) -> None:
    image = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.multiline_text((60, 100), text, fill="black", font_size=34, spacing=20)
    image_path = path.with_suffix(".png")
    image.save(image_path)

    document = fitz.open()
    page = document.new_page(width=700, height=350)
    page.insert_image(page.rect, filename=str(image_path))
    page.insert_textbox(
        fitz.Rect(30, 30, 670, 320),
        layer_text or text,
        fontsize=10,
        render_mode=3,
    )
    document.save(path)
    document.close()
    image_path.unlink()


@pytest.fixture
def pipeline(tmp_path: Path) -> IngestionPipeline:
    return IngestionPipeline(Settings.from_root(tmp_path))


def test_ingestion_persists_lineage_and_skips_duplicate(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "quality.pdf"
    make_digital_pdf(
        pdf_path,
        [
            "Certificate of Quality\nProduct: Flow Kit\nLot Number: 12345678\n" * 10,
            "Packaging Component Specification\nPart Number: PKG-100\n" * 10,
        ],
    )

    first = pipeline.ingest_paths([pdf_path])
    second = pipeline.ingest_paths([pdf_path])

    assert first["processed_files"] == 1
    assert first["page_count"] == 2
    assert first["chunk_count"] >= 2
    assert second["skipped_files"] == 1

    pages = pipeline.database.fetch_all(
        "SELECT page_number, document_type FROM pages ORDER BY page_number"
    )
    assert [row["document_type"] for row in pages] == [
        "Certificate of Quality",
        "Packaging Specification",
    ]


def test_critical_fields_preserve_spaced_identifiers() -> None:
    fields = extract_critical_fields(
        "Lot Number:\n18356721\n"
        "Product Article Number: 28 9301 82\n"
        "Expiration Date: 2026-03-15\n"
        "Release Status: Pass"
    )

    assert fields == {
        "lot_number": "18356721",
        "article_number": "28930182",
        "expiration_date": "20260315",
        "release_status": "pass",
    }


def test_changed_file_supersedes_current_version(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "quality.pdf"
    make_digital_pdf(pdf_path, ["Certificate of Quality\nLot Number: FIRST\n" * 8])
    first = pipeline.ingest_paths([pdf_path])

    pdf_path.unlink()
    make_digital_pdf(pdf_path, ["Certificate of Quality\nLot Number: SECOND\n" * 8])
    second = pipeline.ingest_paths([pdf_path])

    assert first["processed_files"] == 1
    assert second["processed_files"] == 1
    assert second["results"][0]["supersedes_document_id"] == first["results"][0]["document_id"]

    rows = pipeline.database.fetch_all(
        "SELECT is_current FROM documents WHERE logical_name = 'quality.pdf' ORDER BY ingested_at"
    )
    assert [row["is_current"] for row in rows] == [0, 1]


def test_invalid_pdf_is_recorded_and_quarantined(
    pipeline: IngestionPipeline,
) -> None:
    invalid = pipeline.settings.incoming_dir / "broken.pdf"
    invalid.write_text("not a PDF", encoding="utf-8")

    result = pipeline.scan_incoming()

    assert result["failed_files"] == 1
    assert not invalid.exists()
    assert any(pipeline.settings.quarantine_dir.glob("broken*.pdf"))
    assert pipeline.database.summary()["error_count"] == 1


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_image_only_page_uses_ocr(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    make_scanned_pdf(pdf_path, "Certificate of Quality Lot Number 87654321")

    result = pipeline.ingest_paths([pdf_path])

    assert result["processed_files"] == 1
    assert result["results"][0]["ocr_page_count"] == 1
    page = pipeline.database.fetch_all(
        "SELECT extraction_method, text FROM pages WHERE document_id = ?",
        (result["results"][0]["document_id"],),
    )[0]
    assert page["extraction_method"] == "ocr"
    assert "87654321" in page["text"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_corrupt_hidden_text_layer_uses_ocr(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "corrupt-layer.pdf"
    make_scanned_pdf_with_corrupt_text_layer(
        pdf_path,
        "Lot Number 24681357",
    )

    result = pipeline.ingest_paths([pdf_path])

    assert result["processed_files"] == 1
    assert result["results"][0]["ocr_page_count"] == 1
    page = pipeline.database.fetch_all(
        "SELECT extraction_method, text FROM pages WHERE document_id = ?",
        (result["results"][0]["document_id"],),
    )[0]
    assert page["extraction_method"] == "ocr"
    assert "24681357" in page["text"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_correct_hidden_text_layer_is_preserved(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "correct-layer.pdf"
    expected_text = (
        "Certificate of Quality\n"
        "Lot Number 13572468\n"
        "Package Integrity Pass\n"
        "Release Status Approved"
    )
    make_scanned_pdf_with_correct_text_layer(pdf_path, expected_text)

    result = pipeline.ingest_paths([pdf_path])

    page = pipeline.database.fetch_all(
        "SELECT extraction_method, text FROM pages WHERE document_id = ?",
        (result["results"][0]["document_id"],),
    )[0]
    assert page["extraction_method"] == "digital"
    assert "Integrity Pass" in page["text"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_critical_field_conflict_fails_ingestion(
    pipeline: IngestionPipeline, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "conflict-layer.pdf"
    visible_text = (
        "Certificate of Quality\n"
        "Lot Number: 18356721\n"
        "Article Number: 28 9301 82\n"
        "Release Status: Pass\n"
        "Package Integrity: Pass"
    )
    hidden_text = (
        "Certificate of Quality\n"
        "Lot Number: 18356722\n"
        "Article Number: 28 9301 83\n"
        "Release Status: Fail\n"
        "Package Integrity: Pass"
    )
    make_scanned_pdf_with_correct_text_layer(
        pdf_path, visible_text, layer_text=hidden_text
    )

    result = pipeline.ingest_paths([pdf_path])

    assert result["failed_files"] == 1
    assert result["results"][0]["error_type"] == "OcrConflictError"
    assert pipeline.database.summary()["document_versions"] == 0
