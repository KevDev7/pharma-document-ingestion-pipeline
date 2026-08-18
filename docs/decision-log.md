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

## 005: Manifest-driven FDA corpus

**Decision:** Build the first benchmark from 16 FDA-authored final guidances and compliance-program PDFs, commit their provenance and expected hashes, and keep downloaded files out of Git.

**Reason:** The corpus is relevant to pharmaceutical document processing, can be reconstructed from official sources, and has a clearer reuse basis than sponsor-authored labels or submissions. Frozen hashes make source changes visible instead of silently changing benchmark inputs.

**Trade-off:** The 430-page core is mostly born-digital and is useful for ingestion and retrieval evaluation, but not broad enough for OCR accuracy claims. Historical scanned FDA material will be evaluated as a separate, explicitly labeled stress corpus.

## 006: Page-level labels with development, validation, acceptance, and test splits

**Decision:** Label relevant sources by immutable document SHA-256 plus one-based page number, use 32 questions for development, 16 for validation, 16 for acceptance, and 16 untouched questions for final evaluation.

**Reason:** Filename-only labels can silently point at a changed document version. The first validation audit found an incomplete multi-page label, so that split was not treated as untouched test data. Acceptance questions made the final design decision, and a fifth question per document was reserved for untouched evaluation. Duplicate chunks from one page are collapsed before scoring so overlap does not inflate metrics.

**Trade-off:** Eighty internally reviewed questions are enough to compare local designs, but not enough to claim general production accuracy or independent regulatory validation.

## 007: Exact vector search at current corpus size

**Decision:** Cache normalized sentence-transformer embeddings and use exact NumPy cosine search for the retrieval experiment.

**Reason:** The largest tested index contains 2,068 chunks. Exact search keeps the comparison deterministic and avoids tuning an approximate index before scale requires it.

**Trade-off:** Exact search will not remain appropriate if the corpus grows by orders of magnitude. PostgreSQL/pgvector or another durable vector store should be benchmarked before adding concurrent workers.

## 008: Retain boundary-aware BM25 after acceptance testing

**Decision:** Keep boundary-aware chunks with SQLite FTS5 BM25 as the current retrieval design.

**Reason:** Full-page BGE-small hybrid retrieval ranked first in development and looked better on the audited validation split, but the gain did not generalize. On 16 acceptance questions, BM25 achieved 100.00% Recall@5 and 95.00% MRR versus 93.75% and 87.50% for the hybrid candidate, so BM25 was locked before final testing.

**Trade-off:** BM25 does not capture semantic similarity as directly as an embedding model. On the untouched test, it achieved 100.00% Recall@5 versus 87.50% for hybrid and had 1.17 ms p95 retrieval latency versus 11.74 ms. The perfect Recall@5 result is based on only 16 test questions and must not be generalized.

## 009: Maintain BM25 transactionally in SQLite

**Decision:** Store the selected BM25 index as an FTS5 external-content table maintained by triggers on the existing `chunks` table.

**Reason:** The corpus and ingestion state already live in one single-worker SQLite database. Transactional triggers make new chunks searchable immediately, prevent a separate indexing job from falling behind, and keep every result linked to its document version and page.

**Trade-off:** Superseded chunks remain in source tables for audit but are removed from the active index, adding document-version trigger logic to prevent stale versions from affecting BM25 scores. At this small corpus size, full rebuilds and incremental chunk batches take roughly the same wall-clock time, so this milestone proves bounded updates and consistency rather than a meaningful latency win. PostgreSQL or a managed search system is not justified until concurrency or corpus size changes substantially.

## 010: Route OCR using text-quality signals

**Decision:** Keep the 50-character floor, but also route embedded text dominated by one-character tokens or paired with a full-page raster image. Retain Tesseract `--psm 6` for the current CPU pipeline.

**Reason:** On 38 controlled routing scenarios, the character-only rule missed 19 scans carrying hidden text and reached 38.71% recall. The page-aware rule reached 100% routing recall, and the final extractor produced the expected outcome in all 38 scenarios, including six accurate text layers, a clean repetitive certificate, and one rejected critical-field conflict. It added no OCR routes across the 430-page core audit. On 12 unique visible scan images, `--psm 6` produced 2.10% mean word error rate versus 2.27% for `--psm 3`; both recovered every labeled phrase. Timing used warm-ups and alternating order, with only a modest local `--psm 6` advantage.

**Trade-off:** The stress set is controlled and uses born-digital text as its reference. It does not establish general OCR accuracy or prove Tesseract is better than EasyOCR or PaddleOCR. A critical-field disagreement quarantines the whole file because the current schema does not preserve two competing transcriptions; this loses automatic throughput to prevent a potentially wrong compliance value from becoming searchable.

## 011: Export operational metrics from the control database

**Decision:** Generate a versioned JSON operational snapshot directly from SQLite run, error, document, page, and search-index records.

**Reason:** The pipeline already records the required facts transactionally. Reading those facts avoids a second metrics database before deployment and creates stable definitions for current-corpus size, duplicate skips, failures, OCR usage, run latency, and index health.

**Trade-off:** This is a point-in-time export, not a live dashboard or alerting system. It is sufficient for the single-worker local milestone; cloud monitoring and alarms should consume the same metric meanings once a deployed worker exists.
