import json
import sqlite3
from pathlib import Path

import pytest

from pharma_pipeline.evaluation import RetrievalResult
from pharma_pipeline.retrieval import (
    HybridRetriever,
    IndexedChunk,
    IndexedPage,
    KeywordRetriever,
    VectorRetriever,
    build_chunks,
    load_corpus_chunks,
    load_corpus_pages,
)


def chunk(chunk_id: str, text: str, page: int = 1) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id,
        document_sha256="hash",
        filename="guidance.pdf",
        page_number=page,
        text=text,
        document_type="FDA Guidance",
        topic="Quality",
    )


def test_keyword_retriever_ranks_matching_chunk() -> None:
    retriever = KeywordRetriever(
        [
            chunk("unrelated", "General manufacturing background."),
            chunk("match", "The acceptance criterion requires sterility testing."),
        ]
    )

    results = retriever.retrieve("What is the sterility acceptance criterion?", top_k=1)

    assert results[0].chunk_id == "match"
    assert results[0].score > 0


def test_build_chunks_supports_page_fixed_and_boundary_strategies() -> None:
    page = IndexedPage(
        document_sha256="hash",
        filename="guidance.pdf",
        page_number=1,
        text=("Sentence one. Sentence two. " * 40),
        document_type="FDA Guidance",
        topic="Quality",
    )

    page_chunks = build_chunks([page], "page", chunk_size=100, overlap=20)
    fixed_chunks = build_chunks([page], "fixed", chunk_size=100, overlap=20)
    boundary_chunks = build_chunks([page], "boundary", chunk_size=100, overlap=20)

    assert len(page_chunks) == 1
    assert len(fixed_chunks) > 1
    assert len(boundary_chunks) > 1
    assert all(chunk.page_number == 1 for chunk in boundary_chunks)


class StaticRetriever:
    def __init__(self, name: str, results):
        self.name = name
        self.results = results

    def retrieve(self, query: str, top_k: int):
        return self.results[:top_k]


def retrieval_result(chunk_id: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_sha256="hash",
        filename="guidance.pdf",
        page_number=1,
        text="text",
        score=1.0,
        document_type="FDA Guidance",
    )


def test_hybrid_reciprocal_rank_fusion_rewards_shared_results() -> None:
    keyword = StaticRetriever("keyword", [retrieval_result("shared"), retrieval_result("keyword")])
    vector = StaticRetriever("vector", [retrieval_result("vector"), retrieval_result("shared")])
    hybrid = HybridRetriever(keyword, vector)

    results = hybrid.retrieve("query", top_k=3)

    assert results[0].chunk_id == "shared"


class FakeEmbeddingBackend:
    model_name = "fake"
    ADAPTER_VERSION = "fake-v1"
    resolved_revision = "fake-revision"
    sentence_transformers_version = "test"

    def encode_documents(self, texts):
        return [[1.0, 0.0], [0.0, 1.0]]

    def encode_query(self, query):
        return [0.0, 1.0]


def test_vector_retriever_uses_normalized_dot_product_ranking(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    chunks = [chunk("first", "first"), chunk("second", "second")]
    retriever = VectorRetriever(
        chunks,
        model_name="fake",
        cache_dir=tmp_path,
        backend=FakeEmbeddingBackend(),
    )

    results = retriever.retrieve("query", top_k=1)

    assert results[0].chunk_id == "second"
    metadata = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert metadata["resolved_revision"] == "fake-revision"


def test_load_corpus_chunks_uses_manifest_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "pipeline.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE documents (
            document_id TEXT, sha256 TEXT, logical_name TEXT, is_current INTEGER
        );
        CREATE TABLE chunks (
            chunk_id TEXT, document_id TEXT, page_number INTEGER,
            chunk_index INTEGER, text TEXT
        );
        INSERT INTO documents VALUES ('doc', 'hash', 'guidance.pdf', 1);
        INSERT INTO chunks VALUES ('chunk', 'doc', 2, 0, 'content');
        """
    )
    connection.commit()
    connection.close()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "filename": "guidance.pdf",
                        "document_type": "FDA Guidance",
                        "topic": "Process Validation",
                        "title": "Guidance",
                        "expected_sha256": "hash",
                        "expected_pages": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    chunks = load_corpus_chunks(database_path, manifest_path)

    assert chunks[0].document_type == "FDA Guidance"
    assert chunks[0].topic == "Process Validation"


def test_load_corpus_chunks_rejects_manifest_hash_mismatch(tmp_path: Path) -> None:
    database_path = tmp_path / "pipeline.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE documents (
            document_id TEXT, sha256 TEXT, logical_name TEXT, is_current INTEGER
        );
        CREATE TABLE chunks (
            chunk_id TEXT, document_id TEXT, page_number INTEGER,
            chunk_index INTEGER, text TEXT
        );
        INSERT INTO documents VALUES ('doc', 'wrong-hash', 'guidance.pdf', 1);
        INSERT INTO chunks VALUES ('chunk', 'doc', 2, 0, 'content');
        """
    )
    connection.commit()
    connection.close()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "filename": "guidance.pdf",
                        "document_type": "FDA Guidance",
                        "topic": "Process Validation",
                        "title": "Guidance",
                        "expected_sha256": "expected-hash",
                        "expected_pages": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match frozen manifest"):
        load_corpus_chunks(database_path, manifest_path)


def test_load_corpus_pages_rejects_incomplete_page_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "pipeline.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE documents (
            document_id TEXT, sha256 TEXT, logical_name TEXT, is_current INTEGER
        );
        CREATE TABLE pages (
            document_id TEXT, page_number INTEGER, text TEXT
        );
        INSERT INTO documents VALUES ('doc', 'hash', 'guidance.pdf', 1);
        INSERT INTO pages VALUES ('doc', 1, 'first page only');
        """
    )
    connection.commit()
    connection.close()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "filename": "guidance.pdf",
                        "document_type": "FDA Guidance",
                        "topic": "Process Validation",
                        "title": "Guidance",
                        "expected_sha256": "hash",
                        "expected_pages": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="page rows are incomplete"):
        load_corpus_pages(database_path, manifest_path)
