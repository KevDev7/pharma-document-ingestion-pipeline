import json
import shutil
from pathlib import Path

import fitz
import pytest

from pharma_pipeline.extractor import should_use_ocr
from pharma_pipeline.ocr_benchmark import build_stress_corpus, run_ocr_benchmark


def make_source_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        (
            "Contains Nonbinding Recommendations\n"
            "A manufacturer must successfully complete PPQ before commercial distribution.\n"
        )
        * 12,
        fontsize=11,
    )
    document.save(path)
    document.close()


def write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "variants": [
                    {"name": "digital_control", "should_ocr": False, "expected_method": "digital"},
                    {"name": "image_scan_200dpi", "should_ocr": True, "expected_method": "ocr"},
                    {"name": "degraded_scan_120dpi", "should_ocr": True, "expected_method": "ocr"},
                    {"name": "corrupt_text_layer_200dpi", "should_ocr": True, "expected_method": "ocr"},
                    {"name": "corrupt_plausible_layer_200dpi", "should_ocr": True, "expected_method": "ocr"},
                    {"name": "correct_text_layer_200dpi", "should_ocr": True, "expected_method": "digital"},
                ],
                "synthetic_controls": [
                    {
                        "name": "repetitive_digital_certificate",
                        "should_ocr": False,
                        "expected_method": "digital",
                        "text": "Certificate of Quality\nResult: Pass\n" * 12,
                    }
                ],
                "synthetic_scan_controls": [
                    {
                        "name": "matched_length_critical_field_conflict",
                        "should_ocr": True,
                        "expected_method": "rejected",
                        "visible_text": (
                            "Certificate of Quality\nLot Number: 18356721\n"
                            "Article Number: 28 9301 82\n"
                            "Release Status: Pass\n"
                        ),
                        "hidden_text": (
                            "Certificate of Quality\nLot Number: 18356722\n"
                            "Article Number: 28 9301 83\n"
                            "Release Status: Fail\n"
                        ),
                    }
                ],
                "pages": [
                    {
                        "document_id": "source",
                        "filename": "source.pdf",
                        "page": 1,
                        "key_phrase": "must successfully complete PPQ",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_quality_router_detects_corrupted_text_without_routing_clean_text() -> None:
    clean = "This is a complete and readable pharmaceutical quality record. " * 8
    corrupted = "l " * 120

    assert not should_use_ocr(clean)
    assert should_use_ocr(corrupted)
    assert should_use_ocr("")


def test_stress_corpus_exposes_hidden_text_routing_failure(tmp_path: Path) -> None:
    source_dir = tmp_path / "raw"
    output_dir = tmp_path / "scanned"
    source_dir.mkdir()
    make_source_pdf(source_dir / "source.pdf")
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest_path)

    cases = build_stress_corpus(source_dir, output_dir, manifest_path)
    embedded = {}
    for case in cases:
        with fitz.open(case["generated_path"]) as document:
            embedded[case["variant"]] = document[0].get_text("text")

    assert len(embedded["digital_control"]) >= 50
    assert len(embedded["image_scan_200dpi"]) == 0
    assert len(embedded["corrupt_text_layer_200dpi"]) >= 50
    assert len(embedded["corrupt_plausible_layer_200dpi"]) >= 50
    assert len(embedded["correct_text_layer_200dpi"]) >= 50
    assert not should_use_ocr(embedded["digital_control"])
    assert should_use_ocr(embedded["corrupt_text_layer_200dpi"])
    assert not should_use_ocr(embedded["repetitive_digital_certificate"])


def test_stress_corpus_rejects_changed_source_bytes(tmp_path: Path) -> None:
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    make_source_pdf(source_dir / "source.pdf")
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0]["source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Source hash mismatch"):
        build_stress_corpus(source_dir, tmp_path / "scanned", manifest_path)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_ocr_benchmark_reports_router_and_configuration_metrics(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    source_dir = tmp_path / "raw"
    output_dir = tmp_path / "scanned"
    source_dir.mkdir()
    make_source_pdf(source_dir / "source.pdf")
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest_path)
    expected_text = (
        "A manufacturer must successfully complete PPQ before commercial distribution."
    )

    monkeypatch.setattr(
        "pharma_pipeline.ocr_benchmark._ocr_image",
        lambda pdf_path, config: (expected_text, 0.01),
    )
    monkeypatch.setattr("pharma_pipeline.ocr_benchmark.shutil.which", lambda name: "/tesseract")

    result = run_ocr_benchmark(source_dir, output_dir, manifest_path)

    assert result["generated_cases"] == 8
    assert result["routing"]["character_threshold"]["recall"] < 1.0
    assert result["routing"]["quality_aware"]["recall"] == 1.0
    assert result["core_corpus_routing_audit"] == {
        "pages": 1,
        "character_threshold_routes": 0,
        "quality_router_routes": 0,
        "additional_quality_routes": 0,
        "additional_route_examples": [],
    }
    assert set(result["ocr_configurations"]) == {"psm3_auto", "psm6_block"}
    assert result["ocr_configurations"]["psm6_block"]["key_phrase_accuracy"] == 1.0
