# Benchmark Corpus

The benchmark corpus is reconstructed from `manifest.json`. Raw PDFs are downloaded into `data/corpus/raw/` and are excluded from Git.

The initial collection uses official pharmaceutical-quality guidance documents hosted by the U.S. Food and Drug Administration. FDA's website policy says that FDA website text and graphics are public domain unless otherwise noted. It also recommends linking to the current source because documents may be updated.

Sources:

- FDA pharmaceutical quality catalog: https://www.fda.gov/drugs/pharmaceutical-quality-resources/search-pharmaceutical-quality-documents
- FDA website copying policy: https://www.fda.gov/about-fda/about-website/website-policies

The manifest preserves both the landing page and direct PDF URL. Raw PDFs remain uncommitted even when public-domain reuse appears permitted; this keeps the repository small. Expected SHA-256 hashes freeze the benchmark bytes, so a changed upstream file fails validation instead of silently changing later measurements.

## Commands

```bash
pharma-pipeline download-corpus
pharma-pipeline corpus-status
pharma-pipeline ingest-corpus
```

`download-corpus` appends a receipt to `data/state/corpus_downloads.jsonl` for every attempted document. Each successful receipt contains the direct source URL, access timestamp, file size, response metadata, and SHA-256 hash.

The validated core contains 16 PDFs and 430 pages. It intentionally uses FDA-authored guidance and compliance-program documents rather than sponsor-authored labels or submissions, whose redistribution rights can be less clear.

This corpus currently emphasizes digitally generated guidance documents. Image-only test variants will be created separately from a documented subset so OCR results can be compared against the original digital text.
