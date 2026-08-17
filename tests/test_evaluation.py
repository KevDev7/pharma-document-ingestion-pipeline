import json
from pathlib import Path

import pytest

from pharma_pipeline.evaluation import (
    EvaluationQuery,
    PageLabel,
    RetrievalResult,
    evaluate_retriever,
    load_evaluation_queries,
    score_query,
    validate_relevant_pages,
)


def result(chunk_id: str, filename: str, page_number: int) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_sha256=filename,
        filename=filename,
        page_number=page_number,
        text="text",
        score=1.0,
        document_type="FDA Guidance",
    )


def test_score_query_uses_page_labels_for_recall_precision_and_rank() -> None:
    query = EvaluationQuery(
        query_id="q1",
        query="question",
        category="exact_fact",
        split="development",
        relevant_pages={PageLabel("a.pdf", 2, "a.pdf"), PageLabel("a.pdf", 3, "a.pdf")},
        reference_answer="answer",
    )

    scored = score_query(
        query,
        [
            result("wrong", "b.pdf", 1),
            result("right-1", "a.pdf", 2),
            result("right-2", "a.pdf", 3),
        ],
        top_k=3,
    )

    assert scored["recall_at_k"] == 1.0
    assert scored["precision_at_k"] == pytest.approx(2 / 3)
    assert scored["reciprocal_rank"] == 0.5


def test_duplicate_chunks_from_one_page_do_not_inflate_recall() -> None:
    query = EvaluationQuery(
        query_id="q1",
        query="question",
        category="exact_fact",
        split="development",
        relevant_pages={PageLabel("a.pdf", 2, "a.pdf"), PageLabel("a.pdf", 3, "a.pdf")},
        reference_answer="answer",
    )

    scored = score_query(
        query,
        [result("one", "a.pdf", 2), result("two", "a.pdf", 2)],
        top_k=2,
    )

    assert scored["recall_at_k"] == 0.5
    assert scored["precision_at_k"] == 0.5


def test_load_evaluation_queries_rejects_duplicate_ids(tmp_path: Path) -> None:
    row = {
        "query_id": "duplicate",
        "query": "question",
        "category": "exact_fact",
        "split": "development",
        "reference_answer": "answer",
        "relevant_pages": [
            {"document_sha256": "a.pdf", "filename": "a.pdf", "page_number": 1}
        ],
    }
    path = tmp_path / "queries.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate query_id"):
        load_evaluation_queries(path)


def test_validate_relevant_pages_rejects_stale_labels() -> None:
    query = EvaluationQuery(
        query_id="q1",
        query="question",
        category="exact_fact",
        split="test",
        relevant_pages={PageLabel("expected-hash", 4, "doc.pdf")},
        reference_answer="answer",
    )

    with pytest.raises(ValueError, match="unavailable pages"):
        validate_relevant_pages([query], {("different-hash", 4)})


class FakeRetriever:
    name = "fake"

    def retrieve(self, query: str, top_k: int):
        return [result("right", "a.pdf", 1)][:top_k]


def test_evaluate_retriever_aggregates_metrics() -> None:
    queries = [
        EvaluationQuery(
            "q1",
            "first",
            "exact_fact",
            "development",
            {PageLabel("a.pdf", 1, "a.pdf")},
            "answer",
        ),
        EvaluationQuery(
            "q2",
            "second",
            "paraphrase",
            "test",
            {PageLabel("b.pdf", 1, "b.pdf")},
            "answer",
        ),
    ]

    report = evaluate_retriever(FakeRetriever(), queries, top_k=1)

    assert report["metrics"]["recall_at_k"] == 0.5
    assert report["metrics"]["precision_at_k"] == 0.5
    assert report["metrics"]["mrr"] == 0.5
