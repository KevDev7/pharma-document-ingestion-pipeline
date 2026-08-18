# Operations

## Initialize and Inspect

```bash
pharma-pipeline init
pharma-pipeline status
pharma-pipeline index-status
```

`status` shows database totals. `index-status` also checks the SQLite FTS5 full-text index for corruption.

## Export Metrics

```bash
pharma-pipeline export-metrics \
  --output data/state/operational-metrics.json
```

The JSON contains five sections:

- `documents`: current corpus size, immutable historical versions, OCR pages, and source bytes.
- `ingestion`: run and file outcomes, trigger counts, p50/p95/max duration, skip rate, and failure rate.
- `extraction`: current page counts by extraction method and the OCR rate.
- `errors`: total failures grouped by exception type.
- `search_index`: integrity status and current/stored chunk counts.

The export excludes document text, source paths, hashes, filenames, and error messages. CI jobs and monitoring tools can use it without copying document content.

## Suggested Checks

Use these checks as signals. They are not fixed alert thresholds:

1. `search_index.status` should be `healthy` and `indexed_chunks` should equal `documents.current_chunks`.
2. `ingestion.status_counts.running` should not remain present after a worker exits normally.
3. Investigate a nonzero failure rate through `ingestion_errors` and `data/quarantine/`.
4. A sharp OCR-rate change can indicate a new scan-heavy source or degraded embedded text.
5. Duplicate skips are expected under at-least-once file-event delivery; unexpected growth can indicate repeated upstream uploads.

## Recovery

Failed PDFs are isolated in `data/quarantine/` and do not stop unrelated files. After correcting the cause, move a file back to `data/incoming/` or run `pharma-pipeline ingest PATH`.

If the FTS integrity check fails, use:

```bash
pharma-pipeline rebuild-search-index
pharma-pipeline index-status
```

Normal ingestion updates the index in the same transaction as the source rows. It does not require a rebuild.
