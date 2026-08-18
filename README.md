# Pharmaceutical Document Ingestion Pipeline

An event-driven data pipeline that turns pharmaceutical PDFs into validated page and chunk records with source lineage. It accepts local file-created events or version-aware S3 object-created events delivered through SQS.

This repository intentionally separates ingestion from the future retrieval application. SQLite is the local control store for the first milestone; the storage interface can later move to PostgreSQL and pgvector without changing PDF extraction or chunking.

## What Works

- Detects new PDFs placed in a watched directory.
- Uses SHA-256 content hashes to skip duplicate documents.
- Extracts digital PDF text with PyMuPDF.
- Runs Tesseract OCR when embedded text is missing or shows clear corruption signals.
- Preserves document, page, and chunk lineage in SQLite.
- Updates a durable SQLite FTS5/BM25 index in the same transaction as new chunks.
- Searches only current document versions while retaining superseded source records for audit.
- Tracks processing runs, failures, superseded file versions, page counts, and OCR usage.
- Archives successfully processed files and quarantines failures.
- Polls an encrypted SQS queue for S3 object-created events and records the exact S3 object version as source lineage.
- Copies successful or duplicate objects to `processed/`, moves deterministic document failures to `quarantine/`, and leaves transient failures unacknowledged for retry.
- Includes automated tests for ingestion, duplicate handling, version replacement, and OCR fallback.
- Reconstructs a hash-frozen FDA corpus from a versioned provenance manifest.
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

The deployed S3/SQS ingress can be consumed by the same processing boundary:

```bash
pharma-pipeline watch-s3 \
  --bucket YOUR_PRIVATE_BUCKET \
  --queue-name YOUR_STANDARD_QUEUE \
  --region us-east-1 \
  --profile YOUR_WORKER_PROFILE
```

The verified deployment uses a private, versioned, SSE-S3 bucket; an encrypted standard SQS queue; a dead-letter queue after three failed receives; and a least-privilege worker identity. A fresh cloud-triggered run processed the complete 16-PDF FDA corpus into 430 pages and 1,987 chunks with one OCR fallback page, zero failures, version-qualified S3 lineage for every document, and an empty queue afterward. See [the S3 integration evidence](docs/benchmarks/aws-integration-2026-08-18.json) for the live scenarios and limitations.

## Reproducible Corpus

The repository commits source metadata and expected SHA-256 hashes, not the source PDFs. Rebuild and inspect the corpus with:

```bash
pharma-pipeline download-corpus
pharma-pipeline corpus-status
pharma-pipeline ingest-corpus
pharma-pipeline benchmark-corpus --runs 10 --output docs/benchmarks/corpus-ingestion.json
```

The current core corpus contains 16 FDA-authored pharmaceutical quality documents totaling 430 pages. The verified ingestion run produced 1,987 page-linked chunks, and a duplicate replay skipped all 16 files by content hash. See [corpus/README.md](corpus/README.md) for provenance and [the saved benchmark](docs/benchmarks/corpus-ingestion-2026-08-17.json) for full measurements and limitations.

## OCR Stress Evaluation

Generate controlled digital, image-only, degraded, and corrupted-text-layer variants from six frozen FDA source pages:

```bash
pharma-pipeline benchmark-ocr \
  --output docs/benchmarks/ocr-routing-2026-08-17.json
```

The original character-count router reached 38.71% recall on the 38-case stress set because hidden text bypassed OCR verification. The page-aware router reached 100% recall, while the production extractor produced the expected outcome in all 38 scenarios, including preserving six accurate text layers and rejecting one matched-length critical-field conflict. The router added no work during a separate audit of all 430 core-corpus pages. On 12 unique visible scan images, Tesseract `--psm 6` recovered every labeled phrase with 2.10% mean word error rate and 0.92-second p50 OCR time per page. These are controlled local measurements, not a general OCR accuracy claim.

## Retrieval Evaluation

Install the optional retrieval dependencies and run the committed evaluation set:

```bash
pip install -e '.[retrieval,dev]'
pharma-pipeline evaluate-retrieval \
  --output docs/benchmarks/retrieval-evaluation-2026-08-17.json
```

The experiment compares page, fixed-window, and boundary-aware chunks; MiniLM, BGE-small, and E5-small embeddings; and keyword, vector, and hybrid retrieval. A 16-question acceptance split retained boundary-aware BM25 before the final test was run. On 16 untouched test questions, BM25 achieved 100% Recall@5 versus 87.5% for the hybrid development candidate, with 1.17 ms versus 11.74 ms p95 retrieval latency. BM25 remains the recommended local design. See [docs/retrieval-evaluation.md](docs/retrieval-evaluation.md) for methodology and limitations.

## Data Model

The SQLite database at `data/state/pipeline.db` contains:

- `ingestion_runs`: operational counts and elapsed time for every run.
- `documents`: content hash, source path, version, processing status, and aggregate counts.
- `pages`: extracted text, page number, document type, and extraction method.
- `chunks`: deterministic chunk IDs and page-level source lineage.
- `chunk_search`: durable FTS5 index maintained by chunk-table triggers.
- `search_index_state`: index schema, initial backfill, and recovery-rebuild metadata.
- `ingestion_errors`: files that failed with their error messages.

See [docs/architecture.md](docs/architecture.md) for the system boundary and [docs/resume-evidence.md](docs/resume-evidence.md) for the claim ledger.

## Operational Metrics

Every ingestion run writes file counts, page and chunk totals, duration, and errors to SQLite. Export a content-free operational snapshot with:

```bash
pharma-pipeline export-metrics \
  --output data/state/operational-metrics.json
```

The export separates current documents from historical versions, reports processed/skipped/failed files, p50/p95 run duration, OCR usage, grouped error types, and FTS5 integrity. It does not include extracted document text or source paths. See [docs/operations.md](docs/operations.md) for field definitions and suggested checks.

## Current Scope

This milestone does not use Docker, Airflow, or a managed vector database. SQLite FTS5 is sufficient for the measured single-worker corpus and updates incrementally with each ingested document. S3/SQS ingress is deployed and verified with a locally invoked worker; it is not presented as an always-on hosted service or a distributed SQLite design. See [docs/s3-handoff.md](docs/s3-handoff.md) for the cloud boundary and outcome policy.
