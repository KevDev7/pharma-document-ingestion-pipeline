import sqlite3
from pathlib import Path

import fitz
import pytest

from pharma_pipeline.config import Settings
from pharma_pipeline.database import PipelineDatabase
from pharma_pipeline.pipeline import IngestionPipeline
from pharma_pipeline.search_benchmark import benchmark_search_index


def make_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 540, 720), text, fontsize=11)
    document.save(path)
    document.close()


def test_initialize_backfills_chunks_from_a_legacy_database(tmp_path: Path) -> None:
    database_path = tmp_path / "pipeline.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            logical_name TEXT NOT NULL,
            is_current INTEGER NOT NULL
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        INSERT INTO documents VALUES ('doc-1', 'hash-1', 'legacy.pdf', 1);
        INSERT INTO chunks VALUES (
            'chunk-1', 'doc-1', 2, 0, 'FDA Guidance', 44,
            'Sterility testing requires a validated method.'
        );
        """
    )
    connection.commit()
    connection.close()

    database = PipelineDatabase(database_path)
    database.initialize()

    results = database.search_chunks("validated sterility method")
    status = database.search_index_status()
    assert results[0]["chunk_id"] == "chunk-1"
    assert results[0]["page_number"] == 2
    assert status["status"] == "healthy"
    assert status["initial_backfill_chunks"] == 1
    assert status["indexed_chunks"] == 1


def test_initialize_recovers_an_interrupted_search_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "pipeline.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            logical_name TEXT NOT NULL,
            is_current INTEGER NOT NULL
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        INSERT INTO documents VALUES ('doc-1', 'hash-1', 'interrupted.pdf', 1);
        INSERT INTO chunks VALUES (
            'chunk-1', 'doc-1', 1, 0, 'FDA Guidance', 30,
            'Interrupted migration recovery term.'
        );
        CREATE VIRTUAL TABLE chunk_search USING fts5(
            text, content = 'chunks', content_rowid = 'rowid'
        );
        """
    )
    connection.commit()
    connection.close()

    database = PipelineDatabase(database_path)
    database.initialize()

    assert database.search_chunks("recovery term")[0]["chunk_id"] == "chunk-1"
    assert database.search_index_status()["schema_version"] == 2


def test_ingestion_updates_search_index_without_rebuilding(
    tmp_path: Path,
) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "quality.pdf"
    make_pdf(
        pdf_path,
        "Certificate of Quality\nLot Number: FIRSTUNIQUE\nSterility: Pass\n" * 12,
    )

    first = pipeline.ingest_paths([pdf_path])
    after_first = pipeline.database.search_index_status()
    duplicate = pipeline.ingest_paths([pdf_path])
    after_duplicate = pipeline.database.search_index_status()

    assert first["processed_files"] == 1
    assert duplicate["skipped_files"] == 1
    assert after_duplicate["indexed_chunks"] == after_first["indexed_chunks"]
    assert pipeline.database.search_chunks("FIRSTUNIQUE")[0]["filename"] == "quality.pdf"

    pdf_path.unlink()
    make_pdf(
        pdf_path,
        "Certificate of Quality\nLot Number: SECONDUNIQUE\nSterility: Pass\n" * 12,
    )
    second = pipeline.ingest_paths([pdf_path])

    assert second["processed_files"] == 1
    assert pipeline.database.search_chunks("FIRSTUNIQUE") == []
    current_results = pipeline.database.search_chunks(
        "SECONDUNIQUE", document_type="Certificate of Quality"
    )
    assert current_results[0]["document_id"] == second["results"][0]["document_id"]
    assert current_results[0]["document_sha256"] == second["results"][0]["sha256"]
    status = pipeline.database.search_index_status()
    assert status["indexed_chunks"] == second["results"][0]["chunk_count"]
    assert status["total_stored_chunks"] > status["indexed_chunks"]

    reopened = PipelineDatabase(pipeline.settings.database_path)
    reopened.initialize()
    assert reopened.search_chunks("SECONDUNIQUE")[0]["chunk_id"] == current_results[0][
        "chunk_id"
    ]


