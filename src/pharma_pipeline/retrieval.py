import json
import hashlib
import importlib.metadata
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Sequence

from .evaluation import RetrievalResult
from .search import build_fts_query
from .text import clean_text, split_text


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    document_sha256: str
    filename: str
    page_number: int
    text: str
    document_type: str
    topic: str


@dataclass(frozen=True)
class IndexedPage:
    document_sha256: str
    filename: str
    page_number: int
    text: str
    document_type: str
    topic: str


class EmbeddingBackend(Protocol):
    model_name: str

    def encode_documents(self, texts: Sequence[str]):
        ...

    def encode_query(self, query: str):
        ...


def load_manifest_metadata(path: Path) -> Dict[str, Dict[str, str]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(document["filename"]): {
            "document_type": str(document["document_type"]),
            "topic": str(document["topic"]),
            "title": str(document["title"]),
            "expected_sha256": str(document["expected_sha256"]),
            "expected_pages": str(document["expected_pages"]),
        }
        for document in manifest["documents"]
    }


def _validate_corpus_identity(
    rows, metadata: Dict[str, Dict[str, str]], validate_pages: bool = False
) -> None:
    observed: Dict[str, set] = {filename: set() for filename in metadata}
    for row in rows:
        filename = str(row["filename"])
        if filename in observed:
            observed[filename].add(str(row["document_sha256"]))

    missing = sorted(filename for filename, hashes in observed.items() if not hashes)
    if missing:
        raise ValueError(f"Current corpus is missing manifest files: {', '.join(missing)}")

    mismatched = []
    for filename, hashes in observed.items():
        expected = metadata[filename]["expected_sha256"]
        if hashes != {expected}:
            mismatched.append(f"{filename}: expected {expected}, found {sorted(hashes)}")
    if mismatched:
        raise ValueError("Current corpus does not match frozen manifest: " + "; ".join(mismatched))

    if validate_pages:
        observed_pages = {filename: set() for filename in metadata}
        for row in rows:
            filename = str(row["filename"])
            if filename in observed_pages:
                observed_pages[filename].add(int(row["page_number"]))
        incomplete = []
        for filename, page_numbers in observed_pages.items():
            expected_count = int(metadata[filename]["expected_pages"])
            expected_numbers = set(range(1, expected_count + 1))
            if page_numbers != expected_numbers:
                incomplete.append(
                    f"{filename}: expected pages 1-{expected_count}, "
                    f"found {len(page_numbers)} pages"
                )
        if incomplete:
            raise ValueError("Current corpus page rows are incomplete: " + "; ".join(incomplete))


