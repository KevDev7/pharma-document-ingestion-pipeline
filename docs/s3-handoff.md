# S3 Integration Handoff

## Shared Processing Logic

Local and S3 inputs use the same processing code:

- SHA-256 file fingerprints prevent duplicate records when events repeat.
- A failed file is recorded and isolated without stopping the batch.
- Documents, pages, chunks, versions, run metrics, and errors are persisted.
- The selected BM25 index updates in the same transaction as current chunks.
- Tests and saved results cover corpus ingestion, OCR routing, retrieval, indexing, and metrics export.

## Deployed Cloud Increment

S3/SQS changes how files arrive. It does not replace the ingestion logic:

```text
PDF uploaded to S3 raw prefix
        -> S3 object-created notification
        -> SQS standard queue
        -> single Python worker polls message
        -> download object to a temporary local file
        -> IngestionPipeline.ingest_paths(..., trigger_type="s3_object_created")
        -> acknowledge message according to the outcome policy
```

SQS sits between S3 and the worker. It buffers uploads, hides messages during processing, and sends repeatedly failing messages to a dead-letter queue. S3 can deliver an event more than once, so the SHA-256 database constraint remains the duplicate safeguard.

The worker deletes a message after successful or duplicate processing. It also deletes a message after storing and quarantining a permanent document error, such as an invalid PDF. Temporary download, database, and worker failures leave the message in the queue for retry.

## Scope of the First Deployment

The deployed design uses one worker and SQLite on that worker's storage. Before adding another worker, move the control tables and search index to shared storage. Then repeat the ingestion and retrieval benchmarks.

The first version uses a long-running worker instead of Lambda. PyMuPDF and Tesseract require native packages, and OCR time can vary by page. A worker avoids Lambda package and execution-time limits for this test scope.

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

After these runs, `incoming/` and the main queue were empty. `processed/` contained three hash-addressed objects, and `quarantine/` contained one object. The search index passed its integrity check. Automated tests cover temporary failures and retries. Run IDs, hashes, destinations, counts, and limits are in [`benchmarks/aws-integration-2026-08-18.json`](benchmarks/aws-integration-2026-08-18.json).

The full FDA corpus also used the same `incoming/` path and a fresh database. Sixteen S3 events produced 430 pages, 1,987 chunks, and one OCR page. No files failed or skipped. Every document stored a distinct S3 version ID. The FTS5 index contained all 1,987 current chunks, and the main queue returned to zero messages. Stored processing time excludes upload, queue transit, and download time.

## AWS References

- [S3 event notification delivery, ordering, and duplicates](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-event-types-and-destinations.html)
- [SQS message deletion and idempotent consumer behavior](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_DeleteMessage.html)
