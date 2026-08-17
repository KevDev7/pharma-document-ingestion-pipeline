import hashlib
import json
import platform
import re
import shutil
import statistics
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import fitz
import pytesseract
from PIL import Image, ImageDraw, ImageEnhance

from .extractor import (
    OcrConflictError,
    PdfExtractor,
    page_has_full_page_image,
    should_use_ocr,
)
from .text import clean_text


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _percentile(values: Sequence[float], percentile: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def _word_error_rate(reference: str, hypothesis: str) -> float:
    expected = _normalize(reference).split()
    actual = _normalize(hypothesis).split()
    previous = list(range(len(actual) + 1))
    for expected_word in expected:
        current = [previous[0] + 1]
        for column, actual_word in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected_word != actual_word),
                )
            )
        previous = current
    return previous[-1] / max(len(expected), 1)


def _render_page(source: fitz.Page, dpi: int, degraded: bool) -> bytes:
    pixmap = source.get_pixmap(dpi=dpi, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    if degraded:
        image = image.convert("L")
        image = ImageEnhance.Contrast(image).enhance(0.55)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=45 if degraded else 88)
    return buffer.getvalue()


def _write_variant(
    source_path: Path,
    page_number: int,
    variant: str,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(source_path) as source_document:
        source_page = source_document[page_number - 1]
        output = fitz.open()
        if variant == "digital_control":
            output.insert_pdf(
                source_document,
                from_page=page_number - 1,
                to_page=page_number - 1,
            )
        else:
            degraded = variant == "degraded_scan_120dpi"
            image_bytes = _render_page(source_page, 120 if degraded else 200, degraded)
            page = output.new_page(width=source_page.rect.width, height=source_page.rect.height)
            page.insert_image(page.rect, stream=image_bytes)
            if variant == "corrupt_text_layer_200dpi":
                page.insert_textbox(
                    fitz.Rect(24, 24, source_page.rect.width - 24, 80),
                    "l " * 120,
                    fontsize=5,
                    render_mode=3,
                )
            elif variant == "corrupt_plausible_layer_200dpi":
                page.insert_textbox(
                    fitz.Rect(24, 24, source_page.rect.width - 24, 120),
                    (
                        "Certificate of Analysis Product 8KTA L0t 183S6721 "
                        "Result Conf0rms Article Nurnber 28930182 Release Approved"
                    ),
                    fontsize=7,
                    render_mode=3,
                )
            elif variant == "correct_text_layer_200dpi":
                page.insert_textbox(
                    fitz.Rect(24, 24, source_page.rect.width - 24, source_page.rect.height - 24),
                    source_page.get_text("text"),
                    fontsize=4,
                    render_mode=3,
                )
        output.save(destination)
        output.close()


def build_stress_corpus(
    source_dir: Path,
    output_dir: Path,
    manifest_path: Path,
) -> List[Dict[str, object]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = []
    for page_spec in manifest["pages"]:
        source_path = source_dir / page_spec["filename"]
        if not source_path.exists():
            raise FileNotFoundError(f"Missing source corpus PDF: {source_path}")
        expected_hash = page_spec.get("source_sha256")
        if expected_hash:
            actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(f"Source hash mismatch for {source_path.name}")
        with fitz.open(source_path) as document:
            if page_spec["page"] < 1 or page_spec["page"] > document.page_count:
                raise ValueError(f"Invalid source page for {source_path.name}")
            reference_text = clean_text(document[page_spec["page"] - 1].get_text("text"))
        if _normalize(page_spec["key_phrase"]) not in _normalize(reference_text):
            raise ValueError(
                f"Key phrase is absent from {source_path.name} page {page_spec['page']}"
            )

        for variant_spec in manifest["variants"]:
            case_name = f"{page_spec['document_id']}-p{page_spec['page']:04d}-{variant_spec['name']}"
            generated_path = output_dir / f"{case_name}.pdf"
            _write_variant(
                source_path,
                int(page_spec["page"]),
                variant_spec["name"],
                generated_path,
            )
            cases.append(
                {
                    "case_id": case_name,
                    "source_document_id": page_spec["document_id"],
                    "source_filename": page_spec["filename"],
                    "source_page": page_spec["page"],
                    "variant": variant_spec["name"],
                    "should_ocr": variant_spec["should_ocr"],
                    "expected_method": variant_spec.get(
                        "expected_method", "ocr" if variant_spec["should_ocr"] else "digital"
                    ),
                    "key_phrase": page_spec["key_phrase"],
                    "reference_text": reference_text,
                    "generated_path": str(generated_path),
                    "synthetic": False,
                }
            )

    for control in manifest.get("synthetic_controls", []):
        case_name = f"synthetic-{control['name']}"
        generated_path = output_dir / f"{case_name}.pdf"
        output = fitz.open()
        page = output.new_page()
        page.insert_textbox(
            fitz.Rect(72, 72, 540, 720),
            control["text"],
            fontsize=11,
        )
        output.save(generated_path)
        output.close()
        cases.append(
            {
                "case_id": case_name,
                "source_document_id": case_name,
                "source_filename": None,
                "source_page": 1,
                "variant": control["name"],
                "should_ocr": control["should_ocr"],
                "expected_method": control.get("expected_method", "digital"),
                "key_phrase": "",
                "reference_text": clean_text(control["text"]),
                "generated_path": str(generated_path),
                "synthetic": True,
            }
        )

    for control in manifest.get("synthetic_scan_controls", []):
        case_name = f"synthetic-{control['name']}"
        generated_path = output_dir / f"{case_name}.pdf"
        image = Image.new("RGB", (1400, 900), "white")
        draw = ImageDraw.Draw(image)
        draw.multiline_text(
            (90, 100),
            control["visible_text"],
            fill="black",
            font_size=38,
            spacing=24,
        )
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        output = fitz.open()
        page = output.new_page(width=700, height=450)
        page.insert_image(page.rect, stream=buffer.getvalue())
        page.insert_textbox(
            fitz.Rect(40, 40, 660, 410),
            control["hidden_text"],
            fontsize=10,
            render_mode=3,
        )
        output.save(generated_path)
        output.close()
        cases.append(
            {
                "case_id": case_name,
                "source_document_id": case_name,
                "source_filename": None,
                "source_page": 1,
                "variant": control["name"],
                "should_ocr": control["should_ocr"],
                "expected_method": control["expected_method"],
                "key_phrase": "",
                "reference_text": clean_text(control["visible_text"]),
                "generated_path": str(generated_path),
                "synthetic": True,
            }
        )
    return cases


def _routing_metrics(
    labels_and_predictions: Iterable[Tuple[bool, bool]],
) -> Dict[str, object]:
    pairs = list(labels_and_predictions)
    true_positive = sum(expected and predicted for expected, predicted in pairs)
    false_positive = sum(not expected and predicted for expected, predicted in pairs)
    false_negative = sum(expected and not predicted for expected, predicted in pairs)
    true_negative = sum(not expected and not predicted for expected, predicted in pairs)
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-12), 4),
    }


