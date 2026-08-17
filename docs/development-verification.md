# Development Verification

Date: August 17, 2026

These results verify the first milestone. They are not a retrieval benchmark and should not be used as final resume scale claims.

## Automated Tests

Command:

```bash
.venv/bin/pytest
```

Result: **11 tests passed** in 3.14 seconds.

The tests cover:

- Text cleanup, document-type labels, and overlapping chunk boundaries.
- Digital PDF extraction with document/page/chunk lineage.
- Duplicate delivery using the same content hash.
- A changed file superseding the prior document version.
- A corrupt PDF being recorded and moved to quarantine.
- Tesseract OCR fallback on a generated image-only PDF.

## Real PDF Ingestion

The first manual run used two pharmaceutical blob PDFs from the completed externship:

| Input | Pages | Chunks | OCR pages |
| --- | ---: | ---: | ---: |
| `pharma-blob-sample.pdf` | 10 | 20 | 0 |
| `pharma-blob-test.pdf` | 10 | 27 | 0 |
| **Total** | **20** | **47** | **0** |

The run finished successfully with no failed files. The stored duration was 0.0325 seconds on the local machine; this small digital-text sample is not large enough for a meaningful throughput claim.

## Duplicate Replay

Re-ingesting `pharma-blob-sample.pdf` produced:

- Processed files: 0
- Skipped files: 1
- Reason: `duplicate_content_hash`
- Stored duration: 0.0062 seconds

## File-Created Event

With `pharma-pipeline watch` running, `pharmaceutical-sdf-page3-certificate-quality.pdf` was copied into `data/incoming/`.

The watcher detected the completed copy, processed one page into three chunks, recorded the trigger as `file_created_event`, and moved the PDF into `data/archive/`.

## Current Local State

- Document versions: 3
- Current documents: 3
- Pages: 21
- Chunks: 50
- Source bytes: 113,069
- Recorded errors: 0

These counts intentionally remain modest. A larger, licensed benchmark corpus and labeled retrieval questions are required before producing final scale, quality, or latency claims.
