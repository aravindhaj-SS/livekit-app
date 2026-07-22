import json
import logging
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)
DB_PATH = "leads.db"


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
                cost_usd REAL,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await db.commit()

        # Migrate pre-existing databases that predate this column.
        try:
            await db.execute("ALTER TABLE leads ADD COLUMN cost_usd REAL")
            await db.commit()
        except Exception:
            pass  # column already exists
    logger.info("Database initialised")


async def save_lead(lead_data: dict) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        cursor = await db.execute(
            """
            INSERT INTO leads
                (name, company, phone_number, interest_area, preferred_language, email_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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


async def update_lead_state(
    lead_id: int,
    state: dict,
    classification: Optional[str] = None,
    transcript: Optional[list] = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().isoformat()
        await db.execute(
            """UPDATE leads SET
               lead_state = ?, classification = ?,
               email_id = ?, discovery_call_scheduled = ?,
               transcript = ?,
               budget = ?, timeline = ?, decision_maker_status = ?,
               cost_usd = ?,
               updated_at = ?
               WHERE id = ?""",
            (
                json.dumps(state),
                classification,
                state.get("email_id"),
                1 if state.get("discovery_call_scheduled") else 0,
                json.dumps(transcript) if transcript else None,
                state.get("budget"),
                state.get("timeline"),
                state.get("decision_maker_status"),
                (state.get("call_metrics") or {}).get("cost_usd"),
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


async def get_all_leads() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leads ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
