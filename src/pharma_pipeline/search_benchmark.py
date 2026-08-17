import platform
import sqlite3
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .database import PipelineDatabase


def _percentile(values: List[float], percentile: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def _copy_database(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _duration_summary(values: List[float]) -> Dict[str, float]:
    return {
        "minimum": round(min(values), 6),
        "p50": round(statistics.median(values), 6),
        "p95": round(_percentile(values, 95), 6),
        "maximum": round(max(values), 6),
    }


def benchmark_search_index(database_path: Path, runs: int = 10) -> Dict[str, object]:
    if runs < 1:
        raise ValueError("runs must be at least 1")

    database = PipelineDatabase(database_path)
    database.initialize()
    source_index_status = database.search_index_status()
    with database.connect() as connection:
        documents = connection.execute(
            """
            SELECT d.document_id, d.logical_name, COUNT(c.chunk_id) AS chunk_count
            FROM documents d
            JOIN chunks c ON c.document_id = d.document_id
            WHERE d.is_current = 1
            GROUP BY d.document_id, d.logical_name
            ORDER BY chunk_count, d.logical_name
            """
        ).fetchall()
        if not documents:
            raise ValueError("Search index benchmark requires at least one current document")
        representative = documents[len(documents) // 2]
        source_chunks = connection.execute(
            """
            SELECT chunk_id, document_id, page_number, chunk_index,
                   document_type, character_count, text
            FROM chunks
            WHERE document_id = ?
            ORDER BY page_number, chunk_index
            """,
            (representative["document_id"],),
        ).fetchall()
        total_chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    incremental_durations = []
    rebuild_durations = []
    run_results = []
    for run_number in range(1, runs + 1):
        with tempfile.TemporaryDirectory(prefix="pharma-search-index-benchmark-") as directory:
            incremental_path = Path(directory) / "incremental.db"
            _copy_database(database_path, incremental_path)
            incremental_database = PipelineDatabase(incremental_path)
            incremental_database.initialize()
            started = time.perf_counter()
            with incremental_database.connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, page_number, chunk_index,
                        document_type, character_count, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            f"benchmark-{run_number}-{row['chunk_id']}",
                            row["document_id"],
                            row["page_number"],
                            row["chunk_index"] + 100000,
                            row["document_type"],
                            row["character_count"],
                            row["text"],
                        )
                        for row in source_chunks
                    ],
                )
            incremental_seconds = time.perf_counter() - started
            incremental_count = int(
                incremental_database.search_index_status()["indexed_chunks"]
            )
            if incremental_count != total_chunks + len(source_chunks):
                raise RuntimeError("Incremental index benchmark produced an invalid chunk count")

            rebuild_path = Path(directory) / "rebuild.db"
            _copy_database(database_path, rebuild_path)
            rebuild_database = PipelineDatabase(rebuild_path)
            rebuild_database.initialize()
            rebuild = rebuild_database.rebuild_search_index()
            rebuild_seconds = float(rebuild["duration_seconds"])

        incremental_durations.append(incremental_seconds)
        rebuild_durations.append(rebuild_seconds)
        run_results.append(
            {
                "run": run_number,
                "incremental_seconds": round(incremental_seconds, 6),
                "full_rebuild_seconds": round(rebuild_seconds, 6),
            }
        )

    incremental_summary = _duration_summary(incremental_durations)
    rebuild_summary = _duration_summary(rebuild_durations)
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "runs": runs,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        },
        "source_index_chunks": total_chunks,
        "source_index_status": source_index_status,
        "representative_document": {
            "filename": representative["logical_name"],
            "incremental_chunks": len(source_chunks),
        },
        "incremental_update_seconds": incremental_summary,
        "full_rebuild_seconds": rebuild_summary,
        "p50_full_rebuild_over_incremental_ratio": round(
            rebuild_summary["p50"] / incremental_summary["p50"], 2
        ),
        "run_results": run_results,
        "notes": [
            "Each measurement uses a temporary SQLite backup of the same source database.",
            "Incremental timing covers one transaction that inserts a representative chunk batch and its FTS5 trigger updates.",
            "Full rebuild timing reindexes every current chunk and is a recovery operation, not the normal ingestion path.",
            "PDF extraction and chunk generation are excluded from both measurements.",
        ],
    }
