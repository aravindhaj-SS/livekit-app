"""AI-generated per-lead insight panel for the Leads page
(/dashboard/leads) — analyzes ONE contact's full call history (every call
from the same phone number, transcripts included) and produces a
business-facing read: what they're interested in, what they've asked about
across calls, their intent, and how likely they are to convert.

Same "model phrases, never computes" split as core/insights.py: every number
that can be derived deterministically (call counts, best-ever classification,
meeting date, reschedule count, cost) is computed in plain Python and handed
to the model as fact, never left for it to infer — a figure on this page can
never be a hallucination. Same local model (llama3.1:8b) and same dedicated,
always-warm Ollama instance as core/insights.py — see core/ollama_client.py
for why this is a separate instance from the machine's shared ollama.service.
"""
import json
import logging
import time
from typing import Optional

from core.ollama_client import chat_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You analyze one sales lead's full call history for a voice AI sales "
    "agent's ops dashboard. You are given the lead's structured facts and "
    "every call's transcript, oldest first. Write a concise, business-facing "
    "read using ONLY what's in the facts/transcripts — never invent a name, "
    "number, date, or budget that isn't present in the input, and never "
    "restate the facts object back verbatim.\n\n"
    "Output JSON exactly:\n"
    "{\n"
    '  "summary": "<2-3 sentence overview of who they are and where things stand>",\n'
    '  "enquiries": ["<short phrase — one specific thing they asked about or expressed interest in, across any call>", ...],\n'
    '  "intent": "<one short phrase — what they seem to want, e.g. \'evaluating for a Q2 rollout\'>",\n'
    '  "interest_level": "<one of exactly: High, Medium, Low>",\n'
    '  "conversion_potential": "<one of exactly: High, Medium, Low>",\n'
    '  "conversion_reason": "<one sentence — why you scored conversion_potential that way>",\n'
    '  "notes": ["<short phrase — any other detail useful to a salesperson reviewing this lead>", ...]\n'
    "}\n"
    "enquiries and notes: 1-5 items each, each under 80 characters, omit the "
    "key's content (empty list) rather than padding with filler if nothing's notable."
)

# Cached per contact (keyed by phone) until that contact's own call data
# changes — a fixed wall-clock TTL alone would either re-spend a model call
# on every dashboard open with nothing new to say, or show stale analysis
# after a fresh call just came in; keying the cache on a hash of each call's
# (id, updated_at) pair means it only recomputes when there's actually
# something new to analyze.
_CACHE_TTL_S = 3600.0
_cache: dict[str, dict] = {}

_CLASSIFICATION_RANK = {"Hot": 3, "Warm": 2, "Cold": 1}

_EMPTY_INSIGHT = {
    "summary": None, "enquiries": [], "intent": None, "interest_level": None,
    "conversion_potential": None, "conversion_reason": None, "notes": [], "generated": False,
}


def _facts_key(calls: list[dict]) -> str:
    sig = sorted((c.get("id"), c.get("updated_at")) for c in calls)
    return json.dumps(sig)


def _reschedule_count(calls: list[dict]) -> int:
    """Number of extra call rows sharing the same calendar_event_id, beyond
    the first. A reschedule (core/calendar.reschedule_discovery_call) always
    carries the ORIGINAL event's id forward onto the new call's row rather
    than minting a fresh one (see voice/gemini_bridge.py's booking sequence
    and core/database.save_meeting_link) specifically so this is countable —
    two or more rows on the same event_id IS that meeting having been moved,
    not a guess or an AI estimate."""
    groups: dict[str, int] = {}
    for c in calls:
        eid = c.get("calendar_event_id")
        if eid:
            groups[eid] = groups.get(eid, 0) + 1
    return sum(n - 1 for n in groups.values() if n > 1)


def _meeting_date_for(calls: list[dict]) -> Optional[str]:
    """preferred_slot only lives inside lead_state (not its own DB/API
    column — see core/analytics.list_meetings doing the same lookup), so it
    has to be read from whichever call actually holds the current booking.
    Picks the most-recently-updated call that both has a meeting link AND a
    slot, matching _distinct_meetings' own "latest wins" rule for a
    reschedule chain."""
    best_dt, best_updated = None, ""
    for c in calls:
        if not c.get("discovery_call_scheduled"):
            continue
        slot = (c.get("lead_state") or {}).get("preferred_slot")
        if not slot:
            continue
        updated = c.get("updated_at") or c.get("created_at") or ""
        if updated >= best_updated:
            best_updated, best_dt = updated, slot
    return best_dt


