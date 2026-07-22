"""Single-account, process-lifetime dashboard auth. Deliberately simple —
this guards an internal ops dashboard, not multi-tenant user data. The HMAC
secret regenerates on every process restart, so a deploy logs everyone out."""
import hashlib
import hmac
import os

_DASH_SECRET = os.urandom(32)


def dash_token() -> str:
    return hmac.new(_DASH_SECRET, b"dash-authenticated", hashlib.sha256).hexdigest()


def dash_token_valid(token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token, dash_token())
