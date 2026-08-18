# Development Verification

Date: August 18, 2026

This file records completed tests and measured results. It also states what each result does not prove.

## Automated Tests

Command:

```bash
.venv/bin/pytest
```

Result: **66 tests passed**.

The tests cover:

- Text cleanup, document-type labels, and overlapping chunk boundaries.
- Digital PDF extraction with document/page/chunk lineage.
- Duplicate delivery using the same content hash.
- A changed file superseding the prior document version.
- A corrupt PDF being recorded and moved to quarantine.
- Tesseract OCR fallback on a generated image-only PDF.
- Quality-aware OCR routing for corrupted hidden text plus deterministic stress-corpus generation and metric aggregation.
- Corpus manifest validation, download receipts, frozen-hash enforcement, and repeatable benchmark counts.
- Retrieval label validation, page-level scoring, duplicate-page collapse, chunking strategies, FTS5 BM25, reciprocal-rank fusion, manifest metadata, and vector cache behavior.
- Durable-index migration/backfill, interrupted-migration recovery, transactional insert/update/delete behavior, duplicate stability, current-version indexing, restart persistence, metadata filtering, rollback, and recovery rebuild.
- S3 event parsing, exact object-version downloads, processed/quarantine archival, message acknowledgement, and retryable failure behavior.

## Real PDF Ingestion

The first manual run used two pharmaceutical blob PDFs from the completed externship:

| Input | Pages | Chunks | OCR pages |
| --- | ---: | ---: | ---: |
| `pharma-blob-sample.pdf` | 10 | 20 | 0 |
| `pharma-blob-test.pdf` | 10 | 27 | 0 |
| **Total** | **20** | **47** | **0** |

The run had no failures. Stored processing time was 0.0325 seconds on the local machine. Two small digital PDFs are not enough for a throughput claim.

## Duplicate Replay

Re-ingesting `pharma-blob-sample.pdf` produced:

- Processed files: 0
- Skipped files: 1
- Reason: `duplicate_content_hash`
- Stored duration: 0.0062 seconds

## File-Created Event

With `pharma-pipeline watch` running, `pharmaceutical-sdf-page3-certificate-quality.pdf` was copied into `data/incoming/`.

The watcher detected the completed copy, processed one page into three chunks, recorded the trigger as `file_created_event`, and moved the PDF into `data/archive/`.

## Pre-Corpus Local State

- Document versions: 3
- Current documents: 3
- Pages: 21
- Chunks: 50
- Source bytes: 113,069
- Recorded errors: 0

These were early development counts. The frozen corpus below provides repeatable ingestion measurements. Retrieval claims use a separate labeled question set.

## FDA Core Corpus

The versioned manifest reconstructed 16 FDA-authored pharmaceutical quality PDFs. All 16 downloads passed PDF validation and matched their expected page counts and SHA-256 hashes.

| Measure | Result |
| --- | ---: |
| PDFs | 16 |
| Source pages | 430 |
| Source bytes | 6,159,783 |
| Stored chunks | 1,987 |
| Digital extraction pages | 429 |
| OCR-routed pages | 1 |
| Failed files | 0 |

The OCR-routed page had almost no embedded text. This confirms that OCR ran. It does not measure transcription accuracy.

The immediate duplicate replay discovered the same 16 files, processed none, skipped all 16 by SHA-256, and completed in 0.0208 seconds.

After the corpus run, the local control database contains 19 document versions, 451 pages, and 2,037 chunks when combined with the earlier development files. Resume scale claims use the isolated 16-document corpus counts above, not this mixed total.

## Repeated Ingestion Timing

Ten runs each used a fresh temporary SQLite database and produced identical file, page, and chunk counts.

| Measure | Result |
| --- | ---: |
| p50 duration | 1.3138 seconds |
| p95 duration | 1.3622 seconds |
| Pages/second at p50 duration | 327.28 |
| Pages/second at p95 duration | 315.67 |

