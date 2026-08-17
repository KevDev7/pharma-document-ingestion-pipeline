import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import fitz


REQUIRED_DOCUMENT_FIELDS = {
    "id",
    "title",
    "document_type",
    "topic",
    "publisher",
    "landing_page_url",
    "pdf_url",
    "filename",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def is_pdf(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def load_manifest(path: Path) -> Dict[str, object]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported corpus manifest schema_version")
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Corpus manifest must contain a non-empty documents list")

    ids = set()
    filenames = set()
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise ValueError(f"Manifest document {index} must be an object")
        missing = REQUIRED_DOCUMENT_FIELDS - set(document)
        if missing:
            raise ValueError(f"Manifest document {index} is missing: {sorted(missing)}")

        document_id = document["id"]
        filename = document["filename"]
        if document_id in ids:
            raise ValueError(f"Duplicate corpus document id: {document_id}")
        if filename in filenames:
            raise ValueError(f"Duplicate corpus filename: {filename}")
        if Path(filename).name != filename or not filename.lower().endswith(".pdf"):
            raise ValueError(f"Unsafe or invalid corpus filename: {filename}")
        ids.add(document_id)
        filenames.add(filename)
    return manifest


def _download_one(
    document: Dict[str, str],
    destination_dir: Path,
    max_bytes: int,
) -> Dict[str, object]:
    target = destination_dir / document["filename"]
    if target.exists() and is_pdf(target):
        local_hash = sha256_file(target)
        expected_hash = document.get("expected_sha256")
        if expected_hash and local_hash != expected_hash:
            raise ValueError(
                f"Local file hash does not match frozen manifest for {document['id']}"
            )
        return {
            "id": document["id"],
            "filename": document["filename"],
            "outcome": "skipped",
            "reason": "valid_local_file_exists",
            "bytes": target.stat().st_size,
            "sha256": local_hash,
            "downloaded_at": utc_now(),
            "pdf_url": document["pdf_url"],
        }

    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        document["pdf_url"],
        headers={"User-Agent": "pharma-document-ingestion-pipeline/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(
                    f"Download exceeds configured limit: {content_length} bytes for {document['id']}"
                )

            copied = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                copied += len(block)
                if copied > max_bytes:
                    raise ValueError(f"Download exceeded configured limit for {document['id']}")
                output.write(block)

            receipt = {
                "id": document["id"],
                "filename": document["filename"],
                "outcome": "downloaded",
                "bytes": copied,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "downloaded_at": utc_now(),
                "pdf_url": document["pdf_url"],
            }

        if not is_pdf(temporary):
            raise ValueError(f"Downloaded content is not a PDF for {document['id']}")
        downloaded_hash = sha256_file(temporary)
        expected_hash = document.get("expected_sha256")
        if expected_hash and downloaded_hash != expected_hash:
            raise ValueError(
                f"Downloaded file hash does not match frozen manifest for {document['id']}"
            )
        temporary.replace(target)
        receipt["sha256"] = downloaded_hash
        return receipt
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def append_receipts(receipt_path: Path, receipts: Iterable[Dict[str, object]]) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with receipt_path.open("a", encoding="utf-8") as handle:
        for receipt in receipts:
            handle.write(json.dumps(receipt, sort_keys=True) + "\n")


def download_manifest(
    manifest_path: Path,
    destination_dir: Path,
    receipt_path: Path,
    limit: Optional[int] = None,
    max_megabytes_per_file: int = 75,
) -> Dict[str, object]:
    manifest = load_manifest(manifest_path)
    documents: List[Dict[str, str]] = manifest["documents"]  # type: ignore[assignment]
    selected = documents[:limit] if limit else documents
    destination_dir.mkdir(parents=True, exist_ok=True)

    receipts = []
    failures = []
    for document in selected:
        try:
            receipt = _download_one(
                document,
                destination_dir,
                max_bytes=max_megabytes_per_file * 1024 * 1024,
            )
            receipts.append(receipt)
        except Exception as error:
            failure = {
                "id": document["id"],
                "filename": document["filename"],
                "outcome": "failed",
                "error_type": type(error).__name__,
                "error_message": str(error),
                "downloaded_at": utc_now(),
                "pdf_url": document["pdf_url"],
            }
            receipts.append(failure)
            failures.append(failure)

    append_receipts(receipt_path, receipts)
    return {
        "manifest": str(manifest_path),
        "destination": str(destination_dir),
        "selected_documents": len(selected),
        "downloaded": sum(item["outcome"] == "downloaded" for item in receipts),
        "skipped": sum(item["outcome"] == "skipped" for item in receipts),
        "failed": len(failures),
        "total_bytes": sum(int(item.get("bytes", 0)) for item in receipts),
        "receipts": receipts,
    }


def summarize_corpus(directory: Path) -> Dict[str, object]:
    pdf_paths = sorted(directory.glob("*.pdf"))
    total_pages = 0
    total_bytes = 0
    failures = []
    documents = []
    for path in pdf_paths:
        try:
            with fitz.open(path) as pdf:
                pages = pdf.page_count
            size = path.stat().st_size
            total_pages += pages
            total_bytes += size
            documents.append(
                {
                    "filename": path.name,
                    "pages": pages,
                    "bytes": size,
                    "sha256": sha256_file(path),
                }
            )
        except Exception as error:
            failures.append(
                {
                    "filename": path.name,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
            )
    return {
        "directory": str(directory),
        "pdf_files": len(pdf_paths),
        "valid_pdf_files": len(documents),
        "failed_pdf_files": len(failures),
        "total_pages": total_pages,
        "total_bytes": total_bytes,
        "documents": documents,
        "failures": failures,
    }
