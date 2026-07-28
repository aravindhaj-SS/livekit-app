import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from core.config import settings
from core.pending_calls import normalize_phone

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

# Columns added after the original schema — applied via ALTER TABLE at
# startup so an existing database upgrades in place instead of needing a
# manual migration. Mirrors the same pattern the old SQLite version used;
# Postgres raises duplicate_column (42701) for an already-existing column,
# caught the same way.
_ADDED_COLUMNS = [
    ("cost_usd", "DOUBLE PRECISION"),
    ("direction", "TEXT DEFAULT 'outbound'"),
    ("exotel_cost_inr", "DOUBLE PRECISION"),
    ("total_cost_inr", "DOUBLE PRECISION"),
    ("avg_latency_s", "DOUBLE PRECISION"),
    ("recording_path", "TEXT"),
    ("callback_time", "TEXT"),
    # Returning-caller support (see get_previous_calls) — normalized_phone is
    # the same "last 10 digits" shape as core/pending_calls.normalize_phone,
    # kept in sync with it deliberately (see the backfill UPDATE below and
    # save_lead/create_inbound_lead) so a phone-number lookup is a plain
    # indexed equality match, not a per-row substring scan.
    ("normalized_phone", "TEXT"),
    # One-line, cheap-LLM post-call summary (agent/lead_agent.py's
    # summarize_call) — lets a returning caller's next conversation ground
    # a free-text reference ("last time we spoke about...") in real content,
    # not just the structured budget/timeline/decision_maker_status columns.
    ("call_summary", "TEXT"),
    # Google Calendar event id from book_discovery_call — stored so a
    # returning caller asking to move an existing meeting can be handled via
    # reschedule_discovery_call's .events().patch() instead of blindly
    # creating a duplicate booking.
    ("calendar_event_id", "TEXT"),
]


def _now() -> str:
    """ISO 8601 string, same format the app has always stored created_at/
    updated_at as — kept as plain TEXT columns (not native TIMESTAMP) so
    every read/write path in the rest of the app stays unchanged."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=10)

    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                name TEXT,
                company TEXT,
                phone_number TEXT,
                interest_area TEXT,
                preferred_language TEXT,
                email_id TEXT,
                lead_state TEXT,
                classification TEXT,
                discovery_call_scheduled BOOLEAN DEFAULT FALSE,
                transcript TEXT,
                budget TEXT,
                timeline TEXT,
                decision_maker_status TEXT,
                meeting_link TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        for column, col_type in _ADDED_COLUMNS:
            try:
                await conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {col_type}")
            except asyncpg.exceptions.DuplicateColumnError:
                pass  # column already exists

        # One-time-per-row backfill for rows created before normalized_phone
        # existed — idempotent (only touches NULL rows), safe to run on every
        # startup like the ALTER TABLEs above. Must match
        # core/pending_calls.normalize_phone's own logic (strip non-digits,
        # keep the last 10) or old rows would silently never match a
        # returning caller's new call.
        await conn.execute("""
            UPDATE leads SET normalized_phone = RIGHT(regexp_replace(phone_number, '\\D', '', 'g'), 10)
            WHERE normalized_phone IS NULL AND phone_number IS NOT NULL AND phone_number != ''
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_normalized_phone ON leads(normalized_phone)"
        )

    logger.info("Database initialised (Postgres)")


async def close_db():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def save_lead(lead_data: dict) -> int:
    """Outbound path only — the lead already submitted a form before the call
    was placed (api/routes.py's /callback). Inbound leads are created by
    create_inbound_lead() instead, at call start, since nothing is known yet."""
    now = _now()
    phone = lead_data.get("phone_number") or ""
    async with _pool.acquire() as conn:
        lead_id = await conn.fetchval(
            """
            INSERT INTO leads
                (name, company, phone_number, normalized_phone, interest_area, preferred_language,
                 email_id, direction, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'outbound', $8, $8)
            RETURNING id
            """,
            lead_data.get("name"),
            lead_data.get("company"),
            phone,
            normalize_phone(phone) if phone else None,
            lead_data.get("interest_area"),
            lead_data.get("preferred_language"),
            lead_data.get("email_id"),
            now,
        )
    return lead_id


async def create_inbound_lead(phone_number: str) -> int:
    """Called the moment an inbound call connects — nothing is known about
    the caller yet except their number; name/company/interest are filled in
    live via the save_lead_info tool and persisted at call end."""
    now = _now()
    async with _pool.acquire() as conn:
        lead_id = await conn.fetchval(
            """
            INSERT INTO leads (phone_number, normalized_phone, direction, created_at, updated_at)
            VALUES ($1, $2, 'inbound', $3, $3)
            RETURNING id
            """,
            phone_number, normalize_phone(phone_number) if phone_number else None, now,
        )
    logger.info(f"Inbound lead created — lead_id={lead_id} phone={phone_number!r}")
    return lead_id


