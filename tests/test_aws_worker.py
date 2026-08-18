import json
from pathlib import Path
from typing import Dict, Optional

import pytest

from pharma_pipeline.aws_worker import (
    RetryableProcessingError,
    S3SqsWorker,
    UnexpectedEventError,
)
from pharma_pipeline.cli import build_parser
from pharma_pipeline.config import Settings
from pharma_pipeline.pipeline import IngestionPipeline

from test_pipeline import make_digital_pdf


BUCKET = "pharma-document-pipeline-645968351825-us-east-1-an"
QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/645968351825/pharma-events"


class FakeS3Client:
    def __init__(self, source_bytes: bytes) -> None:
        self.source_bytes = source_bytes
        self.archived: Dict[str, bytes] = {}
        self.copy_calls = []
        self.delete_calls = []

    def download_file(
        self,
        bucket: str,
        key: str,
        filename: str,
        ExtraArgs: Optional[Dict] = None,
    ) -> None:
        assert bucket == BUCKET
        assert key.startswith("incoming/")
        if ExtraArgs:
            assert ExtraArgs == {"VersionId": "version-1"}
        Path(filename).write_bytes(self.source_bytes)

    def list_objects_v2(self, Bucket: str, Prefix: str, MaxKeys: int) -> Dict:
        assert Bucket == BUCKET
        assert MaxKeys == 1
        return {
            "Contents": [{"Key": key} for key in self.archived if key.startswith(Prefix)][:1]
        }

    def copy_object(self, **kwargs: object) -> None:
        self.copy_calls.append(kwargs)
        self.archived[str(kwargs["Key"])] = self.source_bytes

    def delete_object(self, **kwargs: object) -> None:
        self.delete_calls.append(kwargs)


class FakeSqsClient:
    def __init__(self, messages=None) -> None:  # type: ignore[no-untyped-def]
        self.messages = messages or []
        self.delete_calls = []

    def receive_message(self, **kwargs: object) -> Dict:
        return {"Messages": self.messages}

    def delete_message(self, **kwargs: object) -> None:
        self.delete_calls.append(kwargs)


def s3_message(
    key: str = "incoming/vendor/quality.pdf",
    bucket: str = BUCKET,
    body_override: Optional[str] = None,
) -> Dict:
    body = body_override or json.dumps(
        {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "eventName": "ObjectCreated:Put",
                    "eventTime": "2026-08-18T17:00:00.000Z",
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {
                            "key": key,
                            "versionId": "version-1",
                            "sequencer": "0068A4",
                        },
                    },
                }
            ]
        }
    )
    return {"MessageId": "message-1", "ReceiptHandle": "receipt-1", "Body": body}


def pdf_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "source.pdf"
    make_digital_pdf(path, ["Certificate of Quality\nLot Number: 12345678\n" * 8])
    return path.read_bytes()


def make_worker(tmp_path: Path, source_bytes: bytes):  # type: ignore[no-untyped-def]
    s3 = FakeS3Client(source_bytes)
    sqs = FakeSqsClient()
    pipeline = IngestionPipeline(Settings.from_root(tmp_path / "pipeline"))
    worker = S3SqsWorker(pipeline, s3, sqs, QUEUE_URL, BUCKET)
    return worker, pipeline, s3, sqs


def test_watch_s3_cli_requires_explicit_cloud_resources() -> None:
    args = build_parser().parse_args(
        [
            "watch-s3",
            "--bucket",
            BUCKET,
            "--queue-name",
            "pharma-document-pipeline-events",
            "--profile",
            "pharma-document-pipeline-worker",
            "--once",
        ]
    )

    assert args.bucket == BUCKET
    assert args.queue_name == "pharma-document-pipeline-events"
    assert args.profile == "pharma-document-pipeline-worker"
    assert args.once is True


def test_successful_event_is_ingested_archived_and_acknowledged(tmp_path: Path) -> None:
    worker, pipeline, s3, sqs = make_worker(tmp_path, pdf_bytes(tmp_path))

    outcome = worker.process_message(s3_message())

    assert outcome["outcome"] == "acknowledged"
    assert outcome["events"][0]["outcome"] == "processed"
    assert len(s3.copy_calls) == 1
    assert list(s3.archived)[0].startswith("processed/vendor/quality-")
    assert s3.delete_calls == [{"Bucket": BUCKET, "Key": "incoming/vendor/quality.pdf"}]
    assert len(sqs.delete_calls) == 1
    document = pipeline.database.fetch_all(
        "SELECT logical_name, source_path FROM documents"
    )[0]
    assert document["logical_name"] == "vendor/quality.pdf"
    assert document["source_path"].endswith("?versionId=version-1")


def test_duplicate_event_does_not_duplicate_document_or_archive(tmp_path: Path) -> None:
    worker, pipeline, s3, sqs = make_worker(tmp_path, pdf_bytes(tmp_path))

    worker.process_message(s3_message())
    second = worker.process_message(s3_message())

    assert second["events"][0]["outcome"] == "skipped"
    assert pipeline.database.summary()["document_versions"] == 1
    assert len(s3.copy_calls) == 1
    assert len(s3.delete_calls) == 2
    assert len(sqs.delete_calls) == 2


def test_invalid_pdf_is_quarantined_and_acknowledged(tmp_path: Path) -> None:
    worker, pipeline, s3, sqs = make_worker(tmp_path, b"not a PDF")

    outcome = worker.process_message(s3_message(key="incoming/broken.pdf"))

    assert outcome["events"][0]["outcome"] == "quarantined"
    assert list(s3.archived)[0].startswith("quarantine/broken-")
    assert pipeline.database.summary()["error_count"] == 1
    assert len(sqs.delete_calls) == 1


def test_retryable_pipeline_failure_leaves_message_and_object() -> None:
    class RetryablePipeline:
        def ingest_sources(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return {
                "run_id": "run-1",
                "results": [{"outcome": "failed", "error_type": "OperationalError"}],
            }

    s3 = FakeS3Client(b"some bytes")
    sqs = FakeSqsClient()
    worker = S3SqsWorker(RetryablePipeline(), s3, sqs, QUEUE_URL, BUCKET)  # type: ignore[arg-type]

    with pytest.raises(RetryableProcessingError):
        worker.process_message(s3_message())

    assert s3.copy_calls == []
    assert s3.delete_calls == []
    assert sqs.delete_calls == []


def test_test_event_is_acknowledged_without_s3_work(tmp_path: Path) -> None:
    worker, _, s3, sqs = make_worker(tmp_path, b"")
    message = s3_message(body_override=json.dumps({"Event": "s3:TestEvent"}))

    outcome = worker.process_message(message)

    assert outcome["outcome"] == "ignored_test_event"
    assert s3.copy_calls == []
    assert len(sqs.delete_calls) == 1


@pytest.mark.parametrize(
    ("key", "bucket"),
    [
        ("outside/quality.pdf", BUCKET),
        ("incoming/quality.txt", BUCKET),
        ("incoming/../quality.pdf", BUCKET),
        ("incoming//quality.pdf", BUCKET),
        ("incoming/quality.pdf", "wrong-bucket"),
    ],
)
def test_unexpected_events_are_not_acknowledged(
    tmp_path: Path, key: str, bucket: str
) -> None:
    worker, _, _, sqs = make_worker(tmp_path, pdf_bytes(tmp_path))

    with pytest.raises(UnexpectedEventError):
        worker.process_message(s3_message(key=key, bucket=bucket))

    assert sqs.delete_calls == []
