import platform
import statistics
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import fitz

from .config import Settings
from .pipeline import IngestionPipeline


def _percentile(values: List[float], percentile: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def benchmark_corpus(corpus_dir: Path, runs: int = 10) -> Dict[str, object]:
    if runs < 1:
        raise ValueError("runs must be at least 1")

    resolved_corpus_dir = corpus_dir.expanduser().resolve()
    try:
        display_corpus_dir = resolved_corpus_dir.relative_to(Path.cwd().resolve())
    except ValueError:
        display_corpus_dir = resolved_corpus_dir
    paths = sorted(resolved_corpus_dir.glob("*.pdf"))
    if not paths:
        raise ValueError(f"No PDF files found in {corpus_dir}")

    results = []
    expected_counts = None
    for run_number in range(1, runs + 1):
        with tempfile.TemporaryDirectory(prefix="pharma-ingestion-benchmark-") as directory:
            settings = replace(
                Settings.from_root(Path(directory)),
                corpus_raw_dir=resolved_corpus_dir,
            )
            result = IngestionPipeline(settings).ingest_paths(
                paths,
                trigger_type="corpus_benchmark",
            )

        counts = (
            result["processed_files"],
            result["failed_files"],
            result["page_count"],
            result["chunk_count"],
        )
        if expected_counts is None:
            expected_counts = counts
        elif counts != expected_counts:
            raise RuntimeError(
                f"Benchmark run {run_number} produced inconsistent counts: {counts}"
            )
        results.append(
            {
                "run": run_number,
                "duration_seconds": result["duration_seconds"],
                "processed_files": result["processed_files"],
                "failed_files": result["failed_files"],
                "page_count": result["page_count"],
                "chunk_count": result["chunk_count"],
            }
        )

    durations = [float(result["duration_seconds"]) for result in results]
    page_count = int(results[0]["page_count"])
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "corpus_directory": str(display_corpus_dir),
        "corpus_files": len(paths),
        "corpus_bytes": sum(path.stat().st_size for path in paths),
        "runs": runs,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pymupdf": fitz.version[0],
        },
        "counts_per_run": {
            "processed_files": results[0]["processed_files"],
            "failed_files": results[0]["failed_files"],
            "pages": page_count,
            "chunks": results[0]["chunk_count"],
        },
        "duration_seconds": {
            "minimum": round(min(durations), 4),
            "p50": round(statistics.median(durations), 4),
            "p95": round(_percentile(durations, 95), 4),
            "maximum": round(max(durations), 4),
        },
        "pages_per_second": {
            "at_p50_duration": round(page_count / statistics.median(durations), 2),
            "at_p95_duration": round(page_count / _percentile(durations, 95), 2),
        },
        "run_results": results,
        "notes": [
            "Each run uses a fresh temporary SQLite database.",
            "The same local corpus files are reused, so operating-system file caches may be warm.",
            "These timings include transactional FTS5 index updates during ingestion.",
            "Corpus download time is excluded.",
        ],
    }
