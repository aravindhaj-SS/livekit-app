"""Deterministic text extractors + the pre-call in-scope classifier.

No longer runs live, per turn — the realtime model reports what it learns
directly via the save_lead_info tool (voice/gemini_bridge.py). What's here
is used two ways:
  1. classify_in_scope_form() — pre-call gate on the lead's submitted
     interest_area, before the Exotel call is placed (api/routes.py).
  2. The extract_*/detect_* functions — a background reconciliation pass
     run once against the full transcript after a call ends, to fill in
     anything the model's own tool-reporting missed. Cheap insurance, paid
     for after the call instead of on the live turn path.
"""

import json
import logging
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)


def _normalize_for_phrase_match(text: str) -> str:
    if not text:
        return " "
    low = re.sub(r"[,.!?;:]+", " ", text.lower())
    low = re.sub(r"\s+", " ", low).strip()
    return " " + low + " "


# ─────────────────────────────────────────────────────────────────────────────
# Phonetic corrections — STT mis-hears of brand/product names.
# ─────────────────────────────────────────────────────────────────────────────

PHONETIC_CORRECTIONS: dict[str, str] = {
    r"\bswaran\s?softe?\b": "Swaran Soft",
    r"\bswarran\s?soft\b": "Swaran Soft",
    r"\bswarn\s?soft\b": "Swaran Soft",
    r"\bsharon\s?soft\b": "Swaran Soft",
    r"\bswara\s?soft\b": "Swaran Soft",
    r"\bswaransoft\b": "Swaran Soft",
    r"\bhigher\s?floor\b":   "Hireflow",
    r"\bhigher\s?flow\b":    "Hireflow",
    r"\bhire\s?flow\b":      "Hireflow",
    r"\bhireflow\b":         "Hireflow",
    r"\bpm\s?flow\b":        "PM Flow",
    r"\bprepvent\b":         "Prepevent",
    r"\bprep\s?vent\b":      "Prepevent",
    r"\bprep\s?event\b":     "Prepevent",
    r"\bsales\s?flow\b":     "Salesflow",
}


def apply_phonetic_corrections(text: str) -> str:
    for pattern, replacement in PHONETIC_CORRECTIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Email extraction
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_SHAPE_RE = re.compile(r"^[a-z0-9._+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$")
_COMMON_EMAIL_PROVIDERS = (
    "gmail", "yahoo", "outlook", "hotmail", "icloud",
    "live", "rediffmail", "protonmail", "aol",
)
_AT_KNOWN_PROVIDER_RE = re.compile(
    r"\bat\s+(?:" + "|".join(_COMMON_EMAIL_PROVIDERS) + r")\b"
)


def looks_like_spelling(text: str) -> bool:
    low = (text or "").lower()
    if len(re.findall(r"(?<!')\b[a-z0-9]\b", low)) >= 3:
        return True
    if re.search(r"\b(?:dot|underscore|dash|hyphen)\b", low):
        return True
    return bool(_AT_KNOWN_PROVIDER_RE.search(low))


def extract_spelled_email(text: str) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    low = re.sub(r"\bp\.?\s?m\.?\b", "pm", low)
    low = re.sub(r"\ba\.?\s?m\.?\b", "am", low)

    # Try a direct match first (a natural "it's x@y.com" reply, not spelled).
    m = re.search(r"([a-z0-9._+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+)", low)
    if m and _EMAIL_SHAPE_RE.match(m.group(1)):
        return m.group(1)

    if not looks_like_spelling(low):
        return None

    low = re.sub(r"\bat\b", "@", low)
    low = low.replace("@", " @ ")
    low = re.sub(r"\bdot\b", ".", low)
    low = re.sub(r"\bunderscore\b", "_", low)
    low = re.sub(r"\b(?:dash|hyphen|minus)\b", "-", low)
    low = re.sub(r"\bplus\b", "+", low)
    low = re.sub(
        r"(?:\b[a-z0-9]\b\s+){1,}\b[a-z0-9]\b",
        lambda m: m.group(0).replace(" ", ""),
        low,
    )
    low = re.sub(r"\s*([@._+\-])\s*", r"\1", low)
    for provider in _COMMON_EMAIL_PROVIDERS:
        low = re.sub(rf"@{provider}(?!\.[a-z]+)\b", f"@{provider}.com", low)

    m = re.search(r"([a-z0-9._+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+)", low)
    if not m:
        return None
    candidate = m.group(1)
    return candidate if _EMAIL_SHAPE_RE.match(candidate) else None


