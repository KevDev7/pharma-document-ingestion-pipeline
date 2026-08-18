# S3 Integration Handoff

## Local Readiness

The local processing boundary is ready to receive a different event source:

- SHA-256 idempotency handles repeated delivery.
- A failed file is recorded and isolated without stopping the batch.
- Documents, pages, chunks, versions, run metrics, and errors are persisted.
- The selected BM25 index updates in the same transaction as current chunks.
- Corpus ingestion, OCR routing, retrieval, indexing, and metrics export have repeatable tests or saved artifacts.

## First Cloud Increment

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

The worker should delete a message after successful or duplicate processing. A deterministic document error, such as an invalid PDF that was durably recorded and quarantined, can also be acknowledged so it does not retry forever. Transient download, database, or worker failures must leave the message undeleted so visibility timeout and the dead-letter policy can retry or isolate it.

## Scope of the First Deployment

Start with one worker and retain SQLite on persistent worker storage. This validates S3/SQS event handling without pretending SQLite supports distributed concurrency. Before adding a second worker or ephemeral compute, move the control tables and search index to a shared durable service and repeat the ingestion/retrieval benchmarks.

Do not use Lambda for the first version: native PyMuPDF/Tesseract packaging, large PDFs, and OCR duration make a long-running worker easier to explain and operate. Do not add Docker or Airflow solely for keyword coverage.

## AWS Inputs Needed

The integration can proceed after the user supplies or confirms:

1. An authenticated AWS CLI profile or SSO session with permission to create and inspect S3/SQS resources.
2. AWS region.
3. Globally unique raw-document bucket name or approved naming prefix.
4. Queue and dead-letter queue names, or approval to use project defaults.
5. Whether the first worker runs on the local machine against AWS or on an existing persistent EC2 host.

The deployed evidence must capture resource identifiers, one successful object event, one duplicate replay, one quarantined failure, queue deletion behavior, and a post-run metrics snapshot before any S3/SQS resume claim is marked verified.

## AWS References

- [S3 event notification delivery, ordering, and duplicates](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-event-types-and-destinations.html)
- [SQS message deletion and idempotent consumer behavior](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_DeleteMessage.html)