def test_chunk_text_update_and_delete_update_search_postings(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "quality.pdf"
    make_pdf(pdf_path, "Certificate of Quality\nORIGINALPOSTING\n" * 10)
    result = pipeline.ingest_paths([pdf_path])
    document_id = result["results"][0]["document_id"]
    chunk = pipeline.database.fetch_all(
        "SELECT rowid, chunk_id FROM chunks WHERE document_id = ? LIMIT 1", (document_id,)
    )[0]

    with pipeline.database.connect() as connection:
        connection.execute(
            "UPDATE chunks SET text = ? WHERE rowid = ?",
            ("UPDATEDPOSTING", chunk["rowid"]),
        )

    assert pipeline.database.search_chunks("ORIGINALPOSTING") == []
    assert pipeline.database.search_chunks("UPDATEDPOSTING")[0]["chunk_id"] == chunk["chunk_id"]

    with pipeline.database.connect() as connection:
        connection.execute("DELETE FROM chunks WHERE rowid = ?", (chunk["rowid"],))

    assert pipeline.database.search_chunks("UPDATEDPOSTING") == []
    assert pipeline.database.search_index_status()["status"] == "healthy"


def test_search_index_can_be_rebuilt_for_recovery(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "packaging.pdf"
    make_pdf(
        pdf_path,
        "Packaging Component Specification\nTamper-evident seal required.\n" * 10,
    )
    pipeline.ingest_paths([pdf_path])

    rebuilt = pipeline.database.rebuild_search_index()

    assert rebuilt["status"] == "rebuilt"
    assert rebuilt["indexed_chunks"] >= 1
    assert pipeline.database.search_chunks("tamper evident seal")
    assert pipeline.database.search_index_status()["last_rebuilt_at"] is not None


def test_failed_chunk_insert_rolls_back_version_and_search_changes(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "quality.pdf"
    make_pdf(pdf_path, "Certificate of Quality\nLot Number: SAFELOT\n" * 10)
    first = pipeline.ingest_paths([pdf_path])
    current_id = first["results"][0]["document_id"]
    existing_chunk = pipeline.database.fetch_all(
        "SELECT chunk_id FROM chunks WHERE document_id = ? LIMIT 1", (current_id,)
    )[0]["chunk_id"]
    run_id = "failed-index-transaction"
    pipeline.database.start_run(run_id, "test", 1)

    with pytest.raises(sqlite3.IntegrityError):
        pipeline.database.save_document(
            {
                "document_id": "replacement-document",
                "sha256": "replacement-hash",
                "logical_name": "quality.pdf",
                "source_path": str(pdf_path),
                "size_bytes": pdf_path.stat().st_size,
                "page_count": 0,
                "chunk_count": 1,
                "ocr_page_count": 0,
                "parser_version": "test",
                "chunker_version": "test",
                "ingestion_run_id": run_id,
                "supersedes_document_id": current_id,
            },
            [],
            [
                {
                    "chunk_id": existing_chunk,
                    "document_id": "replacement-document",
                    "page_number": 1,
                    "chunk_index": 0,
                    "document_type": "Certificate of Quality",
                    "character_count": 4,
                    "text": "fail",
                }
            ],
        )

    current = pipeline.database.get_current_document("quality.pdf")
    assert current["document_id"] == current_id
    assert pipeline.database.get_document_by_hash("replacement-hash") is None
    assert pipeline.database.search_chunks("SAFELOT")[0]["document_id"] == current_id


def test_search_index_benchmark_compares_incremental_update_and_rebuild(
    tmp_path: Path,
) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "quality.pdf"
    make_pdf(pdf_path, "Certificate of Quality\nRelease testing passed.\n" * 10)
    pipeline.ingest_paths([pdf_path])

    result = benchmark_search_index(pipeline.settings.database_path, runs=2)

    assert result["runs"] == 2
    assert result["source_index_chunks"] >= 1
    assert result["representative_document"]["filename"] == "quality.pdf"
    assert result["representative_document"]["incremental_chunks"] >= 1
    assert len(result["run_results"]) == 2
