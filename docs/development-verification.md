# Development Verification

Date: August 17, 2026

These results verify the ingestion milestone and the first labeled retrieval comparison.

## Automated Tests

Command:

```bash
.venv/bin/pytest
```

Result: **44 tests passed**.

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

## Pre-Corpus Local State

- Document versions: 3
- Current documents: 3
- Pages: 21
- Chunks: 50
- Source bytes: 113,069
- Recorded errors: 0

These early counts intentionally remained modest. The core corpus below supplies reproducible ingestion scale; a labeled query set is still required before making retrieval-quality claims.

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

The OCR-routed page had almost no embedded text. This confirms the routing path was used; it does not measure OCR transcription accuracy.

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

These local timings include transactional FTS5 updates, exclude download time, and reuse the same files, so operating-system caches may be warm. They are a reproducible development benchmark, not a production capacity claim. Raw run data and environment details are saved in [`docs/benchmarks/corpus-ingestion-2026-08-17.json`](benchmarks/corpus-ingestion-2026-08-17.json).

## OCR Routing and Accuracy

Six frozen FDA pages were converted into 36 cases: digital controls, 200-DPI image scans, degraded 120-DPI scans, two corrupted hidden-text styles, and accurate hidden-text scans. A clean repetitive certificate and a matched-length critical-field conflict brought the total to 38 scenarios. Thirty-one were labeled to run OCR; 24 were expected to store OCR output, six accurate-layer scans were expected to retain embedded text, and one field conflict was expected to fail extraction.

| Routing design | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| Character count only | 100.00% | 38.71% | 55.81% |
| Page-aware routing | 100.00% | 100.00% | 100.00% |

The production extractor produced the expected outcome on all 38 scenarios and recovered the labeled phrase in all 24 scenarios expected to store OCR output. It preserved all six accurate hidden layers and rejected the matched-length lot-number/status conflict. A separate short-value regression test confirms that short valid OCR can replace longer fragmented garbage. The improved router added no OCR routes when separately audited across all 430 core-corpus pages; both methods routed the same one low-text page.

| Tesseract configuration | Mean word error rate | Key-phrase accuracy | p50 seconds/page | p95 seconds/page |
| --- | ---: | ---: | ---: | ---: |
| `--psm 3` | 2.27% | 100.00% | 0.9696 | 1.3233 |
| `--psm 6` | 2.10% | 100.00% | 0.9231 | 1.2730 |

OCR quality uses 12 unique visible images rather than double-counting hidden-text variants that share the same raster. The digital source text is controlled ground truth, not an independent transcription. OCR latency excludes rasterization, uses one warm-up per configuration, and alternates execution order. The result supports the routing change and retaining `--psm 6`; it does not establish performance on handwriting or heavily damaged scans. Raw cases and measurements are saved in [`docs/benchmarks/ocr-routing-2026-08-17.json`](benchmarks/ocr-routing-2026-08-17.json).

## Durable Search Index

The existing local database was migrated once, backfilling 2,037 current chunks in 0.0290 seconds. A second process reported the same initialization timestamp, confirming that normal startup did not rebuild the index. SQLite's FTS integrity check passed.

Two untouched FDA test questions were run through the durable CLI index. The expected process-validation page 14 and aseptic-processing page 38 ranked first, matching their committed evaluation labels.

Ten temporary-database runs compared a 77-chunk transaction from the median-sized current document with rebuilding all 2,037 stored chunks:

| Index operation | Chunks touched | p50 | p95 |
| --- | ---: | ---: | ---: |
| Incremental chunk batch | 77 | 0.0124 seconds | 0.0127 seconds |
| Full recovery rebuild | 2,037 | 0.0136 seconds | 0.0142 seconds |

At this scale, transaction overhead dominates and the wall-clock difference is negligible. The result supports incremental work isolation and transactional correctness, not a speedup claim. Raw measurements are saved in [`docs/benchmarks/search-index-2026-08-17.json`](benchmarks/search-index-2026-08-17.json).

## Labeled Retrieval Evaluation

The evaluation uses 80 questions across all 16 FDA documents: 32 development questions, 16 validation questions, 16 acceptance questions, and 16 untouched test questions. The labels cover 80 unique source pages, and the largest tested chunk set contains 2,068 records.

Acceptance and test questions each target one relevant page. Multi-page cases appear only in development and audited validation, so the held-out result is limited to single-page retrieval.

Development scoring compared 21 combinations of chunking strategy, embedding model, and retrieval method. It nominated full-page chunks with BGE-small hybrid retrieval, but the acceptance split selected boundary-aware BM25 before the final test was run.

| Untouched test metric at K=5 | BM25 baseline | Hybrid candidate | Candidate difference |
| --- | ---: | ---: | ---: |
| Recall@5 | 100.00% | 87.50% | -12.50 points |
| Precision@5 | 20.00% | 17.50% | -2.50 points |
| MRR | 96.88% | 73.96% | -22.92 points |
| p95 retrieval latency | 1.17 ms | 11.74 ms | +10.57 ms |

Input hashes, environment versions, raw rankings, and results at K=1/3/5/10 are saved in [`docs/benchmarks/retrieval-evaluation-2026-08-17.json`](benchmarks/retrieval-evaluation-2026-08-17.json). See [`docs/retrieval-evaluation.md`](retrieval-evaluation.md) for the method and limitations.