def _ocr_image(pdf_path: Path, config: str) -> Tuple[str, float]:
    with fitz.open(pdf_path) as document:
        page = document[0]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    started = time.perf_counter()
    text = clean_text(pytesseract.image_to_string(image, config=config))
    return text, time.perf_counter() - started


def run_ocr_benchmark(
    source_dir: Path,
    output_dir: Path,
    manifest_path: Path,
) -> Dict[str, object]:
    if shutil.which("tesseract") is None:
        raise RuntimeError("Tesseract is required for the OCR benchmark")
    cases = build_stress_corpus(source_dir, output_dir, manifest_path)
    extractor = PdfExtractor()
    routing_rows = []
    production_rows = []
    for case in cases:
        pdf_path = Path(str(case["generated_path"]))
        with fitz.open(pdf_path) as document:
            page = document[0]
            embedded_text = clean_text(page.get_text("text"))
            full_page_image = page_has_full_page_image(page)
        baseline_prediction = len(embedded_text) < 50
        quality_prediction = should_use_ocr(
            embedded_text,
            min_chars=50,
            has_full_page_image=full_page_image,
        )
        routing_rows.append(
            {
                "case_id": case["case_id"],
                "variant": case["variant"],
                "expected_ocr": case["should_ocr"],
                "embedded_characters": len(embedded_text),
                "full_page_image": full_page_image,
                "character_threshold_prediction": baseline_prediction,
                "quality_router_prediction": quality_prediction,
            }
        )

        started = time.perf_counter()
        try:
            extracted = extractor.extract(pdf_path)[0]
            final_method = extracted.extraction_method
            final_text = extracted.text
        except OcrConflictError:
            final_method = "rejected"
            final_text = ""
        production_duration = time.perf_counter() - started
        normalized_output = _normalize(final_text)
        key_phrase = _normalize(str(case["key_phrase"]))
        production_rows.append(
            {
                "case_id": case["case_id"],
                "variant": case["variant"],
                "expected_method": case["expected_method"],
                "final_method": final_method,
                "method_correct": final_method == case["expected_method"],
                "key_phrase_recovered": not key_phrase or key_phrase in normalized_output,
                "word_error_rate": None
                if final_method == "rejected"
                else round(_word_error_rate(str(case["reference_text"]), final_text), 4),
                "duration_seconds": round(production_duration, 6),
            }
        )

    visual_cases = [
        case
        for case in cases
        if case["variant"] in {"image_scan_200dpi", "degraded_scan_120dpi"}
    ]
    configurations_to_test = (("psm3_auto", "--psm 3"), ("psm6_block", "--psm 6"))
    if visual_cases:
        for _, config in configurations_to_test:
            _ocr_image(Path(str(visual_cases[0]["generated_path"])), config)

    ocr_rows = []
    for case_index, case in enumerate(visual_cases):
        ordered_configs = (
            configurations_to_test
            if case_index % 2 == 0
            else tuple(reversed(configurations_to_test))
        )
        for name, config in ordered_configs:
            ocr_text, duration = _ocr_image(Path(str(case["generated_path"])), config)
            normalized_reference = _normalize(str(case["reference_text"]))
            normalized_ocr = _normalize(ocr_text)
            ocr_rows.append(
                {
                    "case_id": case["case_id"],
                    "variant": case["variant"],
                    "configuration": name,
                    "duration_seconds": round(duration, 6),
                    "word_error_rate": round(
                        _word_error_rate(str(case["reference_text"]), ocr_text), 4
                    ),
                    "character_similarity": round(
                        SequenceMatcher(None, normalized_reference, normalized_ocr).ratio(),
                        4,
                    ),
                    "key_phrase_recovered": _normalize(str(case["key_phrase"]))
                    in normalized_ocr,
                }
            )

    configurations = {}
    for configuration in sorted({row["configuration"] for row in ocr_rows}):
        rows = [row for row in ocr_rows if row["configuration"] == configuration]
        durations = [float(row["duration_seconds"]) for row in rows]
        configurations[configuration] = {
            "pages": len(rows),
            "mean_word_error_rate": round(
                statistics.mean(float(row["word_error_rate"]) for row in rows), 4
            ),
            "mean_character_similarity": round(
                statistics.mean(float(row["character_similarity"]) for row in rows), 4
            ),
            "key_phrase_accuracy": round(
                sum(bool(row["key_phrase_recovered"]) for row in rows) / len(rows), 4
            ),
            "seconds_per_page": {
                "p50": round(statistics.median(durations), 4),
                "p95": round(_percentile(durations, 95), 4),
            },
        }

    core_pages = 0
    baseline_routes = 0
    quality_routes = 0
    additional_quality_routes = []
    for source_path in sorted(source_dir.glob("*.pdf")):
        with fitz.open(source_path) as document:
            for page_index, page in enumerate(document):
                direct_text = clean_text(page.get_text("text"))
                baseline_prediction = len(direct_text) < 50
                quality_prediction = should_use_ocr(
                    direct_text,
                    min_chars=50,
                    has_full_page_image=page_has_full_page_image(page),
                )
                core_pages += 1
                baseline_routes += int(baseline_prediction)
                quality_routes += int(quality_prediction)
                if quality_prediction and not baseline_prediction:
                    additional_quality_routes.append(
                        {
                            "filename": source_path.name,
                            "page": page_index + 1,
                            "embedded_characters": len(direct_text),
                        }
                    )

    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pymupdf": fitz.__version__,
            "tesseract": str(pytesseract.get_tesseract_version()).splitlines()[0],
        },
        "manifest": manifest_path.name,
        "source_pages": len(
            {case["source_document_id"] for case in cases if not case["synthetic"]}
        ),
        "generated_cases": len(cases),
        "routing": {
            "character_threshold": _routing_metrics(
                (bool(row["expected_ocr"]), bool(row["character_threshold_prediction"]))
                for row in routing_rows
            ),
            "quality_aware": _routing_metrics(
                (bool(row["expected_ocr"]), bool(row["quality_router_prediction"]))
                for row in routing_rows
            ),
        },
        "core_corpus_routing_audit": {
            "pages": core_pages,
            "character_threshold_routes": baseline_routes,
            "quality_router_routes": quality_routes,
            "additional_quality_routes": len(additional_quality_routes),
            "additional_route_examples": additional_quality_routes[:10],
        },
        "production_extractor": {
            "cases": len(production_rows),
            "correct_method_cases": sum(
                bool(row["method_correct"]) for row in production_rows
            ),
            "method_accuracy": round(
                sum(bool(row["method_correct"]) for row in production_rows)
                / len(production_rows),
                4,
            ),
            "ocr_scenarios": sum(
                row["expected_method"] == "ocr" for row in production_rows
            ),
            "rejected_conflict_scenarios": sum(
                row["final_method"] == "rejected" for row in production_rows
            ),
            "ocr_scenario_phrase_accuracy": round(
                sum(
                    row["expected_method"] == "ocr" and row["key_phrase_recovered"]
                    for row in production_rows
                )
                / max(
                    sum(row["expected_method"] == "ocr" for row in production_rows),
                    1,
                ),
                4,
            ),
        },
        "ocr_configurations": configurations,
        "routing_cases": routing_rows,
        "production_cases": production_rows,
        "ocr_cases": ocr_rows,
        "notes": [
            "Stress PDFs are deterministic derivatives of frozen FDA corpus pages and are not committed.",
            "Digital source text is the reference; this is a controlled engineering benchmark, not independently transcribed ground truth.",
            "Character similarity uses SequenceMatcher; word error rate uses token-level edit distance.",
            "OCR configuration metrics use 12 unique visible scan images; hidden-text variants are evaluated only as routing scenarios.",
            "A matched-length critical-field disagreement is expected to fail extraction rather than select either conflicting value.",
            "OCR latency excludes PDF rasterization, includes only the Tesseract call, uses one warm-up per configuration, and alternates execution order by page.",
        ],
    }
