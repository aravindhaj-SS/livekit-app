import logging
import pickle
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

_TOKEN_PATH = Path("token.pkl")


async def book_discovery_call(
    lead_name: str,
    lead_email: str,
    organizer_email: str,
    preferred_slot: Optional[str],
    additional_emails: Optional[Sequence[str]] = None,
) -> Optional[tuple[str, str]]:
    """
    Creates a Google Calendar event with a Meet link using the saved OAuth
    token (run scripts/auth_setup.py once to create token.pkl).
    Returns (meet_link, event_id) on success — event_id is stored on the lead
    row (core/database.save_meeting_link) so a returning caller can later
    reschedule this exact event via reschedule_discovery_call() below instead
    of a fresh call always creating a duplicate booking. Returns None on
    failure or misconfiguration.
    """
    extra = [e for e in (additional_emails or []) if e]
    logger.info(
        f"book_discovery_call invoked — lead='{lead_name}' email='{lead_email}' "
        f"organizer='{organizer_email}' slot='{preferred_slot}' "
        f"additional_emails={extra}"
    )
    if not lead_email:
        logger.warning("No lead email — cannot book discovery call")
        return None
    if not _TOKEN_PATH.exists():
        logger.warning(
            "token.pkl not found — run scripts/auth_setup.py once to authorise "
            "Google Calendar access"
        )
        return None

    try:
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        with open(_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

        if creds.expired and creds.refresh_token:
            logger.info("OAuth creds expired — refreshing")
            creds.refresh(Request())
            with open(_TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)
            logger.info("OAuth creds refreshed and persisted")

        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        start_dt = _parse_slot(preferred_slot)
        end_dt = start_dt + timedelta(hours=1)
        logger.info(f"Event window resolved — start={start_dt.isoformat()} end={end_dt.isoformat()}")

        event_body = {
            "summary": f"Discovery Call — {lead_name}",
            "description": (
                f"Discovery call with {lead_name} arranged by Mira, "
                f"Swaran Soft AI lead qualification agent."
            ),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "attendees": [
                {"email": organizer_email},
                {"email": lead_email},
                *[{"email": e} for e in extra],
            ],
            "conferenceData": {
                "createRequest": {
                    "requestId": f"swaran-{lead_email}-{int(start_dt.timestamp())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 60},
                    {"method": "popup", "minutes": 15},
                ],
            },
        }

        created = (
            service.events()
            .insert(
                calendarId="primary",
                body=event_body,
                conferenceDataVersion=1,
                sendUpdates="all",
            )
            .execute()
        )

        event_id = created.get("id", "")
        meet_link = created.get("hangoutLink", "")
        attendees = [a.get("email") for a in created.get("attendees", [])]
        logger.info(
            f"Discovery call booked — event_id={event_id} meet_link={meet_link} "
            f"attendees={attendees} sendUpdates=all (Google will email all attendees)"
        )
        if not meet_link:
            logger.warning(
                "Event created but hangoutLink is empty — conferenceDataVersion=1 may "
                "have failed; check the event in Google Calendar."
            )
        return meet_link, event_id

    except ImportError:
        logger.error(
            "google-api-python-client not installed — run: "
            "pip install google-api-python-client google-auth google-auth-httplib2"
        )
        return None
    except Exception as e:
        body = getattr(e, "content", None) or getattr(e, "resp", None)
        logger.error(
            f"Calendar booking failed — type={type(e).__name__} msg={e!r} extra={body!r}",
            exc_info=True,
        )
        return None


async def reschedule_discovery_call(
    event_id: str,
    organizer_email: str,
    preferred_slot: Optional[str],
) -> Optional[tuple[str, str]]:
    """
    Moves an EXISTING discovery-call event to a new time via .events().patch()
    (start/end only — attendees, the Meet link/conferenceData, and everything
    else about the event are left untouched). Used when a returning caller
    asks to move a meeting already booked from a prior call (see
    voice/gemini_bridge.py's _end — the reschedule-vs-create decision is made
    purely on whether the previous call's row has a calendar_event_id).

    Returns (meet_link, event_id) on success, same shape as book_discovery_call
    so both share one call site. Returns None on failure (including the
    original event having been deleted/not found) — the caller falls back to
    book_discovery_call to create a fresh booking rather than leaving the
    lead with nothing.
    """
    logger.info(f"reschedule_discovery_call invoked — event_id={event_id} slot='{preferred_slot}'")
    if not event_id:
        logger.warning("No event_id — cannot reschedule, caller should fall back to booking fresh")
        return None
    if not _TOKEN_PATH.exists():
        logger.warning(
            "token.pkl not found — run scripts/auth_setup.py once to authorise "
            "Google Calendar access"
        )
        return None

    try:
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        with open(_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

        if creds.expired and creds.refresh_token:
            logger.info("OAuth creds expired — refreshing")
            creds.refresh(Request())
            with open(_TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)
            logger.info("OAuth creds refreshed and persisted")

        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        start_dt = _parse_slot(preferred_slot)
        end_dt = start_dt + timedelta(hours=1)
        logger.info(f"Reschedule window resolved — start={start_dt.isoformat()} end={end_dt.isoformat()}")

        updated = (
            service.events()
            .patch(
                calendarId="primary",
                eventId=event_id,
                body={
                    "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
                    "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
                },
                sendUpdates="all",
            )
            .execute()
        )

        meet_link = updated.get("hangoutLink", "")
        logger.info(f"Discovery call rescheduled — event_id={event_id} new_meet_link={meet_link}")
        return meet_link, event_id

    except ImportError:
        logger.error(
            "google-api-python-client not installed — run: "
            "pip install google-api-python-client google-auth google-auth-httplib2"
        )
        return None
    except Exception as e:
        body = getattr(e, "content", None) or getattr(e, "resp", None)
        logger.error(
            f"Calendar reschedule failed — event_id={event_id} type={type(e).__name__} "
            f"msg={e!r} extra={body!r}",
            exc_info=True,
        )
        return None


def _parse_slot(preferred_slot: Optional[str]) -> datetime:
    """Parse ISO slot string; fall back to next business day at 10 AM."""
    if preferred_slot:
        try:
            return datetime.fromisoformat(preferred_slot)
        except ValueError:
            pass
    return _next_business_day_10am()


def _next_business_day_10am() -> datetime:
    d = date.today()
    days_ahead = 1
    while True:
        candidate = d + timedelta(days=days_ahead)
        if candidate.weekday() < 5:  # Mon-Fri
            return datetime(candidate.year, candidate.month, candidate.day, 10, 0, 0)
        days_ahead += 1
