# S3 Integration Handoff

## Local Readiness

The local processing boundary is ready to receive a different event source:

- SHA-256 idempotency handles repeated delivery.
- A failed file is recorded and isolated without stopping the batch.
- Documents, pages, chunks, versions, run metrics, and errors are persisted.
- The selected BM25 index updates in the same transaction as current chunks.
- Corpus ingestion, OCR routing, retrieval, indexing, and metrics export have repeatable tests or saved artifacts.

## Deployed Cloud Increment

Keep the processing code unchanged and replace only the landing mechanism:

```text
PDF uploaded to S3 raw prefix
        -> S3 object-created notification
        -> SQS standard queue
        -> single Python worker polls message
        -> download object to a temporary local file
        -> IngestionPipeline.ingest_paths(..., trigger_type="s3_object_created")
        -> acknowledge message according to the outcome policy
```

SQS is intentionally between S3 and the worker. It buffers bursts, supports retry visibility, and allows a dead-letter queue without treating S3 notifications as exactly-once delivery. Direct S3 notifications require an SQS standard queue. The SHA-256 database constraint remains the idempotency boundary.

The worker deletes a message after successful or duplicate processing. A deterministic document error, such as an invalid PDF that was durably recorded and quarantined, is also acknowledged so it does not retry forever. Transient download, database, or worker failures leave the message undeleted so visibility timeout and the dead-letter policy can retry or isolate it.

## Scope of the First Deployment

Start with one worker and retain SQLite on persistent worker storage. This validates S3/SQS event handling without pretending SQLite supports distributed concurrency. Before adding a second worker or ephemeral compute, move the control tables and search index to a shared durable service and repeat the ingestion/retrieval benchmarks.

Do not use Lambda for the first version: native PyMuPDF/Tesseract packaging, large PDFs, and OCR duration make a long-running worker easier to explain and operate. Do not add Docker or Airflow solely for keyword coverage.

## Verified AWS Configuration

- Private general-purpose S3 bucket in `us-east-1`.
- Bucket versioning, SSE-S3 encryption, owner-enforced ownership, and all public access blocked.
- Notification filter: `incoming/` prefix, `.pdf` suffix, all object-created events.
- Encrypted standard SQS queue with 15-minute visibility timeout and 20-second long polling.
- Encrypted dead-letter queue with a 14-day retention period and redrive after three failed receives.
- Dedicated worker IAM user restricted to the required S3 prefixes and main queue actions.
- One locally invoked worker using the named AWS profile and the existing SQLite control store.

## Live Verification

Four live events were run on August 18, 2026:

1. A previously indexed FDA PDF was skipped by SHA-256 and archived under `processed/`.
2. A new synthetic certificate produced one page and one searchable chunk with a version-qualified S3 source URI.
3. Changed contents uploaded under the same object key created a new immutable document version, linked `supersedes_document_id`, and replaced the active FTS5 posting.
4. An intentionally malformed `.pdf` produced a durable `FileDataError`, moved to `quarantine/`, and was acknowledged.

After the runs, `incoming/` and the main queue were empty, `processed/` contained three hash-addressed objects, `quarantine/` contained one object, and the search-index integrity check remained healthy. Transient retry behavior is covered by the worker test suite rather than a deliberately broken live permission. Exact run IDs, hashes, destinations, counts, and limitations are saved in [`benchmarks/aws-integration-2026-08-18.json`](benchmarks/aws-integration-2026-08-18.json).

The full frozen FDA corpus was also uploaded through the same `incoming/` event path and consumed into a fresh isolated database. Sixteen S3 events completed successfully and produced 430 pages, 1,987 chunks, and one OCR fallback page with no failed or skipped files. Every document stored a distinct version-qualified S3 source URI, the FTS5 index contained all 1,987 current chunks, and the main queue returned to zero visible, in-flight, and delayed messages. Stored processing durations exclude upload, queue transit, and object download, so they are not presented as end-to-end cloud latency.

## AWS References

- [S3 event notification delivery, ordering, and duplicates](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-event-types-and-destinations.html)
- [SQS message deletion and idempotent consumer behavior](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_DeleteMessage.html)
