from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class LeadState:
    """Minimal per-call record. No FSM bookkeeping — the realtime model drives
    the conversation itself; this just accumulates what it reports plus
    identity fields hydrated from the lead's DB row at call start."""

    name: str = ""
    company: str = ""
    phone_number: str = ""
    interest_area: str = ""
    lead_id: Optional[int] = None
    direction: str = "outbound"

    pain: Optional[str] = None
    budget: Optional[str] = None
    timeline: Optional[str] = None
    decision_maker_status: Optional[str] = None

    email_id: Optional[str] = None
    email_confirmed: bool = False
    preferred_slot: Optional[str] = None
    availability_notes: Optional[str] = None

    discovery_call_agreed: bool = False
    discovery_call_scheduled: bool = False
    meeting_link: Optional[str] = None

    classification: Optional[str] = None
    out_of_scope: bool = False
    call_complete: bool = False

    # Busy-now / callback handling (outbound and inbound).
    callback_requested: bool = False
    callback_time: Optional[str] = None

    # Returning-caller support (see voice/gemini_bridge.py's _on_start,
    # core/database.get_previous_calls). previous_call holds a plain dict of
    # whatever the most recent prior completed call from this same phone
    # number captured — name/company/interest_area/budget/timeline/
    # decision_maker_status/email_id/discovery_call_scheduled/meeting_link/
    # calendar_event_id/callback_time/summary — so agent/prompt.py can inject
    # it without a second DB round trip mid-call. None for a first-time caller.
    is_returning_caller: bool = False
    previous_call: Optional[dict] = None

    call_metrics: dict = field(default_factory=lambda: {"call_duration_s": 0.0})

    def to_dict(self) -> dict:
        return asdict(self)
