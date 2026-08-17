# Retrieval Evaluation Set

`queries.jsonl` contains 80 answerable questions across the 16-document FDA core corpus. Each document contributes two development, one validation, one acceptance, and one test question so long compliance manuals do not dominate aggregate metrics.

Every row contains:

- `query_id`: stable identifier.
- `query`: the text sent to each retriever.
- `category`: `exact_fact`, `paraphrase`, `terminology`, or `multi_page`.
- `relevant_pages`: one or more immutable document SHA-256 and 1-based page labels; the filename is retained for readable reports.
- `reference_answer`: a concise answer used for human review; retrieval metrics do not score generated prose.

A chunk is relevant only when both its source document hash and page number match a label. Duplicate chunks from the same source page are collapsed before scoring. Recall@K counts unique relevant pages retrieved, Precision@K counts relevant pages among the top K, and MRR uses the first relevant page rank.

The labels were drafted from the stored page text and reviewed against the source pages on August 17, 2026. They are suitable for a development benchmark, but they are not an externally adjudicated regulatory QA dataset.

The validation split was used for result auditing after an incomplete multi-page label was found. The acceptance split then made the final BM25-versus-candidate design decision. The test split was authored from source text without inspecting retrieval output and was run only after the recommendation was locked; it compares both systems without changing that decision.

Acceptance and test questions currently label one relevant page each. Multi-page questions are present only in development and audited validation, so the held-out metrics do not establish multi-page retrieval quality.
