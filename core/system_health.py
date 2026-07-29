"""Live reachability + latency checks for this app's actual runtime
dependencies — feeds the master dashboard's System Health panel. Each check
is short-timeout and never raises; a failed/slow dependency reports itself
as "down" rather than taking the dashboard request down with it.

Deliberately does NOT include an Exotel liveness check: this app only ever
calls Exotel's Calls/connect endpoint (api/routes.py's /api/callback) and
has no confirmed, documented lightweight "is the account reachable" GET to
poll instead — guessing at one against a live, billed third-party API risked
either a false "down" reading or unexpected noise on Exotel's side, so it's
left out rather than faked. "LiveKit"/"Redis" don't apply at all — this app
has no real LiveKit server/Redis (see CLAUDE.md); the four checks below are
this app's actual dependencies (Postgres, the local RAG service, Google
Calendar's OAuth token, OpenAI's API).
"""
import asyncio
import logging
import pickle
import time
from pathlib import Path
from typing import Optional

import httpx

from core import database
from core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_S = 3.0
_TOKEN_PATH = Path("token.pkl")


async def _timed(coro) -> tuple[bool, Optional[int]]:
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(coro, timeout=_TIMEOUT_S)
        return True, round((time.monotonic() - t0) * 1000)
    except Exception as e:
        logger.warning(f"system_health check failed: {e!r}")
        return False, None


async def _check_postgres() -> dict:
    ok, ms = await _timed(database.ping())
    return {"name": "Postgres", "status": "operational" if ok else "down", "latency_ms": ms}


async def _check_rag() -> dict:
    async def _connect():
        reader, writer = await asyncio.open_connection("localhost", settings.RAG_PORT)
        writer.close()
        await writer.wait_closed()
    ok, ms = await _timed(_connect())
    return {"name": "RAG service", "status": "operational" if ok else "down", "latency_ms": ms}


async def _check_calendar() -> dict:
    """No live Calendar API call here (would spend real OAuth-quota on every
    dashboard tick) — checks that token.pkl exists and isn't a dead,
    unrefreshable credential, which is the actual failure mode that's bitten
    this app before (core/calendar.py's own "run scripts/auth_setup.py"
    warning path)."""
    t0 = time.monotonic()
    if not _TOKEN_PATH.exists():
        return {"name": "Google Calendar", "status": "down", "latency_ms": None}
    try:
        with open(_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
        ok = (not creds.expired) or bool(creds.refresh_token)
        return {
            "name": "Google Calendar",
            "status": "operational" if ok else "down",
            "latency_ms": round((time.monotonic() - t0) * 1000),
        }
    except Exception as e:
        logger.warning(f"Calendar token check failed: {e!r}")
        return {"name": "Google Calendar", "status": "down", "latency_ms": None}


async def _check_openai() -> dict:
    async def _ping():
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {settings.OPEN_AI_API_KEY}"},
            )
            r.raise_for_status()
    ok, ms = await _timed(_ping())
    return {"name": "OpenAI API", "status": "operational" if ok else "down", "latency_ms": ms}


async def _check_ollama_mira() -> dict:
    """Mira's own dedicated Ollama instance (deploy/ollama-mira.service —
    see core/ollama_client.py), backing the AI Insights panel and the Leads
    page's per-lead analysis. Deliberately NOT the machine's shared
    ollama.service — a ping here says nothing about that instance's health
    and shouldn't, they're independent processes on different ports."""
    async def _ping():
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            r = await client.get(f"{settings.OLLAMA_MIRA_HOST.rstrip('/')}/api/tags")
            r.raise_for_status()
    ok, ms = await _timed(_ping())
    return {"name": "Local insights model", "status": "operational" if ok else "down", "latency_ms": ms}


_cache: dict = {"data": None, "at": 0.0}
_CACHE_TTL_S = 20.0


async def get_system_health() -> list[dict]:
    """Cached briefly so the dashboard's own auto-refresh doesn't re-hit
    OpenAI's API (or spam the RAG service) every single tick."""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["at"]) < _CACHE_TTL_S:
        return _cache["data"]
    results = await asyncio.gather(
        _check_postgres(), _check_rag(), _check_calendar(), _check_openai(), _check_ollama_mira(),
    )
    _cache["data"] = list(results)
    _cache["at"] = now
    return _cache["data"]
