# Operations

## Initialize and Inspect

```bash
pharma-pipeline init
pharma-pipeline status
pharma-pipeline index-status
```

`status` is a quick console view. `index-status` also runs the SQLite FTS5 integrity check.

## Export Metrics

```bash
pharma-pipeline export-metrics \
  --output data/state/operational-metrics.json
```

The JSON has five operational sections:

- `documents`: current corpus size, immutable historical versions, OCR pages, and source bytes.
- `ingestion`: run and file outcomes, trigger counts, p50/p95/max duration, skip rate, and failure rate.
- `extraction`: current page counts by extraction method and the OCR rate.
- `errors`: total failures grouped by exception type.
- `search_index`: integrity status and current/stored chunk counts.

The export excludes extracted text, source paths, hashes, filenames, and error messages. This makes it suitable for CI artifacts or external monitoring without copying document content.

## Suggested Checks

Treat these as operational signals, not universal alert thresholds:

1. `search_index.status` should be `healthy` and `indexed_chunks` should equal `documents.current_chunks`.
2. `ingestion.status_counts.running` should not remain present after a worker exits normally.
3. A nonzero failure rate should be investigated through `ingestion_errors` and files in `data/quarantine/`.
4. A sharp OCR-rate change can indicate a new scan-heavy source or degraded embedded text.
5. Duplicate skips are expected under at-least-once file-event delivery; unexpected growth can indicate repeated upstream uploads.

## Recovery

Failed PDFs are isolated in `data/quarantine/` and do not stop unrelated files. After correcting the cause, move a file back to `data/incoming/` or run `pharma-pipeline ingest PATH`.

If the FTS integrity check fails, use:

```bash
pharma-pipeline rebuild-search-index
pharma-pipeline index-status
```

Normal ingestion updates the index transactionally and does not require rebuilds.
