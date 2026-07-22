"""Exotel bidirectional-audio WebSocket <-> Gemini Live bridge.

Lightweight architecture: build the agent's instructions ONCE per call
(agent/prompt.py's build_instructions) and let Gemini Live's own native
conversational ability + tool-calling carry the interaction — no per-turn
instruction rebuild, no live state-machine, no short-circuit classifiers.

This replaces an earlier per-turn design (rebuild the whole system prompt
every turn via update_instructions(), gate turns through a 9-state FSM)
that measurably hurt call quality: resending a multi-KB prompt every turn
has no prefix-caching benefit on Gemini Live (unlike the old Groq/OpenAI
cascade), and a watchdog that fired off caller-mic-silence alone collided
with the model's own in-flight generations — confirmed directly in a real
call's logs (generate_reply timeouts + "server cancelled tool calls"
correlated with 30-50s stalls, and repeated questions where a cancelled
tool call meant that turn's reported fields never landed).

The model reports what it learns via one tool (save_lead_info), called
opportunistically through the call, not once per turn. A background pass
at call end (agent/lead_agent.py's reconcile_transcript) re-runs the
deterministic extractors over the full transcript as a cheap safety net —
paid for after the call, not on the live turn path.

The watchdog now gates on AgentSession.agent_state, only counting silence
while the agent is actually "listening" — not "thinking" or "speaking" —
so it can no longer interrupt a generation that's still in progress.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import date
from typing import Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.agents.llm import function_tool
from livekit.agents.voice import io as agent_io
from livekit.plugins.google.realtime.realtime_api import RealtimeModel as GeminiRealtimeModel
from livekit.plugins.openai.realtime.realtime_model import RealtimeModel
from openai.types.realtime import RealtimeReasoning
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

from agent import lead_agent as la
from agent.lead_state import LeadState
from agent.prompt import build_instructions
from core.calendar import book_discovery_call
from core.config import settings
from core.costs import compute_call_cost_usd
from core.database import get_lead, save_meeting_link, update_lead_state
from core.pending_calls import consume as consume_pending_call
from core.rag import ask as rag_ask

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Exotel wire constants ────────────────────────────────────────────────────
EXOTEL_SAMPLE_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = EXOTEL_SAMPLE_RATE * FRAME_MS // 1000  # 160
FRAME_BYTES = FRAME_SAMPLES * 2  # 320 (16-bit PCM)

# ── Watchdog / safety-net timers ─────────────────────────────────────────────
WATCHDOG_TICK_S = 5.0
SILENCE_REPROMPT_MS = 15_000
SILENCE_HANGUP_MS = 35_000

# Cap on Gemini Live session reconnects per call — a known, currently-
# unresolved bug pattern in livekit-plugins-google can kill the session
# outright (see this module's _reconnect() docstring). Recover a couple of
# times, but if it keeps happening this call is having a genuinely bad time
# talking to Gemini — end gracefully rather than loop forever.
MAX_RECONNECTS = 2

_SILENCE_REPROMPT_LINE = "Sorry, are you still there?"
_SILENCE_HANGUP_LINE = "I'll let you go for now — feel free to reach out anytime. Have a great day."


def _decode_inbound_pcm16(payload_b64: str) -> np.ndarray:
    raw = base64.b64decode(payload_b64)
    return np.frombuffer(raw, dtype="<i2")


def _resample(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or pcm.size == 0:
        return pcm
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(src_rate, dst_rate)
    up, down = dst_rate // g, src_rate // g
    resampled = resample_poly(pcm.astype(np.float32), up, down)
    return np.clip(resampled, -32768, 32767).astype(np.int16)


def _say_exactly(line: str) -> str:
    return f'Say EXACTLY this and nothing else: "{line}"'


class ExotelAudioInput(agent_io.AudioInput):
    """Feeds Exotel's inbound 8kHz PCM16 frames into the AgentSession —
    the roomless equivalent of subscribing to a room participant's track."""

    def __init__(self):
        super().__init__(label="exotel")
        self._queue: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()

    def push(self, frame: rtc.AudioFrame) -> None:
        self._queue.put_nowait(frame)

    async def __anext__(self) -> rtc.AudioFrame:
        return await self._queue.get()


