import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from core.config import settings

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
    async with _pool.acquire() as conn:
        lead_id = await conn.fetchval(
            """
            INSERT INTO leads
                (name, company, phone_number, interest_area, preferred_language, email_id,
                 direction, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'outbound', $7, $7)
            RETURNING id
            """,
            lead_data.get("name"),
            lead_data.get("company"),
            lead_data.get("phone_number"),
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
            INSERT INTO leads (phone_number, direction, created_at, updated_at)
            VALUES ($1, 'inbound', $2, $2)
            RETURNING id
            """,
            phone_number, now,
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


async def save_meeting_link(lead_id: int, meeting_link: str):
    """Separate from update_lead_state because the Meet link is only known
    AFTER the discovery-call booking runs, which happens after the main
    state persist in the session's end handler — update_lead_state() already
    wrote discovery_call_scheduled=False (LeadState only flips it to True
    *after* the booking succeeds, i.e. after that persist already ran) and
    would never see it become True otherwise. Must also flip
    discovery_call_scheduled here, or the dashboard shows every booked
    meeting as unbooked ("—") despite meeting_link being correctly saved —
    confirmed bug: this column was never set True by any code path."""
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE leads SET meeting_link = $1, discovery_call_scheduled = TRUE WHERE id = $2",
            meeting_link, lead_id,
        )
    logger.info(f"Lead {lead_id} meeting_link saved, discovery_call_scheduled=True")


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
