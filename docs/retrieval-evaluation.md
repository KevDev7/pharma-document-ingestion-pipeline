# Retrieval Evaluation

Date: August 17, 2026

This experiment measures whether a query retrieves the correct source page from the 16-document FDA corpus. It evaluates retrieval only; it does not score generated answers.

## Evaluation Set

- 80 project-authored, page-checked questions, with five questions per source document.
- 32 development questions used to choose a candidate configuration.
- 16 validation questions used to audit that candidate and correct label defects.
- 16 acceptance questions used to choose between the BM25 baseline and development candidate.
- 16 untouched test questions used for a post-lock comparison of the recommended baseline and rejected development candidate, without changing the recommendation.
- 80 unique relevant source pages identified by document SHA-256 and one-based page number.

Duplicate chunks from the same page are collapsed before scoring. This prevents overlapping chunks from receiving extra credit for retrieving one source page repeatedly.

The first validation run revealed that one two-page answer had only one page labeled. That label was corrected against the stored source text, so validation remained an audit split. A separate acceptance question per document was used to lock the design, and a fifth question per document was then reserved as an untouched final test.

The acceptance and test questions each label one relevant page. Multi-page retrieval is represented in development and audited validation data, but the held-out metrics below should not be read as evidence for multi-page answer coverage.

## Compared Configurations

The development grid contains 21 configurations:

- Chunking: full page, fixed 700-character windows with 120-character overlap, and boundary-aware 700-character chunks with 120-character overlap.
- Embeddings: MiniLM, BGE-small, and E5-small.
- Retrieval: SQLite FTS5 BM25 keyword search, exact cosine vector search, and reciprocal-rank-fusion hybrid search.

Embedding inputs are split to each model's token limit before encoding. Segment vectors are mean-pooled and normalized so full-page embeddings do not silently truncate long pages. Model-specific query and passage prefixes are applied for BGE and E5.

The cache key includes the resolved Hugging Face model revision, adapter version, Sentence Transformers, Torch, and Transformers versions, chunk IDs, and text hashes. The benchmark also rejects database documents whose content hash or page range does not match the frozen corpus manifest.

## Development Candidate

The predefined development rule maximized Recall@5, then MRR, then Precision@5, then lower p95 latency. It selected:

- Full-page records with token-aware segment pooling.
- `BAAI/bge-small-en-v1.5` embeddings.
- Hybrid BM25 and vector retrieval with reciprocal rank fusion (`k=60`).

On the audited validation split, this candidate improved Recall@5 from 93.75% to 96.88%. The acceptance split then compared it against boundary-aware BM25: BM25 reached 100.00% Recall@5 and 95.00% MRR, while hybrid reached 93.75% and 87.50%. BM25 was therefore locked as the recommendation before the final test.

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

Latency uses one warmup per retriever followed by five interleaved repetitions of all 16 test queries, producing 80 samples per system. The candidate missed two relevant pages within the first five results and still missed one within the first ten. BM25 retrieved every labeled test page within the first three results.

## Decision

Keep boundary-aware BM25 as the current retrieval design. It won the acceptance comparison, remained more accurate on the untouched test split, had a smaller index than fixed windows, and was about 10 times faster at p95. BGE hybrid retrieval remains an experiment, not a production dependency.

This result is useful precisely because it rejected the more complex design. The development and validation gains did not generalize to acceptance or test data, so adopting hybrid retrieval would have increased cost and complexity without evidence of better retrieval.

## Reproduce

Install the retrieval dependencies after ingesting the corpus:

```bash
pip install -e '.[retrieval,dev]'
pharma-pipeline evaluate-retrieval \
  --output docs/benchmarks/retrieval-evaluation-2026-08-17.json
```

The saved artifact includes input hashes, resolved model revisions, package versions, all 21 development runs, validation and acceptance results, raw per-query rankings, untouched test results at K=1/3/5/10, and repeated latency samples. Model download and index-build time are reported separately from query latency.

## Limits

The questions and page labels were created and reviewed within this project, not by independent regulatory experts. Sixteen single-page test questions are too few for a broad production-accuracy claim, and a perfect Recall@5 on this test set should not be generalized. The corpus is English-only and mostly born-digital. A future multi-page held-out set must be authored independently rather than added after reviewing these results.
