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

## OCR Stress Set

`ocr-stress-manifest.json` selects six labeled pages from the frozen core corpus. `pharma-pipeline benchmark-ocr` deterministically generates six one-page variants for each source page in `data/corpus/scanned/`:

- Original digital page as a control.
- 200-DPI image-only scan.
- Lower-contrast 120-DPI JPEG scan.
- 200-DPI scan with a repetitive corrupted hidden text layer.
- 200-DPI scan with plausible but incorrect hidden OCR text.
- 200-DPI scan with an accurate hidden text layer, used to verify that OCR does not replace better embedded text.

A separate clean digital certificate with repetitive `Pass` values acts as a form-style negative control. A synthetic scan with a matched-length but conflicting lot number and release status verifies that ambiguous critical fields fail extraction instead of silently selecting either value.

Generated PDFs remain ignored because the manifest, frozen source hashes, page numbers, and transformation code reproduce them. Original digital text is used as controlled ground truth. This design measures known degradation modes but does not represent handwriting, severe rotation, damaged pages, or every pharmaceutical form layout.
