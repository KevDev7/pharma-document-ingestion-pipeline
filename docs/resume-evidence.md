# Resume and Interview Evidence

This file prevents unsupported resume claims. A claim moves to **verified** only when its value can be reproduced from a command, database query, or evaluation artifact in this repository.

| Potential claim | Evidence required | Current status |
| --- | --- | --- |
| Processed 16 PDFs and 430 pages into 1,987 chunks | Frozen manifest, corpus ingestion run, and saved 10-run benchmark | Verified locally |
| Skipped duplicate and unchanged PDFs | Duplicate-ingestion test plus corpus replay | Verified: all 16 corpus files skipped on replay |
| Incrementally handled updated PDFs | Version test showing `supersedes_document_id` and one current version | Implemented and tested |
| Improved OCR routing for scanned and hidden-text pages | Versioned 38-case stress manifest, production-extractor results, full-corpus routing audit, and saved raw benchmark | Verified locally: routing recall improved from 38.71% to 100%; all expected outcomes, including one rejected field conflict; no added routes across the 430-page core audit |
| Measured Tesseract transcription quality and latency | 12 unique visible scan images with digital references and six labeled phrases | Verified locally: `--psm 6` mean WER 2.10%, 100% phrase recovery, 0.9231-second p50; controlled set only |
| Evaluated 21 retrieval configurations on 80 labeled questions | Committed development/validation/acceptance/test labels and raw experiment artifact | Verified locally |
| Retained BM25 after hybrid retrieval underperformed on acceptance data | Separate 16-question acceptance split used before the final test | Verified locally: 100.00% versus 93.75% Recall@5 |
| Confirmed the locked choice on untouched test data | Final 16-question test split created without retrieval-output inspection | Verified locally: BM25 100.00% versus hybrid 87.50% Recall@5; do not generalize perfect recall |
| Measured p95 retrieval latency across repeated interleaved trials | 80 raw timing samples per retriever | Verified locally: BM25 1.17 ms; hybrid candidate 11.74 ms |
| Incrementally maintained a durable BM25 index | Trigger lifecycle tests, restart test, integrity check, and saved index benchmark | Verified locally: 77-chunk update transaction; duplicate replay added zero chunks |
| Exported operational ingestion and index metrics | Versioned JSON schema plus CLI and tests covering versions, duplicates, latency, OCR, errors, and index health | Implemented and tested; development snapshot is not a scale benchmark |
| Ran on S3/SQS | Deployed infrastructure and captured run evidence | Not built; do not claim |

## Intended Interview Stories

### Idempotency

File-created events may be delivered more than once. The worker calculates a SHA-256 hash and checks the document table before extracting anything. The unique hash constraint is the final database safeguard.

### Incremental replacement

A changed PDF receives a new immutable document version. The earlier version remains auditable but is no longer current, and the new row records which version it superseded.

### OCR routing

The original character threshold handled image-only scans but trusted hidden OCR layers. A reproducible stress set exposed the gap: baseline recall was 38.71%. Adding full-page image evidence and a fragmented-text signal raised routing recall to 100% across 38 scenarios without adding routes in a 430-page core-corpus audit. Output selection preserved accurate embedded text after OCR verification, while critical-field disagreement failed safely instead of becoming searchable. Tesseract `--psm 6` remained because it had slightly lower word error rate than `--psm 3`; engine-level comparisons remain a future experiment.

### Failure isolation

One corrupt PDF produces an error record and moves to quarantine. Other files in the same run continue processing.

### Retrieval design

The project compared 21 configurations using separate development, validation, acceptance, and untouched test questions. BGE-small hybrid retrieval looked stronger during development but lost to boundary-aware BM25 on acceptance Recall@5 and MRR. The simpler BM25 path was locked before final testing. On the untouched test, BM25 reached 100% Recall@5 versus 87.5% for hybrid and was about 10 times faster at p95. The largest tested index was only 2,068 chunks, so exact local vector search was more explainable than adding an approximate vector database.

### Incremental search indexing

FTS5 triggers update the selected BM25 index inside the same transaction as chunk and document-version changes. Duplicate events add no index rows, superseded versions are removed from the active index, and a one-time migration backfilled existing data. The local benchmark did not show a meaningful latency win over rebuilding at this corpus size, so the defensible claim is bounded incremental work and consistency, not faster indexing.

### Operational metrics

The worker writes each run and file failure to SQLite, and the export command produces a content-free JSON snapshot with current versus historical corpus size, skip/failure rates, OCR usage, latency percentiles, grouped error types, and search-index integrity. The saved 19-document development snapshot mixes controlled runs and is useful as proof of observability, not as a benchmark or resume-scale claim.

## Draft Resume Shape

1. Built an event-driven pipeline that processed **16 FDA PDFs / 430 pages** into **1,987 page-linked chunks**, using content hashes to skip duplicate files.
2. Added OCR fallback, document versioning, failure quarantine, page-level lineage, and transactional BM25 index updates.
3. Evaluated **21 retrieval configurations on 80 labeled questions** and retained BM25 after acceptance testing; it later beat the hybrid candidate by **12.5 Recall@5 points** on untouched test data.
