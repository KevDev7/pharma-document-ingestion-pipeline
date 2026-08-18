# Setup and Commands

This guide covers local installation, AWS profile use, pipeline commands, corpus tools, evaluations, and tests.

## Requirements

- Python 3.10 or newer.
- Tesseract installed as a system command.
- An AWS CLI profile only when running the S3/SQS worker.

Tesseract is an external program. Install it with the package manager for your operating system before running OCR tests or processing scanned PDFs.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Install the optional embedding and retrieval packages only when running the retrieval comparison:

```bash
pip install -e '.[retrieval,dev]'
```

Install the optional API and browser interface packages when serving search:

```bash
pip install -e '.[serve,dev]'
```

## Initialize Local State

```bash
pharma-pipeline init
pharma-pipeline status
pharma-pipeline index-status
```

The default database path is `data/state/pipeline.db`. Runtime data under `data/` is excluded from Git.

## Process PDFs

Process one or more files directly:

```bash
pharma-pipeline ingest /path/to/one.pdf /path/to/two.pdf
```

Process files already placed in `data/incoming/`:

```bash
pharma-pipeline scan
```

Watch the local landing directory for new PDFs:

```bash
pharma-pipeline watch
```

Successful and duplicate files move to `data/archive/`. Permanent document failures move to `data/quarantine/`.

## Run the S3/SQS Worker

Use a standard AWS CLI profile. Do not store access keys in the repository.

The profile needs permission to:

- Read exact object versions under the S3 `incoming/` prefix.
- Copy or delete objects under the required `incoming/`, `processed/`, and `quarantine/` prefixes.
- Receive, delete, and inspect messages on the main SQS queue.

Run the worker:

```bash
pharma-pipeline watch-s3 \
  --bucket YOUR_PRIVATE_BUCKET \
  --queue-name YOUR_STANDARD_QUEUE \
  --region us-east-1 \
  --profile YOUR_WORKER_PROFILE
```

The tested AWS settings and message rules are documented in [S3/SQS Integration](s3-handoff.md).

## Search and Index Recovery

Search current document versions:

```bash
pharma-pipeline search "What must be completed before commercial distribution?" --top-k 5
```

Check or rebuild the FTS5 index:

```bash
pharma-pipeline index-status
pharma-pipeline rebuild-search-index
pharma-pipeline index-status
```

Normal ingestion updates the index automatically. Rebuild it only for recovery.

## Serve Search

Start the FastAPI retrieval service in one terminal:

```bash
pharma-pipeline serve-api --host 127.0.0.1 --port 8000
```

The JSON API is available at `http://127.0.0.1:8000`, and its generated API documentation is at `http://127.0.0.1:8000/docs`. It exposes:

- `GET /health` for pipeline and search-index status.
- `GET /document-types` for available filters.
- `POST /search` for ranked, source-linked passages.

Start the Gradio client in a second terminal:

```bash
pharma-pipeline serve-ui \
  --api-url http://127.0.0.1:8000 \
  --host 127.0.0.1 \
  --port 7860
```

Open `http://127.0.0.1:7860`. Add `--share` only when a temporary public Gradio link is needed. The UI displays retrieved evidence; it does not add an LLM or generate claims beyond the stored passages.

## Corpus Commands

The source PDFs are not stored in Git. Rebuild the frozen FDA corpus from `corpus/manifest.json`:

```bash
pharma-pipeline download-corpus
pharma-pipeline corpus-status
pharma-pipeline ingest-corpus
```

Run the repeated ingestion benchmark with a fresh SQLite database for each run:

```bash
pharma-pipeline benchmark-corpus \
  --runs 10 \
  --output docs/benchmarks/corpus-ingestion.json
```

See [Corpus](../corpus/README.md) for source records and hash validation.

## OCR Evaluation

Rebuild the controlled scan variants and run the OCR evaluation:

```bash
pharma-pipeline benchmark-ocr \
  --output docs/benchmarks/ocr-routing.json
```

Generated scan files remain outside Git. See [Development Verification](development-verification.md) for the saved results and limits.

## Retrieval Evaluation

After installing the optional retrieval packages and ingesting the corpus:

```bash
pharma-pipeline evaluate-retrieval \
  --output docs/benchmarks/retrieval-evaluation.json
```

The command compares chunking, embedding, keyword, vector, and hybrid retrieval settings. See [Retrieval Evaluation](retrieval-evaluation.md) for the labels and scoring method.

## Metrics and Operations

Export a JSON metrics file that excludes document content:

```bash
pharma-pipeline export-metrics \
  --output data/state/operational-metrics.json
```

See [Operations](operations.md) for field definitions, health checks, and recovery steps.

## Tests

Run the complete automated suite:

```bash
.venv/bin/pytest
```

The tests create temporary files and databases. They do not require the downloaded FDA corpus unless a specific corpus command is run.
