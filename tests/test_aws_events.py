import json

import pytest

from pharma_pipeline.aws_events import parse_s3_event_message


def make_record(key: str = "incoming/vendor%20quality%2Bfinal.pdf") -> dict:
    return {
        "eventSource": "aws:s3",
        "eventName": "ObjectCreated:Put",
        "eventTime": "2026-08-18T17:00:00.000Z",
        "s3": {
            "bucket": {"name": "pharma-bucket"},
            "object": {
                "key": key,
                "versionId": "version-1",
                "sequencer": "0068A4",
            },
        },
    }


def test_parse_s3_event_decodes_key_and_preserves_version() -> None:
    events = parse_s3_event_message(json.dumps({"Records": [make_record()]}))

    assert len(events) == 1
    assert events[0].key == "incoming/vendor quality+final.pdf"
    assert events[0].source_uri == (
        "s3://pharma-bucket/incoming/vendor quality+final.pdf?versionId=version-1"
    )


def test_parse_s3_event_supports_multiple_records() -> None:
    events = parse_s3_event_message(
        json.dumps({"Records": [make_record("incoming/one.pdf"), make_record("incoming/two.pdf")]})
    )

    assert [event.key for event in events] == ["incoming/one.pdf", "incoming/two.pdf"]


def test_parse_s3_test_event_is_ignored() -> None:
    assert parse_s3_event_message(json.dumps({"Event": "s3:TestEvent"})) == []


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        "{}",
        json.dumps({"Records": [{"eventSource": "aws:s3"}]}),
    ],
)
def test_parse_s3_event_rejects_malformed_messages(body: str) -> None:
    with pytest.raises(ValueError):
        parse_s3_event_message(body)
