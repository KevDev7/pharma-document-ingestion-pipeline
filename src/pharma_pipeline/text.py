import re
from typing import List


def clean_text(text: str) -> str:
    normalized = (text or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def classify_document_type(text: str) -> str:
    lower = text.lower()
    rules = (
        (("storage condition",), "Storage Conditions Letter"),
        (("certificate of quality",), "Certificate of Quality"),
        (("packaging component specification", "packaging specification"), "Packaging Specification"),
        (("transmissible spongiform", "bse/tse", "bse", "tse"), "BSE/TSE Declaration"),
        (("material description",), "Material Description"),
        (("supplier qualification",), "Supplier Qualification"),
        (("chain of custody",), "Chain of Custody"),
        (("animal origin",), "Animal Origin Statement"),
    )
    for phrases, label in rules:
        if any(phrase in lower for phrase in phrases):
            return label
    return "Other"


def split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be between 0 and chunk_size - 1")

    normalized = clean_text(text)
    if not normalized:
        return []

    chunks = []
    start = 0
    text_length = len(normalized)

    while start < text_length:
        requested_end = min(start + chunk_size, text_length)
        end = requested_end

        if requested_end < text_length:
            boundary_floor = start + int(chunk_size * 0.6)
            candidates = [
                normalized.rfind("\n", boundary_floor, requested_end),
                normalized.rfind(". ", boundary_floor, requested_end),
                normalized.rfind(" ", boundary_floor, requested_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (1 if normalized[boundary] == "\n" else 0)

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break

        next_start = max(0, end - overlap)
        start = next_start if next_start > start else end

    return chunks
