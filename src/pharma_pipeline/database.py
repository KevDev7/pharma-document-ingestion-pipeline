import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    trigger_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    discovered_files INTEGER NOT NULL DEFAULT 0,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    skipped_files INTEGER NOT NULL DEFAULT 0,
                    failed_files INTEGER NOT NULL DEFAULT 0,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    logical_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    page_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    ocr_page_count INTEGER NOT NULL,
                    parser_version TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    ingestion_run_id TEXT NOT NULL,
                    supersedes_document_id TEXT,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    ingested_at TEXT NOT NULL,
                    FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_runs(run_id),
                    FOREIGN KEY (supersedes_document_id) REFERENCES documents(document_id)
                );

                CREATE INDEX IF NOT EXISTS idx_documents_logical_name
                    ON documents(logical_name, is_current);

                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    document_type TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    character_count INTEGER NOT NULL,
                    word_count INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    PRIMARY KEY (document_id, page_number),
                    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    document_type TEXT NOT NULL,
                    character_count INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_document
                    ON chunks(document_id, page_number, chunk_index);

                CREATE TABLE IF NOT EXISTS ingestion_errors (
                    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    sha256 TEXT,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
                );
                """
            )

    def start_run(self, run_id: str, trigger_type: str, discovered_files: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs (
                    run_id, trigger_type, status, started_at, discovered_files
                ) VALUES (?, ?, 'running', ?, ?)
                """,
                (run_id, trigger_type, utc_now(), discovered_files),
            )

    def finish_run(self, run_id: str, metrics: Dict[str, object]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE ingestion_runs
                SET status = ?, completed_at = ?, processed_files = ?, skipped_files = ?,
                    failed_files = ?, page_count = ?, chunk_count = ?, duration_seconds = ?
                WHERE run_id = ?
                """,
                (
                    metrics["status"],
                    utc_now(),
                    metrics["processed_files"],
                    metrics["skipped_files"],
                    metrics["failed_files"],
                    metrics["page_count"],
                    metrics["chunk_count"],
                    metrics["duration_seconds"],
                    run_id,
                ),
            )

    def get_document_by_hash(self, sha256: str) -> Optional[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM documents WHERE sha256 = ?", (sha256,)
            ).fetchone()

    def get_current_document(self, logical_name: str) -> Optional[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM documents
                WHERE logical_name = ? AND is_current = 1
                ORDER BY ingested_at DESC
                LIMIT 1
                """,
                (logical_name,),
            ).fetchone()

    def save_document(
        self,
        document: Dict[str, object],
        pages: Iterable[Dict[str, object]],
        chunks: Iterable[Dict[str, object]],
    ) -> None:
        pages = list(pages)
        chunks = list(chunks)
        with self.connect() as connection:
            connection.execute(
                "UPDATE documents SET is_current = 0 WHERE logical_name = ? AND is_current = 1",
                (document["logical_name"],),
            )
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, sha256, logical_name, source_path, size_bytes, status,
                    page_count, chunk_count, ocr_page_count, parser_version,
                    chunker_version, ingestion_run_id, supersedes_document_id,
                    is_current, ingested_at
                ) VALUES (?, ?, ?, ?, ?, 'processed', ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    document["document_id"],
                    document["sha256"],
                    document["logical_name"],
                    document["source_path"],
                    document["size_bytes"],
                    document["page_count"],
                    document["chunk_count"],
                    document["ocr_page_count"],
                    document["parser_version"],
                    document["chunker_version"],
                    document["ingestion_run_id"],
                    document.get("supersedes_document_id"),
                    utc_now(),
                ),
            )
            connection.executemany(
                """
                INSERT INTO pages (
                    document_id, page_number, document_type, extraction_method,
                    character_count, word_count, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        page["document_id"],
                        page["page_number"],
                        page["document_type"],
                        page["extraction_method"],
                        page["character_count"],
                        page["word_count"],
                        page["text"],
                    )
                    for page in pages
                ],
            )
            connection.executemany(
                """
                INSERT INTO chunks (
                    chunk_id, document_id, page_number, chunk_index,
                    document_type, character_count, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk["chunk_id"],
                        chunk["document_id"],
                        chunk["page_number"],
                        chunk["chunk_index"],
                        chunk["document_type"],
                        chunk["character_count"],
                        chunk["text"],
                    )
                    for chunk in chunks
                ],
            )

    def update_source_path(self, document_id: str, source_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE documents SET source_path = ? WHERE document_id = ?",
                (source_path, document_id),
            )

    def record_error(
        self,
        run_id: str,
        source_path: str,
        sha256: Optional[str],
        error: Exception,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_errors (
                    run_id, source_path, sha256, error_type, error_message, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, source_path, sha256, type(error).__name__, str(error), utc_now()),
            )

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS document_versions,
                    COALESCE(SUM(is_current), 0) AS current_documents,
                    COALESCE(SUM(page_count), 0) AS pages,
                    COALESCE(SUM(chunk_count), 0) AS chunks,
                    COALESCE(SUM(ocr_page_count), 0) AS ocr_pages,
                    COALESCE(SUM(size_bytes), 0) AS source_bytes
                FROM documents
                """
            ).fetchone()
            runs = connection.execute(
                "SELECT COUNT(*) AS run_count FROM ingestion_runs"
            ).fetchone()
            errors = connection.execute(
                "SELECT COUNT(*) AS error_count FROM ingestion_errors"
            ).fetchone()
        return {
            **dict(counts),
            **dict(runs),
            **dict(errors),
            "database_path": str(self.path),
        }

    def fetch_all(self, query: str, parameters: tuple = ()) -> List[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(query, parameters).fetchall()
