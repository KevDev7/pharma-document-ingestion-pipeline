import gc
import hashlib
import importlib.metadata
import json
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from .evaluation import evaluate_retriever, load_evaluation_queries, validate_relevant_pages
from .retrieval import (
    HybridRetriever,
    KeywordRetriever,
    SentenceTransformerBackend,
    VectorRetriever,
    build_chunks,
    load_corpus_pages,
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _config_id(method: str, strategy: str, model_name: str = "none") -> str:
    model_slug = model_name.replace("/", "_")
    return f"{strategy}__{method}__{model_slug}"


def _embedding_summary(vector: VectorRetriever) -> Dict[str, object]:
    return {
        key: value
        for key, value in vector.embedding_metadata.items()
        if key not in {"chunk_ids"}
    }


def _select_configuration(rows: List[Dict[str, object]]) -> Dict[str, object]:
    return max(
        rows,
        key=lambda row: (
            float(row["metrics"]["recall_at_k"]),  # type: ignore[index]
            float(row["metrics"]["mrr"]),  # type: ignore[index]
            float(row["metrics"]["precision_at_k"]),  # type: ignore[index]
            -float(row["metrics"]["latency_ms_p95"]),  # type: ignore[index]
        ),
    )


def _build_retriever(
    descriptor: Dict[str, object],
    chunks_by_strategy: Dict[str, list],
    cache_dir: Path,
):
    strategy = str(descriptor["chunk_strategy"])
    method = str(descriptor["method"])
    chunks = chunks_by_strategy[strategy]
    keyword = KeywordRetriever(chunks)
    if method == "keyword":
        return keyword

    model_name = str(descriptor["embedding_model"])
    vector = VectorRetriever(chunks, model_name, cache_dir)
    if method == "vector":
        return vector
    return HybridRetriever(keyword, vector)


def _percentile(values: List[float], percentile: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def _measure_interleaved_latency(
    retrievers: Dict[str, object],
    queries: list,
    candidate_k: int,
    repetitions: int,
) -> Dict[str, object]:
    if repetitions < 1:
        raise ValueError("latency_repetitions must be at least 1")
    if not queries:
        raise ValueError("Latency benchmark requires at least one query")

    names = list(retrievers)
    for name in names:
        retrievers[name].retrieve(queries[0].query, candidate_k)  # type: ignore[attr-defined]

    samples: Dict[str, List[float]] = {name: [] for name in names}
    for repetition in range(repetitions):
        for query_index, query in enumerate(queries):
            order = names if (repetition + query_index) % 2 == 0 else list(reversed(names))
            for name in order:
                started = time.perf_counter()
                retrievers[name].retrieve(query.query, candidate_k)  # type: ignore[attr-defined]
                samples[name].append((time.perf_counter() - started) * 1000)

    return {
        "method": "one warmup per retriever, then repeated interleaved query trials",
        "candidate_k": candidate_k,
        "repetitions": repetitions,
        "query_count": len(queries),
        "results": {
            name: {
                "sample_count": len(values),
                "latency_ms_p50": round(statistics.median(values), 4),
                "latency_ms_p95": round(_percentile(values, 95), 4),
                "samples_ms": [round(value, 4) for value in values],
            }
            for name, values in samples.items()
        },
    }


def run_retrieval_experiment(
    database_path: Path,
    manifest_path: Path,
    query_path: Path,
    config_path: Path,
    cache_dir: Path,
) -> Dict[str, object]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported retrieval experiment schema_version")

    queries = load_evaluation_queries(query_path)
    development_queries = [query for query in queries if query.split == "development"]
    validation_queries = [query for query in queries if query.split == "validation"]
    acceptance_queries = [query for query in queries if query.split == "acceptance"]
    test_queries = [query for query in queries if query.split == "test"]
    pages = load_corpus_pages(database_path, manifest_path)
    validate_relevant_pages(
        queries,
        {(page.document_sha256, page.page_number) for page in pages},
    )
    chunks_by_strategy = {
        strategy: build_chunks(
            pages,
            strategy,
            chunk_size=int(config["chunk_size"]),
            overlap=int(config["chunk_overlap"]),
        )
        for strategy in config["chunk_strategies"]
    }

    development_runs = []
    development_top_k = int(config["development_top_k"])
    candidate_k = int(config["evaluation_candidate_k"])
    for strategy, chunks in chunks_by_strategy.items():
        keyword = KeywordRetriever(chunks)
        report = evaluate_retriever(
            keyword, development_queries, development_top_k, candidate_k=candidate_k
        )
        development_runs.append(
            {
                "config_id": _config_id("keyword", strategy),
                "method": "keyword",
                "chunk_strategy": strategy,
                "chunk_count": len(chunks),
                "embedding_model": None,
                "model_startup_seconds": 0.0,
                "index_build_seconds": 0.0,
                "cache_hit": None,
                **report,
            }
        )

    model_startup = {}
    for model_name in config["embedding_models"]:
        started = time.perf_counter()
        backend = SentenceTransformerBackend(model_name)
        model_startup[model_name] = round(time.perf_counter() - started, 4)
        for strategy, chunks in chunks_by_strategy.items():
            vector = VectorRetriever(
                chunks,
                model_name,
                cache_dir,
                backend=backend,
            )
            vector_report = evaluate_retriever(
                vector,
                development_queries,
                development_top_k,
                candidate_k=candidate_k,
            )
            development_runs.append(
                {
                    "config_id": _config_id("vector", strategy, model_name),
                    "method": "vector",
                    "chunk_strategy": strategy,
                    "chunk_count": len(chunks),
                    "embedding_model": model_name,
                    "model_startup_seconds": model_startup[model_name],
                    "index_build_seconds": vector.index_build_seconds,
                    "cache_hit": vector.cache_hit,
                    "embedding_metadata": _embedding_summary(vector),
                    **vector_report,
                }
            )

            hybrid = HybridRetriever(
                KeywordRetriever(chunks),
                vector,
                rrf_constant=int(config["hybrid_rrf_constant"]),
            )
            hybrid_report = evaluate_retriever(
                hybrid,
                development_queries,
                development_top_k,
                candidate_k=candidate_k,
            )
            development_runs.append(
                {
                    "config_id": _config_id("hybrid", strategy, model_name),
                    "method": "hybrid",
                    "chunk_strategy": strategy,
                    "chunk_count": len(chunks),
                    "embedding_model": model_name,
                    "model_startup_seconds": model_startup[model_name],
                    "index_build_seconds": vector.index_build_seconds,
                    "cache_hit": vector.cache_hit,
                    "embedding_metadata": _embedding_summary(vector),
                    **hybrid_report,
                }
            )
        del backend
        gc.collect()

    selected = _select_configuration(development_runs)
    baseline_descriptor = {
        "method": "keyword",
        "chunk_strategy": "boundary",
        "embedding_model": None,
    }
    selected_descriptor = {
        "method": selected["method"],
        "chunk_strategy": selected["chunk_strategy"],
        "embedding_model": selected["embedding_model"],
    }
    baseline_retriever = _build_retriever(
        baseline_descriptor,
        chunks_by_strategy,
        cache_dir,
    )
    selected_retriever = _build_retriever(
        selected_descriptor,
        chunks_by_strategy,
        cache_dir,
    )
    validation_results = {
        "baseline": evaluate_retriever(
            baseline_retriever,
            validation_queries,
            development_top_k,
            candidate_k=candidate_k,
        ),
        "development_candidate": evaluate_retriever(
            selected_retriever,
            validation_queries,
            development_top_k,
            candidate_k=candidate_k,
        ),
    }
    acceptance_results = {
        "baseline": evaluate_retriever(
            baseline_retriever,
            acceptance_queries,
            development_top_k,
            candidate_k=candidate_k,
        ),
        "development_candidate": evaluate_retriever(
            selected_retriever,
            acceptance_queries,
            development_top_k,
            candidate_k=candidate_k,
        ),
    }
    baseline_acceptance = acceptance_results["baseline"]["metrics"]
    candidate_acceptance = acceptance_results["development_candidate"]["metrics"]
    recommended_descriptor = (
        selected_descriptor
        if (
            float(candidate_acceptance["recall_at_k"]),
            float(candidate_acceptance["mrr"]),
            float(candidate_acceptance["precision_at_k"]),
            -float(candidate_acceptance["latency_ms_p95"]),
        )
        > (
            float(baseline_acceptance["recall_at_k"]),
            float(baseline_acceptance["mrr"]),
            float(baseline_acceptance["precision_at_k"]),
            -float(baseline_acceptance["latency_ms_p95"]),
        )
        else baseline_descriptor
    )

    test_results = {"baseline": {}, "development_candidate": {}}
    for top_k in config["test_top_k_values"]:
        test_results["baseline"][str(top_k)] = evaluate_retriever(
            baseline_retriever,
            test_queries,
            int(top_k),
            candidate_k=candidate_k,
        )
        test_results["development_candidate"][str(top_k)] = evaluate_retriever(
            selected_retriever,
            test_queries,
            int(top_k),
            candidate_k=candidate_k,
        )

    latency_benchmark = _measure_interleaved_latency(
        {
            "baseline": baseline_retriever,
            "development_candidate": selected_retriever,
        },
        test_queries,
        candidate_k=candidate_k,
        repetitions=int(config["latency_repetitions"]),
    )
    for name, by_k in test_results.items():
        latency = latency_benchmark["results"][name]
        for result in by_k.values():
            result["metrics"]["latency_ms_p50"] = latency["latency_ms_p50"]
            result["metrics"]["latency_ms_p95"] = latency["latency_ms_p95"]

    baseline_at_5 = test_results["baseline"]["5"]["metrics"]
    selected_at_5 = test_results["development_candidate"]["5"]["metrics"]
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": _package_version("numpy"),
            "sentence_transformers": _package_version("sentence-transformers"),
            "torch": _package_version("torch"),
            "transformers": _package_version("transformers"),
        },
        "inputs": {
            "manifest_sha256": _file_sha256(manifest_path),
            "queries_sha256": _file_sha256(query_path),
            "config_sha256": _file_sha256(config_path),
            "documents": len({page.document_sha256 for page in pages}),
            "pages": len(pages),
            "development_queries": len(development_queries),
            "validation_queries": len(validation_queries),
            "acceptance_queries": len(acceptance_queries),
            "test_queries": len(test_queries),
            "chunk_counts": {
                strategy: len(chunks) for strategy, chunks in chunks_by_strategy.items()
            },
        },
        "selection_rule": (
            "Highest development Recall@5, then MRR, then Precision@5, "
            "then lower p95 retrieval latency"
        ),
        "acceptance_rule": (
            "Compare the BM25 baseline with the development candidate on acceptance "
            "questions; prefer higher Recall@5, then MRR, then Precision@5, then "
            "lower p95 retrieval latency"
        ),
        "development_runs": development_runs,
        "development_candidate_configuration": selected_descriptor,
        "baseline_configuration": baseline_descriptor,
        "recommended_configuration": recommended_descriptor,
        "validation_results": validation_results,
        "acceptance_results": acceptance_results,
        "test_results": test_results,
        "test_latency_benchmark": latency_benchmark,
        "test_candidate_recall_at_5_delta": round(
            float(selected_at_5["recall_at_k"]) - float(baseline_at_5["recall_at_k"]),
            4,
        ),
    }
