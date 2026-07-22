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

    call_metrics: dict = field(default_factory=lambda: {"call_duration_s": 0.0})

    def to_dict(self) -> dict:
        return asdict(self)
