"""
Embedding Provider Abstraction
==============================
Generates vector embeddings for memory content and query text.

Current impl: Voyage AI (async). The `EmbeddingProvider` protocol lets us swap
to a local model (sentence-transformers / BGE) later without touching callers.

All failures are swallowed and return None — embeddings are a ranking signal,
not a correctness requirement. Callers should fall back to importance-ordered
retrieval when embeddings are unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, Optional, Sequence

from app.config import get_settings

logger = logging.getLogger(__name__)


# Cosine similarity helper — used by retrieval code; kept here so it lives
# next to the vector format it operates on.
def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity for two equal-length float sequences. Returns 0.0 on shape mismatch."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


class EmbeddingProvider(Protocol):
    """Protocol every embedding backend must satisfy."""

    model_name: str
    dimensions: int

    async def embed(self, text: str) -> Optional[list[float]]:
        """Embed a single string. Returns None on failure."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Embed a batch of strings. Failures return None at the matching index."""
        ...


class VoyageProvider:
    """
    Voyage AI embedding provider.

    Models (dims):
      - voyage-3-lite   → 512  (default; cheapest, fine for retrieval)
      - voyage-3        → 1024
      - voyage-3-large  → 1024
    """

    _DIMS = {
        "voyage-3-lite": 512,
        "voyage-3": 1024,
        "voyage-3-large": 1024,
    }

    def __init__(self, api_key: str, model: str = "voyage-3-lite"):
        self.model_name = model
        self.dimensions = self._DIMS.get(model, 512)
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import voyageai
                self._client = voyageai.AsyncClient(api_key=self._api_key)
            except ImportError:
                logger.error("voyageai package not installed; embeddings disabled")
                return None
            except Exception as e:
                logger.error(f"Voyage client init failed: {e}")
                return None
        return self._client

    async def embed(self, text: str) -> Optional[list[float]]:
        if not text or not text.strip():
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            # input_type="document" for stored memories, "query" for search — we
            # default to "document" here and expose embed_query() for callers
            # that want to bias a query vector appropriately.
            result = await asyncio.wait_for(
                client.embed([text], model=self.model_name, input_type="document"),
                timeout=10.0,
            )
            if result and result.embeddings:
                return list(result.embeddings[0])
        except asyncio.TimeoutError:
            logger.warning(f"Voyage embed timed out for model {self.model_name}")
        except Exception as e:
            logger.warning(f"Voyage embed failed: {e}")
        return None

    async def embed_query(self, text: str) -> Optional[list[float]]:
        """Embed with input_type='query' — Voyage recommends this for search queries."""
        if not text or not text.strip():
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            result = await asyncio.wait_for(
                client.embed([text], model=self.model_name, input_type="query"),
                timeout=10.0,
            )
            if result and result.embeddings:
                return list(result.embeddings[0])
        except asyncio.TimeoutError:
            logger.warning("Voyage embed_query timed out")
        except Exception as e:
            logger.warning(f"Voyage embed_query failed: {e}")
        return None

    async def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        clean = [t for t in texts if t and t.strip()]
        if not clean:
            return [None] * len(texts)
        client = self._get_client()
        if client is None:
            return [None] * len(texts)
        try:
            result = await asyncio.wait_for(
                client.embed(clean, model=self.model_name, input_type="document"),
                timeout=30.0,
            )
            embeds = list(result.embeddings) if result and result.embeddings else []
            # Map back onto original positions (some may have been blank).
            out: list[Optional[list[float]]] = []
            ei = 0
            for t in texts:
                if t and t.strip() and ei < len(embeds):
                    out.append(list(embeds[ei]))
                    ei += 1
                else:
                    out.append(None)
            return out
        except asyncio.TimeoutError:
            logger.warning("Voyage embed_batch timed out")
        except Exception as e:
            logger.warning(f"Voyage embed_batch failed: {e}")
        return [None] * len(texts)


_provider_cache: Optional[EmbeddingProvider] = None


def get_embedding_provider() -> Optional[EmbeddingProvider]:
    """
    Factory. Returns None if embeddings are disabled or unconfigured —
    callers must handle that.
    """
    global _provider_cache
    if _provider_cache is not None:
        return _provider_cache

    settings = get_settings()
    if not settings.embedding_enabled:
        return None
    if not settings.voyage_api_key:
        logger.info("VOYAGE_API_KEY not set — semantic memory retrieval disabled")
        return None

    _provider_cache = VoyageProvider(
        api_key=settings.voyage_api_key,
        model=settings.embedding_model,
    )
    return _provider_cache
