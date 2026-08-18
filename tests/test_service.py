from pathlib import Path

import fitz
import httpx
from fastapi.testclient import TestClient

from pharma_pipeline.config import Settings
from pharma_pipeline.pipeline import IngestionPipeline
from pharma_pipeline.service import create_app
from pharma_pipeline.ui import format_search_response, search_api


def make_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 540, 720), text, fontsize=11)
    document.save(path)
    document.close()


def prepare_root(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(Settings.from_root(tmp_path))
    pdf_path = tmp_path / "quality.pdf"
    make_pdf(
        pdf_path,
        "Certificate of Quality\nLot Number: SERVICELOT\nRelease testing passed.\n" * 8,
    )
    pipeline.ingest_paths([pdf_path])


def test_health_and_document_types_report_current_state(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    client = TestClient(create_app(tmp_path))

    health = client.get("/health")
    document_types = client.get("/document-types")

    assert health.status_code == 200
    assert health.json()["pipeline"]["current_documents"] == 1
    assert "database_path" not in health.json()["pipeline"]
    assert health.json()["search_index"]["status"] == "healthy"
    assert document_types.json() == ["Certificate of Quality"]


def test_search_returns_grounded_source_metadata(tmp_path: Path) -> None:
    prepare_root(tmp_path)
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/search",
        json={
            "query": "What is the lot number?",
            "top_k": 3,
            "document_type": "Certificate of Quality",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_count"] >= 1
    assert payload["results"][0]["filename"] == "quality.pdf"
    assert payload["results"][0]["page_number"] == 1
    assert "SERVICELOT" in payload["results"][0]["text"]
    assert payload["results"][0]["document_sha256"]


def test_search_validates_limits(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    assert client.post("/search", json={"query": "", "top_k": 5}).status_code == 422
    assert client.post("/search", json={"query": "   ", "top_k": 5}).status_code == 422
    assert client.post("/search", json={"query": "valid", "top_k": 21}).status_code == 422


def test_ui_formats_results_and_preserves_source_details() -> None:
    output = format_search_response(
        {
            "latency_ms": 1.234,
            "results": [
                {
                    "filename": "quality.pdf",
                    "page_number": 2,
                    "document_type": "Certificate of Quality",
                    "score": 3.14159,
                    "text": "Lot Number: ABC123",
                }
            ],
        }
    )

    assert "quality.pdf · page 2" in output
    assert "Lot Number: ABC123" in output
    assert "not confidence probabilities" in output


def test_ui_calls_search_api(monkeypatch) -> None:
    request = {}

    def fake_post(url, json, timeout):
        request.update({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"latency_ms": 0.5, "results": []},
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    history, cleared = search_api(
        "Where is the release status?",
        [],
        "http://api.test/",
        4,
        "Certificate of Quality",
    )

    assert request["url"] == "http://api.test/search"
    assert request["json"]["top_k"] == 4
    assert request["json"]["document_type"] == "Certificate of Quality"
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Where is the release status?"
    assert history[1]["role"] == "assistant"
    assert "could not find" in history[1]["content"]
    assert cleared == ""