class ExotelAudioOutput(agent_io.AudioOutput):
    """Sink for the agent's spoken audio — resamples to 8kHz, frames into
    20ms/320-byte Exotel chunks, paces them in real time, and supports
    barge-in via clear_buffer(). The roomless equivalent of publishing a
    track into a room."""

    def __init__(self, send_frame, get_sid, on_segment_finished):
        super().__init__(label="exotel", capabilities=agent_io.AudioOutputCapabilities(pause=False))
        self._send_frame = send_frame
        self._get_sid = get_sid
        self._on_segment_finished = on_segment_finished
        self._buf = bytearray()
        self._next_frame_time: Optional[float] = None
        self._cleared = False
        self._captured_since_flush = False

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        self._cleared = False
        self._captured_since_flush = True
        pcm = np.frombuffer(frame.data, dtype="<i2")
        pcm8k = _resample(pcm, frame.sample_rate, EXOTEL_SAMPLE_RATE)
        self._buf.extend(pcm8k.tobytes())
        await self._drain_full_frames()

    async def _drain_full_frames(self) -> None:
        if self._next_frame_time is None:
            self._next_frame_time = time.monotonic()
        while len(self._buf) >= FRAME_BYTES and not self._cleared:
            chunk = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            await self._send_frame(chunk)
            self._next_frame_time += FRAME_MS / 1000
            sleep_for = self._next_frame_time - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    def flush(self) -> None:
        super().flush()
        if self._buf and not self._cleared:
            tail = bytes(self._buf).ljust(FRAME_BYTES, b"\x00")
            self._buf.clear()
            asyncio.create_task(self._send_frame(tail))
        self._next_frame_time = None
        if self._captured_since_flush:
            self._captured_since_flush = False
            self.on_playback_finished(playback_position=0, interrupted=False)
            if self._on_segment_finished:
                self._on_segment_finished()

    def clear_buffer(self) -> None:
        """Barge-in: drop whatever's buffered and flush Exotel's own jitter
        buffer of not-yet-played audio."""
        self._cleared = True
        self._buf.clear()
        self._next_frame_time = None
        sid = self._get_sid()
        if sid:
            asyncio.create_task(self._send_clear(sid))
        if self._captured_since_flush:
            self._captured_since_flush = False
            self.on_playback_finished(playback_position=0, interrupted=True)

    async def _send_clear(self, sid: str) -> None:
        try:
            await self._send_frame(None, clear_sid=sid)
        except Exception:
            pass


