# Pharmaceutical Document Ingestion Pipeline

An event-driven data pipeline that turns pharmaceutical PDFs into validated page and chunk records with source lineage. The current version runs locally and processes a document when it appears in `data/incoming/`.

This repository intentionally separates ingestion from the future retrieval application. SQLite is the local control store for the first milestone; the storage interface can later move to PostgreSQL and pgvector without changing PDF extraction or chunking.

## What Works

- Detects new PDFs placed in a watched directory.
- Uses SHA-256 content hashes to skip duplicate documents.
- Extracts digital PDF text with PyMuPDF.
- Runs Tesseract OCR only when a page has too little embedded text.
- Preserves document, page, and chunk lineage in SQLite.
- Tracks processing runs, failures, superseded file versions, page counts, and OCR usage.
- Archives successfully processed files and quarantines failures.
- Includes automated tests for ingestion, duplicate handling, version replacement, and OCR fallback.
- Reconstructs a hash-frozen FDA corpus from a versioned provenance manifest.
- Benchmarks repeated ingestion with a fresh SQLite database for each run.

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
```

## Reproducible Corpus

The repository commits source metadata and expected SHA-256 hashes, not the source PDFs. Rebuild and inspect the corpus with:

```bash
pharma-pipeline download-corpus
pharma-pipeline corpus-status
pharma-pipeline ingest-corpus
pharma-pipeline benchmark-corpus --runs 10 --output docs/benchmarks/corpus-ingestion.json
```

The current core corpus contains 16 FDA-authored pharmaceutical quality documents totaling 430 pages. The verified ingestion run produced 1,987 page-linked chunks, and a duplicate replay skipped all 16 files by content hash. See [corpus/README.md](corpus/README.md) for provenance and [the saved benchmark](docs/benchmarks/corpus-ingestion-2026-08-17.json) for full measurements and limitations.

## Data Model

The SQLite database at `data/state/pipeline.db` contains:

- `ingestion_runs`: operational counts and elapsed time for every run.
- `documents`: content hash, source path, version, processing status, and aggregate counts.
- `pages`: extracted text, page number, document type, and extraction method.
- `chunks`: deterministic chunk IDs and page-level source lineage.
- `ingestion_errors`: files that failed with their error messages.

See [docs/architecture.md](docs/architecture.md) for the system boundary and [docs/resume-evidence.md](docs/resume-evidence.md) for the claim ledger.

## Current Scope

This milestone does not use Docker, Airflow, a vector database, or cloud services. Those tools are not needed to prove the ingestion contract. The next milestone is a labeled retrieval evaluation; cloud object events come later only if the local measurements justify the migration.
