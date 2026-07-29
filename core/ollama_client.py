"""Thin client for Mira's OWN dedicated Ollama instance (deploy/ollama-mira.
service) — deliberately NOT the machine's shared ollama.service (port 11434).

That shared instance runs a different, latency-critical single-stream voice
agent and is tuned OLLAMA_NUM_PARALLEL=1 GLOBALLY — one request at a time,
across every model on it, forever (see its own
/etc/systemd/system/ollama.service.d/voice-agent.conf, which isn't part of
this repo). Routing Mira's dashboard-insight traffic through that instance
would contend for its one inference slot and could stall someone else's live
phone call. This module instead talks to a second, fully isolated `ollama
serve` process that exists only for this app (deploy/ollama-mira.service),
so there's no shared-resource risk in either direction.

Talks to Ollama's NATIVE /api/chat endpoint, not its OpenAI-compatible shim —
confirmed (via the other project's own tuning notes, carried into this
module's design) that the OpenAI-compat endpoint silently drops `keep_alive`
from the request body. The native endpoint honors it per-request, used here
as a belt-and-suspenders on top of the dedicated instance's own
OLLAMA_KEEP_ALIVE=-1 (its primary always-warm mechanism, safe to set
service-wide since nothing else shares this instance).
"""
import json
import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# Generous — covers a cold model-load (observed ~60s on this hardware for an
# unrelated model of comparable size) without failing a request that would
# otherwise succeed once warm. Every call after the first one lands in
# low-single-digit seconds since OLLAMA_KEEP_ALIVE=-1 never lets it unload.
_TIMEOUT_S = 120.0


async def chat_json(system_prompt: str, user_content: str, max_tokens: int = 500, temperature: float = 0.3) -> dict:
    """POSTs one turn to Mira's dedicated Ollama instance in JSON mode and
    returns the parsed object. Raises on any failure (connection refused,
    timeout, non-2xx, invalid JSON) — callers already have their own
    fail-open/cache-fallback logic (see core/insights.py, core/
    lead_insights.py), same contract the OpenAI path they replace had."""
    url = f"{settings.OLLAMA_MIRA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": settings.OLLAMA_MIRA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "format": "json",
        "stream": False,
        "keep_alive": -1,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    raw = (data.get("message") or {}).get("content") or ""
    return json.loads(raw)
