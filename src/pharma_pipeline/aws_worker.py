import json
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from .aws_events import S3ObjectCreatedEvent, parse_s3_event_message
from .pipeline import IngestionPipeline, IngestionSource, sha256_file


DETERMINISTIC_DOCUMENT_ERRORS = {
    "EmptyFileError",
    "FileDataError",
    "OcrConflictError",
    "PdfValidationError",
}


class UnexpectedEventError(ValueError):
    pass


class RetryableProcessingError(RuntimeError):
    pass


class S3SqsWorker:
    def __init__(
        self,
        pipeline: IngestionPipeline,
        s3_client: object,
        sqs_client: object,
        queue_url: str,
        bucket: str,
        incoming_prefix: str = "incoming/",
        processed_prefix: str = "processed/",
        quarantine_prefix: str = "quarantine/",
    ) -> None:
        self.pipeline = pipeline
        self.s3 = s3_client
        self.sqs = sqs_client
        self.queue_url = queue_url
        self.bucket = bucket
        self.incoming_prefix = incoming_prefix
        self.processed_prefix = processed_prefix
        self.quarantine_prefix = quarantine_prefix

    def run_once(self, max_messages: int = 1, wait_time_seconds: int = 20) -> List[Dict]:
        response = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        outcomes = []
        for message in response.get("Messages", []):
            outcomes.append(self.process_message(message))
        return outcomes

    def run_forever(self) -> None:
        while True:
            try:
                for outcome in self.run_once(max_messages=1, wait_time_seconds=20):
                    print(json.dumps(outcome))
            except Exception as error:
                print(
                    json.dumps(
                        {
                            "outcome": "message_not_acknowledged",
                            "error_type": type(error).__name__,
                            "error": str(error),
                        }
                    )
                )

    def process_message(self, message: Dict) -> Dict:
        message_id = message.get("MessageId")
        receipt_handle = message.get("ReceiptHandle")
        if not receipt_handle:
            raise UnexpectedEventError("SQS message is missing its receipt handle")

        events = parse_s3_event_message(message.get("Body", ""))
        event_outcomes = [self._process_event(event) for event in events]
        self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
        return {
            "message_id": message_id,
            "outcome": "ignored_test_event" if not events else "acknowledged",
            "events": event_outcomes,
        }

    def _process_event(self, event: S3ObjectCreatedEvent) -> Dict:
        relative_key = self._validate_event(event)
        with tempfile.TemporaryDirectory(prefix="pharma-s3-") as temporary_dir:
            local_path = Path(temporary_dir) / PurePosixPath(relative_key).name
            download_args = {}
            if event.version_id:
                download_args["ExtraArgs"] = {"VersionId": event.version_id}
            self.s3.download_file(event.bucket, event.key, str(local_path), **download_args)
            content_hash = sha256_file(local_path)
            metrics = self.pipeline.ingest_sources(
                [
                    IngestionSource(
                        local_path=local_path,
                        source_uri=event.source_uri,
                        logical_name=relative_key,
                    )
                ],
                trigger_type="s3_object_created",
            )
            result = metrics["results"][0]

            if result["outcome"] in {"processed", "skipped"}:
                destination_key = self._destination_key(
                    self.processed_prefix, relative_key, content_hash
                )
                self._copy_if_missing(event, destination_key)
                self.s3.delete_object(Bucket=event.bucket, Key=event.key)
                return {
                    "source": event.source_uri,
                    "outcome": result["outcome"],
                    "destination": f"s3://{event.bucket}/{destination_key}",
                    "run_id": metrics["run_id"],
                }

            if result["error_type"] not in DETERMINISTIC_DOCUMENT_ERRORS:
                raise RetryableProcessingError(
                    f"Retryable ingestion failure: {result['error_type']}"
                )

            destination_key = self._destination_key(
                self.quarantine_prefix, relative_key, content_hash
            )
            self._copy_if_missing(event, destination_key)
            self.s3.delete_object(Bucket=event.bucket, Key=event.key)
            return {
                "source": event.source_uri,
                "outcome": "quarantined",
                "error_type": result["error_type"],
                "destination": f"s3://{event.bucket}/{destination_key}",
                "run_id": metrics["run_id"],
            }

    def _validate_event(self, event: S3ObjectCreatedEvent) -> str:
        if event.bucket != self.bucket:
            raise UnexpectedEventError(f"Unexpected S3 bucket: {event.bucket}")
        if not event.key.startswith(self.incoming_prefix):
            raise UnexpectedEventError(f"Unexpected S3 key prefix: {event.key}")
        if not event.key.lower().endswith(".pdf"):
            raise UnexpectedEventError(f"Unexpected S3 object type: {event.key}")
        relative_key = event.key[len(self.incoming_prefix) :]
        path = PurePosixPath(relative_key)
        if (
            not relative_key
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise UnexpectedEventError(f"Unsafe S3 object key: {event.key}")
        return relative_key

    def _copy_if_missing(self, event: S3ObjectCreatedEvent, destination_key: str) -> None:
        existing = self.s3.list_objects_v2(
            Bucket=event.bucket,
            Prefix=destination_key,
            MaxKeys=1,
        ).get("Contents", [])
        if any(item.get("Key") == destination_key for item in existing):
            return
        copy_source = {"Bucket": event.bucket, "Key": event.key}
        if event.version_id:
            copy_source["VersionId"] = event.version_id
        self.s3.copy_object(
            Bucket=event.bucket,
            Key=destination_key,
            CopySource=copy_source,
            ServerSideEncryption="AES256",
        )

    @staticmethod
    def _destination_key(prefix: str, relative_key: str, content_hash: str) -> str:
        path = PurePosixPath(relative_key)
        parent = "" if str(path.parent) == "." else f"{path.parent}/"
        return f"{prefix}{parent}{path.stem}-{content_hash[:12]}{path.suffix.lower()}"
