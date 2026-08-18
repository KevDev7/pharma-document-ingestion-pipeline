import math
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from .database import PipelineDatabase


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def _keyed_counts(rows: Iterable[Mapping[str, object]], key: str) -> Dict[str, int]:
    return {str(row[key]): int(row["count"]) for row in rows}


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def export_operational_metrics(database: PipelineDatabase) -> Dict[str, object]:
    document_counts = database.fetch_all(
        """
        SELECT
            COUNT(*) AS document_versions,
            COALESCE(SUM(is_current), 0) AS current_documents,
            COALESCE(SUM(CASE WHEN is_current = 0 THEN 1 ELSE 0 END), 0)
                AS superseded_versions,
            COALESCE(SUM(page_count), 0) AS historical_pages,
            COALESCE(SUM(chunk_count), 0) AS historical_chunks,
            COALESCE(SUM(CASE WHEN is_current = 1 THEN page_count ELSE 0 END), 0)
                AS current_pages,
            COALESCE(SUM(CASE WHEN is_current = 1 THEN chunk_count ELSE 0 END), 0)
                AS current_chunks,
            COALESCE(SUM(CASE WHEN is_current = 1 THEN ocr_page_count ELSE 0 END), 0)
                AS current_ocr_pages,
            COALESCE(SUM(CASE WHEN is_current = 1 THEN size_bytes ELSE 0 END), 0)
                AS current_source_bytes
        FROM documents
        """
    )[0]
    run_totals = database.fetch_all(
        """
        SELECT
            COUNT(*) AS runs,
            COALESCE(SUM(discovered_files), 0) AS discovered_files,
            COALESCE(SUM(processed_files), 0) AS processed_files,
            COALESCE(SUM(skipped_files), 0) AS skipped_files,
            COALESCE(SUM(failed_files), 0) AS failed_files,
            COALESCE(SUM(page_count), 0) AS processed_pages,
            COALESCE(SUM(chunk_count), 0) AS produced_chunks
        FROM ingestion_runs
        """
    )[0]
    duration_rows = database.fetch_all(
        """
        SELECT duration_seconds
        FROM ingestion_runs
        WHERE duration_seconds IS NOT NULL AND status != 'running'
        """
    )
    durations = [float(row["duration_seconds"]) for row in duration_rows]
    status_counts = _keyed_counts(
        database.fetch_all(
            "SELECT status, COUNT(*) AS count FROM ingestion_runs GROUP BY status"
        ),
        "status",
    )
    trigger_counts = _keyed_counts(
        database.fetch_all(
            "SELECT trigger_type, COUNT(*) AS count FROM ingestion_runs GROUP BY trigger_type"
        ),
        "trigger_type",
    )
    extraction_counts = _keyed_counts(
        database.fetch_all(
            """
            SELECT p.extraction_method, COUNT(*) AS count
            FROM pages p
            JOIN documents d ON d.document_id = p.document_id
            WHERE d.is_current = 1
            GROUP BY p.extraction_method
            """
        ),
        "extraction_method",
    )
    error_counts = _keyed_counts(
        database.fetch_all(
            "SELECT error_type, COUNT(*) AS count FROM ingestion_errors GROUP BY error_type"
        ),
        "error_type",
    )

    current_pages = int(document_counts["current_pages"])
    discovered_files = int(run_totals["discovered_files"])
    failed_files = int(run_totals["failed_files"])
    skipped_files = int(run_totals["skipped_files"])
    current_ocr_pages = int(document_counts["current_ocr_pages"])

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": {key: int(value) for key, value in dict(document_counts).items()},
        "ingestion": {
            **{key: int(value) for key, value in dict(run_totals).items()},
            "status_counts": status_counts,
            "trigger_counts": trigger_counts,
            "duration_seconds": {
                "samples": len(durations),
                "p50": _percentile(durations, 0.50),
                "p95": _percentile(durations, 0.95),
                "max": round(max(durations), 6) if durations else 0.0,
            },
            "skip_rate": _safe_rate(skipped_files, discovered_files),
            "failure_rate": _safe_rate(failed_files, discovered_files),
        },
        "extraction": {
            "current_page_methods": extraction_counts,
            "current_ocr_rate": _safe_rate(current_ocr_pages, current_pages),
        },
        "errors": {
            "total": sum(error_counts.values()),
            "by_type": error_counts,
        },
        "search_index": database.search_index_status(),
    }