# ─────────────────────────────────────────────────────────────────────────────
# Slot / time extraction
# ─────────────────────────────────────────────────────────────────────────────

_TIME_FRAGMENT_SPECIFIC_RE = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)"
    r"|\b\d{1,2}\s+o['’]?clock"
    r"|\b\d{1,2}\s+in\s+the\s+(?:morning|afternoon|evening|night)"
    r"|\b(?:noon|midnight)\b"
    r"|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)
_TIME_FRAGMENT_DAYPART_RE = re.compile(
    r"\b(?:morning|afternoon|evening|night|tonight)\b", re.IGNORECASE,
)
_TIME_FRAGMENT_BARE_HOUR_RE = re.compile(
    r"\bat\s+\d{1,2}\b(?!\s*(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|\s*o['’]?clock)",
    re.IGNORECASE,
)
_DAY_FRAGMENT_RE = re.compile(
    r"\bday\s+after\s+tomorrow\b|\btomorrow\b|\btmrw\b|\btmr\b|\btoday\b|\btonight\b"
    r"|\bnext\s+week\b|\b(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_DAYPART_DEFAULT_HOUR = {"morning": 10, "afternoon": 14, "evening": 18, "night": 21, "tonight": 21}
_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _parse_day_fragment(text: str, today: date) -> Optional[date]:
    low = text.lower()
    m = _DAY_FRAGMENT_RE.search(low)
    if not m:
        return None
    frag = m.group(0)
    if "day after tomorrow" in frag:
        return today + timedelta(days=2)
    if "tomorrow" in frag or "tmrw" in frag or "tmr" in frag:
        return today + timedelta(days=1)
    if "today" in frag or "tonight" in frag:
        return today
    if "next week" in frag:
        return today + timedelta(days=7)
    for wd_name, wd_idx in _WEEKDAY_INDEX.items():
        if wd_name in frag:
            days_ahead = (wd_idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead)
    return None


def _parse_time_fragment(
    text: str, daypart_context: Optional[str], fallback_time: Optional[dtime]
) -> Optional[dtime]:
    low = text.lower()
    if "noon" in low:
        return dtime(12, 0)
    if "midnight" in low:
        return dtime(0, 0)

    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", low)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        suffix = m.group(3).replace(".", "")
        if suffix == "pm" and hour != 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        return dtime(hour % 24, minute)

    m = re.search(r"(\d{1,2}):(\d{2})\b", low)
    if m:
        return dtime(int(m.group(1)) % 24, int(m.group(2)))

    m = re.search(r"(\d{1,2})\s+o['’]?clock", low)
    if m:
        hour = int(m.group(1))
        if daypart_context in ("afternoon", "evening", "night", "tonight") and hour < 12:
            hour += 12
        return dtime(hour % 24, 0)

    m = re.search(r"(\d{1,2})\s+in\s+the\s+(morning|afternoon|evening|night)", low)
    if m:
        hour = int(m.group(1))
        part = m.group(2)
        if part in ("afternoon", "evening", "night") and hour < 12:
            hour += 12
        return dtime(hour % 24, 0)

    for part, default_hour in _DAYPART_DEFAULT_HOUR.items():
        if part in low:
            return dtime(default_hour, 0)

    m = _TIME_FRAGMENT_BARE_HOUR_RE.search(low)
    if m:
        hour = int(re.search(r"\d{1,2}", m.group(0)).group(0))
        if daypart_context in ("afternoon", "evening", "night", "tonight") and hour < 12:
            hour += 12
        return dtime(hour % 24, 0)

    if fallback_time is not None:
        return fallback_time
    return None


def extract_preferred_slot(
    text: str, today: date, fallback_time: Optional[dtime] = None
) -> Optional[str]:
    """Specific time beats daypart regardless of word order. Sanity-bounds
    the result to [today, today+30d]. Returns a full ISO 8601 datetime
    string, or None if nothing usable was said."""
    if not text:
        return None
    low = text.lower()

    daypart_match = _TIME_FRAGMENT_DAYPART_RE.search(low)
    daypart_context = daypart_match.group(0) if daypart_match else None

    specific = _TIME_FRAGMENT_SPECIFIC_RE.search(low)
    fragment = specific.group(0) if specific else (daypart_match.group(0) if daypart_match else None)
    if fragment is None:
        return None

    day = _parse_day_fragment(low, today) or today
    if (day - today).days < 0 or (day - today).days > 30:
        return None

    tm = _parse_time_fragment(fragment, daypart_context, fallback_time)
    if tm is None:
        return None

    return datetime(day.year, day.month, day.day, tm.hour, tm.minute, 0).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Budget / timeline / decision-maker extraction
# ─────────────────────────────────────────────────────────────────────────────

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50,
}
_INDIAN_UNIT_MULTIPLIERS = {
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "crore": 1e7, "crores": 1e7,
    "k": 1e3, "thousand": 1e3,
}

