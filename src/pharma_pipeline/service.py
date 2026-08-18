import time
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_ROOT, Settings
from .database import PipelineDatabase


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    document_type: str | None = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must contain searchable text")
        return cleaned


class SearchResult(BaseModel):
    chunk_id: str
    document_id: str
    document_sha256: str
    filename: str
    page_number: int
    chunk_index: int
    document_type: str
    text: str
    score: float


class SearchResponse(BaseModel):
    query: str
    document_type: str | None
    result_count: int
    latency_ms: float
    results: list[SearchResult]


def create_app(root: Path = DEFAULT_ROOT) -> FastAPI:
    settings = Settings.from_root(root)
    settings.ensure_directories()
    database = PipelineDatabase(settings.database_path)
    database.initialize()

    app = FastAPI(
        title="Pharmaceutical Document Retrieval API",
        version="0.1.0",
        description="Searches current pharmaceutical document chunks with SQLite FTS5/BM25.",
    )
    app.state.database = database

    @app.get("/health")
    def health() -> dict:
        pipeline_summary = database.summary()
        pipeline_summary.pop("database_path", None)
        return {
            "status": "healthy",
            "pipeline": pipeline_summary,
            "search_index": database.search_index_status(),
        }

    @app.get("/document-types", response_model=list[str])
    def document_types() -> list[str]:
        return database.list_document_types()

    @app.post("/search", response_model=SearchResponse)
    def search(request: SearchRequest) -> SearchResponse:
        started = time.perf_counter()
        results = database.search_chunks(
            request.query,
            top_k=request.top_k,
            document_type=request.document_type,
        )
        return SearchResponse(
            query=request.query,
            document_type=request.document_type,
            result_count=len(results),
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            results=[SearchResult(**result) for result in results],
        )

    return app
