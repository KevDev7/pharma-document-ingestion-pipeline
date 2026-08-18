# Decision Log

## 001: Start ingestion from file events

**Decision:** Process a PDF when it appears in the landing directory.

**Reason:** PDFs arrive as individual files. A file event can start processing immediately instead of waiting for a schedule.

**Trade-off:** A file watcher does not provide Airflow's scheduling UI or DAG history. Run and failure history are stored explicitly in the control database instead.

## 002: Use a Python environment for the development worker

**Decision:** Use a normal Python virtual environment and system Tesseract installation.

**Reason:** The first worker runs on one development machine. A Python virtual environment is enough to test incremental processing, lineage, and failure handling.

**Trade-off:** Tesseract installation differs by operating system. The README documents it as an external requirement.

## 003: Use SQLite for the single-worker design

**Decision:** Persist ingestion state in SQLite while building the local event workflow.

**Reason:** SQLite supports the required tables, transactions, duplicate checks, and automated tests with no separate database service.

**Trade-off:** SQLite is not designed for several concurrent ingestion workers. PostgreSQL should be evaluated before adding more workers.

## 004: Conditional OCR

**Decision:** Read embedded PDF text first. Use Tesseract only when the page fails the OCR routing checks.

**Reason:** Digital extraction is faster and avoids OCR transcription errors. OCR remains available for image-only scans.

**Trade-off:** The first character-count rule missed scans with plausible but incorrect hidden text. Decision 010 records the tested replacement.

## 005: Manifest-driven FDA corpus

**Decision:** Build the benchmark from 16 FDA guidance and compliance-program PDFs. Commit their source records and expected hashes, but keep downloaded files out of Git.

**Reason:** The documents come from official sources and can be downloaded again. Expected hashes expose upstream file changes instead of silently changing benchmark inputs.

**Trade-off:** The 430-page corpus is mostly born-digital. It supports ingestion and retrieval tests but cannot support broad OCR accuracy claims. OCR uses a separate controlled stress set.

## 006: Page-level labels with development, validation, acceptance, and test splits

**Decision:** Label relevant sources by immutable document SHA-256 plus one-based page number, use 32 questions for development, 16 for validation, 16 for acceptance, and 16 untouched questions for final evaluation.

**Reason:** A filename can point to changed content, but a document hash and page number identify one exact source. The validation audit found one incomplete multi-page label, so validation was not treated as untouched data. The acceptance set made the design choice. A fifth question per document stayed untouched for the final test. Scoring collapses duplicate chunks from the same page so overlap does not inflate results.

**Trade-off:** Eighty project-reviewed questions can compare local designs. They cannot prove general production accuracy or independent regulatory validation.

## 007: Exact vector search at current corpus size

**Decision:** Cache normalized sentence-transformer embeddings and use exact NumPy cosine search for the retrieval experiment.

**Reason:** The largest tested index contains 2,068 chunks. Exact search checks every vector and avoids tuning an approximate index before the corpus needs one.

**Trade-off:** Exact search will become slower as the corpus grows. PostgreSQL/pgvector or another vector store should be benchmarked before adding concurrent workers or a much larger corpus.

## 008: Retain boundary-aware BM25 after acceptance testing

**Decision:** Keep boundary-aware chunks with SQLite FTS5 BM25 as the current retrieval design.

**Reason:** Full-page BGE-small hybrid retrieval ranked first in development and improved validation results. That gain did not hold on acceptance data. On 16 acceptance questions, BM25 reached 100.00% Recall@5 and 95.00% MRR. Hybrid retrieval reached 93.75% and 87.50%. BM25 was selected before the final test.

**Trade-off:** BM25 matches words rather than semantic similarity from embeddings. On the untouched test, it reached 100.00% Recall@5 versus 87.50% for hybrid. Its p95 latency was 1.17 ms versus 11.74 ms. The test has only 16 questions, so the perfect Recall@5 result does not apply to other datasets.

## 009: Maintain BM25 transactionally in SQLite

**Decision:** Store the selected BM25 index as an FTS5 external-content table maintained by triggers on the existing `chunks` table.

**Reason:** Corpus data and ingestion state already live in one SQLite database. Database triggers make new chunks searchable in the same transaction. This removes a separate indexing job and keeps each result linked to its document version and page.

**Trade-off:** Older chunks stay in source tables for audit but leave the active index. This requires extra trigger logic. At this corpus size, full rebuilds and incremental updates take about the same time. The benefit is consistent, bounded updates rather than a measured speedup. A shared database becomes useful when worker count or corpus size increases.

## 010: Route OCR using text-quality signals

**Decision:** Keep the 50-character floor, but also route embedded text dominated by one-character tokens or paired with a full-page raster image. Retain Tesseract `--psm 6` for the current CPU pipeline.

**Reason:** On 38 controlled cases, the character-only rule missed 19 scans with hidden text and reached 38.71% recall. The page-aware rule reached 100% routing recall. The final extractor produced the expected outcome in all 38 cases. It also added no OCR routes during an audit of all 430 corpus pages. On 12 visible scan images, `--psm 6` produced 2.10% mean word error rate versus 2.27% for `--psm 3`. Both recovered every labeled phrase.

**Trade-off:** The stress set uses born-digital text as its reference. It does not prove general OCR accuracy or compare Tesseract with EasyOCR and PaddleOCR. A conflict in a critical field quarantines the file because the schema stores only one transcription. This reduces automatic throughput but prevents a disputed compliance value from entering search.

## 011: Export operational metrics from the control database

**Decision:** Generate a versioned JSON operational snapshot directly from SQLite run, error, document, page, and search-index records.

**Reason:** The pipeline already records these facts in SQLite. Reading them avoids a second metrics database and keeps one definition for corpus size, duplicate skips, failures, OCR usage, run time, and index health.

**Trade-off:** This is a point-in-time export, not a live dashboard or alerting system. A hosted worker would need monitoring and alarms built from the same metric definitions.

## 012: Use S3 notifications through SQS instead of direct worker coupling

**Decision:** Publish versioned `incoming/*.pdf` S3 object-created events to an encrypted standard SQS queue, then let one Python worker download and process each object version.

**Reason:** SQS buffers uploads, supports long polling and retries, and moves repeatedly failing messages to a dead-letter queue. The worker reuses the same ingestion code. SHA-256 and a unique database constraint handle duplicate delivery.

**Trade-off:** The verified worker runs from the development machine and writes to SQLite. It proves the cloud upload and queue flow, not an always-on or horizontally scaled service. Concurrent workers would require persistent compute and a shared database.

## 013: Acknowledge deterministic document failures after quarantine

**Decision:** Copy invalid PDFs and other permanent document failures to `quarantine/`. Store the error, delete the incoming object, and acknowledge the SQS message. Do not acknowledge temporary download, database, or worker failures.

**Reason:** Retrying the same malformed file cannot repair it. Stored errors and hash-addressed quarantine objects preserve evidence without blocking other uploads.

**Trade-off:** The code must classify permanent error types. Unknown failures retry by default so recoverable work is not discarded.
