# Benchmark Corpus

`manifest.json` lists every benchmark document. The download command places raw PDFs in `data/corpus/raw/`, which Git ignores.

The collection uses pharmaceutical-quality guidance documents from the U.S. Food and Drug Administration (FDA). FDA states that website text and graphics are public domain unless noted otherwise. FDA also recommends linking to the current source because documents can change.

Sources:

- FDA pharmaceutical quality catalog: https://www.fda.gov/drugs/pharmaceutical-quality-resources/search-pharmaceutical-quality-documents
- FDA website copying policy: https://www.fda.gov/about-fda/about-website/website-policies

The manifest stores each landing page and direct PDF URL. Raw PDFs remain outside Git to keep the repository small. Expected SHA-256 hashes identify the exact tested files. If an FDA file changes, validation fails instead of silently changing the benchmark.

## Commands

```bash
pharma-pipeline download-corpus
pharma-pipeline corpus-status
pharma-pipeline ingest-corpus
```

`download-corpus` writes one receipt per attempted document to `data/state/corpus_downloads.jsonl`. A successful receipt contains the source URL, access time, file size, response metadata, and SHA-256 hash.

The validated corpus contains 16 PDFs and 430 pages. It uses FDA-authored guidance and compliance documents. Sponsor-authored labels and submissions are excluded because their reuse terms can be less clear.

## OCR Stress Set

`ocr-stress-manifest.json` selects six labeled pages from the frozen corpus. `pharma-pipeline benchmark-ocr` creates the same six one-page variants for each source page in `data/corpus/scanned/`:

- Original digital page as a control.
- 200-DPI image-only scan.
- Lower-contrast 120-DPI JPEG scan.
- 200-DPI scan with a repetitive corrupted hidden text layer.
- 200-DPI scan with plausible but incorrect hidden OCR text.
- 200-DPI scan with an accurate hidden text layer, used to verify that OCR does not replace better embedded text.

A clean digital certificate with repeated `Pass` values checks that repetitive forms do not trigger OCR by mistake. A synthetic scan contains conflicting lot numbers and release statuses of similar length. It verifies that critical-field conflicts fail extraction instead of silently selecting one value.

Generated PDFs remain outside Git because the manifest and transformation code can rebuild them. The original digital text serves as controlled ground truth. The set covers known degradation cases, not handwriting, severe rotation, damaged pages, or every pharmaceutical form.
