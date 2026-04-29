"""Memory utilities — currently the embedding-based RAG layer for
``AgentMemory``. Kept as a separate package so the heavy
``sentence-transformers`` dependency is opt-in (``[project.optional-
dependencies] memory-rag``) without making the base install pull torch
twice.
"""

from autonoma.memory.embeddings import (
    encode_texts,
    is_rag_available,
    score_against,
)

__all__ = ["encode_texts", "is_rag_available", "score_against"]
