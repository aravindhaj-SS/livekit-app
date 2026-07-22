import json
import logging
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)
DB_PATH = "leads.db"

# Columns added after the original schema — applied via ALTER TABLE at
# startup so an existing leads.db upgrades in place instead of needing a
# manual migration.
_ADDED_COLUMNS = [
    ("cost_usd", "REAL"),
    ("direction", "TEXT DEFAULT 'outbound'"),
    ("exotel_cost_inr", "REAL"),
    ("total_cost_inr", "REAL"),
    ("avg_latency_s", "REAL"),
    ("recording_path", "TEXT"),
    ("callback_time", "TEXT"),
]


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                company TEXT,
                phone_number TEXT,
                interest_area TEXT,
                preferred_language TEXT,
                email_id TEXT,
                lead_state TEXT,
                classification TEXT,
                discovery_call_scheduled INTEGER DEFAULT 0,
                transcript TEXT,
                budget TEXT,
                timeline TEXT,
                decision_maker_status TEXT,
                meeting_link TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await db.commit()

        for column, col_type in _ADDED_COLUMNS:
            try:
                await db.execute(f"ALTER TABLE leads ADD COLUMN {column} {col_type}")
                await db.commit()
            except Exception:
                pass  # column already exists
    logger.info("Database initialised")


async def save_lead(lead_data: dict) -> int:
    """Outbound path only — the lead already submitted a form before the call
    was placed (api/routes.py's /callback). Inbound leads are created by
    create_inbound_lead() instead, at call start, since nothing is known yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        cursor = await db.execute(
            """
            INSERT INTO leads
                (name, company, phone_number, interest_area, preferred_language, email_id,
                 direction, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'outbound', ?, ?)
            """,
            (
                lead_data.get("name"),
                lead_data.get("company"),
                lead_data.get("phone_number"),
                lead_data.get("interest_area"),
                lead_data.get("preferred_language"),
                lead_data.get("email_id"),
                now,
                now,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def create_inbound_lead(phone_number: str) -> int:
    """Called the moment an inbound call connects — nothing is known about
    the caller yet except their number; name/company/interest are filled in
    live via the save_lead_info tool and persisted at call end."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        cursor = await db.execute(
            """
            INSERT INTO leads (phone_number, direction, created_at, updated_at)
            VALUES (?, 'inbound', ?, ?)
            """,
            (phone_number, now, now),
        )
        await db.commit()
        lead_id = cursor.lastrowid
    logger.info(f"Inbound lead created — lead_id={lead_id} phone={phone_number!r}")
    return lead_id


async def update_lead_state(
    lead_id: int,
    state: dict,
    classification: Optional[str] = None,
    transcript: Optional[list] = None,
):
    metrics = state.get("call_metrics") or {}
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        await db.execute(
            """UPDATE leads SET
               name = COALESCE(?, name), company = COALESCE(?, company),
               interest_area = COALESCE(?, interest_area),
               lead_state = ?, classification = ?,
               email_id = ?, discovery_call_scheduled = ?,
               transcript = ?,
               budget = ?, timeline = ?, decision_maker_status = ?,
               cost_usd = ?, exotel_cost_inr = ?, total_cost_inr = ?,
               avg_latency_s = ?, recording_path = ?, callback_time = ?,
               updated_at = ?
               WHERE id = ?""",
            (
                state.get("name") or None,
                state.get("company") or None,
                state.get("interest_area") or None,
                json.dumps(state),
                classification,
                state.get("email_id"),
                1 if state.get("discovery_call_scheduled") else 0,
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
                now,
                lead_id,
            ),
        )
        await db.commit()
    logger.info(f"Lead {lead_id} updated — classification={classification}")


async def save_meeting_link(lead_id: int, meeting_link: str):
    """Separate from update_lead_state because the Meet link is only known
    AFTER the discovery-call booking runs, which happens after the main
    state persist in the session's end handler."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE leads SET meeting_link = ? WHERE id = ?",
            (meeting_link, lead_id),
        )
        await db.commit()
    logger.info(f"Lead {lead_id} meeting_link saved")


async def get_lead(lead_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_leads(direction: Optional[str] = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if direction:
            query = "SELECT * FROM leads WHERE direction = ? ORDER BY created_at DESC"
            params = (direction,)
        else:
            query = "SELECT * FROM leads ORDER BY created_at DESC"
            params = ()
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
