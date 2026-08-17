# Decision Log

## 001: Event-driven ingestion instead of Airflow

**Decision:** Process a PDF when it appears in the landing directory.

**Reason:** Documents arrive as discrete files and should become available without waiting for a schedule. This project also gains more portfolio value by demonstrating a different ingestion pattern than the existing Airflow project.

**Trade-off:** A file watcher does not provide Airflow's scheduling UI or DAG history. Run and failure history are stored explicitly in the control database instead.

## 002: No Docker in the first milestone

**Decision:** Use a normal Python virtual environment and system Tesseract installation.

**Reason:** Containerization is already demonstrated elsewhere and does not address the first milestone's central risk: correct incremental processing and lineage.

**Trade-off:** Tesseract installation differs by operating system. The README documents it as an external requirement.

## 003: SQLite before PostgreSQL/pgvector

**Decision:** Persist ingestion state in SQLite while building the local event workflow.

**Reason:** SQLite makes the control tables, transactions, duplicate handling, and tests immediately reproducible. Retrieval storage has not been selected yet.

**Trade-off:** SQLite is not intended for several concurrent ingestion workers. PostgreSQL will be evaluated before adding cloud workers or vector retrieval.

## 004: Conditional OCR

**Decision:** Read embedded PDF text first and use Tesseract only when a page contains fewer than 50 extracted characters.

**Reason:** Digital extraction is faster and avoids OCR transcription errors. OCR remains available for image-only scans.

**Trade-off:** Character count is a simple routing signal and may miss pages containing substantial but corrupted embedded text. A later experiment will compare richer quality checks.
