"""In-process tracker for calls currently in progress. Nothing like this
existed before this feature — every CallSession (voice/gemini_bridge.py) was
a throwaway local object with no shared registry, so "how many calls are
live right now" was unanswerable anywhere in the app.

Single-process app (one uvicorn worker — see deploy/livekit-app.service),
so a plain module-level counter is enough; no Redis or cross-process
coordination needed. Feeds the master dashboard's Live Calls / Concurrent
Calls stat cards (see api/routes.py's /api/live-calls).
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

_active = 0
# "Maximum today" resets at the IST calendar-day boundary rather than
# tracking an all-time peak — matches what a dashboard viewer actually means
# by "today's peak concurrency".
_peak_today = 0
_peak_date = None


def _today_ist():
    return datetime.now(_IST).date()


def call_started() -> None:
    global _active, _peak_today, _peak_date
    _active += 1
    today = _today_ist()
    if _peak_date != today:
        _peak_date = today
        _peak_today = _active
    else:
        _peak_today = max(_peak_today, _active)


def call_ended() -> None:
    global _active
    _active = max(0, _active - 1)


def snapshot() -> dict:
    """{"active": N, "peak_today": M} — peak_today reports the current
    active count (not a stale prior day's peak) if no call has started yet
    today, so a fresh day never shows yesterday's number."""
    today = _today_ist()
    peak = _peak_today if _peak_date == today else _active
    return {"active": _active, "peak_today": peak}
