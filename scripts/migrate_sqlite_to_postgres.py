"""One-time data migration: copies every row out of the old leads.db SQLite
file into the new Postgres database (core/config.py's DATABASE_URL).

Run once, from the project root: python scripts/migrate_sqlite_to_postgres.py

Preserves each row's original id — recordings/{id}.wav filenames and any
already-shared dashboard links depend on the id staying the same, so this
does NOT let Postgres auto-generate new ids. The leads_id_seq sequence is
reset afterward so future inserts continue from the right number.

Safe to re-run: skips any id that already exists in Postgres (ON CONFLICT
DO NOTHING), so a partial or repeated run never duplicates rows.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402

SQLITE_PATH = "leads.db"

_COLUMNS = [
    "id", "name", "company", "phone_number", "interest_area", "preferred_language",
    "email_id", "lead_state", "classification", "discovery_call_scheduled",
    "transcript", "budget", "timeline", "decision_maker_status", "meeting_link",
    "created_at", "updated_at", "cost_usd", "direction", "exotel_cost_inr",
    "total_cost_inr", "avg_latency_s", "recording_path", "callback_time",
]


async def main():
    if not Path(SQLITE_PATH).exists():
        print(f"No {SQLITE_PATH} found — nothing to migrate.")
        return

    sconn = sqlite3.connect(SQLITE_PATH)
    sconn.row_factory = sqlite3.Row
    rows = sconn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    sconn.close()
    print(f"Read {len(rows)} rows from {SQLITE_PATH}")
    if not rows:
        return

    pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id SERIAL PRIMARY KEY,
                    name TEXT, company TEXT, phone_number TEXT, interest_area TEXT,
                    preferred_language TEXT, email_id TEXT, lead_state TEXT,
                    classification TEXT, discovery_call_scheduled BOOLEAN DEFAULT FALSE,
                    transcript TEXT, budget TEXT, timeline TEXT, decision_maker_status TEXT,
                    meeting_link TEXT, created_at TEXT, updated_at TEXT,
                    cost_usd DOUBLE PRECISION, direction TEXT DEFAULT 'outbound',
                    exotel_cost_inr DOUBLE PRECISION, total_cost_inr DOUBLE PRECISION,
                    avg_latency_s DOUBLE PRECISION, recording_path TEXT, callback_time TEXT
                )
            """)

            placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
            col_list = ", ".join(_COLUMNS)
            inserted = 0
            for row in rows:
                values = [
                    bool(row["discovery_call_scheduled"]) if col == "discovery_call_scheduled"
                    else row[col]
                    for col in _COLUMNS
                ]
                result = await conn.execute(
                    f"INSERT INTO leads ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO NOTHING",
                    *values,
                )
                if result.endswith("1"):
                    inserted += 1

            max_id = await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM leads")
            await conn.execute(
                "SELECT setval(pg_get_serial_sequence('leads', 'id'), $1, true)", max_id
            )
            total = await conn.fetchval("SELECT count(*) FROM leads")

        print(f"Inserted {inserted} new row(s); leads table now has {total} row(s) total.")
        print(f"Sequence advanced past id={max_id}.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
