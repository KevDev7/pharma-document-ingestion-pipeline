import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Protocol, Sequence, Set, Tuple


PageKey = Tuple[str, int]


@dataclass(frozen=True)
class PageLabel:
    document_sha256: str
    page_number: int
    filename: str = field(compare=False)

    @property
    def key(self) -> PageKey:
        return (self.document_sha256, self.page_number)


@dataclass(frozen=True)
class EvaluationQuery:
    query_id: str
    query: str
    category: str
    split: str
    relevant_pages: Set[PageLabel]
    reference_answer: str


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    document_sha256: str
    filename: str
    page_number: int
    text: str
    score: float
    document_type: str


class Retriever(Protocol):
    name: str

    def retrieve(self, query: str, top_k: int) -> Sequence[RetrievalResult]:
        ...


def load_evaluation_queries(path: Path) -> List[EvaluationQuery]:
    queries = []
    seen_ids = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            query_id = str(row["query_id"])
            if query_id in seen_ids:
                raise ValueError(f"Duplicate query_id on line {line_number}: {query_id}")
            seen_ids.add(query_id)

            relevant_pages = {
                PageLabel(
                    document_sha256=str(item["document_sha256"]),
                    filename=str(item["filename"]),
                    page_number=int(item["page_number"]),
                )
                for item in row["relevant_pages"]
            }
            if not relevant_pages:
                raise ValueError(f"Query {query_id} must have at least one relevant page")
            split = str(row["split"])
            if split not in {"development", "validation", "acceptance", "test"}:
                raise ValueError(f"Query {query_id} has unsupported split: {split}")
            queries.append(
                EvaluationQuery(
                    query_id=query_id,
                    query=str(row["query"]),
                    category=str(row["category"]),
                    split=split,
                    relevant_pages=relevant_pages,
                    reference_answer=str(row["reference_answer"]),
                )
            )
    if not queries:
        raise ValueError("Evaluation file contains no queries")
    return queries


def validate_relevant_pages(
    queries: Iterable[EvaluationQuery], available_pages: Set[PageKey]
) -> None:
    missing = sorted(
        {
            page.key
            for query in queries
            for page in query.relevant_pages
            if page.key not in available_pages
        }
    )
    if missing:
        preview = ", ".join(f"{sha[:12]}:p{page}" for sha, page in missing[:5])
        raise ValueError(f"Evaluation labels reference unavailable pages: {preview}")


def score_query(
    query: EvaluationQuery,
    results: Sequence[RetrievalResult],
    top_k: int,
) -> Dict[str, object]:
    relevant_keys = {page.key for page in query.relevant_pages}
    ranked = []
    seen_pages = set()
    for item in results:
        page_key = (item.document_sha256, item.page_number)
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        ranked.append(item)
        if len(ranked) == top_k:
            break

    retrieved_pages = [(item.document_sha256, item.page_number) for item in ranked]
    relevant_hits = [page for page in retrieved_pages if page in relevant_keys]
    first_relevant_rank = next(
        (
            rank
            for rank, page in enumerate(retrieved_pages, start=1)
            if page in relevant_keys
        ),
        None,
    )
    reciprocal_rank = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
    return {
        "query_id": query.query_id,
        "query": query.query,
        "category": query.category,
        "split": query.split,
        "recall_at_k": len(relevant_hits) / len(query.relevant_pages),
        "precision_at_k": len(relevant_hits) / top_k,
        "reciprocal_rank": reciprocal_rank,
        "first_relevant_rank": first_relevant_rank,
        "relevant_pages": [
            {
                "document_sha256": page.document_sha256,
                "filename": page.filename,
                "page_number": page.page_number,
            }
            for page in sorted(
                query.relevant_pages,
                key=lambda item: (item.filename, item.page_number),
            )
        ],
        "retrieved": [
            {
                "rank": rank,
                "chunk_id": item.chunk_id,
                "document_sha256": item.document_sha256,
                "filename": item.filename,
                "page_number": item.page_number,
                "score": item.score,
                "is_relevant": (item.document_sha256, item.page_number) in relevant_keys,
            }
            for rank, item in enumerate(ranked, start=1)
        ],
    }


def _percentile(values: List[float], percentile: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def evaluate_retriever(
    retriever: Retriever,
    queries: Iterable[EvaluationQuery],
    top_k: int,
    candidate_k: int = 50,
) -> Dict[str, object]:
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if candidate_k < top_k:
        raise ValueError("candidate_k must be greater than or equal to top_k")

    query_results = []
    latencies_ms = []
    for query in queries:
        started = time.perf_counter()
        retrieved = retriever.retrieve(query.query, top_k=candidate_k)
        latency_ms = (time.perf_counter() - started) * 1000
        scored = score_query(query, retrieved, top_k)
        scored["latency_ms"] = round(latency_ms, 4)
        query_results.append(scored)
        latencies_ms.append(latency_ms)

    if not query_results:
        raise ValueError("At least one evaluation query is required")

    return {
        "retriever": retriever.name,
        "top_k": top_k,
        "candidate_k": candidate_k,
        "query_count": len(query_results),
        "metrics": {
            "recall_at_k": round(
                statistics.mean(float(item["recall_at_k"]) for item in query_results), 4
            ),
            "precision_at_k": round(
                statistics.mean(float(item["precision_at_k"]) for item in query_results), 4
            ),
            "mrr": round(
                statistics.mean(float(item["reciprocal_rank"]) for item in query_results), 4
            ),
            "latency_ms_p50": round(statistics.median(latencies_ms), 4),
            "latency_ms_p95": round(_percentile(latencies_ms, 95), 4),
        },
        "query_results": query_results,
    }