These local timings include FTS5 index updates but exclude download time. Every run reuses the same files, so operating-system caches may be warm. The results are a repeatable development benchmark, not production capacity. Raw timings and environment details are in [`docs/benchmarks/corpus-ingestion-2026-08-17.json`](benchmarks/corpus-ingestion-2026-08-17.json).

## OCR Routing and Accuracy

Six frozen FDA pages were converted into 36 cases: digital controls, 200-DPI image scans, degraded 120-DPI scans, two corrupted hidden-text styles, and accurate hidden-text scans. A clean repetitive certificate and a matched-length critical-field conflict brought the total to 38 scenarios. Thirty-one were labeled to run OCR; 24 were expected to store OCR output, six accurate-layer scans were expected to retain embedded text, and one field conflict was expected to fail extraction.

| Routing design | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| Character count only | 100.00% | 38.71% | 55.81% |
| Page-aware routing | 100.00% | 100.00% | 100.00% |

The production extractor produced the expected outcome in all 38 cases. It recovered the labeled phrase in all 24 cases expected to store OCR text. It kept all six accurate hidden text layers and rejected the conflicting lot number and release status. A separate test confirms that short, valid OCR can replace longer broken text. During a 430-page corpus audit, the new router added no OCR work.

| Tesseract configuration | Mean word error rate | Key-phrase accuracy | p50 seconds/page | p95 seconds/page |
| --- | ---: | ---: | ---: | ---: |
| `--psm 3` | 2.27% | 100.00% | 0.9696 | 1.3233 |
| `--psm 6` | 2.10% | 100.00% | 0.9231 | 1.2730 |

OCR quality uses 12 distinct visible images. Hidden-text variants that share an image are not counted twice. Digital source text serves as controlled ground truth, not an independent transcription. OCR timing excludes PDF rasterization. Each configuration received one warmup, and execution order alternated. The result supports `--psm 6` for this pipeline. It does not measure handwriting or heavily damaged scans. Raw results are in [`docs/benchmarks/ocr-routing-2026-08-17.json`](benchmarks/ocr-routing-2026-08-17.json).

## Operational Metrics Export

The `export-metrics` command was run against the local development database after corpus, watcher, and manual verification runs. The snapshot reported 19 current documents, 451 current pages, 2,037 current searchable chunks, one OCR page, six completed runs, zero recorded failures, and a healthy FTS5 integrity check.

This snapshot mixes several development runs. It is not a corpus benchmark or production scale result. It verifies that metrics can be exported without document text, filenames, hashes, source paths, or error messages. The output is in [`docs/benchmarks/operational-metrics-2026-08-17.json`](benchmarks/operational-metrics-2026-08-17.json).

## Live S3/SQS Integration

A private, versioned S3 bucket in `us-east-1` publishes `incoming/*.pdf` creation events to an encrypted standard SQS queue. The queue uses a 15-minute visibility timeout, 20-second long polling, and an encrypted dead-letter queue after three failed receives. A dedicated least-privilege IAM user runs the single local worker.

| Live scenario | Worker outcome | Pages | Chunks | Queue handling |
| --- | --- | ---: | ---: | --- |
| Existing FDA PDF replay | Skipped by SHA-256 | 0 | 0 | Acknowledged |
| New synthetic certificate | Processed | 1 | 1 | Acknowledged |
| Changed certificate under the same key | Processed as a new version | 1 | 1 | Acknowledged |
| Malformed `.pdf` | Quarantined with `FileDataError` | 0 | 0 | Acknowledged |

The changed certificate points to the earlier document through `supersedes_document_id`. Only one version remains current, and only that version appears in FTS5 search. Every source record contains the S3 `versionId`. After the test, `incoming/` and the main queue were empty. `processed/` contained three hash-addressed objects, and `quarantine/` contained the malformed object. The index passed its integrity check.

