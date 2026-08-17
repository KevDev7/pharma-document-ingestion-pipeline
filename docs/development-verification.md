# Development Verification

Date: August 17, 2026

These results verify the ingestion milestone and the first labeled retrieval comparison.

## Automated Tests

Command:

```bash
.venv/bin/pytest
```

Result: **29 tests passed**.

The tests cover:

- Text cleanup, document-type labels, and overlapping chunk boundaries.
- Digital PDF extraction with document/page/chunk lineage.
- Duplicate delivery using the same content hash.
- A changed file superseding the prior document version.
- A corrupt PDF being recorded and moved to quarantine.
- Tesseract OCR fallback on a generated image-only PDF.
- Corpus manifest validation, download receipts, frozen-hash enforcement, and repeatable benchmark counts.
- Retrieval label validation, page-level scoring, duplicate-page collapse, chunking strategies, FTS5 BM25, reciprocal-rank fusion, manifest metadata, and vector cache behavior.

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
| p50 duration | 1.2475 seconds |
| p95 duration | 1.2794 seconds |
| Pages/second at p50 duration | 344.70 |
| Pages/second at p95 duration | 336.09 |

These local timings exclude download time and reuse the same files, so operating-system caches may be warm. They are a reproducible development benchmark, not a production capacity claim. Raw run data and environment details are saved in [`docs/benchmarks/corpus-ingestion-2026-08-17.json`](benchmarks/corpus-ingestion-2026-08-17.json).

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
