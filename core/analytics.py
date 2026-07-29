"""Cross-call aggregation for the master analytics dashboard (api/routes.py's
/api/dashboard/stats) and the meetings page (/api/meetings). Both read
straight off core/database.get_all_leads() — no separate rollup table, no
scheduled job. This app's call volume (single business, not high-traffic
SaaS) is small enough that recomputing these in Python over the full row set
on each request is simpler and easier to keep correct than hand-rolled SQL
aggregation, and it avoids duplicating the lead_state JSON-parsing that's
already needed for the handful of per-call fields (blended_cost_per_min_inr,
avg_rag_latency_s, preferred_slot) that only live inside that column, not as
their own top-level DB columns.
"""
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_UTC = ZoneInfo("UTC")


def _parse_lead_state(row: dict) -> dict:
    try:
        return json.loads(row["lead_state"]) if row.get("lead_state") else {}
    except (TypeError, ValueError):
        return {}


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_ist(naive_utc: datetime) -> datetime:
    """created_at/updated_at are stored as naive UTC (core/database._now()).
    Bucketing call time-of-day for an India-based team without this
    conversion would put "peak inbound hours" off by 5:30 and silently
    wrong — matches core/calendar.py's own Asia/Kolkata convention."""
    return naive_utc.replace(tzinfo=_UTC).astimezone(IST)


def _direction(row: dict) -> str:
    return row.get("direction") or "outbound"


def _meeting_group_key(row: dict) -> str:
    """Groups every call row that represents the SAME booked meeting. A
    reschedule carries the original calendar_event_id forward onto the new
    call's row via COALESCE (core/calendar.reschedule_discovery_call +
    core/database.save_meeting_link) specifically so the original and
    rescheduled rows share one event id — they must count as ONE meeting,
    not two, or every reschedule would inflate "meetings booked". A row with
    no event id (booked before this column existed, or a booking that never
    got one back) can never be a reschedule target, so it's its own
    singleton group keyed by its own row id."""
    event_id = row.get("calendar_event_id")
    return f"evt:{event_id}" if event_id else f"id:{row.get('id')}"


def _distinct_meetings(leads: list[dict]) -> list[dict]:
    """One row per real-world meeting — the most-recently-updated row in
    each reschedule group, since that row carries the CURRENT preferred_slot
    and meeting_link, not a stale pre-reschedule one."""
    groups: dict[str, dict] = {}
    for row in leads:
        if not row.get("discovery_call_scheduled"):
            continue
        key = _meeting_group_key(row)
        current = groups.get(key)
        if current is None or (row.get("updated_at") or "") > (current.get("updated_at") or ""):
            groups[key] = row
    return list(groups.values())


def _contact_key(row: dict) -> str:
    return row.get("normalized_phone") or f"id:{row.get('id')}"


def _distinct_contacts_latest(leads: list[dict]) -> list[dict]:
    """One row per unique phone number — the most recent call for that
    number. Correct for anything that's genuinely about "their current
    situation" (e.g. _pending_callbacks below: a callback already handled by
    a newer call is genuinely no longer pending). NOT correct for anything
    that's an achievement that shouldn't regress — see
    _best_classification_counts, which exists specifically because using
    this function for Hot/Warm/Cold counts made a contact's own most recent
    (shorter, less-substantive) call silently undo an earlier Hot
    classification and an earlier booked meeting."""
    groups: dict[str, dict] = {}
    for row in leads:
        key = _contact_key(row)
        current = groups.get(key)
        if current is None or (row.get("updated_at") or "") > (current.get("updated_at") or ""):
            groups[key] = row
    return list(groups.values())


_CLASSIFICATION_RANK = {"Hot": 3, "Warm": 2, "Cold": 1}