def load_corpus_chunks(database_path: Path, manifest_path: Path) -> List[IndexedChunk]:
    metadata = load_manifest_metadata(manifest_path)
    filenames = sorted(metadata)
    if not filenames:
        raise ValueError("Manifest contains no corpus documents")

    placeholders = ",".join("?" for _ in filenames)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT c.chunk_id, d.sha256 AS document_sha256,
                   d.logical_name AS filename, c.page_number, c.text
            FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
            WHERE d.is_current = 1 AND d.logical_name IN ({placeholders})
            ORDER BY d.logical_name, c.page_number, c.chunk_index
            """,
            filenames,
        ).fetchall()
    finally:
        connection.close()

    _validate_corpus_identity(rows, metadata)

    chunks = [
        IndexedChunk(
            chunk_id=str(row["chunk_id"]),
            document_sha256=str(row["document_sha256"]),
            filename=str(row["filename"]),
            page_number=int(row["page_number"]),
            text=str(row["text"]),
            document_type=metadata[str(row["filename"])]["document_type"],
            topic=metadata[str(row["filename"])]["topic"],
        )
        for row in rows
    ]
    if not chunks:
        raise ValueError("No current corpus chunks were found in the pipeline database")
    return chunks


def load_corpus_pages(database_path: Path, manifest_path: Path) -> List[IndexedPage]:
    metadata = load_manifest_metadata(manifest_path)
    filenames = sorted(metadata)
    placeholders = ",".join("?" for _ in filenames)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"""
            SELECT d.sha256 AS document_sha256, d.logical_name AS filename,
                   p.page_number, p.text
            FROM pages p
            JOIN documents d ON d.document_id = p.document_id
            WHERE d.is_current = 1 AND d.logical_name IN ({placeholders})
            ORDER BY d.logical_name, p.page_number
            """,
            filenames,
        ).fetchall()
    finally:
        connection.close()
    _validate_corpus_identity(rows, metadata, validate_pages=True)
    return [
        IndexedPage(
            document_sha256=str(row["document_sha256"]),
            filename=str(row["filename"]),
            page_number=int(row["page_number"]),
            text=str(row["text"]),
            document_type=metadata[str(row["filename"])]["document_type"],
            topic=metadata[str(row["filename"])]["topic"],
        )
        for row in rows
    ]


def build_chunks(
    pages: Iterable[IndexedPage],
    strategy: str,
    chunk_size: int = 700,
    overlap: int = 120,
) -> List[IndexedChunk]:
    if strategy not in {"page", "fixed", "boundary"}:
        raise ValueError(f"Unsupported chunk strategy: {strategy}")
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Invalid chunk size or overlap")

    chunks = []
    for page in pages:
        text = clean_text(page.text)
        if not text:
            continue
        if strategy == "page":
            texts = [text]
        elif strategy == "boundary":
            texts = split_text(text, chunk_size=chunk_size, overlap=overlap)
        else:
            step = chunk_size - overlap
            texts = [text[start : start + chunk_size] for start in range(0, len(text), step)]

        for index, chunk_text in enumerate(texts):
            chunks.append(
                IndexedChunk(
                    chunk_id=(
                        f"{page.document_sha256[:16]}-{strategy}-"
                        f"p{page.page_number:04d}-c{index:04d}"
                    ),
                    document_sha256=page.document_sha256,
                    filename=page.filename,
                    page_number=page.page_number,
                    text=chunk_text,
                    document_type=page.document_type,
                    topic=page.topic,
                )
            )
    return chunks


class KeywordRetriever:
    name = "sqlite_fts5_bm25"

    def __init__(self, chunks: Iterable[IndexedChunk]) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_id UNINDEXED,
                document_sha256 UNINDEXED,
                filename UNINDEXED,
                page_number UNINDEXED,
                document_type UNINDEXED,
                text,
                tokenize='porter unicode61'
            )
            """
        )
        self.connection.executemany(
            """
            INSERT INTO chunks_fts (
                chunk_id, document_sha256, filename, page_number, document_type, text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.document_sha256,
                    chunk.filename,
                    chunk.page_number,
                    chunk.document_type,
                    chunk.text,
                )
                for chunk in chunks
            ],
        )

    def retrieve(self, query: str, top_k: int) -> Sequence[RetrievalResult]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        match_query = build_fts_query(query)
        if not match_query:
            return []
        rows = self.connection.execute(
            """
            SELECT chunk_id, document_sha256, filename, page_number, document_type, text,
                   bm25(chunks_fts, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0) AS rank_score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY rank_score
            LIMIT ?
            """,
            (match_query, top_k),
        ).fetchall()
        return [
            RetrievalResult(
                chunk_id=str(row["chunk_id"]),
                document_sha256=str(row["document_sha256"]),
                filename=str(row["filename"]),
                page_number=int(row["page_number"]),
                text=str(row["text"]),
                score=round(-float(row["rank_score"]), 8),
                document_type=str(row["document_type"]),
            )
            for row in rows
        ]


class HybridRetriever:
    def __init__(
        self,
        keyword_retriever: KeywordRetriever,
        vector_retriever: object,
        rrf_constant: int = 60,
    ) -> None:
        self.keyword_retriever = keyword_retriever
        self.vector_retriever = vector_retriever
        self.rrf_constant = rrf_constant
        self.name = f"hybrid_rrf_{getattr(vector_retriever, 'name', 'vector')}"

    def retrieve(self, query: str, top_k: int) -> Sequence[RetrievalResult]:
        candidate_k = max(50, top_k)
        keyword_results = self.keyword_retriever.retrieve(query, candidate_k)
        vector_results = self.vector_retriever.retrieve(query, candidate_k)  # type: ignore[attr-defined]

        scores: Dict[str, float] = {}
        records: Dict[str, RetrievalResult] = {}
        for ranked_results in (keyword_results, vector_results):
            for rank, result in enumerate(ranked_results, start=1):
                scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (
                    self.rrf_constant + rank
                )
                records[result.chunk_id] = result

        ranked_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:top_k]
        return [
            RetrievalResult(
                chunk_id=records[chunk_id].chunk_id,
                document_sha256=records[chunk_id].document_sha256,
                filename=records[chunk_id].filename,
                page_number=records[chunk_id].page_number,
                text=records[chunk_id].text,
                score=round(scores[chunk_id], 8),
                document_type=records[chunk_id].document_type,
            )
            for chunk_id in ranked_ids
        ]


class SentenceTransformerBackend:
    ADAPTER_VERSION = "token-aware-mean-pooling-v3"
    TOKEN_SAFETY_MARGIN = 16

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        first_module = self.model._first_module()
        self.resolved_revision = str(first_module.auto_model.config._commit_hash)
        self.max_seq_length = int(self.model.max_seq_length)
        self.sentence_transformers_version = importlib.metadata.version(
            "sentence-transformers"
        )

    def _split_for_model(self, text: str, reserved_tokens: int = 0) -> List[str]:
        tokenizer = self.model.tokenizer
        token_ids = tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_attention_mask=False,
            return_token_type_ids=False,
            verbose=False,
        )["input_ids"]
        special_tokens = tokenizer.num_special_tokens_to_add(pair=False)
        content_limit = (
            self.max_seq_length
            - special_tokens
            - reserved_tokens
            - self.TOKEN_SAFETY_MARGIN
        )
        if content_limit < 1:
            raise ValueError("Embedding model has no usable content token capacity")
        return [
            tokenizer.decode(
                token_ids[start : start + content_limit],
                clean_up_tokenization_spaces=False,
            )
            for start in range(0, len(token_ids), content_limit)
        ] or [""]

    def _document_text(self, text: str) -> str:
        if "e5-" in self.model_name.lower():
            return f"passage: {text}"
        return text

    def _query_text(self, text: str) -> str:
        model_name = self.model_name.lower()
        if "e5-" in model_name:
            return f"query: {text}"
        if "bge-" in model_name:
            return f"Represent this sentence for searching relevant passages: {text}"
        return text

    def encode_documents(self, texts: Sequence[str]):
        import numpy as np

        segments = []
        owners = []
        segment_counts = []
        for owner, text in enumerate(texts):
            prefix = self._document_text("")
            reserved_tokens = len(
                self.model.tokenizer.encode(prefix, add_special_tokens=False)
            )
            document_segments = self._split_for_model(
                text, reserved_tokens=reserved_tokens
            )
            segment_counts.append(len(document_segments))
            for segment in document_segments:
                prepared = self._document_text(segment)
                encoded_length = len(
                    self.model.tokenizer(
                        prepared,
                        add_special_tokens=True,
                        truncation=False,
                        return_attention_mask=False,
                        return_token_type_ids=False,
                        verbose=False,
                    )["input_ids"]
                )
                if encoded_length > self.max_seq_length:
                    raise ValueError(
                        f"Prepared embedding segment has {encoded_length} tokens; "
                        f"model limit is {self.max_seq_length}"
                    )
                segments.append(prepared)
                owners.append(owner)

        segment_embeddings = self.model.encode(
            segments,
            batch_size=32,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        pooled = np.zeros((len(texts), segment_embeddings.shape[1]), dtype="float32")
        counts = np.zeros(len(texts), dtype="float32")
        for owner, embedding in zip(owners, segment_embeddings):
            pooled[owner] += embedding
            counts[owner] += 1
        pooled /= counts[:, None]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        self.last_encoding_stats = {
            "document_count": len(texts),
            "segment_count": len(segments),
            "multi_segment_documents": sum(count > 1 for count in segment_counts),
            "max_seq_length": self.max_seq_length,
        }
        return pooled / np.maximum(norms, 1e-12)

    def encode_query(self, query: str):
        return self.model.encode(
            [self._query_text(query)],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]


class VectorRetriever:
    def __init__(
        self,
        chunks: Sequence[IndexedChunk],
        model_name: str,
        cache_dir: Path,
        backend: Optional[EmbeddingBackend] = None,
    ) -> None:
        import numpy as np

        self.np = np
        self.chunks = list(chunks)
        if not self.chunks:
            raise ValueError("Vector retrieval requires at least one chunk")
        self.model_name = model_name
        self.name = f"vector_{model_name}"

        model_started = time.perf_counter()
        self.backend = backend or SentenceTransformerBackend(model_name)
        self.model_load_seconds = round(time.perf_counter() - model_started, 4)

        fingerprint = hashlib.sha256()
        fingerprint.update(model_name.encode())
        adapter_version = str(
            getattr(self.backend, "ADAPTER_VERSION", SentenceTransformerBackend.ADAPTER_VERSION)
        )
        resolved_revision = str(getattr(self.backend, "resolved_revision", "unknown"))
        backend_version = str(
            getattr(self.backend, "sentence_transformers_version", "test-backend")
        )
        torch_version = importlib.metadata.version("torch")
        transformers_version = importlib.metadata.version("transformers")
        fingerprint.update(adapter_version.encode())
        fingerprint.update(resolved_revision.encode())
        fingerprint.update(backend_version.encode())
        fingerprint.update(torch_version.encode())
        fingerprint.update(transformers_version.encode())
        for chunk in self.chunks:
            fingerprint.update(chunk.chunk_id.encode())
            fingerprint.update(hashlib.sha256(chunk.text.encode()).digest())
        cache_key = fingerprint.hexdigest()
        cache_dir.mkdir(parents=True, exist_ok=True)
        embedding_path = cache_dir / f"{cache_key}.npy"
        metadata_path = cache_dir / f"{cache_key}.json"

        index_started = time.perf_counter()
        self.cache_hit = embedding_path.exists() and metadata_path.exists()
        if self.cache_hit:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("chunk_ids") != [chunk.chunk_id for chunk in self.chunks]:
                raise ValueError("Embedding cache chunk order does not match current corpus")
            self.embeddings = np.load(embedding_path, allow_pickle=False)
            self.embedding_metadata = metadata
        else:
            self.embeddings = np.asarray(
                self.backend.encode_documents([chunk.text for chunk in self.chunks]),
                dtype="float32",
            )
            temporary_path = embedding_path.with_suffix(".npy.part")
            with temporary_path.open("wb") as handle:
                np.save(handle, self.embeddings, allow_pickle=False)
            temporary_path.replace(embedding_path)
            self.embedding_metadata = {
                "model_name": model_name,
                "adapter_version": adapter_version,
                "resolved_revision": resolved_revision,
                "sentence_transformers_version": backend_version,
                "torch_version": torch_version,
                "transformers_version": transformers_version,
                "chunk_ids": [chunk.chunk_id for chunk in self.chunks],
                "shape": list(self.embeddings.shape),
                "encoding_stats": getattr(self.backend, "last_encoding_stats", None),
            }
            metadata_path.write_text(
                json.dumps(self.embedding_metadata, indent=2)
                + "\n",
                encoding="utf-8",
            )
        if self.embeddings.shape[0] != len(self.chunks):
            raise ValueError("Embedding row count does not match chunk count")
        self.index_build_seconds = round(time.perf_counter() - index_started, 4)

    def retrieve(self, query: str, top_k: int) -> Sequence[RetrievalResult]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        query_embedding = self.np.asarray(self.backend.encode_query(query), dtype="float32")
        scores = self.np.sum(self.embeddings * query_embedding, axis=1, dtype="float32")
        top_indices = self.np.argsort(-scores, kind="stable")[: min(top_k, len(self.chunks))]
        return [
            RetrievalResult(
                chunk_id=self.chunks[int(index)].chunk_id,
                document_sha256=self.chunks[int(index)].document_sha256,
                filename=self.chunks[int(index)].filename,
                page_number=self.chunks[int(index)].page_number,
                text=self.chunks[int(index)].text,
                score=round(float(scores[int(index)]), 8),
                document_type=self.chunks[int(index)].document_type,
            )
            for index in top_indices
        ]