_BUDGET_CUE_RE = re.compile(
    r"\b(budget|rupees?|rs\.?|inr|lakhs?|lacs?|crores?|spend|cost|price)\b",
    re.IGNORECASE,
)
_BUDGET_AMOUNT_RE = re.compile(
    r"\b(?:rs\.?|inr|rupees?)?\s*"
    r"(\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS.keys()) + r")\s*"
    r"(lakhs?|lacs?|crores?|k|thousand)?\s*"
    r"(?:rupees?|rs\.?|inr)?",
    re.IGNORECASE,
)
_BUDGET_DECLINE_RE = re.compile(
    r"\b("
    r"no budget|not sure about (?:the |a )?budget|"
    r"(?:don'?t|do not|does'?nt|does not) have a budget|"
    r"no budget in mind|confused about (?:the )?budget|"
    r"haven'?t (?:thought|decided) (?:about|on) (?:a |the )?budget|"
    r"not decided (?:on|about) (?:a |the )?budget|"
    r"can'?t (?:say|share) (?:the |a )?budget|no idea (?:about|on) (?:the |a )?budget|"
    r"no (?:fixed |specific |exact )?(?:number|figure|amount) in mind|"
    r"just exploring|just browsing|just researching"
    r")\b", re.IGNORECASE,
)


def _format_indian_currency(amount: float) -> str:
    n = int(round(amount))
    s = str(n)
    if len(s) <= 3:
        grouped = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        grouped = ",".join(parts) + "," + last3
    return f"Rs.{grouped}"


def extract_budget(text: str) -> Optional[str]:
    if not text:
        return None
    if not _BUDGET_CUE_RE.search(text):
        return "nil" if _BUDGET_DECLINE_RE.search(text) else None
    low = text.lower()
    matches = list(_BUDGET_AMOUNT_RE.finditer(low))
    chosen = next((mm for mm in reversed(matches) if mm.group(2)), None) or (matches[0] if matches else None)
    if chosen:
        raw_num = chosen.group(1)
        unit = chosen.group(2)
        value = _NUMBER_WORDS.get(raw_num)
        if value is None:
            try:
                value = float(raw_num)
            except ValueError:
                value = None
        if value is not None:
            if unit:
                value *= _INDIAN_UNIT_MULTIPLIERS.get(unit.rstrip("s"), _INDIAN_UNIT_MULTIPLIERS.get(unit, 1))
            return _format_indian_currency(value)
    return "nil" if _BUDGET_DECLINE_RE.search(low) else None


_TIMELINE_DECLINE_RE = re.compile(
    r"\b("
    r"no timeline|not sure (?:about|on) (?:the )?timeline|don'?t have a timeline|"
    r"dont have a timeline|no (?:specific |particular )?timeline|"
    r"just exploring|just browsing|just researching|"
    r"haven'?t decided|not decided yet|no rush|no timeframe|no specific timeframe|"
    r"can'?t (?:really )?say when|still early days|not sure yet|"
    r"depends on how|too early to say|no fixed date|not decided on when"
    r")\b", re.IGNORECASE,
)
_TIMELINE_VAGUE_BIMONTHLY_RE = re.compile(
    r"bi.?monthly|every\s+(?:two|2)\s+months?|every\s+other\s+month", re.IGNORECASE,
)
_TIMELINE_VAGUE_MONTHLY_RE = re.compile(
    r"\bmax(?:imum)?\s+monthly\b|\bmonthly\b|every\s+month|per\s+month|each\s+month", re.IGNORECASE,
)