def compute_lead_facts(calls: list[dict]) -> dict:
    """Every deterministic figure this page shows — no AI involved. calls is
    one contact's full call list, shaped like /api/calls' response."""
    # Most-recent-first — matches dashboard-common.js's groupCallsByContact
    # exactly, so "latest known X" below is genuinely the latest call that
    # HAS that value (first non-null hit walking newest-to-oldest), not
    # blindly the latest call overall, and never disagrees with the contact
    # object the frontend already built from this same call list.
    calls_by_recency = sorted(calls, key=lambda c: c.get("created_at") or "", reverse=True)
    best_classification, best_rank = None, 0
    ever_booked, meeting_link = False, None
    total_cost = 0.0
    best_budget = best_timeline = best_dm = best_interest = None
    for c in calls_by_recency:
        rank = _CLASSIFICATION_RANK.get(c.get("classification"), 0)
        if rank > best_rank:
            best_rank, best_classification = rank, c.get("classification")
        if c.get("discovery_call_scheduled"):
            ever_booked = True
            meeting_link = meeting_link or c.get("meeting_link")
        total_cost += c.get("total_cost_inr") or 0
        best_budget = best_budget or c.get("budget")
        best_timeline = best_timeline or c.get("timeline")
        best_dm = best_dm or c.get("decision_maker_status")
        best_interest = best_interest or c.get("interest_area")

    return {
        "total_calls": len(calls),
        "inbound_calls": sum(1 for c in calls if c.get("direction") == "inbound"),
        "outbound_calls": sum(1 for c in calls if c.get("direction") == "outbound"),
        "best_classification": best_classification or "Uncategorized",
        "ever_booked_meeting": ever_booked,
        "meeting_link": meeting_link,
        "meeting_date": _meeting_date_for(calls),
        "reschedule_count": _reschedule_count(calls),
        "total_cost_inr": round(total_cost, 2),
        "budget": best_budget,
        "timeline": best_timeline,
        "decision_maker_status": best_dm,
        "interest_area": best_interest,
        "first_contact_at": calls_by_recency[-1].get("created_at") if calls_by_recency else None,
        "last_contact_at": calls_by_recency[0].get("created_at") if calls_by_recency else None,
    }


def _transcript_payload(calls: list[dict]) -> list[dict]:
    calls_sorted = sorted(calls, key=lambda c: c.get("created_at") or "")
    return [
        {
            "call_at": c.get("created_at"),
            "direction": c.get("direction"),
            "interest_area": c.get("interest_area"),
            "budget": c.get("budget"),
            "timeline": c.get("timeline"),
            "decision_maker_status": c.get("decision_maker_status"),
            # Capped — keeps token cost bounded on an unusually long call
            # without needing a separate summarization pass first.
            "transcript": [
                {"role": m.get("role"), "content": m.get("content")}
                for m in (c.get("transcript") or [])
            ][:60],
        }
        for c in calls_sorted
    ]


async def generate_lead_insight(calls: list[dict]) -> dict:
    """calls: every call row for ONE contact (same phone number), shaped like
    /api/calls' response. Returns deterministic facts (compute_lead_facts)
    plus an AI-written read — cached per contact until its own call data
    changes."""
    if not calls:
        return {"facts": {}, **_EMPTY_INSIGHT}

    facts = compute_lead_facts(calls)
    phone_key = calls[0].get("normalized_phone") or calls[0].get("phone_number") or "unknown"
    cache_key = _facts_key(calls)
    now = time.monotonic()
    cached = _cache.get(phone_key)
    if cached and cached.get("facts_key") == cache_key and (now - cached["at"]) < _CACHE_TTL_S:
        return cached["data"]

    try:
        user_content = json.dumps({"facts": facts, "calls": _transcript_payload(calls)})
        data = await chat_json(_SYSTEM_PROMPT, user_content, max_tokens=500, temperature=0.3)
        level = {"High", "Medium", "Low"}
        result = {
            "facts": facts,
            "summary": (data.get("summary") or "").strip() or None,
            "enquiries": [s.strip() for s in (data.get("enquiries") or []) if isinstance(s, str) and s.strip()][:5],
            "intent": (data.get("intent") or "").strip() or None,
            "interest_level": data.get("interest_level") if data.get("interest_level") in level else None,
            "conversion_potential": data.get("conversion_potential") if data.get("conversion_potential") in level else None,
            "conversion_reason": (data.get("conversion_reason") or "").strip() or None,
            "notes": [s.strip() for s in (data.get("notes") or []) if isinstance(s, str) and s.strip()][:5],
            "generated": True,
        }
    except Exception as e:
        logger.warning(f"generate_lead_insight failed ({e!r}); returning facts-only")
        result = {"facts": facts, **_EMPTY_INSIGHT}

    _cache[phone_key] = {"data": result, "at": now, "facts_key": cache_key}
    return result
