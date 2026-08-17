# Resume and Interview Evidence

This file prevents unsupported resume claims. A claim moves to **verified** only when its value can be reproduced from a command, database query, or evaluation artifact in this repository.

| Potential claim | Evidence required | Current status |
| --- | --- | --- |
| Processed 16 PDFs and 430 pages into 1,987 chunks | Frozen manifest, corpus ingestion run, and saved 10-run benchmark | Verified locally |
| Skipped duplicate and unchanged PDFs | Duplicate-ingestion test plus corpus replay | Verified: all 16 corpus files skipped on replay |
| Incrementally handled updated PDFs | Version test showing `supersedes_document_id` and one current version | Implemented and tested |
| Used OCR fallback for scanned files | OCR test plus corpus OCR-page count | Verified: generated image-only test and 1 routed corpus page; OCR accuracy not measured |
| Evaluated 21 retrieval configurations on 80 labeled questions | Committed development/validation/acceptance/test labels and raw experiment artifact | Verified locally |
| Retained BM25 after hybrid retrieval underperformed on acceptance data | Separate 16-question acceptance split used before the final test | Verified locally: 100.00% versus 93.75% Recall@5 |
| Confirmed the locked choice on untouched test data | Final 16-question test split created without retrieval-output inspection | Verified locally: BM25 100.00% versus hybrid 87.50% Recall@5; do not generalize perfect recall |
| Measured p95 retrieval latency across repeated interleaved trials | 80 raw timing samples per retriever | Verified locally: BM25 1.17 ms; hybrid candidate 11.74 ms |
| Ran on S3/SQS | Deployed infrastructure and captured run evidence | Not built; do not claim |

## Intended Interview Stories

### Idempotency

File-created events may be delivered more than once. The worker calculates a SHA-256 hash and checks the document table before extracting anything. The unique hash constraint is the final database safeguard.

### Incremental replacement

A changed PDF receives a new immutable document version. The earlier version remains auditable but is no longer current, and the new row records which version it superseded.

### OCR routing

Digital pages use embedded text because it is faster and generally more accurate. Low-text pages are rendered and sent through Tesseract so scanned documents still enter the same page and chunk contract.

### Failure isolation

One corrupt PDF produces an error record and moves to quarantine. Other files in the same run continue processing.

### Retrieval design

The project compared 21 configurations using separate development, validation, acceptance, and untouched test questions. BGE-small hybrid retrieval looked stronger during development but lost to boundary-aware BM25 on acceptance Recall@5 and MRR. The simpler BM25 path was locked before final testing. On the untouched test, BM25 reached 100% Recall@5 versus 87.5% for hybrid and was about 10 times faster at p95. The largest tested index was only 2,068 chunks, so exact local vector search was more explainable than adding an approximate vector database.

## Draft Resume Shape

1. Built an event-driven pipeline that processed **16 FDA PDFs / 430 pages** into **1,987 page-linked chunks**, using content hashes to skip duplicate files.
2. Added OCR fallback, document versioning, failure quarantine, and page-level lineage for incremental PDF ingestion.
3. Evaluated **21 retrieval configurations on 80 labeled questions** and retained BM25 after acceptance testing; it later beat the hybrid candidate by **12.5 Recall@5 points** on untouched test data.
