import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

from .search import build_fts_query


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineDatabase:
    SEARCH_INDEX_SCHEMA_VERSION = 2

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

                CREATE TABLE IF NOT EXISTS search_index_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    initialized_at TEXT NOT NULL,
                    initial_backfill_chunks INTEGER NOT NULL,
                    initial_backfill_seconds REAL NOT NULL,
                    last_rebuilt_at TEXT
                );
                """
            )
            self._initialize_search_index(connection)

    @staticmethod
    def _create_search_triggers(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_search_insert
            AFTER INSERT ON chunks
            WHEN (SELECT is_current FROM documents WHERE document_id = new.document_id) = 1
            BEGIN
                INSERT INTO chunk_search(rowid, text) VALUES (new.rowid, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_search_delete
            AFTER DELETE ON chunks
            WHEN (SELECT is_current FROM documents WHERE document_id = old.document_id) = 1
            BEGIN
                INSERT INTO chunk_search(chunk_search, rowid, text)
                VALUES ('delete', old.rowid, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_search_update
            AFTER UPDATE OF text ON chunks
            WHEN (SELECT is_current FROM documents WHERE document_id = new.document_id) = 1
            BEGIN
                INSERT INTO chunk_search(chunk_search, rowid, text)
                VALUES ('delete', old.rowid, old.text);
                INSERT INTO chunk_search(rowid, text) VALUES (new.rowid, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_search_deactivate
            AFTER UPDATE OF is_current ON documents
            WHEN old.is_current = 1 AND new.is_current = 0
            BEGIN
                INSERT INTO chunk_search(chunk_search, rowid, text)
                SELECT 'delete', rowid, text
                FROM chunks
                WHERE document_id = old.document_id;
            END;

            CREATE TRIGGER IF NOT EXISTS documents_search_activate
            AFTER UPDATE OF is_current ON documents
            WHEN old.is_current = 0 AND new.is_current = 1
            BEGIN
                INSERT INTO chunk_search(rowid, text)
                SELECT rowid, text
                FROM chunks
                WHERE document_id = new.document_id;
            END;
            """
        )

    @staticmethod
    def _drop_search_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS chunks_search_insert;
            DROP TRIGGER IF EXISTS chunks_search_delete;
            DROP TRIGGER IF EXISTS chunks_search_update;
            DROP TRIGGER IF EXISTS documents_search_deactivate;
            DROP TRIGGER IF EXISTS documents_search_activate;
            DROP TABLE IF EXISTS chunk_search;
            DROP VIEW IF EXISTS search_current_chunks;
            """
        )

    @staticmethod
    def _create_search_table(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE VIEW IF NOT EXISTS search_current_chunks AS
            SELECT c.rowid AS rowid, c.text AS text
            FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
            WHERE d.is_current = 1;

            CREATE VIRTUAL TABLE chunk_search USING fts5(
                text,
                content = 'search_current_chunks',
                content_rowid = 'rowid',
                tokenize = 'porter unicode61'
            );
            """
        )

    def _initialize_search_index(self, connection: sqlite3.Connection) -> None:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chunk_search'"
        ).fetchone()
        state = connection.execute(
            "SELECT * FROM search_index_state WHERE singleton = 1"
        ).fetchone()
        if exists and state and state["schema_version"] == self.SEARCH_INDEX_SCHEMA_VERSION:
            self._create_search_triggers(connection)
            return

        started = time.perf_counter()
        self._drop_search_schema(connection)
        self._create_search_table(connection)
        self._create_search_triggers(connection)
        chunk_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE d.is_current = 1
                """
            ).fetchone()[0]
        )
        connection.execute("INSERT INTO chunk_search(chunk_search) VALUES ('rebuild')")
        elapsed = round(time.perf_counter() - started, 6)
        connection.execute(
            """
            INSERT OR REPLACE INTO search_index_state (
                singleton, schema_version, initialized_at,
                initial_backfill_chunks, initial_backfill_seconds
            ) VALUES (1, ?, ?, ?, ?)
            """,
            (self.SEARCH_INDEX_SCHEMA_VERSION, utc_now(), chunk_count, elapsed),
        )

    def rebuild_search_index(self) -> Dict[str, object]:
        started = time.perf_counter()
        with self.connect() as connection:
            connection.execute("INSERT INTO chunk_search(chunk_search) VALUES ('rebuild')")
            chunk_count = int(
                connection.execute("SELECT COUNT(*) FROM search_current_chunks").fetchone()[0]
            )
            rebuilt_at = utc_now()
            connection.execute(
                """
                UPDATE search_index_state
                SET last_rebuilt_at = ?
                WHERE singleton = 1
                """,
                (rebuilt_at,),
            )
        return {
            "status": "rebuilt",
            "indexed_chunks": chunk_count,
            "duration_seconds": round(time.perf_counter() - started, 6),
            "rebuilt_at": rebuilt_at,
        }

    def search_index_status(self) -> Dict[str, object]:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO chunk_search(chunk_search, rank) VALUES ('integrity-check', 1)"
            )
            counts = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN d.is_current = 1 THEN 1 ELSE 0 END), 0)
                        AS indexed_chunks,
                    COALESCE(SUM(CASE WHEN d.is_current = 1 THEN 1 ELSE 0 END), 0)
                        AS current_searchable_chunks,
                    COUNT(*) AS total_stored_chunks
                FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                """
            ).fetchone()
            state = connection.execute(
                "SELECT * FROM search_index_state WHERE singleton = 1"
            ).fetchone()
        return {
            "status": "healthy",
            **dict(counts),
            "schema_version": state["schema_version"],
            "initialized_at": state["initialized_at"],
            "initial_backfill_chunks": state["initial_backfill_chunks"],
            "initial_backfill_seconds": state["initial_backfill_seconds"],
            "last_rebuilt_at": state["last_rebuilt_at"],
        }

    def search_chunks(
        self,
        query: str,
        top_k: int = 5,
        document_type: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        match_query = build_fts_query(query)
        if not match_query:
            return []

        type_clause = "AND c.document_type = ?" if document_type else ""
        parameters: List[object] = [match_query]
        if document_type:
            parameters.append(document_type)
        parameters.append(top_k)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.chunk_id, c.page_number, c.chunk_index, c.document_type,
                       c.text, d.document_id, d.sha256 AS document_sha256,
                       d.logical_name AS filename,
                       -bm25(chunk_search, 1.0) AS score
                FROM chunk_search
                JOIN chunks c ON c.rowid = chunk_search.rowid
                JOIN documents d ON d.document_id = c.document_id
                WHERE chunk_search MATCH ? AND d.is_current = 1
                    {type_clause}
                ORDER BY score DESC, c.chunk_id
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return [
            {
                **dict(row),
                "score": round(float(row["score"]), 8),
            }
            for row in rows
        ]

    def list_document_types(self) -> List[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT c.document_type
                FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE d.is_current = 1
                ORDER BY c.document_type
                """
            ).fetchall()
        return [str(row["document_type"]) for row in rows]

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
