# Architecture

## Current Local Flow

```text
data/incoming/*.pdf
        |
        v
filesystem event + stable-file check
        |
        v
SHA-256 duplicate/version check
        |
        v
PyMuPDF extraction --> Tesseract fallback for low-text pages
        |
        v
page classification + overlapping chunks
        |
        v
SQLite documents/pages/chunks/run metrics
        |
        v
transactional FTS5 trigger update
        |
        +--> data/archive/      successful or duplicate PDFs
        +--> data/quarantine/   failed PDFs
```

The benchmark path uses the same processing boundary but reads hash-frozen files reconstructed from `corpus/manifest.json`. Benchmark runs write to fresh temporary SQLite databases so prior ingestion state cannot turn a measured run into a duplicate replay.

## Retrieval Evaluation Flow

```text
current corpus pages in SQLite + manifest metadata
        |
        v
page / fixed-window / boundary-aware chunks
        |
        +--> SQLite FTS5 BM25 keyword retrieval
        +--> normalized sentence-transformer embeddings + exact cosine search
        +--> reciprocal-rank-fusion hybrid retrieval
        |
        v
page-level Recall@K / Precision@K / MRR / p50-p95 latency
```

Embedding arrays are cached locally using a fingerprint of the resolved model revision, adapter behavior, Sentence Transformers, Torch, and Transformers versions, chunk IDs, and text hashes. Long embedding inputs are split to the model token limit, then mean-pooled and normalized. Evaluation labels use immutable document hashes plus page numbers, and retrieval refuses database records that do not match the frozen corpus manifest or expected page range. The current experiment performs exact vector search because the largest tested index contains 2,068 chunks.

## Data Contract

Each stored chunk has:

- A deterministic chunk ID derived from the document content hash, page, and chunk position.
- A foreign key to an immutable document version.
- A one-based source page number.
- The page-level document type.
- The exact text and character count used downstream.

Documents with the same filename but different content hashes are separate versions. The newest successful version is marked current, and its row points to the version it superseded.

## Event Semantics

Filesystem notifications, like cloud object-created events, can be repeated. The pipeline therefore provides at-least-once event handling with idempotent processing:

1. Wait until the uploaded file size stops changing.
2. Calculate the complete file hash.
3. Skip processing if that hash already exists.
4. Commit the document, pages, and chunks in one database transaction.

This prevents duplicate records without claiming distributed exactly-once delivery.

## Durable Search Index

`chunk_search` is an FTS5 external-content index backed by a view of chunks from current document versions. Chunk and document-version triggers maintain it inside the same transaction as the source change. A successful ingestion therefore cannot commit current chunks without also making them searchable.

Existing databases receive a one-time backfill when schema version 1 is first initialized. Normal startup checks the existing index and does not rebuild it. `pharma-pipeline index-status` runs SQLite's FTS integrity check, while `rebuild-search-index` is reserved for recovery.

Search joins through `documents` and requires `is_current = 1`. When a version is superseded, its immutable source rows remain auditable but its postings are removed from the active index so stale versions cannot affect results or BM25 scores. Duplicate file events are rejected by SHA-256 before chunk insertion and therefore do not grow the index.

## Intended Cloud Boundary

The eventual cloud version can replace the watched folder with an S3 object-created event and SQS message. The `IngestionPipeline.ingest_paths()` processing boundary remains the same. That migration remains deferred until durable incremental retrieval is measured locally.
