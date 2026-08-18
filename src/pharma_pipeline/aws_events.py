import json
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import unquote_plus


@dataclass(frozen=True)
class S3ObjectCreatedEvent:
    bucket: str
    key: str
    version_id: Optional[str]
    event_name: str
    event_time: str
    sequencer: Optional[str]

    @property
    def source_uri(self) -> str:
        suffix = f"?versionId={self.version_id}" if self.version_id else ""
        return f"s3://{self.bucket}/{self.key}{suffix}"


def parse_s3_event_message(body: str) -> List[S3ObjectCreatedEvent]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("SQS message body is not valid JSON") from error

    if not isinstance(payload, dict):
        raise ValueError("SQS message body must contain a JSON object")

    if payload.get("Event") == "s3:TestEvent":
        return []

    records = payload.get("Records")
    if not isinstance(records, list) or not records:
        raise ValueError("SQS message does not contain S3 event records")

    events = []
    for record in records:
        try:
            event_source = record["eventSource"]
            event_name = record["eventName"]
            event_time = record["eventTime"]
            bucket = record["s3"]["bucket"]["name"]
            object_record = record["s3"]["object"]
            key = unquote_plus(object_record["key"])
        except (KeyError, TypeError) as error:
            raise ValueError("S3 event record is missing required fields") from error

        if event_source != "aws:s3" or not event_name.startswith("ObjectCreated:"):
            raise ValueError(f"Unsupported event: {event_source} {event_name}")
        events.append(
            S3ObjectCreatedEvent(
                bucket=bucket,
                key=key,
                version_id=object_record.get("versionId"),
                event_name=event_name,
                event_time=event_time,
                sequencer=object_record.get("sequencer"),
            )
        )
    return events
