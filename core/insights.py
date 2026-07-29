"""AI Insights panel for the master dashboard — a small local model PHRASES
pre-computed facts into short natural-language lines; it never computes or
estimates a number itself (avoids a hallucinated figure ending up on the
dashboard next to real ones).

Model: llama3.1:8b, served by Mira's OWN dedicated Ollama instance (see
deploy/ollama-mira.service), kept warm indefinitely (OLLAMA_KEEP_ALIVE=-1) —
NOT the machine's shared ollama.service. That shared instance runs a
different, latency-critical single-stream voice agent and is tuned
OLLAMA_NUM_PARALLEL=1 globally (one request at a time, across every model on
it); routing this dashboard's traffic through it would contend for that one
slot and could stall someone else's live call. See core/ollama_client.py for
the isolated-instance rationale and API details.
"""
import json
import logging
import time

from core.ollama_client import chat_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You write short insight lines for a voice AI sales agent's ops dashboard. "
    "You are given pre-computed facts as a JSON object — phrase them naturally, "
    "using ONLY the numbers given, never invent, estimate, or round differently "
    "than what's provided. Skip any fact that is null or missing rather than "
    "guessing at it or mentioning its absence. Prefer the most notable 2-4 facts "
    "(biggest changes, clearest signals) over covering everything.\n\n"
    'Output JSON exactly: {"insights": ["<short factual sentence>", ...]} — '
    "2 to 4 sentences, each under 100 characters, no fluff, no repeated numbers "
    "across sentences, third person, plain business language."
)

# Cached by exact input — the underlying data changes at most once per call
# ending, so there's no reason to re-spend a model call every dashboard
# auto-refresh (30s) when the facts haven't moved.
_CACHE_TTL_S = 300.0
_cache: dict = {"data": None, "at": 0.0, "facts_key": None}


def _facts_key(facts: dict) -> str:
    return json.dumps(facts, sort_keys=True)


async def generate_insights(facts: dict) -> list[str]:
    now = time.monotonic()
    key = _facts_key(facts)
    if _cache["data"] is not None and key == _cache["facts_key"] and (now - _cache["at"]) < _CACHE_TTL_S:
        return _cache["data"]

    try:
        data = await chat_json(_SYSTEM_PROMPT, json.dumps(facts), max_tokens=300, temperature=0.3)
        insights = [s.strip() for s in (data.get("insights") or []) if isinstance(s, str) and s.strip()][:4]
        _cache.update(data=insights, at=now, facts_key=key)
        return insights
    except Exception as e:
        logger.warning(f"generate_insights failed ({e!r}); returning last-known/empty")
        return _cache["data"] or []