class MiraAgent(Agent):
    """Static instructions built once from lead identity. Tools are auto-
    discovered and auto-executed by AgentSession."""

    def __init__(self, state: LeadState):
        self.lead_state = state
        self.seen_chunk_ids: list[str] = []
        self.last_rag_topic: str = ""
        self.call_complete = False
        super().__init__(instructions=build_instructions(state))

    @function_tool(
        name="save_lead_info",
        description=(
            "Call this whenever you learn something new about the lead during "
            "the call — a budget figure, a timeline, their decision-maker role, "
            "a confirmed or corrected email, their availability, or that they've "
            "agreed to a discovery call. Call it as many times as needed through "
            "the call. When the call is naturally wrapping up, call it once more "
            "with call_complete=true."
        ),
    )
    async def save_lead_info(
        self,
        budget: str | None = None,
        timeline: str | None = None,
        decision_maker_status: str | None = None,
        email: str | None = None,
        availability: str | None = None,
        discovery_call_agreed: bool = False,
        out_of_scope: bool = False,
        call_complete: bool = False,
        notes: str | None = None,
    ) -> str:
        s = self.lead_state
        if budget:
            s.budget = budget
        if timeline:
            s.timeline = timeline
        if decision_maker_status:
            s.decision_maker_status = decision_maker_status
        if email:
            s.email_id = email
            s.email_confirmed = True
        if availability:
            s.availability_notes = availability
            parsed = la.extract_preferred_slot(availability, date.today())
            if parsed:
                s.preferred_slot = parsed
        if discovery_call_agreed:
            s.discovery_call_agreed = True
        if out_of_scope:
            s.out_of_scope = True
        if notes:
            s.pain = notes
        if call_complete:
            self.call_complete = True
        logger.info(
            f"save_lead_info: budget={s.budget!r} timeline={s.timeline!r} "
            f"dm={s.decision_maker_status!r} email={s.email_id!r} "
            f"slot={s.preferred_slot!r} discovery_agreed={s.discovery_call_agreed} "
            f"out_of_scope={s.out_of_scope} call_complete={self.call_complete}"
        )
        return "ok"

    @function_tool(
        name="search_knowledge_base",
        description=(
            "Search Swaran Soft's product/knowledge base for grounded facts "
            "before answering ANY question about what the company offers, how "
            "it works, pricing, deployment, integrations, or capabilities. "
            "Never answer such a question from memory without calling this "
            "first. Returns the authoritative context text, or an empty result "
            "if nothing relevant was found — treat an empty result as 'you "
            "don't know', never guess."
        ),
    )
    async def search_knowledge_base(self, query: str) -> str:
        resolved_query = query
        if self.last_rag_topic and len(query.split()) <= 6:
            resolved_query = f"{self.last_rag_topic} {query}"
        filters = None
        interest = (self.lead_state.interest_area or "").lower()
        if "hir" in interest or "recruit" in interest:
            filters = {"industry": "recruitment", "solution_type": "recruitment_ai"}
        context, chunk_ids = await rag_ask(
            resolved_query, filters=filters, excluded_chunk_ids=list(self.seen_chunk_ids)
        )
        self.seen_chunk_ids.extend(chunk_ids)
        self.last_rag_topic = query
        logger.info(f"search_knowledge_base: query={query!r} -> {len(context)} chars")
        return context or "No specific information found in the knowledge base for this."


