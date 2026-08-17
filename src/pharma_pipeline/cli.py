import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .config import DEFAULT_ROOT, Settings
from .pipeline import IngestionPipeline
from .watcher import watch_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pharma-pipeline",
        description="Event-driven pharmaceutical PDF ingestion pipeline",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Repository root containing the data directory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create data directories and initialize SQLite")

    ingest = subparsers.add_parser("ingest", help="Process one or more PDF files")
    ingest.add_argument("paths", nargs="+", type=Path)

    subparsers.add_parser("scan", help="Process and archive PDFs currently in data/incoming")
    subparsers.add_parser("watch", help="Watch data/incoming and process new PDFs")
    subparsers.add_parser("status", help="Print persisted pipeline counts")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings.from_root(args.root)
    pipeline = IngestionPipeline(settings)

    if args.command == "init":
        output = {
            "status": "initialized",
            "root": str(settings.root),
            "incoming_dir": str(settings.incoming_dir),
            "database_path": str(settings.database_path),
        }
    elif args.command == "ingest":
        output = pipeline.ingest_paths(args.paths)
    elif args.command == "scan":
        output = pipeline.scan_incoming()
    elif args.command == "watch":
        watch_directory(pipeline)
        return
    elif args.command == "status":
        output = pipeline.database.summary()
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(json.dumps(output, indent=2))
