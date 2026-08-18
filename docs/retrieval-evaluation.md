# Retrieval Evaluation

Date: August 17, 2026

This experiment tests whether a query retrieves the correct page from the 16-document FDA corpus. It measures search results, not generated answers.

## Evaluation Set

- 80 project-authored, page-checked questions, with five questions per source document.
- 32 development questions used to choose a candidate configuration.
- 16 validation questions used to audit that candidate and correct label defects.
- 16 acceptance questions used to choose between the BM25 baseline and development candidate.
- 16 untouched test questions used to compare the selected baseline with the development candidate after the choice was locked.
- 80 unique relevant source pages identified by document SHA-256 and one-based page number.

Scoring counts each source page once. Overlapping chunks from the same page cannot earn extra credit.

The first validation run found one two-page answer with only one labeled page. The label was corrected against the stored text, so validation remained an audit set. One acceptance question per document then selected the design. A fifth question per document stayed untouched for the final test.

Each acceptance and test question labels one relevant page. Development and validation contain multi-page cases. The held-out results below do not measure multi-page answer coverage.

## Compared Configurations

The development grid contains 21 configurations:

- Chunking: full page, fixed 700-character windows with 120-character overlap, and boundary-aware 700-character chunks with 120-character overlap.
- Embeddings: MiniLM, BGE-small, and E5-small.
- Retrieval: SQLite FTS5 BM25 keyword search, exact cosine vector search, and reciprocal-rank-fusion hybrid search.

Text is split at each embedding model's token limit. Segment vectors are averaged and normalized so long pages are not silently cut off. BGE and E5 receive their required query and passage prefixes.

The cache key includes the Hugging Face model revision, adapter version, package versions, chunk IDs, and text hashes. The benchmark rejects database rows whose document hash or page range does not match the frozen corpus manifest.

## Development Candidate

The development rule ranked configurations by Recall@5, MRR, Precision@5, and then lower p95 latency. It selected:

- Full-page records with token-aware segment pooling.
- `BAAI/bge-small-en-v1.5` embeddings.
- Hybrid BM25 and vector retrieval with reciprocal rank fusion (`k=60`).

On validation data, this candidate improved Recall@5 from 93.75% to 96.88%. Acceptance data gave a different result. BM25 reached 100.00% Recall@5 and 95.00% MRR. Hybrid retrieval reached 93.75% and 87.50%. BM25 was selected before the final test.

## Untouched Test Results

The baseline is boundary-aware chunks with BM25 keyword retrieval. Each timed query retrieves 50 chunk candidates and scores the top K unique source pages.

| Metric | BM25 baseline | Development candidate | Candidate minus baseline |
| --- | ---: | ---: | ---: |
| Recall@1 | 93.75% | 62.50% | -31.25 points |
| Recall@3 | 100.00% | 87.50% | -12.50 points |
| Recall@5 | 100.00% | 87.50% | -12.50 points |
| MRR at K=5 | 96.88% | 73.96% | -22.92 points |
| Precision@5 | 20.00% | 17.50% | -2.50 points |
| p95 retrieval latency | 1.17 ms | 11.74 ms | +10.57 ms |

Latency testing used one warmup and five alternating runs of all 16 queries. This produced 80 timing samples per system. Hybrid retrieval missed two relevant pages in the first five results and one in the first ten. BM25 retrieved every labeled test page in the first three results.

## Decision

Keep boundary-aware BM25 as the current retrieval design. It performed better on acceptance and test data, used a smaller index than fixed windows, and had about one-tenth the p95 latency of hybrid retrieval. BGE hybrid retrieval remains an experiment.

The more complex design improved development and validation results but not acceptance or test results. Hybrid retrieval would add model and index costs without measured improvement on the held-out data.

## Reproduce

Install the retrieval dependencies after ingesting the corpus:

```bash
pip install -e '.[retrieval,dev]'
pharma-pipeline evaluate-retrieval \
  --output docs/benchmarks/retrieval-evaluation-2026-08-17.json
```

The saved JSON includes input hashes, model revisions, package versions, all 21 development runs, validation and acceptance results, query rankings, final test results, and latency samples. Model download and index-build time are separate from query latency.

## Limits

The project created and reviewed its own questions and page labels. Independent regulatory experts did not validate them. Sixteen single-page test questions are too few for a broad production claim. The corpus is English-only and mostly born-digital. A future multi-page test set should be written before its retrieval results are inspected.
