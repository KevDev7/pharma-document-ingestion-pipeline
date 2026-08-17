# Architecture

## Current Local Flow

```text
data/incoming/*.pdf
        |
        v
filesystem event + stable-file check
        |
        v
SHA-256 duplicate/version check
        |
        v
PyMuPDF extraction --> Tesseract fallback for low-text pages
        |
        v
page classification + overlapping chunks
        |
        v
SQLite documents/pages/chunks/run metrics
        |
        +--> data/archive/      successful or duplicate PDFs
        +--> data/quarantine/   failed PDFs
```

## Data Contract

Each stored chunk has:

- A deterministic chunk ID derived from the document content hash, page, and chunk position.
- A foreign key to an immutable document version.
- A one-based source page number.
- The page-level document type.
- The exact text and character count used downstream.

Documents with the same filename but different content hashes are separate versions. The newest successful version is marked current, and its row points to the version it superseded.

## Event Semantics

Filesystem notifications, like cloud object-created events, can be repeated. The pipeline therefore provides at-least-once event handling with idempotent processing:

1. Wait until the uploaded file size stops changing.
2. Calculate the complete file hash.
3. Skip processing if that hash already exists.
4. Commit the document, pages, and chunks in one database transaction.

This prevents duplicate records without claiming distributed exactly-once delivery.

## Intended Cloud Boundary

The eventual cloud version can replace the watched folder with an S3 object-created event and SQS message. The `IngestionPipeline.ingest_paths()` processing boundary remains the same. That migration is deliberately outside the first milestone.