def _best_classification_counts(leads: list[dict]) -> dict:
    """Per contact, the BEST classification ever achieved across ALL their
    calls — not whichever call happens to be most recent. A contact who
    booked a meeting and was marked Hot, then later made a short unrelated
    call that only qualified as Warm, must still show as Hot: the later call
    genuinely was a lighter conversation, but that doesn't erase what the
    earlier one achieved. (Real bug this fixes: confirmed in production data
    — a contact Hot+booked on call 1, Warm+unbooked on call 2, was showing
    as Warm; classification_counts also undercounted true Hot leads by more
    than 2x for the same reason.)"""
    best_rank: dict[str, int] = {}
    best_label: dict[str, str] = {}
    all_keys: set[str] = set()
    for r in leads:
        key = _contact_key(r)
        all_keys.add(key)
        rank = _CLASSIFICATION_RANK.get(r.get("classification"), 0)
        if rank > best_rank.get(key, 0):
            best_rank[key] = rank
            best_label[key] = r["classification"]

    counts = {"Hot": 0, "Warm": 0, "Cold": 0, "Uncategorized": 0}
    for key in all_keys:
        counts[best_label.get(key, "Uncategorized")] += 1
    return counts


def _avg(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _avg_call_metric(rows: list[dict], metric: str) -> Optional[float]:
    """Averages a field that only lives inside lead_state.call_metrics (not
    its own DB column) — ai_cost_per_min_usd, blended_cost_per_min_inr,
    avg_rag_latency_s. One call = one equally-weighted sample, same
    convention the existing per-call dashboards already use for avg_latency_s
    (see dashboard-common.js's renderKpiRow) — not duration-weighted."""
    vals = []
    for r in rows:
        v = (_parse_lead_state(r).get("call_metrics") or {}).get(metric)
        if v is not None:
            vals.append(v)
    return _avg(vals)


def _call_duration_minutes(row: dict) -> Optional[float]:
    d = (_parse_lead_state(row).get("call_metrics") or {}).get("call_duration_s")
    return d / 60.0 if d and d > 0 else None


def _avg_cost_per_call_inr(rows: list[dict]) -> Optional[float]:
    """All-in (AI + telephony) cost per call, INR — total_cost_inr is a
    top-level DB column set for every completed call regardless of when it
    ran, so this (unlike a per-call-metrics-JSON-only figure) has full
    historical coverage, not just calls made after some later instrumentation
    change."""
    return _avg([r.get("total_cost_inr") for r in rows])


def _avg_cost_per_min_inr(rows: list[dict]) -> Optional[float]:
    """Same all-in cost, divided by that call's own duration — deliberately
    NOT read from call_metrics.blended_cost_per_min_inr (a field that only
    exists on calls processed after that instrumentation was added): dividing
    the already-universal total_cost_inr by call_duration_s gives the same
    number with full historical coverage instead of silently averaging over
    an incomplete, recency-biased sample."""
    vals = []
    for r in rows:
        minutes = _call_duration_minutes(r)
        total = r.get("total_cost_inr")
        if minutes and total is not None:
            vals.append(total / minutes)
    return _avg(vals)


def _daily_trend(leads: list[dict], days: int = 14) -> list[dict]:
    """Call volume (by direction), call minutes, and blended cost, bucketed
    by IST calendar day, for the last `days` days including today — the
    master dashboard's lifetime totals otherwise give no sense of whether
    volume/cost is rising or falling. total_minutes feeds both the "Minutes
    Today"/"Avg Call Duration Today" stat cards and _headline_deltas below."""
    today = datetime.now(_UTC).astimezone(IST).date()
    oldest = today - timedelta(days=days - 1)
    buckets = {
        (oldest + timedelta(days=i)).isoformat(): {
            "calls_inbound": 0, "calls_outbound": 0, "total_cost_inr": 0.0, "total_minutes": 0.0,
        }
        for i in range(days)
    }
    for r in leads:
        dt = _parse_dt(r.get("created_at"))
        if not dt:
            continue
        day = _to_ist(dt).date()
        if day < oldest or day > today:
            continue
        bucket = buckets[day.isoformat()]
        bucket["calls_inbound" if _direction(r) == "inbound" else "calls_outbound"] += 1
        bucket["total_cost_inr"] += r.get("total_cost_inr") or 0
        bucket["total_minutes"] += _call_duration_minutes(r) or 0
    return [
        {"date": d, "calls_inbound": b["calls_inbound"], "calls_outbound": b["calls_outbound"],
         "total_cost_inr": round(b["total_cost_inr"], 2), "total_minutes": round(b["total_minutes"], 2)}
        for d, b in sorted(buckets.items())
    ]


def _today_hourly(leads: list[dict]) -> dict:
    """Today only (IST), 24 hourly buckets — total calls (both directions)
    and blended cost. Distinct from peak_inbound_hours below, which is a
    lifetime, inbound-only pattern; this answers "what happened today
    specifically", matching what a live ops dashboard actually wants hour by
    hour, not a longstanding pattern."""
    today = datetime.now(_UTC).astimezone(IST).date()
    calls = [0] * 24
    cost = [0.0] * 24
    for r in leads:
        dt = _parse_dt(r.get("created_at"))
        if not dt:
            continue
        ist_dt = _to_ist(dt)
        if ist_dt.date() != today:
            continue
        calls[ist_dt.hour] += 1
        cost[ist_dt.hour] += r.get("total_cost_inr") or 0
    return {
        "calls": [{"hour": h, "count": calls[h]} for h in range(24)],
        "cost_inr": [{"hour": h, "value": round(cost[h], 2)} for h in range(24)],
    }


def _pct_change(today_v: float, yesterday_v: float) -> Optional[float]:
    """None when yesterday was 0 — "infinite %" is meaningless on a
    dashboard; the raw numbers themselves already show the change."""
    if not yesterday_v:
        return None
    return round((today_v - yesterday_v) / yesterday_v * 100, 1)


def _headline_deltas(daily_trend: list[dict]) -> dict:
    """Today vs yesterday, for the four "Today" stat cards — nothing like
    this existed before (see _daily_trend's docstring on the prior turn);
    derived from the same daily buckets rather than a second pass over
    `leads`, so it can never disagree with what daily_trend itself shows."""
    today = daily_trend[-1] if daily_trend else None
    yesterday = daily_trend[-2] if len(daily_trend) >= 2 else None

    def calls(d):
        return (d["calls_inbound"] + d["calls_outbound"]) if d else 0

    def avg_duration(d):
        c = calls(d)
        return (d["total_minutes"] / c) if d and c else 0.0

    t_calls, y_calls = calls(today), calls(yesterday)
    t_minutes, y_minutes = (today or {}).get("total_minutes", 0.0), (yesterday or {}).get("total_minutes", 0.0)
    t_avg_dur, y_avg_dur = avg_duration(today), avg_duration(yesterday)
    t_cost, y_cost = (today or {}).get("total_cost_inr", 0.0), (yesterday or {}).get("total_cost_inr", 0.0)

    return {
        "calls_today": t_calls, "calls_delta_pct": _pct_change(t_calls, y_calls),
        "minutes_today": round(t_minutes, 1), "minutes_delta_pct": _pct_change(t_minutes, y_minutes),
        "avg_duration_min_today": round(t_avg_dur, 2), "avg_duration_delta_pct": _pct_change(t_avg_dur, y_avg_dur),
        "cost_today_inr": round(t_cost, 2), "cost_delta_pct": _pct_change(t_cost, y_cost),
    }


def _returning_caller_stats(leads: list[dict]) -> dict:
    """How much of the returning-caller feature (voice/gemini_bridge.py's
    _apply_returning_caller_context) actually gets exercised — invisible
    from the lifetime totals alone."""
    per_contact_counts: dict[str, int] = {}
    for r in leads:
        key = _contact_key(r)
        per_contact_counts[key] = per_contact_counts.get(key, 0) + 1
    total_contacts = len(per_contact_counts)
    returning_contacts = sum(1 for n in per_contact_counts.values() if n >= 2)
    return {
        "total_contacts": total_contacts,
        "returning_contacts": returning_contacts,
        "returning_rate": round(returning_contacts / total_contacts, 4) if total_contacts else None,
        "avg_calls_per_contact": round(len(leads) / total_contacts, 2) if total_contacts else None,
    }


def _call_outcome_breakdown(leads: list[dict]) -> dict:
    """Mutually exclusive per call, checked in priority order — a call that
    both got a callback request AND was later flagged out-of-scope (rare, but
    possible mid-call) counts once, as whichever came first in this order.
    callback_time (top-level column) stands in for "callback requested":
    _end() always sets it once callback_requested is true (defaulting to
    next-day if no time was given), so it's a reliable proxy without needing
    to parse lead_state for the boolean itself. out_of_scope IS only in
    lead_state (no top-level column), so that one check needs the parse."""
    counts = {"meeting_booked": 0, "callback_requested": 0, "out_of_scope": 0, "no_outcome": 0}
    for r in leads:
        if r.get("discovery_call_scheduled"):
            counts["meeting_booked"] += 1
        elif r.get("callback_time"):
            counts["callback_requested"] += 1
        elif _parse_lead_state(r).get("out_of_scope"):
            counts["out_of_scope"] += 1
        else:
            counts["no_outcome"] += 1
    return counts


def _language_breakdown(leads: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for r in leads:
        lang = r.get("preferred_language") or "Not detected"
        counts[lang] = counts.get(lang, 0) + 1
    return [{"language": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


def _model_breakdown(leads: list[dict]) -> list[dict]:
    """engine/model only live inside lead_state.call_metrics (see
    core/model_config.py — the catalog itself has no DB presence), but cost
    and latency are read from their own top-level columns for full
    historical coverage, same reasoning as _avg_cost_per_min_inr above."""
    groups: dict[tuple, list[dict]] = {}
    for r in leads:
        cm = _parse_lead_state(r).get("call_metrics") or {}
        engine, model = cm.get("engine"), cm.get("model")
        if not engine and not model:
            continue
        groups.setdefault((engine or "unknown", model or "unknown"), []).append(r)
    out = [
        {
            "engine": engine, "model": model, "calls": len(rows),
            "avg_cost_per_call_inr": _avg([r.get("total_cost_inr") for r in rows]),
            "avg_latency_s": _avg([r.get("avg_latency_s") for r in rows]),
        }
        for (engine, model), rows in groups.items()
    ]
    out.sort(key=lambda x: -x["calls"])
    return out


def _pending_callbacks(leads: list[dict]) -> list[dict]:
    """A contact whose LATEST call still shows a requested callback time —
    i.e. no later call has happened since it was requested, which would
    otherwise mean it got actioned (either we called back, or they called in
    again themselves)."""
    out = [
        {
            "name": c.get("name") or None,
            "phone_number": c.get("phone_number"),
            "direction": _direction(c),
            "callback_time": c.get("callback_time"),
        }
        for c in _distinct_contacts_latest(leads)
        if c.get("callback_time")
    ]
    out.sort(key=lambda x: x["callback_time"] or "")
    return out


def compute_dashboard_stats(leads: list[dict]) -> dict:
    inbound = [r for r in leads if _direction(r) == "inbound"]
    outbound = [r for r in leads if _direction(r) == "outbound"]

    distinct_meetings = _distinct_meetings(leads)
    meetings_inbound = [m for m in distinct_meetings if _direction(m) == "inbound"]
    meetings_outbound = [m for m in distinct_meetings if _direction(m) == "outbound"]

    contacts_inbound = {_contact_key(r) for r in inbound}
    contacts_outbound = {_contact_key(r) for r in outbound}

    def rate(numerator: int, denominator: int) -> Optional[float]:
        return round(numerator / denominator, 4) if denominator else None

    # Best-EVER classification per contact, not "whatever their most recent
    # call happened to be" — see _best_classification_counts's docstring.
    classification_counts = _best_classification_counts(leads)

    hour_counts = [0] * 24
    for r in inbound:
        dt = _parse_dt(r.get("created_at"))
        if dt:
            hour_counts[_to_ist(dt).hour] += 1

    timeline_counts: dict[str, int] = {}
    for m in distinct_meetings:
        slot_dt = _parse_dt(_parse_lead_state(m).get("preferred_slot"))
        if slot_dt:
            key = slot_dt.date().isoformat()
            timeline_counts[key] = timeline_counts.get(key, 0) + 1

    exotel_cost_inr_total = sum((r.get("exotel_cost_inr") or 0) for r in leads)
    total_cost_inr_total = sum((r.get("total_cost_inr") or 0) for r in leads)
    # Derived by subtraction, not converted independently from cost_usd — so
    # ai_cost_inr_total + exotel_cost_inr_total == total_cost_inr_total
    # exactly, by construction, never a rounding-drift mismatch between tiles
    # that are supposed to add up to each other.
    ai_cost_inr_total = round(total_cost_inr_total - exotel_cost_inr_total, 2)

    meeting_outcome_counts = {"completed": 0, "no_show": 0, "pending": 0}
    for m in distinct_meetings:
        outcome = m.get("meeting_outcome") or "pending"
        meeting_outcome_counts[outcome if outcome in meeting_outcome_counts else "pending"] += 1
    completed_count = meeting_outcome_counts["completed"]

    daily_trend = _daily_trend(leads)

    return {
        "totals": {
            "calls_total": len(leads),
            "calls_inbound": len(inbound),
            "calls_outbound": len(outbound),
            "ai_cost_inr_total": ai_cost_inr_total,
            "exotel_cost_inr_total": round(exotel_cost_inr_total, 2),
            "total_cost_inr_total": round(total_cost_inr_total, 2),
            "meetings_booked_total": len(distinct_meetings),
        },
        # All-in (AI + telephony), INR only — see _avg_cost_per_call_inr/
        # _avg_cost_per_min_inr docstrings for why these are derived from
        # total_cost_inr rather than the newer per-engine call_metrics
        # fields (full historical coverage, one currency throughout).
        "avg_cost_per_call_inr": {
            "inbound": _avg_cost_per_call_inr(inbound),
            "outbound": _avg_cost_per_call_inr(outbound),
        },
        "avg_cost_per_min_inr": {
            "inbound": _avg_cost_per_min_inr(inbound),
            "outbound": _avg_cost_per_min_inr(outbound),
        },
        "meetings_booked": {
            "inbound_count": len(meetings_inbound),
            "inbound_rate": rate(len(meetings_inbound), len(contacts_inbound)),
            "outbound_count": len(meetings_outbound),
            "outbound_rate": rate(len(meetings_outbound), len(contacts_outbound)),
        },
        "avg_latency_s": {
            # avg_latency_s is its own top-level DB column already (unlike
            # the call_metrics-only fields above), so no JSON parsing needed.
            "model": _avg([r.get("avg_latency_s") for r in leads]),
            "rag": _avg_call_metric(leads, "avg_rag_latency_s"),
        },
        "cost_per_meeting_inr": (
            round(total_cost_inr_total / len(distinct_meetings), 2) if distinct_meetings else None
        ),
        # Per-CALL (not per-contact) conversion — distinguishes this from
        # meetings_booked.inbound_rate/outbound_rate above, which are
        # per-CONTACT and can exceed 1 for a heavily-repeat-called number.
        # This one is bounded 0-1 by construction (a meeting can't happen
        # without at least one call), safe to show as a plain percentage.
        "meeting_conversion_rate": rate(len(distinct_meetings), len(leads)),
        "classification_counts": classification_counts,
        "peak_inbound_hours": [{"hour": h, "count": hour_counts[h]} for h in range(24)],
        "meetings_timeline": [{"date": d, "count": c} for d, c in sorted(timeline_counts.items())],
        "daily_trend": daily_trend,
        "today_hourly": _today_hourly(leads),
        "headline_deltas": _headline_deltas(daily_trend),
        "returning_caller": _returning_caller_stats(leads),
        "call_outcomes": _call_outcome_breakdown(leads),
        "language_breakdown": _language_breakdown(leads),
        "model_breakdown": _model_breakdown(leads),
        "pending_callbacks": _pending_callbacks(leads),
        "meeting_outcomes": meeting_outcome_counts,
        # A different, more honest question than cost_per_meeting_inr above
        # ("booked") — this is cost per meeting a human has actually marked
        # as held. None until at least one meeting has been marked completed.
        "cost_per_completed_meeting_inr": (
            round(total_cost_inr_total / completed_count, 2) if completed_count else None
        ),
    }


def list_meetings(leads: list[dict]) -> list[dict]:
    """One entry per real-world meeting (same dedup as compute_dashboard_stats
    — a rescheduled meeting must appear once, at its current time, not once
    per call that ever touched it). Feeds the meetings page."""
    out = []
    for m in _distinct_meetings(leads):
        state = _parse_lead_state(m)
        out.append({
            "lead_id": m.get("id"),
            "name": m.get("name") or None,
            "phone_number": m.get("phone_number"),
            "direction": _direction(m),
            "meeting_date": state.get("preferred_slot"),
            # Deliberately the raw interest_area as captured on the call,
            # not a forced bucket — whatever the lead actually said they
            # wanted, verbatim.
            "agenda": m.get("interest_area") or None,
            "meeting_link": m.get("meeting_link"),
            "meeting_outcome": m.get("meeting_outcome") or "pending",
        })
    out.sort(key=lambda m: m["meeting_date"] or "9999")
    return out
