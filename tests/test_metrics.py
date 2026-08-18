import json
from pathlib import Path

from pharma_pipeline.cli import main
from pharma_pipeline.config import Settings
from pharma_pipeline.metrics import export_operational_metrics
from pharma_pipeline.pipeline import IngestionPipeline

from test_pipeline import make_digital_pdf


def test_operational_metrics_separate_current_and_historical_versions(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "quality.pdf"
    make_digital_pdf(pdf_path, ["Certificate of Quality\nLot Number: FIRST\n" * 8])
    pipeline.ingest_paths([pdf_path], trigger_type="file_event")
    pipeline.ingest_paths([pdf_path], trigger_type="file_event")

    pdf_path.unlink()
    make_digital_pdf(pdf_path, ["Certificate of Quality\nLot Number: SECOND\n" * 8])
    pipeline.ingest_paths([pdf_path], trigger_type="file_event")
    with pipeline.database.connect() as connection:
        current_id = connection.execute(
            "SELECT document_id FROM documents WHERE is_current = 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE pages SET extraction_method = 'ocr' WHERE document_id = ?",
            (current_id,),
        )
        connection.execute(
            "UPDATE documents SET ocr_page_count = 1 WHERE document_id = ?",
            (current_id,),
        )

    invalid_path = tmp_path / "broken.pdf"
    invalid_path.write_text("not a PDF", encoding="utf-8")
    pipeline.ingest_paths([invalid_path], trigger_type="file_event")

    metrics = export_operational_metrics(pipeline.database)

    assert metrics["documents"]["document_versions"] == 2
    assert metrics["documents"]["current_documents"] == 1
    assert metrics["documents"]["superseded_versions"] == 1
    assert metrics["documents"]["historical_pages"] == 2
    assert metrics["documents"]["current_pages"] == 1
    assert metrics["ingestion"]["runs"] == 4
    assert metrics["ingestion"]["processed_files"] == 2
    assert metrics["ingestion"]["skipped_files"] == 1
    assert metrics["ingestion"]["failed_files"] == 1
    assert metrics["ingestion"]["trigger_counts"] == {"file_event": 4}
    assert metrics["ingestion"]["skip_rate"] == 0.25
    assert metrics["ingestion"]["failure_rate"] == 0.25
    assert metrics["extraction"]["current_page_methods"] == {"ocr": 1}
    assert metrics["extraction"]["current_ocr_rate"] == 1.0
    assert metrics["errors"]["total"] == 1
    assert sum(metrics["errors"]["by_type"].values()) == 1
    assert metrics["search_index"]["status"] == "healthy"
    assert metrics["search_index"]["indexed_chunks"] == metrics["documents"]["current_chunks"]


def test_export_metrics_cli_writes_json(tmp_path: Path) -> None:
    output_path = tmp_path / "artifacts" / "metrics.json"

    main(["--root", str(tmp_path), "export-metrics", "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["documents"]["current_documents"] == 0
    assert payload["search_index"]["status"] == "healthy"
