# Project Context for Codex

## Product Goal

Build a portfolio-quality data engineering system for pharmaceutical PDF ingestion and retrieval. The project began from a completed pharmaceutical document-intelligence externship, but this repository is a standalone engineering project.

The primary product is the data pipeline, not the chatbot. A retrieval API or Gradio interface may consume the processed data later, but ingestion reliability, lineage, incremental processing, and measurable performance come first.

Keep explanations and user-facing documentation readable for data engineering hiring managers who may not know AI-specific terminology.

## Current Architecture

The current milestone is a local event-driven pipeline:

```text
PDF added to data/incoming/
        -> stable-file check
        -> SHA-256 duplicate/version check
        -> PyMuPDF text extraction
        -> Tesseract fallback for low-text pages
        -> page classification and overlapping chunks
        -> SQLite documents/pages/chunks/run records
        -> archive on success or quarantine on failure
```

Use the term **event-driven PDF ingestion**, not streaming. Each PDF is a discrete job rather than a continuous stream of records.

## Implemented and Verified

- CLI commands: `init`, `ingest`, `scan`, `watch`, and `status`.
- SHA-256 duplicate detection with a unique database constraint.
- Immutable document versions with `supersedes_document_id` and one current version.
- Page-level extraction metadata and deterministic chunk identifiers.
- Conditional Tesseract OCR fallback.
- Archive and quarantine paths.
- Ingestion run counts, timings, and error records.
- Eleven automated tests, including an image-only OCR test.
- Real development run on two pharmaceutical blob PDFs: 20 pages and 47 chunks.
- Manual watcher verification using a real PDF file-created event.

Read `docs/development-verification.md` for exact evidence. These development counts are not a benchmark and should not be presented as impressive scale.

## Engineering Constraints

- Do not add Airflow. The user already demonstrates Airflow in another portfolio project, and this project intentionally shows event-driven ingestion.
- Do not add Docker unless a later deployment creates a concrete need. The user already demonstrates Docker elsewhere.
- Keep SQLite for the current single-worker milestone. Evaluate PostgreSQL/pgvector before adding concurrent or cloud workers.
- Do not introduce Kafka, Spark Streaming, Kubernetes, exactly-once claims, or distributed workers without an explicit need and user approval.
- Prefer a small, explainable architecture over a collection of resume keywords.
- Preserve raw source lineage from every chunk to document version and page.
- Keep processing idempotent because file-created and future object-created events may be delivered more than once.
- Failed files must not stop unrelated files from processing.

## Resume and Interview Rules

Work backward from truthful, reproducible resume claims. Never add a number or technology to resume material unless the repository contains evidence that supports it.

The intended bullet structure is:

1. Scale and incremental behavior: PDFs, pages, chunks, duplicates skipped, or files updated.
2. Pipeline design: event trigger, extraction/OCR, validation, lineage, and storage.
3. Measured improvement: retrieval quality and latency before versus after a documented design change.

Maintain `docs/resume-evidence.md` as the claim ledger. Update it when an experiment becomes reproducible.

Useful interview stories should include the alternatives considered, measured evidence, selected design, trade-off accepted, and failure mode handled. Do not justify a choice only because it was easier to install.

## Experiment Expectations

Alternatives may be tested without remaining in the final runtime. Save experiment inputs, configuration, raw output, and summary metrics so design choices can be defended later.

Planned comparisons include:

- OCR routing and engines: extraction quality, field accuracy, and seconds per page.
- Chunking: page, fixed overlap, and sentence-aware approaches.
- Embeddings: MiniLM, BGE-small, and E5-small.
- Retrieval: keyword, vector, hybrid, metadata filtering, and optional reranking.
- Storage: local retrieval index versus PostgreSQL/pgvector when incremental retrieval is added.

Use a labeled evaluation set rather than judging a few answers informally. Target metrics include Recall@K, Precision@K, MRR, citation accuracy, p50/p95 latency, OCR usage, ingestion throughput, and full-versus-incremental processing time.

## Next Milestones

1. Assemble a legitimate, redistributable pharmaceutical PDF corpus and record its provenance.
2. Add a repeatable corpus ingestion benchmark and operational metrics export.
3. Build a labeled retrieval evaluation set with queries mapped to correct source pages.
4. Compare chunking, embedding, and retrieval configurations.
5. Select durable retrieval storage based on measured results.
6. Only then consider replacing the local file event with S3 object-created events and SQS.

Do not create S3 resources until the local corpus and retrieval evaluation justify the cloud migration. When cloud work begins, record deployed evidence before claiming S3, SQS, or cloud execution.

## Working Agreement

- Read `README.md`, `docs/architecture.md`, `docs/decision-log.md`, and `docs/resume-evidence.md` before changing architecture.
- Update tests and decision documentation with meaningful behavior changes.
- Run `.venv/bin/pytest` before publishing changes.
- Preserve ignored runtime data under `data/` and never commit source PDFs without checking redistribution rights.
- Keep commits focused and do not silently rewrite unrelated user changes.