def extract_timeline(text: str) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    if re.search(r"\bquarter\b", low):
        return "3 months"
    if re.search(r"\bhalf(?:\s+a)?\s+year\b", low):
        return "6 months"
    m = re.search(
        r"(\d+|" + "|".join(_NUMBER_WORDS.keys()) + r")\s*(months?|weeks?|years?|days?)",
        low,
    )
    if m:
        raw = m.group(1)
        n = _NUMBER_WORDS.get(raw)
        if n is None:
            try:
                n = int(raw)
            except ValueError:
                n = None
        if n is not None:
            unit = m.group(2).rstrip("s")
            return f"{n} {unit}{'s' if n != 1 else ''}"
    if re.search(r"\bnext\s+month\b", low):
        return "1 month"
    if re.search(r"\b(?:a|next)\s+year\b", low):
        return "12 months"
    if re.search(r"\b(?:a|next|the\s+next)\s+week\b", low):
        return "1 week"
    if _TIMELINE_DECLINE_RE.search(low):
        return "nil"
    if _TIMELINE_VAGUE_BIMONTHLY_RE.search(low):
        return "60 days"
    if _TIMELINE_VAGUE_MONTHLY_RE.search(low):
        return "30 days"
    return None


_DECISION_MAKER_YES_RE = re.compile(
    r"\b("
    r"i(?:'m| am) (?:\w+\s+){0,2}the (?:key |primary |main |sole |final |ultimate |sole |top )?decision.?maker|"
    r"i(?:'d| would|'ll| will)\s+(?:\w+\s+){0,2}be\s+(?:\w+\s+){0,2}the (?:key |primary |main |sole |final |ultimate |top )?decision.?maker|"
    r"i(?:'m| am) the one (?:making|who makes) (?:the |this )?decisions?|"
    r"i (?:make|will make) (?:this|these|that) decisions?|"
    r"i(?:'m| am) (?:the )?(?:ceo|cto|coo|founder|co-founder|owner|head|director|vp|manager|lead)|"
    r"i(?:'ll| will) (?:be )?(?:the one )?deciding|i can decide|"
    r"i(?:'m| am) (?:driving|leading|spearheading|heading up) this(?:\s+initiative|\s+effort|\s+project)?|"
    r"i(?:'m| am) driving (?:this|it) (?:completely|entirely|myself)"
    r")\b", re.IGNORECASE,
)
_DECISION_MAKER_NO_RE = re.compile(
    r"\b("
    r"i(?:'m| am) not the decision.?maker|not my decision|not my call|"
    r"i(?:'ll| will) (?:need to|have to) (?:check|ask|run it by|consult) "
    r"(?:with )?(?:my )?(?:boss|manager|team|superior|management|higher.?ups)|"
    r"i(?:'m| am) not (?:the one )?(?:who|that) decides|"
    r"someone else (?:decides|makes that call)|"
    r"not (?:sure|certain) (?:if|whether) i(?:'m| am) the (?:right )?person"
    r")\b", re.IGNORECASE,
)
_DECISION_MAKER_APPROVAL_CHAIN_RE = re.compile(
    r"(?:need|needs|require|requires|has to|have to|got to)\s+"
    r"(?:take |get |seek |obtain )?approval\s+(?:of|from)\s+(?:my |our )?"
    r"(?:seniors?|management|boss|manager|superiors?|higher.?ups|team|leadership)|"
    r"multiple\s+levels?\s+of\s+approvals?|series\s+of\s+approvals?|"
    r"(?:several|multiple)\s+approvals?|chain\s+of\s+approvals?|"
    r"(?:levels?|rounds?)\s+of\s+approvals?", re.IGNORECASE,
)
_DECISION_MAKER_NO_NEGATED_APPROVAL_RE = re.compile(
    r"(?:don'?t|do not|doesn'?t|does not|no|never|without)\s+"
    r"(?:really\s+|actually\s+)?(?:need|require)s?\s+(?:any\s+)?"
    r"(?:take |get |seek |obtain )?approval", re.IGNORECASE,
)
_DECISION_MAKER_AMBIGUOUS_RE = re.compile(
    r"\b(just exploring|just researching|just looking around|just browsing|"
    r"not sure|i don'?t know|dont know|no idea)\b", re.IGNORECASE,
)


