import re


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def build_fts_query(query: str) -> str:
    terms = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", query)
        if len(token) > 1 and token.lower() not in STOP_WORDS
    ]
    return " OR ".join(f'"{term}"' for term in dict.fromkeys(terms))
