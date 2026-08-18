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
FTS5/BM25 index update in the same transaction
        |
        +--> data/archive/      successful or duplicate PDFs
        +--> data/quarantine/   failed PDFs
```

Benchmarks use the same processing code. They read files listed in `corpus/manifest.json`, where expected SHA-256 file hashes freeze the inputs. Each run uses a fresh temporary SQLite database so earlier data cannot cause a duplicate skip.

## Deployed S3/SQS Ingress

```text
private, versioned S3 incoming/*.pdf
        |
        v
S3 ObjectCreated notification
        |
        v
encrypted SQS standard queue --> dead-letter queue after 3 failed receives
        |
        v
single Python worker downloads the exact S3 object version
        |
        v
shared ingestion code + SQLite/FTS5 transaction
        |
        +--> S3 processed/     successful or duplicate objects
        +--> S3 quarantine/    deterministic document failures
        +--> message retained  transient worker/infrastructure failures
```

The worker stores the S3 object version with each source URI. Later uploads under the same key cannot change the meaning of an older database record. The private bucket uses S3-managed encryption and versioning. It publishes events only for PDFs under `incoming/`.

SQS uses at-least-once delivery, so the same event may arrive more than once. The queue uses a 15-minute visibility timeout, 20-second long polling, server-side encryption, and a dead-letter queue after three failed receives.

One worker runs from the development machine and writes to SQLite. This verifies the cloud upload and queue flow. It is not an always-on service, and SQLite is not used for concurrent workers.

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
Recall@K / Precision@K / MRR / p50-p95 latency
```

Embeddings are numeric vectors that represent text meaning. The experiment caches them using the model revision, package versions, chunk IDs, and text hashes. Long text is split at the model token limit. Segment vectors are then averaged and normalized.

Evaluation labels use the document hash and page number. The evaluator rejects database rows that do not match the frozen corpus manifest. Vector retrieval checks every stored vector because the largest tested index contains only 2,068 chunks.

## Data Contract

Each stored chunk has:

- A repeatable chunk ID built from the document hash, page, and chunk position.
- A foreign key to an immutable document version.
- A one-based source page number.
- The page-level document type.
- The exact text and character count used downstream.

Documents with the same filename but different content hashes are separate versions. The newest successful version is marked current, and its row points to the version it superseded.

## Event Semantics

Local and cloud file events can be delivered more than once. The pipeline uses idempotent processing, which means replaying the same file does not create duplicate records:

1. Wait until the uploaded file size stops changing.
2. Calculate the complete file hash.
3. Skip processing if that hash already exists.
4. Commit the document, pages, and chunks in one database transaction.

This prevents duplicate records. It does not depend on exactly-once event delivery.

## Durable Search Index

`chunk_search` is a SQLite FTS5 full-text index over chunks from current document versions. Database triggers update it in the same transaction as the source rows. New chunks cannot commit without also becoming searchable.

Existing databases receive a one-time index backfill. Normal startup checks the index and does not rebuild it. `pharma-pipeline index-status` runs SQLite's FTS integrity check. `rebuild-search-index` is used only for recovery.

Search requires `documents.is_current = 1`. Older immutable versions stay in the source tables for audit. Their search entries are removed so they cannot affect results or BM25 scores. SHA-256 duplicate checks run before chunk insertion, so replayed files do not grow the index.

## OCR Routing

The extractor keeps digital text when it is long enough and does not look broken. It sends a page to Tesseract when embedded text is short, contains too many one-character tokens, or appears with an image covering at least 80% of the page. The image check catches scans whose hidden OCR text looks plausible but is wrong.

The OCR stress set contains 38 cases built from six frozen FDA pages and two synthetic controls. It covers image-only scans, corrupted hidden text, accurate hidden text, and one conflict in a critical field. The benchmark compares the old character-count rule with the page-aware router. It also compares Tesseract `--psm 3` and `--psm 6` on 12 visible scan images.

If OCR and embedded text disagree on a critical field such as lot number or release status, extraction fails instead of choosing one value. Generated scans stay outside Git. Their source hashes, page labels, and transformation settings are versioned.

## Operational Observability

The local operational record includes runs, errors, document totals, page extraction methods, and search-index state. `pharma-pipeline export-metrics` writes these values to JSON without document text or source paths. It reports current documents separately from older versions so a replacement does not look like corpus growth.

Metrics are read from the worker database when requested. A hosted worker could later send the same counters to CloudWatch without changing their definitions.