def extract_decision_maker_status(text: str) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    if _DECISION_MAKER_NO_RE.search(low):
        return "not decision maker"
    if _DECISION_MAKER_APPROVAL_CHAIN_RE.search(low) and not _DECISION_MAKER_NO_NEGATED_APPROVAL_RE.search(low):
        return "not decision maker"
    if _DECISION_MAKER_YES_RE.search(low):
        return "decision maker"
    if _DECISION_MAKER_AMBIGUOUS_RE.search(low):
        return "nil"
    return None


_DISCOVERY_REQUEST_PHRASES = (
    "exploratory call", "discovery call", "demo",
    "want a call", "want a meeting", "want a demo",
    "would like a call", "would like a meeting", "would like a demo",
    "set up a call", "set up a meeting",
    "schedule a call", "schedule a meeting",
    "book a call", "book a meeting",
    "have a call", "having a call", "having an exploratory", "having a discovery",
    "another call", "follow-up call", "follow up call",
    "call with your team", "meeting with your team",
    "let's schedule", "lets schedule", "let us schedule",
    "let's set up", "lets set up",
)


def detect_discovery_request(text: str) -> bool:
    low = _normalize_for_phrase_match(text)
    return any(p in low for p in _DISCOVERY_REQUEST_PHRASES)


def reconcile_transcript(transcript: list[dict]) -> dict:
    """Background, post-call safety net: re-run the deterministic extractors
    over every user turn in the full transcript, returning whichever fields
    they can confirm. Used to fill gaps in what the model itself reported
    live via the save_lead_info tool — never overrides a value the model
    already gave, only fills in what's still missing."""
    found: dict = {"budget": None, "timeline": None, "decision_maker_status": None, "email_id": None}
    for turn in transcript:
        if turn.get("role") != "user":
            continue
        text = apply_phonetic_corrections(turn.get("content") or "")
        if found["budget"] is None:
            found["budget"] = extract_budget(text)
        if found["timeline"] is None:
            found["timeline"] = extract_timeline(text)
        if found["decision_maker_status"] is None:
            found["decision_maker_status"] = extract_decision_maker_status(text)
        if found["email_id"] is None:
            found["email_id"] = extract_spelled_email(text)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Pre-call in-scope classifier — runs BEFORE the Exotel call is placed, on the
# lead's submitted interest_area. Uses a plain (non-Live) Gemini text call,
# since this is a one-shot pre-call gate, not part of the realtime session.
# ─────────────────────────────────────────────────────────────────────────────

_IN_SCOPE_SYSTEM_PROMPT = """You are a strict but lenient inquiry-intake classifier for Swaran Soft.

Swaran Soft delivers: software development, AI / ML solutions, web development, hiring automation (resume screening, candidate scheduling), voice agents, sales / lead-qualification automation, internal tooling, dashboards, API integrations, CRM automation, and adjacent enterprise SaaS work.

Classify the lead's stated INTEREST as either IN_SCOPE (genuine business inquiry that Swaran Soft could plausibly help with) or OUT_OF_SCOPE (clearly unrelated personal request, prank, nonsense, or service we don't offer).

OUT_OF_SCOPE examples:
- "I want to buy a car"
- "Find me a girlfriend / boyfriend / partner"
- "Do my homework"
- "Order me food"
- "Fix my plumbing / electrical / leaking tap"
- "Medical advice"
- "Legal advice / divorce help"
- "Help me write a love letter"
- Random gibberish, single emoji, single profanity, or empty/joke text

IN_SCOPE examples (be generous — many businesses have unusual phrasings):
- "AI for my car wash business" (automation, IN scope)
- "We're a plumbing company that wants to digitize bookings" (IN scope)
- "Hiring software for a girlfriend-matching startup" (still IN scope — the lead is a B2B customer)
- "Voice agent for restaurant orders" (IN scope)
- "Build me a CRM"
- "Web app for my law firm" (IN scope — software for a law firm, NOT legal advice)
- "Lead qualification automation"

When uncertain, choose IN_SCOPE — the in-call agent will catch true pranks at conversation time. Only reject when the request is unambiguously personal, frivolous, or unrelated to enterprise software/automation/AI.

Output JSON exactly: {"in_scope": <bool>, "reason": "<one short sentence>"}"""

