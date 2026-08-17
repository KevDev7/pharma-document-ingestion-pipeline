import hashlib
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import Settings
from .database import PipelineDatabase
from .extractor import PdfExtractor
from .text import split_text


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class IngestionPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.database = PipelineDatabase(settings.database_path)
        self.database.initialize()
        self.extractor = PdfExtractor(ocr_min_chars=settings.ocr_min_chars)

    def ingest_paths(
        self,
        paths: Iterable[Path],
        trigger_type: str = "manual",
        move_after_processing: bool = False,
    ) -> Dict[str, object]:
        input_paths = [Path(path).expanduser().resolve() for path in paths]
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        self.database.start_run(run_id, trigger_type, len(input_paths))

        metrics: Dict[str, object] = {
            "run_id": run_id,
            "trigger_type": trigger_type,
            "status": "completed",
            "discovered_files": len(input_paths),
            "processed_files": 0,
            "skipped_files": 0,
            "failed_files": 0,
            "page_count": 0,
            "chunk_count": 0,
            "results": [],
        }

        for path in input_paths:
            result = self._ingest_one(path, run_id, move_after_processing)
            metrics["results"].append(result)
            outcome = result["outcome"]
            if outcome == "processed":
                metrics["processed_files"] += 1
                metrics["page_count"] += result["page_count"]
                metrics["chunk_count"] += result["chunk_count"]
            elif outcome == "skipped":
                metrics["skipped_files"] += 1
            else:
                metrics["failed_files"] += 1

        if metrics["failed_files"]:
            metrics["status"] = "completed_with_errors"
        metrics["duration_seconds"] = round(time.perf_counter() - started, 4)
        self.database.finish_run(run_id, metrics)
        return metrics

    def scan_incoming(self) -> Dict[str, object]:
        paths = sorted(self.settings.incoming_dir.glob("*.pdf"))
        return self.ingest_paths(paths, trigger_type="directory_scan", move_after_processing=True)

    def _ingest_one(
        self,
        path: Path,
        run_id: str,
        move_after_processing: bool,
    ) -> Dict[str, object]:
        content_hash: Optional[str] = None
        try:
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"File does not exist: {path}")
            if path.suffix.lower() != ".pdf":
                raise ValueError(f"Only PDF files are supported: {path.name}")

            content_hash = sha256_file(path)
            existing = self.database.get_document_by_hash(content_hash)
            if existing:
                archived_path = None
                if move_after_processing:
                    archived_path = self._move_file(path, self.settings.archive_dir, content_hash)
                return {
                    "source_path": str(path),
                    "outcome": "skipped",
                    "reason": "duplicate_content_hash",
                    "existing_document_id": existing["document_id"],
                    "archived_path": str(archived_path) if archived_path else None,
                }

            current = self.database.get_current_document(path.name)
            document_id = str(uuid.uuid4())
            pages = self.extractor.extract(path)
            page_rows: List[Dict[str, object]] = []
            chunk_rows: List[Dict[str, object]] = []

            for page in pages:
                page_rows.append(
                    {
                        "document_id": document_id,
                        "page_number": page.page_number,
                        "document_type": page.document_type,
                        "extraction_method": page.extraction_method,
                        "character_count": page.character_count,
                        "word_count": page.word_count,
                        "text": page.text,
                    }
                )
                for chunk_index, chunk_text in enumerate(
                    split_text(
                        page.text,
                        chunk_size=self.settings.chunk_size,
                        overlap=self.settings.chunk_overlap,
                    )
                ):
                    chunk_rows.append(
                        {
                            "chunk_id": f"{content_hash[:16]}-p{page.page_number:04d}-c{chunk_index:04d}",
                            "document_id": document_id,
                            "page_number": page.page_number,
                            "chunk_index": chunk_index,
                            "document_type": page.document_type,
                            "character_count": len(chunk_text),
                            "text": chunk_text,
                        }
                    )

            document = {
                "document_id": document_id,
                "sha256": content_hash,
                "logical_name": path.name,
                "source_path": str(path),
                "size_bytes": path.stat().st_size,
                "page_count": len(page_rows),
                "chunk_count": len(chunk_rows),
                "ocr_page_count": sum(
                    1 for page in page_rows if page["extraction_method"] == "ocr"
                ),
                "parser_version": self.settings.parser_version,
                "chunker_version": self.settings.chunker_version,
                "ingestion_run_id": run_id,
                "supersedes_document_id": current["document_id"] if current else None,
            }
            self.database.save_document(document, page_rows, chunk_rows)

            archived_path = None
            if move_after_processing:
                archived_path = self._move_file(path, self.settings.archive_dir, content_hash)
                self.database.update_source_path(document_id, str(archived_path))

            return {
                "source_path": str(path),
                "outcome": "processed",
                "document_id": document_id,
                "sha256": content_hash,
                "page_count": len(page_rows),
                "chunk_count": len(chunk_rows),
                "ocr_page_count": document["ocr_page_count"],
                "supersedes_document_id": document["supersedes_document_id"],
                "archived_path": str(archived_path) if archived_path else None,
            }
        except Exception as error:
            self.database.record_error(run_id, str(path), content_hash, error)
            quarantined_path = None
            if move_after_processing and path.exists():
                quarantined_path = self._move_file(path, self.settings.quarantine_dir, content_hash)
            return {
                "source_path": str(path),
                "outcome": "failed",
                "error_type": type(error).__name__,
                "error_message": str(error),
                "quarantined_path": str(quarantined_path) if quarantined_path else None,
            }

    @staticmethod
    def _move_file(path: Path, destination_dir: Path, content_hash: Optional[str]) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / path.name
        if target.exists():
            suffix = content_hash[:8] if content_hash else uuid.uuid4().hex[:8]
            target = destination_dir / f"{path.stem}-{suffix}{path.suffix.lower()}"
        shutil.move(str(path), str(target))
        return target.resolve()