class CallSession:
    def __init__(self, ws: WebSocket):
        self._ws = ws
        self._sid: str = ""
        self.state: Optional[LeadState] = None
        self._agent: Optional[MiraAgent] = None
        self._agent_session: Optional[AgentSession] = None
        self._audio_input: Optional[ExotelAudioInput] = None
        self._ended = False
        self._reconnecting = False
        self._reconnect_count = 0

        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_activity_at = time.monotonic()
        self._reprompt_fired = False
        self._session_start_t = time.monotonic()

    async def run(self):
        try:
            while True:
                raw = await self._ws.receive_text()
                msg = json.loads(raw)
                evt = msg.get("event")
                if evt == "connected":
                    logger.info("Exotel stream connected")
                elif evt == "start":
                    await self._on_start(msg)
                elif evt == "media":
                    await self._on_media(msg)
                elif evt == "stop":
                    await self._end()
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("CallSession.run crashed")
        finally:
            await self._end()

    async def _on_start(self, msg: dict):
        start = msg.get("start", {})
        self._sid = (
            msg.get("stream_sid") or msg.get("streamSid")
            or start.get("stream_sid") or start.get("streamSid") or ""
        )
        params = start.get("custom_parameters") or start.get("customParameters") or {}
        to_number = start.get("from") or start.get("to") or ""

        lead_id = consume_pending_call(to_number) if to_number else None
        if lead_id is None:
            lead_id_raw = params.get("custom_identifier") or params.get("lead_id")
            lead_id = int(lead_id_raw) if lead_id_raw else None

        lead_row = await get_lead(lead_id) if lead_id else None
        if lead_row:
            self.state = LeadState(
                name=lead_row.get("name") or "",
                company=lead_row.get("company") or "",
                phone_number=lead_row.get("phone_number") or "",
                interest_area=lead_row.get("interest_area") or "",
                lead_id=lead_id,
                email_id=lead_row.get("email_id"),
            )
        else:
            logger.warning(f"No lead found for lead_id={lead_id!r} to_number={to_number!r} — using bare state")
            self.state = LeadState(lead_id=lead_id)

        await self._start_agent_session()

        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._last_activity_at = time.monotonic()

    async def _start_agent_session(self, recovery_line: Optional[str] = None):
        """(Re)creates the Gemini Live session. Reuses the SAME LeadState so
        a reconnect never loses anything already learned. Called once from
        _on_start, and again from _on_agent_session_error on an unrecoverable
        session error — that error otherwise leaves the caller on a dead,
        unresponsive line (the session dies with no reconnect attempt)."""
        self._agent = MiraAgent(self.state)
        if settings.DISABLE_TOOLS_FOR_LATENCY_TEST:
            await self._agent.update_tools([])

        # Recorded so compute_call_cost_usd() prices this call against the
        # engine that actually handled it, not whichever is currently
        # configured — matters if REALTIME_ENGINE changes between calls.
        self.state.call_metrics["engine"] = settings.REALTIME_ENGINE
        self.state.call_metrics["model"] = (
            settings.GEMINI_MODEL if settings.REALTIME_ENGINE == "gemini" else settings.OPENAI_REALTIME_MODEL
        )

        if settings.REALTIME_ENGINE == "gemini":
            # One-off comparison test: Gemini Live's native-audio model, same
            # static prompt, no tools (paired with DISABLE_TOOLS_FOR_LATENCY_TEST
            # above) — isolates raw conversational latency/quality from the
            # tool-calling defect that made us move to OpenAI originally.
            model = GeminiRealtimeModel(
                api_key=settings.GEMINI_API_KEY,
                model=settings.GEMINI_MODEL,
                voice="Kore",
            )
        else:
            model = RealtimeModel(
                api_key=settings.OPEN_AI_API_KEY,
                model=settings.OPENAI_REALTIME_MODEL,
                voice="marin",
                speed=1.15,
                # gpt-realtime-2.1 is a reasoning-capable Realtime model — the
                # plugin leaves reasoning unset by default, which lets the server
                # fall back to its own default effort. Pinning "minimal" removes
                # that hidden thinking time from every turn, including ones that
                # just decide whether to call a tool.
                reasoning=RealtimeReasoning(effort="minimal"),
                # The plugin's own default (unset) is semantic_vad/eagerness=medium,
                # which waits to be semantically sure the caller is done talking
                # before it even starts generating — real extra latency on top of
                # the reasoning cost above. A fixed, short silence window responds
                # far faster and is what we actually want for a phone call.
                turn_detection=ServerVad(
                    type="server_vad",
                    silence_duration_ms=350,
                    prefix_padding_ms=300,
                    create_response=True,
                    interrupt_response=True,
                ),
            )
        self._agent_session = AgentSession(llm=model)

        self._audio_input = ExotelAudioInput()
        audio_output = ExotelAudioOutput(
            send_frame=self._send_frame,
            get_sid=lambda: self._sid,
            on_segment_finished=self._on_segment_finished,
        )

        self._agent_session.input.audio = self._audio_input
        self._agent_session.output.audio = audio_output
        self._agent_session.on("error", self._on_agent_session_error)
        self._agent_session.on("agent_state_changed", self._on_agent_state_changed)
        self._agent_session.on("metrics_collected", self._on_metrics_collected)

        await self._agent_session.start(self._agent)

        if recovery_line:
            instructions = _say_exactly(recovery_line)
        else:
            instructions = (
                f"This is the very first turn of the call. Speak a short, warm, two-"
                f"sentence opener: sentence 1 introduces yourself as Mira from Swaran "
                f"Soft and references that this call is about "
                f"{self.state.interest_area or 'their inquiry'}; sentence 2 asks a "
                f"plain yes/no availability question like 'do you have a quick moment "
                f"to chat?'. Do not pitch anything yet."
            )
        try:
            self._agent_session.generate_reply(instructions=instructions)
        except Exception:
            logger.exception("Opening generate_reply failed")

    def _on_agent_session_error(self, ev):
        logger.error(f"AgentSession error: {ev}")
        if getattr(getattr(ev, "error", None), "recoverable", True):
            return
        if self._ended or self._reconnecting:
            return
        if self._reconnect_count >= MAX_RECONNECTS:
            logger.error(
                f"Gemini Live session failed {self._reconnect_count} times — giving up, ending call"
            )
            asyncio.create_task(self._end())
            return
        self._reconnect_count += 1
        self._reconnecting = True
        asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """A known, currently-unresolved bug pattern in livekit-plugins-google
        (see multiple open GitHub issues — different root causes, same 1007
        close code the plugin blanket-labels "context exhausted") can kill
        the Gemini Live session outright, mid-call, with no built-in retry.
        Rather than chase the exact server-side trigger further, recover
        gracefully: tear down the dead session and start a fresh one with
        the SAME LeadState, so nothing already learned is lost, and let the
        caller know we're back instead of leaving them on a silent line."""
        old_session = self._agent_session
        try:
            if old_session:
                await old_session.aclose()
        except Exception:
            pass
        if self._ended:
            self._reconnecting = False
            return
        try:
            await self._start_agent_session(
                recovery_line="Sorry about that, seems we got disconnected for a second — could you say that again?"
            )
        except Exception:
            logger.exception("Reconnect failed")
            await self._end()
        finally:
            self._reconnecting = False

    async def _on_media(self, msg: dict):
        if not self._audio_input:
            return
        media = msg.get("media", {})
        payload = media.get("payload")
        if not payload:
            return
        pcm8k = _decode_inbound_pcm16(payload)
        if pcm8k.size == 0:
            return
        frame = rtc.AudioFrame(
            data=pcm8k.tobytes(), sample_rate=EXOTEL_SAMPLE_RATE, num_channels=1, samples_per_channel=pcm8k.size,
        )
        self._audio_input.push(frame)

        rms = float(np.sqrt(np.mean(pcm8k.astype(np.float32) ** 2))) if pcm8k.size else 0.0
        if rms > 300:
            self._last_activity_at = time.monotonic()
            self._reprompt_fired = False

    async def _send_frame(self, frame: Optional[bytes], clear_sid: Optional[str] = None):
        if clear_sid:
            try:
                await self._ws.send_text(json.dumps({"event": "clear", "stream_sid": clear_sid}))
            except Exception:
                pass
            return
        if not self._sid or frame is None:
            return
        try:
            await self._ws.send_text(json.dumps({
                "event": "media",
                "stream_sid": self._sid,
                "media": {"payload": base64.b64encode(frame).decode()},
            }))
        except Exception:
            pass

    def _extract_transcript(self) -> list[dict]:
        if not self._agent_session:
            return []
        try:
            return [
                {"role": m.role, "content": m.text_content}
                for m in self._agent_session.history.messages()
                if m.role in ("user", "assistant") and m.text_content
            ]
        except Exception:
            logger.exception("Failed to extract transcript from session history")
            return []

    def _on_segment_finished(self):
        if self._agent and self._agent.call_complete and not self._ended:
            asyncio.create_task(self._end())

    def _on_agent_state_changed(self, ev):
        # Only "listening" means the agent is genuinely waiting on the caller —
        # "thinking"/"speaking" must never count toward the silence watchdog,
        # which is exactly what caused the watchdog to collide with in-flight
        # generations before (see this module's docstring).
        if ev.new_state == "listening":
            self._last_activity_at = time.monotonic()
            self._reprompt_fired = False

    def _on_metrics_collected(self, ev):
        """Accumulates OpenAI Realtime token usage for the dashboard's
        per-call cost estimate (core/costs.py), and logs per-response
        ttft/duration — the actual model latency numbers, isolated from
        conversation content (unlike the tool-call timestamps elsewhere in
        this log, which include however long the caller spent talking)."""
        if not self.state:
            return
        m = getattr(ev, "metrics", ev)
        ttft = getattr(m, "ttft", None)
        duration = getattr(m, "duration", None)
        if ttft is not None:
            logger.info(f"realtime turn latency: ttft={ttft:.3f}s duration={duration:.3f}s")
        itd = getattr(m, "input_token_details", None)
        otd = getattr(m, "output_token_details", None)
        if itd is None and otd is None:
            return
        usage = self.state.call_metrics.setdefault("usage", {
            "text_input": 0, "text_input_cached": 0, "text_output": 0,
            "audio_input": 0, "audio_input_cached": 0, "audio_output": 0,
        })
        if itd is not None:
            usage["text_input"] += itd.text_tokens or 0
            usage["audio_input"] += itd.audio_tokens or 0
            cached = getattr(itd, "cached_tokens_details", None)
            if cached is not None:
                usage["text_input_cached"] += cached.text_tokens or 0
                usage["audio_input_cached"] += cached.audio_tokens or 0
        if otd is not None:
            usage["text_output"] += otd.text_tokens or 0
            usage["audio_output"] += otd.audio_tokens or 0

    # ── Watchdog ─────────────────────────────────────────────────────────────

    async def _watchdog_loop(self):
        while not self._ended:
            await asyncio.sleep(WATCHDOG_TICK_S)
            if self._ended:
                return
            if not self._agent_session or self._agent_session.agent_state != "listening":
                continue
            idle_ms = (time.monotonic() - self._last_activity_at) * 1000
            if not self._reprompt_fired and idle_ms >= SILENCE_REPROMPT_MS:
                self._reprompt_fired = True
                self._agent_session.generate_reply(instructions=_say_exactly(_SILENCE_REPROMPT_LINE))
            elif self._reprompt_fired and idle_ms >= SILENCE_HANGUP_MS:
                if self.state:
                    self.state.classification = self.state.classification or "Cold"
                self._agent_session.generate_reply(instructions=_say_exactly(_SILENCE_HANGUP_LINE))
                await asyncio.sleep(3.0)
                await self._end()
                return

    # ── Teardown ─────────────────────────────────────────────────────────────

    async def _end(self):
        if self._ended:
            return
        self._ended = True

        if self._watchdog_task:
            self._watchdog_task.cancel()

        s = self.state
        if s and s.lead_id:
            s.call_metrics["call_duration_s"] = round(time.monotonic() - self._session_start_t, 1)
            s.call_metrics["cost_usd"] = compute_call_cost_usd(
                s.call_metrics.get("usage", {}),
                engine=s.call_metrics.get("engine", "openai"),
                model=s.call_metrics.get("model"),
            )
            transcript = self._extract_transcript()

            # Background reconciliation: fill in anything the model's own
            # tool-reporting missed, from the full transcript. Never
            # overrides what the model already reported.
            found = la.reconcile_transcript(transcript)
            if s.budget is None:
                s.budget = found["budget"]
            if s.timeline is None:
                s.timeline = found["timeline"]
            if s.decision_maker_status is None:
                s.decision_maker_status = found["decision_maker_status"]
            if not s.email_id:
                s.email_id = found["email_id"]

            if s.classification is None:
                if s.out_of_scope:
                    s.classification = "Cold"
                elif s.discovery_call_agreed:
                    s.classification = "Hot"
                elif s.budget or s.timeline or s.decision_maker_status:
                    s.classification = "Warm"

            await update_lead_state(s.lead_id, s.to_dict(), s.classification, transcript=transcript)

        if self._agent_session:
            try:
                await self._agent_session.aclose()
            except Exception:
                pass

        try:
            await self._ws.close()
        except Exception:
            pass

        if s and s.discovery_call_agreed and s.email_id:
            try:
                meet_link = await book_discovery_call(
                    lead_name=s.name,
                    lead_email=s.email_id,
                    organizer_email=settings.CALENDAR_ORGANIZER_EMAIL,
                    preferred_slot=s.preferred_slot,
                )
                if meet_link and s.lead_id:
                    s.meeting_link = meet_link
                    s.discovery_call_scheduled = True
                    await save_meeting_link(s.lead_id, meet_link)
            except Exception:
                logger.exception("book_discovery_call failed")


@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    await CallSession(ws).run()
