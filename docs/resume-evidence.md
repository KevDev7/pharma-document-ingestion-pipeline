# Resume and Interview Evidence

This file prevents unsupported resume claims. A claim moves to **verified** only when its value can be reproduced from a command, database query, or evaluation artifact in this repository.

| Potential claim | Evidence required | Current status |
| --- | --- | --- |
| Processed X PDFs and Y pages into Z chunks | `pharma-pipeline status` after the benchmark corpus is ingested | Development sample verified; benchmark corpus pending |
| Skipped duplicate and unchanged PDFs | Duplicate-ingestion test plus benchmark run counts | Implemented; corpus measurement pending |
| Incrementally handled updated PDFs | Version test showing `supersedes_document_id` and one current version | Implemented and tested |
| Used OCR fallback for scanned files | OCR test plus corpus OCR-page count | Image-only OCR test passed; corpus measurement pending |
| Improved retrieval Recall@5 from A to B | Labeled query set and committed evaluation output | Not built |
| Reduced p95 query latency by C percent | Repeated benchmark with saved raw timings | Not built |
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

## Draft Resume Shape

Do not insert values until the evidence exists.

1. Built an event-driven pipeline that processed **X PDFs / Y pages** into **Z metadata-rich chunks**, using content hashes to skip duplicate files.
2. Added OCR fallback, document versioning, failure quarantine, and page-level lineage for incremental PDF ingestion.
3. Improved **Recall@5 from A to B** while keeping **p95 retrieval latency below C ms** by evaluating chunking and retrieval strategies.
