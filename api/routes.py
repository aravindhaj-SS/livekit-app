import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from agent.lead_agent import classify_in_scope_form
from core.config import settings
from core.dashboard_auth import dash_token_valid
from core.database import get_all_leads, get_lead, save_lead
from core.model_config import MODEL_CATALOG, current_selection, set_engine_and_model
from core.pending_calls import register as register_pending_call

router = APIRouter()
logger = logging.getLogger(__name__)


def _to_exotel_number(phone: str) -> str:
    """Exotel's Calls/connect API expects E.164 (e.g. "+919667601219").
    Assumes India (+91) when given a bare 10-digit local number."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = f"91{digits}"
    return f"+{digits}"


class LeadRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    company: str = Field(..., min_length=1, max_length=100)
    phone_number: str = Field(..., pattern=r"^\+?[1-9]\d{6,14}$")
    interest_area: str = Field(..., min_length=1, max_length=200)
    email_id: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post("/callback")
async def callback(lead: LeadRequest, request: Request):
    logger.info(f"New lead: {lead.name} | {lead.phone_number}")

    # Pre-call form filter — block prank/out-of-scope submissions before we
    # save, or place a call. Fails open on any error (see classify_in_scope_form).
    in_scope, reason = await classify_in_scope_form(lead.interest_area)
    if not in_scope:
        logger.warning(
            f"Form REJECTED as out-of-scope — name={lead.name!r}, "
            f"phone={lead.phone_number!r}, interest={lead.interest_area!r}, reason={reason!r}"
        )
        raise HTTPException(
            status_code=422,
            detail={
                "status": "out_of_scope",
                "reason": reason,
                "message": (
                    "Sorry — this looks outside what we help with. Our team "
                    "focuses on software, AI, web development, and hiring "
                    "automation. If you think this is a mistake, please "
                    "rephrase your request and try again."
                ),
            },
        )

    lead_data = lead.model_dump()
    lead_id = await save_lead(lead_data)

    # Exotel's Voicebot Applet URL is a static App Bazaar flow (EXOTEL_APP_ID),
    # not a per-call resolver — voice/gemini_bridge.py's _on_start looks the
    # lead back up by phone number via this registration.
    customer_number = _to_exotel_number(lead.phone_number)
    register_pending_call(customer_number, lead_id)

    connect_url = f"https://{settings.EXOTEL_SUBDOMAIN}/v1/Accounts/{settings.EXOTEL_ACCOUNT_SID}/Calls/connect.json"
    exoml_flow_url = f"http://my.exotel.com/{settings.EXOTEL_ACCOUNT_SID}/exoml/start_voice/{settings.EXOTEL_APP_ID}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                connect_url,
                auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN),
                data={
                    "From": customer_number,
                    "CallerId": settings.EXOTEL_CALLER_ID,
                    "Url": exoml_flow_url,
                    "CustomField": str(lead_id),
                },
            )
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:500]}
        if resp.status_code not in (200, 201, 202):
            logger.error(f"Exotel call failed ({resp.status_code}): {data}")
            raise HTTPException(status_code=500, detail=f"Call initiation failed: {data}")
        logger.info(f"Call initiated via Exotel for lead_id={lead_id}, customer_number={customer_number}, response={data}")
        return {"status": "success", "lead_id": lead_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error initiating Exotel call: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/leads")
async def list_leads():
    return {"leads": await get_all_leads()}


@router.get("/calls")
async def list_calls(direction: Optional[str] = None, dash_auth: Optional[str] = Cookie(default=None)):
    """Feeds the /dashboard (outbound) and /dashboard/inbound UIs. Gated by
    the same cookie as those pages since this exposes full call transcripts
    and lead PII. direction=outbound|inbound filters to one dashboard's
    rows; omitted returns everything."""
    if not dash_token_valid(dash_auth):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if direction not in (None, "inbound", "outbound"):
        raise HTTPException(status_code=400, detail="direction must be 'inbound' or 'outbound'")

    leads = await get_all_leads(direction=direction)
    calls = []
    for lead in leads:
        try:
            transcript = json.loads(lead["transcript"]) if lead.get("transcript") else []
        except (TypeError, ValueError):
            transcript = []
        try:
            lead_state = json.loads(lead["lead_state"]) if lead.get("lead_state") else None
        except (TypeError, ValueError):
            lead_state = None
        metrics = (lead_state or {}).get("call_metrics") or {}
        calls.append({
            "id": lead.get("id"),
            "direction": lead.get("direction") or "outbound",
            "name": lead.get("name"),
            "company": lead.get("company"),
            "phone_number": lead.get("phone_number"),
            "email_id": lead.get("email_id"),
            "interest_area": lead.get("interest_area"),
            "preferred_language": lead.get("preferred_language"),
            "classification": lead.get("classification"),
            "discovery_call_scheduled": bool(lead.get("discovery_call_scheduled")),
            "meeting_link": lead.get("meeting_link"),
            "budget": lead.get("budget"),
            "timeline": lead.get("timeline"),
            "decision_maker_status": lead.get("decision_maker_status"),
            "callback_time": lead.get("callback_time"),
            # Returning-caller support (see core/database.get_previous_calls,
            # voice/gemini_bridge.py's _apply_returning_caller_context) — a
            # one-line post-call summary, surfaced here mainly so this can be
            # eyeballed/verified against what the agent actually says on that
            # lead's next call.
            "call_summary": lead.get("call_summary"),
            "calendar_event_id": lead.get("calendar_event_id"),
            # AI-model token cost (USD) — see core/costs.py.
            "cost_usd": lead.get("cost_usd"),
            # Exotel telephony leg (INR, per-minute rate x whole minutes).
            "exotel_cost_inr": lead.get("exotel_cost_inr"),
            # Blended total (INR) — cost_usd converted via settings.USD_TO_INR
            # plus exotel_cost_inr. See core/costs.py.compute_call_cost.
            "total_cost_inr": lead.get("total_cost_inr"),
            "avg_latency_s": lead.get("avg_latency_s"),
            "recording_url": f"/api/recordings/{lead['id']}" if lead.get("recording_path") else None,
            # Which engine/model this call's cost was actually priced against
            # (core/costs.py's rate table is keyed on this pair) — surfaced so
            # the token/cost figures below can be cross-checked against the
            # rate that was actually applied.
            "engine": metrics.get("engine"),
            "model": metrics.get("model"),
            # Raw per-call token counts (see voice/gemini_bridge.py's
            # _on_metrics_collected) — the exact inputs to compute_call_cost(),
            # so cost_usd can be independently recomputed and cross-checked.
            "usage": metrics.get("usage"),
            "created_at": lead.get("created_at"),
            "updated_at": lead.get("updated_at"),
            "transcript": transcript,
            "lead_state": lead_state,
        })
    return JSONResponse(calls)


class ModelSelectionRequest(BaseModel):
    engine: str
    model: str


@router.get("/models")
async def list_models(dash_auth: Optional[str] = Cookie(default=None)):
    """Feeds the dashboards' Models picker: the full catalog (see
    core/model_config.py — every entry is a real, verified model for its
    provider) plus which one is currently selected for each direction."""
    if not dash_token_valid(dash_auth):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({"catalog": MODEL_CATALOG, "selection": current_selection()})


@router.post("/models/{direction}")
async def select_model(
    direction: str, body: ModelSelectionRequest, dash_auth: Optional[str] = Cookie(default=None)
):
    """Saves a new engine/model for one call direction. No restart needed:
    get_engine_and_model() (core/model_config.py) re-reads
    runtime_model_config.json from disk on every single call, with no
    caching layer anywhere — the very next call already picks up whatever
    was just saved here. A previous version of this endpoint triggered a
    full process restart "so it takes effect", which was never actually
    necessary given that re-read-per-call behavior, and had a real cost:
    it drops any call already in progress the instant systemd relaunches
    the process (confirmed in production logs — a live inbound call lost
    all of its captured data mid-conversation when a model switch elsewhere
    restarted the process out from under it). Removed rather than kept
    "just in case" — the mechanism it existed for doesn't need it."""
    if not dash_token_valid(dash_auth):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if direction not in ("inbound", "outbound"):
        raise HTTPException(status_code=400, detail="direction must be 'inbound' or 'outbound'")

    try:
        set_engine_and_model(direction, body.engine, body.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "saved", "direction": direction, "engine": body.engine, "model": body.model}


@router.get("/recordings/{lead_id}")
async def get_recording(lead_id: int, dash_auth: Optional[str] = Cookie(default=None)):
    """Streams a call's self-recorded WAV (see voice/gemini_bridge.py's
    _write_recording). Gated the same way as /api/calls — recordings aren't
    served from a public static path. The path served is always the one
    recorded in the DB for this lead_id, never a client-supplied path."""
    if not dash_token_valid(dash_auth):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    lead = await get_lead(lead_id)
    if not lead or not lead.get("recording_path"):
        raise HTTPException(status_code=404, detail="No recording for this call")
    return FileResponse(lead["recording_path"], media_type="audio/wav")
