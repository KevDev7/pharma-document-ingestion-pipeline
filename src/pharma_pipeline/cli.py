import argparse
import json
import time
from pathlib import Path
from typing import Optional, Sequence

import boto3

from .aws_worker import S3SqsWorker
from .benchmark import benchmark_corpus
from .config import DEFAULT_ROOT, Settings
from .corpus import download_manifest, summarize_corpus
from .experiments import run_retrieval_experiment
from .metrics import export_operational_metrics
from .ocr_benchmark import run_ocr_benchmark
from .pipeline import IngestionPipeline
from .search_benchmark import benchmark_search_index
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
    watch_s3 = subparsers.add_parser(
        "watch-s3", help="Poll SQS and process S3 object-created events"
    )
    watch_s3.add_argument("--bucket", required=True)
    watch_s3.add_argument("--queue-name", required=True)
    watch_s3.add_argument("--region", default="us-east-1")
    watch_s3.add_argument("--profile", default=None)
    watch_s3.add_argument(
        "--once", action="store_true", help="Poll once and exit instead of running continuously"
    )
    subparsers.add_parser("status", help="Print persisted pipeline counts")
    metrics = subparsers.add_parser(
        "export-metrics",
        help="Export an operational JSON snapshot without document content",
    )
    metrics.add_argument("--output", type=Path, required=True)

    search = subparsers.add_parser(
        "search", help="Search current document chunks in the durable BM25 index"
    )
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--document-type", default=None)

    subparsers.add_parser(
        "index-status", help="Check the durable search index and print its counts"
    )
    subparsers.add_parser(
        "rebuild-search-index", help="Rebuild the durable search index for recovery"
    )

    search_benchmark = subparsers.add_parser(
        "benchmark-search-index",
        help="Compare one-document incremental indexing with a full index rebuild",
    )
    search_benchmark.add_argument("--runs", type=int, default=10)
    search_benchmark.add_argument("--output", type=Path, default=None)

    ocr_benchmark = subparsers.add_parser(
        "benchmark-ocr",
        help="Generate controlled scan variants and evaluate OCR routing and accuracy",
    )
    ocr_benchmark.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="OCR stress manifest (defaults to corpus/ocr-stress-manifest.json)",
    )
    ocr_benchmark.add_argument("--output", type=Path, default=None)

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

    serve_api = subparsers.add_parser(
        "serve-api", help="Serve health and document search endpoints with FastAPI"
    )
    serve_api.add_argument("--host", default="127.0.0.1")
    serve_api.add_argument("--port", type=int, default=8000)

    serve_ui = subparsers.add_parser(
        "serve-ui", help="Launch the Gradio document search interface"
    )
    serve_ui.add_argument("--api-url", default="http://127.0.0.1:8000")
    serve_ui.add_argument("--host", default="127.0.0.1")
    serve_ui.add_argument("--port", type=int, default=7860)
    serve_ui.add_argument("--share", action="store_true")
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
    elif args.command == "watch-s3":
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        s3_client = session.client("s3")
        sqs_client = session.client("sqs")
        queue_url = sqs_client.get_queue_url(QueueName=args.queue_name)["QueueUrl"]
        worker = S3SqsWorker(
            pipeline=pipeline,
            s3_client=s3_client,
            sqs_client=sqs_client,
            queue_url=queue_url,
            bucket=args.bucket,
        )
        if args.once:
            output = {"messages": worker.run_once(max_messages=1, wait_time_seconds=20)}
        else:
            worker.run_forever()
            return
    elif args.command == "status":
        output = {
            **pipeline.database.summary(),
            "search_index": pipeline.database.search_index_status(),
        }
    elif args.command == "export-metrics":
        output = export_operational_metrics(pipeline.database)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    elif args.command == "search":
        started = time.perf_counter()
        results = pipeline.database.search_chunks(
            args.query,
            top_k=args.top_k,
            document_type=args.document_type,
        )
        output = {
            "query": args.query,
            "top_k": args.top_k,
            "document_type": args.document_type,
            "result_count": len(results),
            "latency_ms": round((time.perf_counter() - started) * 1000, 4),
            "results": results,
        }
    elif args.command == "index-status":
        output = pipeline.database.search_index_status()
    elif args.command == "rebuild-search-index":
        output = pipeline.database.rebuild_search_index()
    elif args.command == "benchmark-search-index":
        output = benchmark_search_index(settings.database_path, runs=args.runs)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    elif args.command == "benchmark-ocr":
        manifest_path = args.manifest or settings.root / "corpus" / "ocr-stress-manifest.json"
        output = run_ocr_benchmark(
            settings.corpus_raw_dir,
            settings.corpus_scanned_dir,
            manifest_path,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
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
    elif args.command == "serve-api":
        import uvicorn

        from .service import create_app

        uvicorn.run(create_app(args.root), host=args.host, port=args.port)
        return
    elif args.command == "serve-ui":
        from .ui import create_demo

        create_demo(args.api_url).launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share,
        )
        return
    else:
        raise ValueError(f"Unsupported command: {args.command}")

    print(json.dumps(output, indent=2))
