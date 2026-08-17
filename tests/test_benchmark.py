from pathlib import Path

import fitz
import pytest

from pharma_pipeline.benchmark import benchmark_corpus


def make_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "FDA pharmaceutical quality guidance with enough digital text for extraction.",
    )
    document.save(path)
    document.close()


def test_benchmark_uses_fresh_state_and_returns_consistent_counts(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    make_pdf(corpus / "guidance.pdf")

    result = benchmark_corpus(corpus, runs=2)

    assert result["runs"] == 2
    assert result["counts_per_run"] == {
        "processed_files": 1,
        "failed_files": 0,
        "pages": 1,
        "chunks": 1,
    }
    assert len(result["run_results"]) == 2
    assert result["duration_seconds"]["p95"] >= result["duration_seconds"]["minimum"]


def test_benchmark_requires_at_least_one_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        benchmark_corpus(tmp_path, runs=0)
