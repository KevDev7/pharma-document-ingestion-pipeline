# Retrieval Evaluation Set

`queries.jsonl` contains 80 answerable questions across the 16-document FDA corpus. Each document contributes two development questions and one validation, acceptance, and test question. This keeps long documents from dominating the metrics.

Every row contains:

- `query_id`: stable identifier.
- `query`: the text sent to each retriever.
- `category`: `exact_fact`, `paraphrase`, `terminology`, or `multi_page`.
- `relevant_pages`: one or more document SHA-256 hashes and 1-based page numbers. The filename remains for readable reports.
- `reference_answer`: a concise answer used for human review; retrieval metrics do not score generated prose.

A chunk is relevant only when its document hash and page number match a label. Scoring counts each source page once. Recall@K measures how many relevant pages appear in the first K results. Precision@K measures how many of those K results are relevant. Mean reciprocal rank (MRR) rewards placing the first relevant page near the top.

The labels were written from stored page text and checked against source pages on August 17, 2026. They support a development benchmark. External regulatory experts did not review them.

Validation data was audited after one incomplete multi-page label was found. Acceptance data then selected BM25 over the development candidate. Test questions were written without inspecting retrieval output. The test ran only after the choice was locked.

Acceptance and test questions label one relevant page each. Only development and validation contain multi-page questions. The held-out results therefore do not measure multi-page retrieval quality.
