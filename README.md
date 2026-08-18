# Pharmaceutical Document Ingestion Pipeline

An event-driven data pipeline that turns pharmaceutical PDFs into validated page and chunk records. A chunk is a smaller text section used for search. Event-driven means processing starts when a PDF arrives instead of waiting for a schedule. Each record keeps source lineage, which links it back to the source file, document version, and page.

The pipeline accepts local file events and events from Amazon S3 object storage. Amazon SQS delivers the S3 events through a queue that holds work until the worker can process it. SQLite stores processing state, lineage, and the search index.

## What Works

- Detects new PDFs placed in a watched directory.
- Uses SHA-256 content hashes, which are file fingerprints, to skip duplicate documents.
- Extracts digital PDF text with PyMuPDF.
- Runs Tesseract optical character recognition (OCR) when embedded text is missing or shows clear corruption signals.
- Preserves document, page, and chunk lineage in SQLite.
- Updates a durable SQLite full-text search index (FTS5) in the same database transaction as new chunks. BM25 ranks the keyword matches.
- Searches only current document versions while retaining superseded source records for audit.
- Tracks processing runs, failures, superseded file versions, page counts, and OCR usage.
- Archives successfully processed files and quarantines failures.
- Polls an encrypted SQS queue for S3 object-created events and records the exact S3 object version as source lineage.
- Copies successful and duplicate objects to `processed/`.
- Moves invalid documents to `quarantine/` and retries temporary worker or infrastructure failures.
- Includes automated tests for ingestion, duplicate handling, version replacement, and OCR fallback.
- Reconstructs the FDA test corpus from a manifest that records source URLs and expected file hashes.
- Benchmarks repeated ingestion with a fresh SQLite database for each run.
- Evaluates 21 chunking, embedding, and retrieval configurations on 80 labeled questions.
- Rebuilds a 38-case OCR stress set from six frozen FDA pages plus synthetic controls and records routing, transcription, and latency metrics.

## Quick Start

Python 3.9+ and the Tesseract command-line program are required for OCR fallback.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

pharma-pipeline init
pharma-pipeline watch
```

While the watcher is running, copy a PDF into `data/incoming/`. The file is processed and moved into `data/archive/`.

You can also process files directly without moving them:

```bash
pharma-pipeline ingest /path/to/document.pdf
pharma-pipeline status
pharma-pipeline export-metrics --output data/state/operational-metrics.json
pharma-pipeline search "What must be completed before commercial distribution?" --top-k 5
pharma-pipeline index-status
```

Run the worker against the deployed S3/SQS input path:

```bash
pharma-pipeline watch-s3 \
  --bucket YOUR_PRIVATE_BUCKET \
  --queue-name YOUR_STANDARD_QUEUE \
  --region us-east-1 \
  --profile YOUR_WORKER_PROFILE
```

The deployment uses a private, versioned S3 bucket with server-side encryption. An encrypted standard SQS queue buffers events. A dead-letter queue isolates messages after three failed receives. The worker identity can access only the required bucket paths and queue actions.

A fresh cloud run processed all 16 FDA PDFs into 430 pages and 1,987 chunks. One page used OCR, no files failed, and every document kept its exact S3 version ID. The queue was empty after the run. See [the S3 integration evidence](docs/benchmarks/aws-integration-2026-08-18.json) for run IDs, object versions, and limits.

## Reproducible Corpus

The repository commits source metadata and expected SHA-256 hashes, not the source PDFs. Rebuild and inspect the corpus with:

```bash
pharma-pipeline download-corpus
pharma-pipeline corpus-status
pharma-pipeline ingest-corpus
pharma-pipeline benchmark-corpus --runs 10 --output docs/benchmarks/corpus-ingestion.json
```

The core corpus contains 16 FDA pharmaceutical-quality documents and 430 pages. A verified run produced 1,987 page-linked chunks. Replaying the same corpus skipped all 16 files by content hash. See [corpus/README.md](corpus/README.md) for source records and [the saved benchmark](docs/benchmarks/corpus-ingestion-2026-08-17.json) for measurements and limits.

## OCR Stress Evaluation

Generate controlled digital, image-only, degraded, and corrupted-text-layer variants from six frozen FDA source pages:

```bash
pharma-pipeline benchmark-ocr \
  --output docs/benchmarks/ocr-routing-2026-08-17.json
```

The original router used only character count. It reached 38.71% recall on 38 controlled cases because hidden text could bypass OCR checks. The page-aware router reached 100% recall on the same set. It also produced the expected final outcome in all 38 cases. This included keeping six accurate text layers and rejecting one conflict in critical fields.

The new router did not add OCR work during a separate audit of all 430 corpus pages. On 12 visible scan images, Tesseract `--psm 6` recovered every labeled phrase. Mean word error rate was 2.10%, and median OCR time was 0.92 seconds per page. These are controlled local results, not a claim about all scanned documents.

## Retrieval Evaluation

Install the optional retrieval dependencies and run the committed evaluation set:

```bash
pip install -e '.[retrieval,dev]'
pharma-pipeline evaluate-retrieval \
  --output docs/benchmarks/retrieval-evaluation-2026-08-17.json
```

The experiment compares three chunking methods, three embedding models, and keyword, vector, and hybrid retrieval. The acceptance set selected boundary-aware chunks with BM25 keyword retrieval before the final test.

On 16 untouched test questions, BM25 reached 100% Recall@5 versus 87.5% for the hybrid candidate. Recall@5 measures whether the correct page appears in the first five results. BM25 p95 retrieval latency was 1.17 ms versus 11.74 ms for hybrid retrieval. The p95 value is the time at or below which 95% of measured queries finished. See [docs/retrieval-evaluation.md](docs/retrieval-evaluation.md) for the method and limits.

## Data Model

The SQLite database at `data/state/pipeline.db` contains:

- `ingestion_runs`: operational counts and elapsed time for every run.
- `documents`: content hash, source path, version, processing status, and totals.
- `pages`: extracted text, page number, document type, and extraction method.
- `chunks`: repeatable chunk IDs and page-level source lineage.
- `chunk_search`: durable FTS5 index maintained by chunk-table triggers.
- `search_index_state`: index schema, initial backfill, and rebuild status.
- `ingestion_errors`: files that failed with their error messages.

See [docs/architecture.md](docs/architecture.md) for the system design. See [docs/resume-evidence.md](docs/resume-evidence.md) for evidence behind resume claims.

## Operational Metrics

Every ingestion run writes file counts, page and chunk totals, duration, and errors to SQLite. Export a JSON metrics file that excludes document content:

```bash
pharma-pipeline export-metrics \
  --output data/state/operational-metrics.json
```

The export separates current documents from older versions. It reports processed, skipped, and failed files; p50 and p95 run time; OCR usage; error types; and FTS5 index health. It does not include document text or source paths. See [docs/operations.md](docs/operations.md) for field definitions and checks.

## Current Scope

This project implements a single-worker, event-driven PDF ingestion pipeline. S3/SQS provides cloud input, while SQLite stores document lineage and maintains the FTS5/BM25 search index. The worker currently runs from a development machine rather than as an always-on hosted service. Supporting concurrent cloud workers would require moving state to shared storage such as PostgreSQL. See [docs/s3-handoff.md](docs/s3-handoff.md) for the AWS setup and message-handling rules.
