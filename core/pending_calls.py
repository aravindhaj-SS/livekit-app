"""Correlates an outbound Exotel call back to its lead_id.

Exotel's Voicebot Applet WSS URL is a static App Bazaar flow, not a
per-call resolver, so the only field the /media-stream WebSocket's `start`
event carries that we can key off of is the customer's phone number. This
module is a one-shot, TTL-bounded phone->lead_id cache bridging the dial
request (api/routes.py) to the WS session (voice/media_stream.py).
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

PENDING_CALL_TTL_S = 120.0
_cache: dict[str, tuple[int, float]] = {}


def normalize_phone(phone: str) -> str:
    """Last 10 digits — strips country code/punctuation."""
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def register(phone_number: str, lead_id: int) -> None:
    _cache[normalize_phone(phone_number)] = (lead_id, time.monotonic())
    logger.info(f"Registered pending call: phone={phone_number!r} -> lead_id={lead_id}")


def consume(phone_number: str) -> Optional[int]:
    entry = _cache.pop(normalize_phone(phone_number), None)
    if entry is None:
        return None
    lead_id, registered_at = entry
    if time.monotonic() - registered_at > PENDING_CALL_TTL_S:
        logger.warning(f"Pending call for phone={phone_number!r} expired (lead_id={lead_id})")
        return None
    return lead_id
