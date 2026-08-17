import json
from pathlib import Path

import fitz
import pytest

from pharma_pipeline.corpus import download_manifest, load_manifest, summarize_corpus


def make_pdf(path: Path, text: str = "FDA pharmaceutical quality guidance") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def manifest_for(source_pdf: Path) -> dict:
    return {
        "schema_version": 1,
        "collection": {"name": "test"},
        "documents": [
            {
                "id": "test-001",
                "title": "Test Guidance",
                "document_type": "FDA Guidance",
                "topic": "Quality",
                "publisher": "U.S. Food and Drug Administration",
                "landing_page_url": "https://www.fda.gov/test",
                "pdf_url": source_pdf.as_uri(),
                "filename": "test-guidance.pdf",
            }
        ],
    }


def test_download_manifest_records_reproducible_receipt(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_for(source)), encoding="utf-8")
    destination = tmp_path / "raw"
    receipts = tmp_path / "state" / "downloads.jsonl"

    first = download_manifest(manifest_path, destination, receipts)
    second = download_manifest(manifest_path, destination, receipts)

    assert first["downloaded"] == 1
    assert first["failed"] == 0
    assert second["skipped"] == 1
    assert (destination / "test-guidance.pdf").exists()
    lines = receipts.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["sha256"] == json.loads(lines[1])["sha256"]

    summary = summarize_corpus(destination)
    assert summary["valid_pdf_files"] == 1
    assert summary["total_pages"] == 1


def test_manifest_rejects_path_traversal_filename(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    manifest = manifest_for(source)
    manifest["documents"][0]["filename"] = "../outside.pdf"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsafe or invalid"):
        load_manifest(manifest_path)


def test_manifest_rejects_duplicate_ids(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    manifest = manifest_for(source)
    duplicate = dict(manifest["documents"][0])
    duplicate["filename"] = "second.pdf"
    manifest["documents"].append(duplicate)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate corpus document id"):
        load_manifest(manifest_path)


def test_download_rejects_content_that_changed_from_frozen_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    manifest = manifest_for(source)
    manifest["documents"][0]["expected_sha256"] = "0" * 64
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = download_manifest(
        manifest_path,
        tmp_path / "raw",
        tmp_path / "state" / "downloads.jsonl",
    )

    assert result["failed"] == 1
    assert result["receipts"][0]["error_type"] == "ValueError"
    assert "frozen manifest" in result["receipts"][0]["error_message"]
