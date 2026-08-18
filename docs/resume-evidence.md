# Resume and Interview Evidence

This file links each resume claim to evidence. A claim is **verified** only when a repository command, database query, or saved result can reproduce it.

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
| Ran on S3/SQS | Deployed infrastructure and captured run evidence | Verified: four outcome-path events plus a fresh 16-PDF / 430-page / 1,987-chunk corpus run; worker was locally invoked, not continuously hosted |
| Exposed retrieval through an API and browser client | FastAPI contract tests plus a real Gradio browser query | Verified locally; not hosted or load tested |

## Intended Interview Stories

### Idempotency

File events may arrive more than once. The worker calculates a SHA-256 file fingerprint before extraction. It skips an existing hash, and a unique database constraint provides a final duplicate check.

### Incremental replacement

A changed PDF creates a new immutable version. The earlier version remains available for audit but is no longer current. The new row records which version it replaced.

### OCR routing

The original character-count rule handled image-only scans but trusted hidden OCR text. A repeatable stress set exposed the gap: recall was 38.71%. Adding a full-page image check and a broken-text signal raised routing recall to 100% across 38 cases. It added no OCR work during a 430-page corpus audit. The extractor kept accurate embedded text and rejected conflicts in critical fields. Tesseract `--psm 6` remained because its word error rate was slightly lower than `--psm 3`.

### Failure isolation

One corrupt PDF produces an error record and moves to quarantine. Other files in the same run continue processing.

### Retrieval design

The project compared 21 configurations with separate development, validation, acceptance, and test questions. BGE-small hybrid retrieval performed better during development but lost to boundary-aware BM25 on acceptance Recall@5 and MRR. BM25 was selected before final testing. On the untouched test, BM25 reached 100% Recall@5 versus 87.5% for hybrid. Its p95 latency was about one-tenth of hybrid retrieval. The largest tested index had 2,068 chunks, so an approximate vector database was not needed.

### Incremental search indexing

FTS5 triggers update the BM25 index in the same transaction as chunk and version changes. Duplicate events add no index rows. Older versions leave the active index, and a one-time migration indexed existing chunks. At this corpus size, incremental updates were not meaningfully faster than rebuilding. The measured benefit is bounded work and consistent state, not lower latency.

### Operational metrics

The worker stores each run and failure in SQLite. The export command writes JSON with current and historical corpus size, skip and failure rates, OCR usage, latency percentiles, error types, and index health. It excludes document content. The saved 19-document snapshot mixes development runs, so it proves metric coverage rather than production scale.

### S3/SQS event ingestion

A private, versioned S3 bucket sends `incoming/*.pdf` events to an encrypted standard SQS queue. A worker with limited permissions downloads the exact object version and uses the same ingestion transaction as local files. Four live events tested duplicate, new, replacement, and quarantine outcomes. A fresh cloud run then processed 16 FDA PDFs into 430 pages and 1,987 chunks with no failures. One page used OCR, every document kept its S3 version ID, and the queue drained to zero. The worker ran from the development machine, not as an always-on hosted service.

## Draft Resume Shape

1. Built an S3/SQS event-driven pipeline that processed **16 FDA PDFs / 430 pages** into **1,987 page-linked chunks**, with content-hash idempotency and zero failures in the fresh cloud run.
2. Added OCR fallback, S3 version lineage, immutable document replacement, failure quarantine, and transactional BM25 index updates.
3. Evaluated **21 retrieval configurations on 80 labeled questions** and retained BM25 after acceptance testing; it later beat the hybrid candidate by **12.5 Recall@5 points** on untouched test data.
