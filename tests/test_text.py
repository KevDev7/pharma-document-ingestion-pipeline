import pytest

from pharma_pipeline.text import classify_document_type, clean_text, split_text


def test_clean_text_preserves_line_boundaries() -> None:
    assert clean_text("  Lot   Number: 123 \r\n\r\n Product: Kit ") == "Lot Number: 123\nProduct: Kit"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Certificate of Quality\nLot Number: 123", "Certificate of Quality"),
        ("Packaging Component Specification", "Packaging Specification"),
        ("Supplier Qualification Record", "Supplier Qualification"),
        ("Unrecognized content", "Other"),
    ],
)
def test_classify_document_type(text: str, expected: str) -> None:
    assert classify_document_type(text) == expected


def test_split_text_limits_chunk_size_and_overlaps() -> None:
    text = " ".join(f"token-{index}" for index in range(250))
    chunks = split_text(text, chunk_size=180, overlap=30)

    assert len(chunks) > 2
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert set(chunks[0].split()[-2:]) & set(chunks[1].split()[:5])


def test_split_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        split_text("text", chunk_size=100, overlap=100)
