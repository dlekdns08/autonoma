"""Optional sentence-transformers embedding backend — feature #5.

Why optional? The base install already carries torch (for OmniVoice +
Cohere ASR), so adding ``sentence-transformers`` is only ~30 MB of new
Python wheels. We keep it opt-in anyway so:

  * test environments that ``uv sync --no-dev`` (without the ``tts``
    extra) keep their fast, model-free boot;
  * deploys that explicitly disable RAG via the ``memory_rag_enabled``
    setting can ``pip uninstall sentence-transformers`` and reclaim
    the resident memory the model would otherwise hold.

Public surface intentionally tiny:

  * :func:`is_rag_available` — capability probe used by AgentMemory to
    decide whether to take the semantic path or fall back to substring
    keyword search.
  * :func:`encode_texts` — batch encode list of strings to vectors;
    returns ``None`` if the backend is unavailable.
  * :func:`score_against` — cosine similarity, kept here so the cosine
    fallback (numpy → pure python) lives in one spot.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Sequence

logger = logging.getLogger(__name__)

# ``paraphrase-multilingual-MiniLM-L12-v2`` is small (~120 MB),
# multilingual (supports Korean among 50+ langs), and converges to
# meaningful similarities on short texts — exactly the diary entry
# length AgentMemory operates on. Override via settings if a different
# model is preferred.
DEFAULT_MODEL_ID: str = "paraphrase-multilingual-MiniLM-L12-v2"

_model_lock = threading.Lock()
_model: object | None = None
_model_id_used: str = ""
_load_failed: bool = False


def _get_model_id() -> str:
    """Read ``settings.memory_rag_model`` lazily — settings imports are
    expensive at module load time and tests often stub them.
    """
    try:
        from autonoma.config import settings
    except Exception:
        return DEFAULT_MODEL_ID
    return getattr(settings, "memory_rag_model", "") or DEFAULT_MODEL_ID


def _ensure_model() -> object | None:
    """Lazy-load the SentenceTransformer model on first call.

    Returns ``None`` if the package isn't installed or the model can't
    be loaded — callers must check.
    """
    global _model, _model_id_used, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _model_lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError:
            logger.info(
                "[memory] sentence-transformers not installed; "
                "RAG falls back to keyword substring search"
            )
            _load_failed = True
            return None
        try:
            mid = _get_model_id()
            logger.info(f"[memory] loading SentenceTransformer model_id={mid}")
            _model = SentenceTransformer(mid)
            _model_id_used = mid
            logger.info(f"[memory] SentenceTransformer ready (dim={_model.get_sentence_embedding_dimension()})")
        except Exception as exc:
            logger.warning(f"[memory] SentenceTransformer load failed: {exc}")
            _load_failed = True
            _model = None
            return None
        return _model


def is_rag_available() -> bool:
    """Cheap capability probe — does NOT trigger model load."""
    if _model is not None:
        return True
    if _load_failed:
        return False
    # Soft probe — only checks import, doesn't construct the model.
    try:
        import sentence_transformers  # noqa: F401  type: ignore[import-not-found]
    except ImportError:
        return False
    return True


def encode_texts(texts: Sequence[str]) -> list[list[float]] | None:
    """Encode a batch of strings to vectors. Returns ``None`` if the
    backend is unavailable; callers must fall back accordingly.
    """
    if not texts:
        return []
    model = _ensure_model()
    if model is None:
        return None
    try:
        # ``encode`` returns numpy by default. We convert to plain
        # Python lists at the boundary so callers don't need numpy
        # imported for every read.
        vecs = model.encode(list(texts), convert_to_numpy=True, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [list(map(float, v)) for v in vecs]
    except Exception as exc:
        logger.warning(f"[memory] encode_texts failed: {exc}")
        return None


def score_against(query_vec: list[float], candidate_vecs: list[list[float]]) -> list[float]:
    """Cosine similarity for normalized vectors.

    With ``normalize_embeddings=True`` from ``encode_texts`` both sides
    are unit length, so cosine === dot product. Pure python so no
    numpy import is forced on the read path.
    """
    if not candidate_vecs:
        return []
    out: list[float] = []
    q_norm = math.sqrt(sum(x * x for x in query_vec)) or 1.0
    for c in candidate_vecs:
        c_norm = math.sqrt(sum(x * x for x in c)) or 1.0
        # Pre-normalized in encode_texts, so q_norm/c_norm are ~1, but we
        # still divide so this function is correct on un-normalized
        # inputs (e.g. tests passing handcrafted vectors).
        dot = sum(a * b for a, b in zip(query_vec, c))
        out.append(dot / (q_norm * c_norm))
    return out
