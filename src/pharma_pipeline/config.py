from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    incoming_dir: Path
    archive_dir: Path
    quarantine_dir: Path
    corpus_raw_dir: Path
    corpus_scanned_dir: Path
    state_dir: Path
    database_path: Path
    chunk_size: int = 700
    chunk_overlap: int = 120
    ocr_min_chars: int = 50
    parser_version: str = "pymupdf-tesseract-v1"
    chunker_version: str = "character-overlap-v1"

    @classmethod
    def from_root(cls, root: Path) -> "Settings":
        resolved = root.expanduser().resolve()
        state_dir = resolved / "data" / "state"
        return cls(
            root=resolved,
            incoming_dir=resolved / "data" / "incoming",
            archive_dir=resolved / "data" / "archive",
            quarantine_dir=resolved / "data" / "quarantine",
            corpus_raw_dir=resolved / "data" / "corpus" / "raw",
            corpus_scanned_dir=resolved / "data" / "corpus" / "scanned",
            state_dir=state_dir,
            database_path=state_dir / "pipeline.db",
        )

    def ensure_directories(self) -> None:
        for path in (
            self.incoming_dir,
            self.archive_dir,
            self.quarantine_dir,
            self.corpus_raw_dir,
            self.corpus_scanned_dir,
            self.state_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