_IN_SCOPE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "in_scope": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["in_scope", "reason"],
}


async def classify_in_scope_form(interest_area: str) -> tuple[bool, str]:
    """Pre-call form-submission filter. FAILS OPEN on any error (network,
    timeout, malformed JSON, rate limit) — returns (True, ...) so a
    transient API blip never blocks a legitimate lead."""
    text = (interest_area or "").strip()
    if not text:
        return False, "empty interest_area"
    try:
        from google import genai as google_genai

        client = google_genai.Client(api_key=settings.GEMINI_API_KEY)
        resp = await client.aio.models.generate_content(
            model="gemini-flash-latest",
            contents=f"Interest: {text!r}",
            config={
                "system_instruction": _IN_SCOPE_SYSTEM_PROMPT,
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "response_schema": _IN_SCOPE_RESPONSE_SCHEMA,
                "thinking_config": {"thinking_budget": 0},
            },
        )
        raw = resp.text or ""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        in_scope = bool(data.get("in_scope", True))
        reason = data.get("reason") or "no reason given"
        logger.info(f"Pre-call in-scope classify: interest={text!r} -> in_scope={in_scope}, reason={reason!r}")
        return in_scope, reason
    except Exception as e:
        logger.warning(f"Pre-call classifier failed for interest={text!r} ({e!r}); failing open")
        return True, f"classifier unavailable ({type(e).__name__})"


# ─────────────────────────────────────────────────────────────────────────────
# Post-call summarizer — returning-caller support (voice/gemini_bridge.py's
# _end()). Runs once, after the call/WS is already closed, same reasoning as
# book_discovery_call running after close: a real network round trip that
# must never delay hanging up on the caller. Fails open (returns None) so a
# summarization hiccup never blocks the rest of call-end persistence.
# ─────────────────────────────────────────────────────────────────────────────

_CALL_SUMMARY_SYSTEM_PROMPT = """Summarize this sales/support call transcript in ONE short sentence (max ~30 words), written for a teammate picking up the SAME caller's next call. Focus on: what they were interested in, key facts learned (budget/timeline/decision-maker if mentioned), and the outcome (booked a call, asked for callback, still deciding, etc). Plain factual language, no fluff, third person ("They..."/"Caller...").

Output JSON exactly: {"summary": "<one sentence>"}"""

_CALL_SUMMARY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

# Bounds the transcript sent to the summarizer — this is a one-line summary,
# not a transcription task, and a very long call doesn't need the whole
# thing to produce one.
_SUMMARY_TRANSCRIPT_CHAR_LIMIT = 8000


async def summarize_call(transcript: list[dict]) -> Optional[str]:
    """One-line, cheap-LLM post-call summary — grounds a returning caller's
    next conversation ("last time we spoke about...") in real content
    instead of just the structured budget/timeline/decision_maker_status
    columns already captured elsewhere."""
    convo = "\n".join(
        f"{t['role']}: {t['content']}" for t in transcript if t.get("content")
    ).strip()
    if not convo:
        return None
    try:
        from google import genai as google_genai

        client = google_genai.Client(api_key=settings.GEMINI_API_KEY)
        resp = await client.aio.models.generate_content(
            model="gemini-flash-latest",
            contents=convo[-_SUMMARY_TRANSCRIPT_CHAR_LIMIT:],
            config={
                "system_instruction": _CALL_SUMMARY_SYSTEM_PROMPT,
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "response_schema": _CALL_SUMMARY_RESPONSE_SCHEMA,
                "thinking_config": {"thinking_budget": 0},
            },
        )
        raw = resp.text or ""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        summary = (data.get("summary") or "").strip()
        return summary or None
    except Exception as e:
        logger.warning(f"summarize_call failed ({e!r}); leaving call_summary unset")
        return None