These tests verify the cloud event path, not throughput. The worker ran from the development machine and was not hosted continuously. Automated tests cover temporary failures and message retries. The live test did not break AWS permissions only to force a dead-letter message. Raw evidence is in [`docs/benchmarks/aws-integration-2026-08-18.json`](benchmarks/aws-integration-2026-08-18.json).

### Full Cloud Corpus Run

The complete frozen FDA corpus was then uploaded to `incoming/` and processed through live S3 object-created events and SQS using a fresh, isolated SQLite database.

| Measure | Result |
| --- | ---: |
| S3-triggered runs | 16 |
| Completed files | 16 |
| Failed or skipped files | 0 |
| Source pages | 430 |
| Stored chunks | 1,987 |
| Digital extraction pages | 429 |
| OCR fallback pages | 1 |
| Version-qualified S3 source records | 16 of 16 |
| Main queue after processing | 0 visible, 0 in flight, 0 delayed |
| Search index | Healthy, 1,987 current chunks |

The isolated database matched the frozen corpus: 6,159,783 source bytes, 430 pages, and 1,987 chunks. All 16 documents kept distinct S3 `versionId` values. Stored processing time was 0.0952 seconds at p50, 0.3427 seconds at p95, and 0.5254 seconds maximum. These times cover local ingestion after download. They exclude browser upload, SQS transit, and S3 download, so they are not end-to-end cloud latency.

The bucket contains 18 processed objects: 16 FDA files and two synthetic versioning checks. One malformed object remains in `quarantine/`, and `incoming/` is empty. The worker cannot list the whole bucket. Its permissions cover only the object paths and queue actions required for processing.

## Durable Search Index

The existing local database was migrated once, backfilling 2,037 current chunks in 0.0290 seconds. A second process reported the same initialization timestamp, confirming that normal startup did not rebuild the index. SQLite's FTS integrity check passed.

Two untouched FDA test questions were run through the durable CLI index. The expected process-validation page 14 and aseptic-processing page 38 ranked first, matching their committed evaluation labels.

Ten temporary-database runs compared a 77-chunk transaction from the median-sized current document with rebuilding all 2,037 stored chunks:

| Index operation | Chunks touched | p50 | p95 |
| --- | ---: | ---: | ---: |
| Incremental chunk batch | 77 | 0.0124 seconds | 0.0127 seconds |
| Full recovery rebuild | 2,037 | 0.0136 seconds | 0.0142 seconds |

At this size, database transaction overhead hides most timing differences. The result supports bounded incremental updates and database consistency, not a speedup claim. Raw measurements are in [`docs/benchmarks/search-index-2026-08-17.json`](benchmarks/search-index-2026-08-17.json).

## Labeled Retrieval Evaluation

The evaluation uses 80 questions across all 16 FDA documents: 32 development questions, 16 validation questions, 16 acceptance questions, and 16 untouched test questions. The labels cover 80 unique source pages, and the largest tested chunk set contains 2,068 records.

Acceptance and test questions each target one relevant page. Multi-page cases appear only in development and audited validation, so the held-out result is limited to single-page retrieval.

Development scoring compared 21 chunking, embedding, and retrieval combinations. It selected full-page BGE-small hybrid retrieval as the development candidate. Acceptance data selected boundary-aware BM25 before the final test.

| Untouched test metric at K=5 | BM25 baseline | Hybrid candidate | Candidate difference |
| --- | ---: | ---: | ---: |
| Recall@5 | 100.00% | 87.50% | -12.50 points |
| Precision@5 | 20.00% | 17.50% | -2.50 points |
| MRR | 96.88% | 73.96% | -22.92 points |
| p95 retrieval latency | 1.17 ms | 11.74 ms | +10.57 ms |

Input hashes, package versions, rankings, and results at K=1/3/5/10 are in [`docs/benchmarks/retrieval-evaluation-2026-08-17.json`](benchmarks/retrieval-evaluation-2026-08-17.json). See [`docs/retrieval-evaluation.md`](retrieval-evaluation.md) for the method and limits.
