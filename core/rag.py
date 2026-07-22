"""Swaran Soft RAG client — stateless local service contract.

Endpoint: POST http://localhost:{RAG_PORT}/ask
Request body:
    {
      "query": str,
      "filters": {"industry": str | null, "solution_type": str | null},
      "excluded_chunk_ids": [str, ...]
    }
Response body:
    {
      "chunks": [str, ...],
      "chunk_ids": [str, ...],
      "graded": bool,
      "fallback": bool
    }

The RAG service is stateless — the call agent owns the per-session
excluded_chunk_ids list and sends previously-seen chunk ids with every
request so the service can deduplicate.

ask() returns (knowledge_context_string, chunk_ids_list). On ANY failure
(timeout, 5xx, parse error, network) it returns ("", []) so the agent falls
through to a deferral rather than hanging the conversation.
"""
import asyncio
import logging
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# Hard cap on a single RAG round-trip, wrapped via asyncio.wait_for so even a
# misbehaving server can't stall the turn. 3.0s keeps knowledge turns
# grounded without risking the LLM falling back to its own (possibly
# hallucinated) prior on a merely-slow retrieval.
_TIMEOUT_S = 3.0

# Process-lifetime persistent client — single connection pool reused across
# all sessions/turns.
_client: Optional[httpx.AsyncClient] = None


def _url() -> str:
    return f"http://localhost:{settings.RAG_PORT}/ask"


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=3.0),
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300.0),
        )
    return _client


async def ask(
    query: str,
    filters: Optional[dict] = None,
    excluded_chunk_ids: Optional[list[str]] = None,
) -> tuple[str, list[str]]:
    """Fetch retrieval chunks for the user query.

    Returns (knowledge_context, chunk_ids) — knowledge_context is the
    concatenated chunk text passed to the model as authoritative product
    info; chunk_ids is the list of ids the caller appends to its
    session-scoped excluded_chunk_ids so the same chunks aren't re-returned
    later in the conversation. Both empty on any failure.
    """
    if not query or not query.strip():
        return "", []
    body = {
        "query": query.strip(),
        "filters": filters or {"industry": None, "solution_type": None},
        "excluded_chunk_ids": excluded_chunk_ids or [],
    }
    try:
        r = await asyncio.wait_for(
            _get_client().post(_url(), json=body),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("RAG timeout after %.1fs", _TIMEOUT_S)
        return "", []
    except Exception as e:
        logger.warning(f"RAG error: {e!r}")
        return "", []

    if r.status_code != 200:
        logger.warning(f"RAG {r.status_code}: {r.text[:200]}")
        return "", []

    try:
        data = r.json()
    except Exception as e:
        logger.warning(f"RAG response parse failed: {e!r}; raw={r.text[:200]}")
        return "", []

    chunks = data.get("chunks") or []
    chunk_ids = data.get("chunk_ids") or []
    fallback = bool(data.get("fallback"))

    knowledge_context = " ".join(c for c in chunks if isinstance(c, str)) if chunks else ""
    if fallback:
        chunk_ids = []
    chunk_ids = [c for c in chunk_ids if isinstance(c, str)]
    return knowledge_context, chunk_ids


async def aclose() -> None:
    """Close the persistent client. Called once at app shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as e:
            logger.warning(f"RAG client close failed: {e}")
        _client = None
