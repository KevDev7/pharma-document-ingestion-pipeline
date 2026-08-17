import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .benchmark import benchmark_corpus
from .config import DEFAULT_ROOT, Settings
from .corpus import download_manifest, summarize_corpus
from .experiments import run_retrieval_experiment
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

    download = subparsers.add_parser(
        "download-corpus", help="Download PDFs declared in a provenance manifest"
    )
    download.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Manifest path; defaults to corpus/manifest.json",
    )
    download.add_argument("--limit", type=int, default=None)
    download.add_argument("--max-mb", type=int, default=75)

    subparsers.add_parser("corpus-status", help="Summarize locally downloaded corpus PDFs")
    subparsers.add_parser("ingest-corpus", help="Ingest all PDFs in data/corpus/raw")

    benchmark = subparsers.add_parser(
        "benchmark-corpus", help="Measure repeated corpus ingestion with fresh SQLite databases"
    )
    benchmark.add_argument("--runs", type=int, default=10)
    benchmark.add_argument("--output", type=Path, default=None)

    retrieval = subparsers.add_parser(
        "evaluate-retrieval",
        help="Compare chunking, embedding, and retrieval configurations",
    )
    retrieval.add_argument(
        "--queries", type=Path, default=None, help="Defaults to evaluation/queries.jsonl"
    )
    retrieval.add_argument(
        "--config", type=Path, default=None, help="Defaults to evaluation/config.json"
    )
    retrieval.add_argument("--output", type=Path, required=True)
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
    elif args.command == "download-corpus":
        manifest_path = args.manifest or settings.root / "corpus" / "manifest.json"
        output = download_manifest(
            manifest_path=manifest_path,
            destination_dir=settings.corpus_raw_dir,
            receipt_path=settings.state_dir / "corpus_downloads.jsonl",
            limit=args.limit,
            max_megabytes_per_file=args.max_mb,
        )
    elif args.command == "corpus-status":
        output = summarize_corpus(settings.corpus_raw_dir)
    elif args.command == "ingest-corpus":
        output = pipeline.ingest_paths(
            sorted(settings.corpus_raw_dir.glob("*.pdf")), trigger_type="corpus_ingestion"
        )
    elif args.command == "benchmark-corpus":
        output = benchmark_corpus(settings.corpus_raw_dir, runs=args.runs)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    elif args.command == "evaluate-retrieval":
        report = run_retrieval_experiment(
            database_path=settings.database_path,
            manifest_path=settings.root / "corpus" / "manifest.json",
            query_path=args.queries or settings.root / "evaluation" / "queries.jsonl",
            config_path=args.config or settings.root / "evaluation" / "config.json",
            cache_dir=settings.state_dir / "retrieval",
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        output = {
            "status": "completed",
            "artifact": str(args.output),
            "development_candidate_configuration": report[
                "development_candidate_configuration"
            ],
            "recommended_configuration": report["recommended_configuration"],
            "test_candidate_recall_at_5_delta": report[
                "test_candidate_recall_at_5_delta"
            ],
        }
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(json.dumps(output, indent=2))