async def update_lead_state(
    lead_id: int,
    state: dict,
    classification: Optional[str] = None,
    transcript: Optional[list] = None,
):
    metrics = state.get("call_metrics") or {}
    async with _pool.acquire() as conn:
        await conn.execute(
            """UPDATE leads SET
               name = COALESCE($1, name), company = COALESCE($2, company),
               interest_area = COALESCE($3, interest_area),
               lead_state = $4, classification = $5,
               email_id = $6, discovery_call_scheduled = $7,
               transcript = $8,
               budget = $9, timeline = $10, decision_maker_status = $11,
               cost_usd = $12, exotel_cost_inr = $13, total_cost_inr = $14,
               avg_latency_s = $15, recording_path = $16, callback_time = $17,
               updated_at = $18
               WHERE id = $19""",
            state.get("name") or None,
            state.get("company") or None,
            state.get("interest_area") or None,
            json.dumps(state),
            classification,
            state.get("email_id"),
            bool(state.get("discovery_call_scheduled")),
            json.dumps(transcript) if transcript else None,
            state.get("budget"),
            state.get("timeline"),
            state.get("decision_maker_status"),
            metrics.get("ai_cost_usd"),
            metrics.get("exotel_cost_inr"),
            metrics.get("total_cost_inr"),
            metrics.get("avg_latency_s"),
            metrics.get("recording_path"),
            state.get("callback_time"),
            _now(),
            lead_id,
        )
    logger.info(f"Lead {lead_id} updated — classification={classification}")


async def save_meeting_link(lead_id: int, meeting_link: str, event_id: Optional[str] = None):
    """Separate from update_lead_state because the Meet link is only known
    AFTER the discovery-call booking runs, which happens after the main
    state persist in the session's end handler — update_lead_state() already
    wrote discovery_call_scheduled=False (LeadState only flips it to True
    *after* the booking succeeds, i.e. after that persist already ran) and
    would never see it become True otherwise. Must also flip
    discovery_call_scheduled here, or the dashboard shows every booked
    meeting as unbooked ("—") despite meeting_link being correctly saved —
    confirmed bug: this column was never set True by any code path.

    event_id (the underlying Google Calendar event id, distinct from the
    Meet link) is stored so a future call from the same number can reschedule
    this exact event (core/calendar.py's reschedule_discovery_call) instead
    of creating a duplicate booking — COALESCE keeps whatever's already on
    file if a reschedule call ever omits it."""
    async with _pool.acquire() as conn:
        await conn.execute(
            """UPDATE leads SET meeting_link = $1, calendar_event_id = COALESCE($2, calendar_event_id),
               discovery_call_scheduled = TRUE WHERE id = $3""",
            meeting_link, event_id, lead_id,
        )
    logger.info(f"Lead {lead_id} meeting_link saved, event_id={event_id!r}, discovery_call_scheduled=True")


async def update_call_summary(lead_id: int, summary: str) -> None:
    """Written separately from update_lead_state, after the WS/session is
    already closed (see voice/gemini_bridge.py's _end) — the summarizer call
    is a real network round trip and shouldn't delay hanging up on the
    caller, same reasoning as why book_discovery_call also runs after close."""
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE leads SET call_summary = $1 WHERE id = $2", summary, lead_id)
    logger.info(f"Lead {lead_id} call_summary saved")


async def get_previous_calls(
    normalized_phone: str, exclude_lead_id: Optional[int] = None, limit: int = 1
) -> list[dict]:
    """Prior COMPLETED calls from the same phone number (lead_state IS NOT
    NULL — only set once update_lead_state has actually persisted a call's
    end, so an in-progress or abandoned row never counts). Used to greet a
    returning caller by name and carry forward what's already known instead
    of re-collecting it (see voice/gemini_bridge.py's _on_start). Ordered
    most-recent-first; exclude_lead_id is a defensive no-op in practice
    (the current call's own row never has lead_state set yet at this point
    in the call), kept for clarity/safety rather than relying on that timing."""
    if not normalized_phone:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM leads
               WHERE normalized_phone = $1 AND lead_state IS NOT NULL
                 AND ($2::int IS NULL OR id != $2)
               ORDER BY updated_at DESC NULLS LAST
               LIMIT $3""",
            normalized_phone, exclude_lead_id, limit,
        )
    return [dict(row) for row in rows]


async def get_lead(lead_id: int) -> Optional[dict]:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM leads WHERE id = $1", lead_id)
        return dict(row) if row else None


async def get_all_leads(direction: Optional[str] = None) -> list[dict]:
    async with _pool.acquire() as conn:
        if direction:
            rows = await conn.fetch(
                "SELECT * FROM leads WHERE direction = $1 ORDER BY created_at DESC", direction
            )
        else:
            rows = await conn.fetch("SELECT * FROM leads ORDER BY created_at DESC")
        return [dict(row) for row in rows]
