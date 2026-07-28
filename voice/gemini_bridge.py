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
import os
import random
import time
import wave
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google.genai import types as genai_types
from livekit import rtc
from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.llm import function_tool
from livekit.agents.voice import io as agent_io
from livekit.plugins.google.realtime.realtime_api import RealtimeModel as GeminiRealtimeModel
from livekit.plugins.openai.realtime.realtime_model import RealtimeModel
from openai.types.realtime import RealtimeReasoning
from openai.types.realtime.realtime_audio_input_turn_detection import ServerVad

from agent import lead_agent as la
from agent.lead_state import LeadState
from agent.prompt import build_instructions
from core.calendar import book_discovery_call, reschedule_discovery_call
from core.config import settings
from core.costs import compute_call_cost
from core.database import (
    create_inbound_lead,
    get_lead,
    get_previous_calls,
    save_meeting_link,
    update_call_summary,
    update_lead_state,
)
from core.model_config import get_engine_and_model, get_model_settings
from core.pending_calls import consume as consume_pending_call
from core.pending_calls import normalize_phone
from core.rag import ask as rag_ask

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Exotel wire constants ────────────────────────────────────────────────────
EXOTEL_SAMPLE_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = EXOTEL_SAMPLE_RATE * FRAME_MS // 1000  # 160
FRAME_BYTES = FRAME_SAMPLES * 2  # 320 (16-bit PCM)

# ── Call recordings — self-recorded (see CallSession._write_recording) ──────
RECORDINGS_DIR = "recordings"
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# gemini-3.1 only (see _gemini_greeting_watchdog) — every other engine now
# confirms/retries the opening line deterministically via the SpeechHandle
# returned by generate_reply() (see OPENING_LINE_MAX_ATTEMPTS below and
# _ensure_opening_line_spoken/_speak_literal), since gemini-3.1's
# speech-trigger path has no such handle to confirm against and still needs
# a state-and-timer fallback. Was 6.0 — lowered now that this model's own
# primary path (_trigger_gemini_via_synthetic_turn) is a ~1-3s synthetic-turn
# call, not the ~4-8s WAV playback that used to be the only option; 6s of
# blind waiting no longer matches the actual expected timing. How long to
# wait after starting a session before assuming the opening line never
# actually produced audio and retrying it once.
GREETING_CONFIRM_TIMEOUT_S = 3.0

# AgentSession.agent_state values that mean a generation is plausibly still
# being produced (livekit/agents/voice/events.py: AgentState = Literal[
# "initializing", "idle", "listening", "thinking", "speaking"]). Retrying
# generate_reply() while in one of these is exactly what produced OpenAI's
# "conversation_already_has_active_response" error in production — a second
# response.create colliding with one that's still in flight (OpenAI's plugin
# never guards against this itself; confirmed in the installed SDK).
_GENERATION_ACTIVE_STATES = {"thinking", "speaking"}

# Once the gemini-3.1 greeting watchdog finds a generation still active at
# the initial GREETING_CONFIRM_TIMEOUT_S check, keep re-checking at this
# interval instead of retrying blind, up to this total bound. Was 20.0 —
# lowered along with GREETING_CONFIRM_TIMEOUT_S now that the synthetic-turn
# fast path (_trigger_gemini_via_synthetic_turn) is the expected common case
# (~1-3s) and the WAV fallback (~4-8s) only kicks in when that isn't
# available; 20s of extra blind waiting on top of either no longer matches
# reality and was part of how a stuck greeting could stretch toward a
# minute-plus of dead air before this class of fix.
GREETING_WATCHDOG_POLL_S = 2.0
GREETING_WATCHDOG_MAX_WAIT_S = 8.0

# For engines that support generate_reply(): how many times
# _ensure_opening_line_spoken retries the opening line if the SpeechHandle
# confirms it came back empty (see that method's docstring for the traced
# root cause), and how long to back off between attempts. Each failed
# attempt resolves quickly (the empty response closes almost immediately —
# ttft=-1/duration=0.000 in the logs, not a multi-second stall), so a few
# tight retries land a real greeting within a second or two rather than
# leaving the caller on silence until they speak first themselves.
#
# Backoff was 1.0s, then 0.3s — confirmed in production logs (2026-07-24,
# gpt-realtime, both directions) that this empty-first-response glitch is
# common, not rare, and frequently needs 2 attempts, sometimes 3, to land a
# real greeting. Each second of backoff was pure, avoidable dead air on top
# of that, since the failure itself resolves near-instantly (duration=0.000)
# and isn't a rate-limit/overload signal that would call for real spacing.
# Dropped further to 0.1s on request — still non-zero (not a tight spin
# loop hammering the API back to back), but as close to negligible as
# reasonable given no evidence any spacing here is actually load-bearing.
OPENING_LINE_MAX_ATTEMPTS = 3
OPENING_LINE_RETRY_BACKOFF_S = 0.1

# Upper bound on how long a single generate_reply() attempt is allowed to
# take before _speak_literal gives up on it and reports failure (so a
# pathological hang can't wedge the retry loop indefinitely). The realtime
# plugin's own internal generate_reply timeout is 10s (realtime_model.py);
# this adds margin for the confirmed line to actually finish playing out
# afterward. Used for mid-conversation reprompt/hangup lines, where a real,
# legitimately-long turn (tool calls, slow generation) can be in flight and
# deserves the room.
GENERATE_REPLY_PLAYOUT_TIMEOUT_S = 15.0

# Opening-line-specific timeout — deliberately tighter than the general one
# above, but NOT as tight as the empty-response failure mode alone would
# suggest. That failure mode (ttft=-1/duration=0.000) does resolve in under
# a second — but a genuinely successful greeting response is a separate,
# slower case: observed directly in production (2026-07-24, lead 84,
# gpt-realtime) taking ~5.03s call-start-to-confirmed on an otherwise
# perfectly healthy call. A 5.0s timeout caught that in-flight *successful*
# response 20ms before it would have confirmed on its own, firing a
# spurious retry warning — harmless that time only because
# _ensure_opening_line_spoken's _greeting_confirmed check (fed by real audio
# arriving via _record_agent_frame, independent of this timeout) happened to
# already be true by the next loop iteration, so no second line was actually
# spoken. Not a coincidence worth relying on twice — widened the margin
# instead. Combined with OPENING_LINE_MAX_ATTEMPTS/OPENING_LINE_RETRY_BACKOFF_S,
# this bounds the worst realistic case for the whole retry loop to ~27s
# instead of ~48s — still a real improvement over the previously possible
# multi-minute-feeling silence, without cutting so close to a normal
# successful response that the retry path second-guesses it.
OPENING_LINE_PLAYOUT_TIMEOUT_S = 8.0

# OpenAI Realtime's error code for the collision above (seen verbatim in
# production logs). Recoverable — the underlying session is fine — but left
# completely unhandled it silently drops whatever turn triggered it (our own
# retry, or the caller's own utterance racing the server's own
# create_response=True auto-reply). _recover_dropped_turn nudges the model
# to respond to conversation history once the colliding generation finishes.
_ACTIVE_RESPONSE_COLLISION_CODE = "conversation_already_has_active_response"
RESPONSE_COLLISION_RECOVERY_MAX_WAIT_S = 10.0
RESPONSE_COLLISION_RECOVERY_COOLDOWN_S = 5.0

# ── Gemini speech trigger (see _speak_literal's docstring) ──────────────────
# For any Gemini model with "3.1" in its name, generate_reply()/update_chat_ctx()
# are both hard no-ops in the plugin, AND — confirmed by testing directly
# against the live API, bypassing the plugin entirely — the raw
# google.genai.live.AsyncSession.send_client_content() method produces zero
# audio for ANY injected text content on this model, synthesized speech
# included (tested: robotic espeak, and Sarvam's neural bulbul:v3 voice at two
# different lengths — all silent). Only genuine human speech, fed through the
# normal realtime audio-input path, reliably triggers a real spoken response
# (confirmed with a real recorded "Hello" — the model transcribed it correctly
# and replied in character, following the system prompt's OPENING rule).
# This is a short, deliberately-recorded "Hello" (a real person, recorded
# specifically for this purpose — not a customer's voice, not synthetic)
# played into the audio-input pipeline exactly like real caller audio would
# arrive, to nudge the model's own server-side VAD into starting a turn. The
# model then composes its own reply guided by the system prompt (which
# already covers both call directions) — this is NOT a literal scripted line
# the way _say_exactly() is for models that support generate_reply(); it's a
# reliable way to make the model speak first at all, not a verbatim-text
# guarantee.
GEMINI_SPEECH_TRIGGER_PATH = "assets/gemini_speech_trigger.wav"
SPEECH_TRIGGER_SAMPLE_RATE = 16000  # matches Gemini's own INPUT_AUDIO_SAMPLE_RATE
SPEECH_TRIGGER_PADDING_S = 0.5
SPEECH_TRIGGER_SETTLE_S = 2.0
_speech_trigger_pcm: Optional[np.ndarray] = None

# How long _trigger_gemini_via_synthetic_turn polls for RealtimeSession's
# internal _active_session to appear before giving up and falling back to
# the WAV trigger. Standalone verification against the live API showed the
# raw session ready in well under a second (0.4-0.76s) — this leaves real
# margin above that before conceding the fast path isn't available this call.
GEMINI_RAW_SESSION_POLL_TIMEOUT_S = 2.0


def _load_speech_trigger() -> Optional[np.ndarray]:
    """Loads and caches assets/gemini_speech_trigger.wav (16kHz mono PCM16)
    once per process. Returns None if the file is missing so the caller can
    degrade to silence rather than crash the call."""
    global _speech_trigger_pcm
    if _speech_trigger_pcm is not None:
        return _speech_trigger_pcm
    try:
        with wave.open(GEMINI_SPEECH_TRIGGER_PATH, "rb") as wf:
            if wf.getframerate() != SPEECH_TRIGGER_SAMPLE_RATE or wf.getnchannels() != 1:
                logger.error(
                    f"{GEMINI_SPEECH_TRIGGER_PATH} must be {SPEECH_TRIGGER_SAMPLE_RATE}Hz mono — "
                    f"got {wf.getframerate()}Hz, {wf.getnchannels()}ch"
                )
                return None
            _speech_trigger_pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    except FileNotFoundError:
        logger.error(f"{GEMINI_SPEECH_TRIGGER_PATH} not found — Gemini speech trigger unavailable")
        return None
    return _speech_trigger_pcm

# ── Watchdog / safety-net timers ─────────────────────────────────────────────
WATCHDOG_TICK_S = 5.0
SILENCE_REPROMPT_MS = 15_000
SILENCE_HANGUP_MS = 35_000

# RMS floor (raw int16 PCM units) for treating an inbound Exotel frame as
# "the caller is actually talking" for OUR OWN silence-watchdog bookkeeping
# (_last_activity_at / _reprompt_fired below) — NOT a gate on what reaches
# the model. Every inbound frame is pushed to the realtime session
# unconditionally regardless of this value (see _on_media) — this constant
# only affects when our reprompt/hangup timers reset, it cannot by itself
# stop the model from reacting to background noise. Was 300, raised to 500
# on request. The actual lever against the model picking up and responding
# to background noise as speech is OpenAI's own server-side VAD sensitivity
# — see the `threshold` field on ServerVad in _start_agent_session.
CALLER_ACTIVITY_RMS_FLOOR = 500

# How long to wait, after the agent's own most recent utterance did NOT end in
# a question, before treating the call as concluded and hanging up. Distinct
# from — and much shorter than — SILENCE_REPROMPT_MS/SILENCE_HANGUP_MS above,
# which are for a caller going quiet mid-conversation while a question is
# still pending (that path is unchanged). This one exists because the only
# other way this app detects "the call is over" is the model calling
# save_lead_info(call_complete=True) — which never fires at all whenever
# DISABLE_TOOLS_FOR_LATENCY_TEST strips tools, and isn't guaranteed even when
# tools are on. This is a tool-independent, content-based backstop: this
# app's own CLOSING prompt instruction always ends a finished call with a
# plain statement, never a question, so "no question + caller says nothing
# back" is a reliable, low-risk signal the conversation is actually done —
# confirmed against a real call in leads.db where the agent's last line was
# a clean goodbye with no trailing "?" and the call just sat open until the
# caller hung up themselves.
# Was 8_000 — raised because the self-cancellation bug in _end() (see
# _watchdog_loop's create_task note) used to silently swallow this path's own
# hangup attempt, masking how trigger-happy 8s actually was. A real call in
# today's logs (lead 73) hit an 18.6s single turn (tool/RAG latency) — well
# past 8s of "caller silence" even though the conversation was still very
# much in progress — and would now genuinely hang up on that false trigger
# instead of silently failing to. 15s matches SILENCE_REPROMPT_MS, the
# threshold this codebase already uses elsewhere for "give the caller
# reasonable time before assuming something's wrong."
GRACEFUL_END_SILENCE_MS = 15_000

# Defense-in-depth, NOT a call-duration policy: _watchdog_loop normally only
# evaluates graceful-end while agent_state == "listening" (the only state
# where "the agent is done and the caller's gone quiet" actually means
# something). But if agent_state gets wedged off "listening" — e.g. a
# response collision (see _ACTIVE_RESPONSE_COLLISION_CODE) leaves the
# session's own turn-taking state stuck — that used to suppress hangup
# detection for the rest of the call, permanently, which is why only one
# call so far has ever hung up cleanly. This is how long agent_state can
# stay off "listening" before we stop trusting it and fall back to content-
# only detection (transcript + idle time) regardless of state. Set well
# above any real single turn's generation+playback time (Gemini 2.5's own
# documented ttft is ~2-5s) so it never fires while a genuine in-progress
# response — including the call's own closing line — is still being spoken.
STUCK_TURN_STATE_MS = 25_000

# Cap on Gemini Live session reconnects per call — a known, currently-
# unresolved bug pattern in livekit-plugins-google can kill the session
# outright (see this module's _reconnect() docstring). Recover a couple of
# times, but if it keeps happening this call is having a genuinely bad time
# talking to Gemini — end gracefully rather than loop forever.
MAX_RECONNECTS = 2

_SILENCE_REPROMPT_LINE = "Sorry, are you still there?"
_SILENCE_HANGUP_LINE = "I'll let you go for now — feel free to reach out anytime. Have a great day."

# ── Tool-call filler backstop (search_knowledge_base) ────────────────────────
# The prompt already tells the model to say a short filler the instant it
# decides to call search_knowledge_base (agent/prompt.py's TOOL-CALL FILLER
# section) — but that's the model's own judgment call, and in practice on
# gpt-realtime it doesn't reliably happen: the model goes straight to the
# tool call and sits silent until the result lands, which is exactly the
# "awkward silence during tool calls" this backstops. RunContext.with_filler
# (see MiraAgent.search_knowledge_base) fires ONE of these automatically if
# the session has been continuously idle for TOOL_FILLER_DWELL_S — i.e. the
# model hasn't even started composing anything of its own — so a model that
# DOES say its own filler never gets a redundant second one; only a model
# that goes silent gets this floor under it.
_TOOL_FILLER_PHRASES = [
    "Let me pull that up.",
    "One moment, please.",
    "Let me check on that.",
    "Give me just a second.",
    "Good question, one moment.",
]

# How long the session must sit continuously idle (no agent_state move to
# "thinking"/"speaking") before the backstop filler fires. Short enough that
# the caller never perceives real dead air — combined with the fixed
# filler's own near-immediate ttft (a short generate_reply, not a full
# composed answer), this keeps worst-case silence-to-sound in the
# sub-second range this was asked for — long enough to give the model a
# real chance to say its own filler first without a redundant second one.
TOOL_FILLER_DWELL_S = 0.15


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


def _build_opening_line(state: LeadState) -> str:
    """A literal, deterministic sentence — not a meta-instruction asking the
    model to compose an opener. The old opening ("This is the very first turn
    of the call. Speak a short, warm, two-sentence opener: ...") was itself
    the reason the agent was silent on pickup: it's exactly the kind of
    freeform instruction that produced zero-audio responses (confirmed in
    logs — ttft=-1/duration=0.000 on almost every call's first turn) and,
    separately, generate_reply() is a hard no-op for some realtime models
    entirely (see GREETING_CONFIRM_TIMEOUT_S above). A literal scripted line
    is what the reconnect recovery line and the silence reprompt/hangup lines
    already used successfully — this brings the opening line in line with
    that, for both directions."""
    if state.direction == "inbound":
        return "Thanks for calling Swaran Soft, this is Mira. Can I get your name, please?"
    name_bit = f", {state.name}" if state.name else ""
    interest = state.interest_area or "your inquiry"
    return (
        f"Hello{name_bit}, this is Mira calling from Swaran Soft about {interest}. "
        f"Do you have a quick moment to chat?"
    )


def _generate_reply_supported(engine: str, model: str | None) -> bool:
    """Mirrors livekit-plugins-google's own capability check (realtime_api.py:
    `mutable = "3.1" not in model`) — generate_reply() is a hard no-op for any
    Gemini model with "3.1" in its name, confirmed directly in logs
    ("generate_reply is not compatible with '<model>'"). OpenAI has no such
    restriction. Used to skip a retry we already know will fail identically,
    rather than to change what's actually attempted."""
    if engine != "gemini":
        return True
    return "3.1" not in (model or "")


def _build_openai_realtime_model(model_name: str, model_settings: dict) -> RealtimeModel:
    """Builds the OpenAI RealtimeModel for a given (model, settings) pair.
    Pulled out as its own function — rather than left inline in
    CallSession._start_agent_session — specifically so voice/warm_pool.py's
    pre-warmed inbound sessions are built from the EXACT same construction
    logic as a normal cold-started call, not a hand-copied approximation
    that could quietly drift out of sync with this one over time."""
    # reasoning_effort is only set in the catalog for models confirmed
    # reasoning-capable (gpt-realtime-2.1 and its mini variant) — omitted
    # entirely, not just set to a default, for models where it's
    # unconfirmed (gpt-realtime) or predates the feature outright
    # (gpt-4o-realtime-preview), since passing an unsupported request param
    # is a real setup-time risk, not just a wasted no-op.
    #
    # turn_detection: the plugin's own default (unset) is
    # semantic_vad/eagerness=medium, which waits to be semantically sure
    # the caller is done talking before it even starts generating — real
    # extra latency on top of the reasoning cost above. A fixed, short
    # silence window responds far faster and is what we actually want for
    # a phone call.
    turn_detection_settings = model_settings.get("turn_detection") or {}
    openai_kwargs = dict(
        api_key=settings.OPEN_AI_API_KEY,
        model=model_name,
        voice=model_settings.get("voice", "marin"),
        speed=model_settings.get("speed", 1.0),
        turn_detection=ServerVad(
            type="server_vad",
            silence_duration_ms=turn_detection_settings.get("silence_duration_ms", 350),
            prefix_padding_ms=turn_detection_settings.get("prefix_padding_ms", 300),
            # Audio-activity sensitivity (0.0-1.0, OpenAI's own default
            # ~0.5) — raised via the catalog to cut down on background
            # noise/line static being misread as speech starting. This is
            # a real field on ServerVad that was never set before
            # (confirmed via the installed SDK's own type —
            # openai.types.realtime.ServerVad.threshold).
            threshold=turn_detection_settings.get("threshold"),
            create_response=True,
            interrupt_response=True,
        ),
        # OpenAI's equivalent of Gemini's context_window_compression —
        # verified directly against the live Realtime API (a raw
        # session.update with this field) that it's accepted cleanly. Less
        # urgent here than for Gemini (OpenAI already caches 86-93% of
        # repeated context per this app's own measured per-call usage, vs
        # 0% for Gemini), but the SDK's own docs note "auto" truncation
        # still "helps improve cached token usage" by amortizing
        # truncations across turns instead of leaving the behavior on an
        # unstated default.
        truncation="auto",
        # Filters input audio before it ever reaches VAD/transcription —
        # never configured before. OpenAI's own docs describe this as
        # improving "VAD and turn detection accuracy (reducing false
        # positives)": directly targets background noise/line static
        # being misheard as speech and transcribed as (sometimes garbled,
        # sometimes wrong-language) caller input — confirmed happening in
        # production (2026-07-24, lead 99: transcript opened with a
        # hallucinated "Thanks a lot." from the caller, followed by
        # non-speech garbage transcribed as other languages). "far_field"
        # fits phone-line audio better than "near_field" (built for
        # close-talking headset mics).
        input_audio_noise_reduction=model_settings.get("noise_reduction"),
    )
    reasoning_effort = model_settings.get("reasoning_effort")
    if reasoning_effort:
        openai_kwargs["reasoning"] = RealtimeReasoning(effort=reasoning_effort)
    return RealtimeModel(**openai_kwargs)


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

    def __init__(self, send_frame, get_sid, on_segment_finished, on_frame=None):
        super().__init__(label="exotel", capabilities=agent_io.AudioOutputCapabilities(pause=False))
        self._send_frame = send_frame
        self._get_sid = get_sid
        self._on_segment_finished = on_segment_finished
        self._on_frame = on_frame
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
            if self._on_frame:
                self._on_frame(chunk)
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
            if self._on_frame:
                self._on_frame(tail)
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
        self._last_tool_filler: Optional[str] = None
        super().__init__(instructions=build_instructions(state))

    def _pick_tool_filler(self) -> str:
        """Never the same filler twice in a row (matches the prompt's own
        'vary it every time, never repeat back to back' instruction, enforced
        here too since this backstop path bypasses the model's own choice)."""
        choices = [p for p in _TOOL_FILLER_PHRASES if p != self._last_tool_filler]
        phrase = random.choice(choices or _TOOL_FILLER_PHRASES)
        self._last_tool_filler = phrase
        return phrase

    @function_tool(
        name="save_lead_info",
        description=(
            "Call this whenever you learn something new about the lead during "
            "the call — their name or company (inbound calls, where this isn't "
            "known up front), a budget figure, a timeline, their decision-maker "
            "role, a confirmed or corrected email, their availability, a "
            "callback request when they're busy, or that they've agreed to a "
            "discovery call. Call it as many times as needed through the call. "
            "When the call is naturally wrapping up, call it once more with "
            "call_complete=true."
        ),
    )
    async def save_lead_info(
        self,
        name: str | None = None,
        company: str | None = None,
        interest_area: str | None = None,
        budget: str | None = None,
        timeline: str | None = None,
        decision_maker_status: str | None = None,
        email: str | None = None,
        availability: str | None = None,
        discovery_call_agreed: bool = False,
        out_of_scope: bool = False,
        callback_requested: bool = False,
        callback_time: str | None = None,
        call_complete: bool = False,
        notes: str | None = None,
    ) -> str:
        """Report or update anything learned about this lead so far. Call it
        as many times as needed through the call, not just once at the end.

        Args:
            name: The caller's own stated name, exactly as they said it. Never guess, infer, or use a placeholder name — leave this unset (None) until the caller has clearly told you their name themselves.
            company: The lead's company name, as they stated it.
            interest_area: What they're calling about / interested in, in their own words.
            budget: Their stated budget range or figure, in English (see TOOL-CALL LANGUAGE in the system prompt).
            timeline: Their stated timeline for getting something in place, in English.
            decision_maker_status: Whether they're the decision maker or exploring on someone else's behalf, in their own words.
            email: A confirmed or corrected email address for this lead.
            availability: Their stated availability for a discovery call, in English day/time phrasing.
            discovery_call_agreed: True once they've explicitly agreed to a discovery call.
            out_of_scope: True if this call turned out to be outside what Swaran Soft helps with.
            callback_requested: True if they asked to be called back later because they're busy.
            callback_time: The time they asked to be called back, in English day/time phrasing.
            call_complete: True on the final call to this tool, once the conversation is naturally wrapping up.
            notes: Any other relevant detail worth recording that doesn't fit the fields above.
        """
        s = self.lead_state
        try:
            if name:
                s.name = name
            if company:
                s.company = company
            if interest_area:
                s.interest_area = interest_area
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
            if callback_requested:
                s.callback_requested = True
            if callback_time:
                parsed_callback = la.extract_preferred_slot(callback_time, date.today())
                s.callback_time = parsed_callback or callback_time
            if notes:
                s.pain = notes
            if call_complete:
                self.call_complete = True
            logger.info(
                f"save_lead_info: name={s.name!r} company={s.company!r} "
                f"budget={s.budget!r} timeline={s.timeline!r} "
                f"dm={s.decision_maker_status!r} email={s.email_id!r} "
                f"slot={s.preferred_slot!r} discovery_agreed={s.discovery_call_agreed} "
                f"callback_requested={s.callback_requested} callback_time={s.callback_time!r} "
                f"out_of_scope={s.out_of_scope} call_complete={self.call_complete}"
            )
        except Exception:
            # A tool-call exception must never propagate into the realtime
            # session — that's how a bad argument turns into a dead line.
            logger.exception("save_lead_info failed")
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
    async def search_knowledge_base(self, run_ctx: RunContext, query: str) -> str:
        def _fallback_filler(step: int):
            # Sync callable, no awaiting here (the scheduler forbids it) —
            # generate_reply() itself is sync, returns its SpeechHandle
            # immediately without waiting for the response.
            return run_ctx.session.generate_reply(
                instructions=_say_exactly(self._pick_tool_filler())
            )

        async with run_ctx.with_filler(_fallback_filler, delay=TOOL_FILLER_DWELL_S):
            return await self._do_search_knowledge_base(query)

    async def _do_search_knowledge_base(self, query: str) -> str:
        try:
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
        except Exception:
            logger.exception("search_knowledge_base failed")
            return "No specific information found in the knowledge base for this."


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
        self._session_start_wall = datetime.now()

        # Opening-line reliability safety net (see GREETING_CONFIRM_TIMEOUT_S).
        self._greeting_confirmed = False

        # When agent_state last moved OFF "listening" (None while listening,
        # or before the first state change) — lets _watchdog_loop tell "still
        # genuinely mid-turn" apart from "wedged", see STUCK_TURN_STATE_MS.
        self._non_listening_since: Optional[float] = None

        # Cooldown so a run of conversation_already_has_active_response
        # errors can't turn into a recovery retry storm — see
        # _recover_dropped_turn.
        self._last_collision_recovery_at = 0.0

        # Self-recorded call audio (issue: playable recording in dashboard).
        # Both buffers are 8kHz mono PCM16, time-aligned to _recording_start_t
        # so caller and agent audio land on a shared timeline (the caller
        # buffer is naturally continuous — Exotel streams inbound audio the
        # whole call — the agent buffer is silence-padded to the elapsed
        # wall-clock time on every chunk since it's only ever produced while
        # speaking).
        self._recording_start_t: Optional[float] = None
        self._caller_buf = bytearray()
        self._agent_buf = bytearray()

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
        except RuntimeError as e:
            # _end() (from the watchdog, call_complete, or a session error) can
            # close this WebSocket from a different task while this loop is
            # blocked on receive_text() — that wakes up as this specific
            # Starlette RuntimeError, not WebSocketDisconnect, even though the
            # call ended perfectly normally. Only swallow that exact expected
            # case; anything else still logs as a real crash.
            if self._ended and "WebSocket is not connected" in str(e):
                logger.info("CallSession.run: socket closed by _end() while a receive was pending (expected)")
            else:
                logger.exception("CallSession.run crashed")
        except Exception:
            logger.exception("CallSession.run crashed")
        finally:
            await self._end()

    async def _on_start(self, msg: dict):
        self._recording_start_t = time.monotonic()
        self._session_start_wall = datetime.now()

        start = msg.get("start", {})
        self._sid = (
            msg.get("stream_sid") or msg.get("streamSid")
            or start.get("stream_sid") or start.get("streamSid") or ""
        )
        params = start.get("custom_parameters") or start.get("customParameters") or {}
        caller_number = start.get("from") or start.get("to") or ""

        lead_id = consume_pending_call(caller_number) if caller_number else None
        if lead_id is None:
            lead_id_raw = params.get("custom_identifier") or params.get("lead_id")
            lead_id = int(lead_id_raw) if lead_id_raw else None

        lead_row = await get_lead(lead_id) if lead_id else None

        if lead_row:
            # A pre-registered outbound call — api/routes.py's /callback
            # registered this phone number before dialing, so full identity
            # is already known.
            self.state = LeadState(
                name=lead_row.get("name") or "",
                company=lead_row.get("company") or "",
                phone_number=lead_row.get("phone_number") or "",
                interest_area=lead_row.get("interest_area") or "",
                lead_id=lead_id,
                email_id=lead_row.get("email_id"),
                direction="outbound",
            )
        else:
            # No matching pre-registered outbound call — either a genuine
            # inbound call (someone dialed the Exophone directly) or an
            # outbound call whose registration couldn't be matched (e.g. the
            # 120s pending-call TTL expired before the callee picked up).
            # Either way, never drop the call silently: create a fresh lead
            # row so there's always somewhere to persist whatever gets
            # learned — this used to fall through to a lead_id=None "bare
            # state" that update_lead_state() then never persisted at all.
            if lead_id is not None:
                logger.warning(f"lead_id={lead_id!r} resolved but no matching row — treating as inbound")
            new_lead_id = await create_inbound_lead(caller_number)
            self.state = LeadState(lead_id=new_lead_id, phone_number=caller_number, direction="inbound")

        logger.info(
            f"Call started — direction={self.state.direction} lead_id={self.state.lead_id} "
            f"phone={caller_number!r}"
        )

        await self._apply_returning_caller_context(caller_number or self.state.phone_number)

        # Inbound only (see voice/warm_pool.py — outbound instructions embed
        # the specific lead's name/company/interest, so a generic pre-warmed
        # session can't be reused for it). Falls straight through to the
        # normal cold-start path below if the pool has nothing ready.
        used_warm = self.state.direction == "inbound" and await self._try_use_warm_inbound_session()
        if not used_warm:
            await self._start_agent_session()

        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._last_activity_at = time.monotonic()

    async def _apply_returning_caller_context(self, phone_for_lookup: str) -> None:
        """Looks up prior COMPLETED calls from this same phone number (see
        core/database.get_previous_calls) and, if found, marks self.state as
        a returning caller and backfills whatever identity fields this call
        doesn't already have (matters most for inbound, which otherwise
        starts every field blank). Must run BEFORE build_instructions() is
        used anywhere for this call — both the cold-start path
        (_start_agent_session, via MiraAgent.__init__) and the warm-pool path
        (_try_use_warm_inbound_session's update_instructions call) read
        self.state.is_returning_caller/previous_call.

        Never raises: a lookup failure just means this call proceeds as if
        it were a first-time caller — never something that can take a live
        call down."""
        try:
            normalized = normalize_phone(phone_for_lookup) if phone_for_lookup else ""
            if not normalized:
                return
            previous = await get_previous_calls(normalized, exclude_lead_id=self.state.lead_id, limit=1)
            if not previous:
                return

            prev = previous[0]
            self.state.is_returning_caller = True
            self.state.previous_call = {
                "name": prev.get("name"),
                "company": prev.get("company"),
                "interest_area": prev.get("interest_area"),
                "budget": prev.get("budget"),
                "timeline": prev.get("timeline"),
                "decision_maker_status": prev.get("decision_maker_status"),
                "email_id": prev.get("email_id"),
                "discovery_call_scheduled": bool(prev.get("discovery_call_scheduled")),
                "meeting_link": prev.get("meeting_link"),
                "calendar_event_id": prev.get("calendar_event_id"),
                "callback_time": prev.get("callback_time"),
                "summary": prev.get("call_summary"),
            }

            # Backfill onto THIS call's own state too — not just the
            # RETURNING CALLER prompt block below reads these, save_lead_info
            # and the existing email_step logic in agent/prompt.py both
            # already work off state.name/company/email_id directly, so
            # filling them in here means no other code needs to know about
            # previous_call at all. Never overwrites a value this call's own
            # source (form submission / save_lead_info) already set.
            if not self.state.name and prev.get("name"):
                self.state.name = prev["name"]
            if not self.state.company and prev.get("company"):
                self.state.company = prev["company"]
            if not self.state.interest_area and prev.get("interest_area"):
                self.state.interest_area = prev["interest_area"]
            if not self.state.email_id and prev.get("email_id"):
                self.state.email_id = prev["email_id"]
                self.state.email_confirmed = True

            logger.info(
                f"Returning caller detected — lead_id={self.state.lead_id} "
                f"previous_lead_id={prev.get('id')} name={self.state.name!r}"
            )
        except Exception:
            logger.exception("_apply_returning_caller_context failed — proceeding as a first-time caller")

    async def _start_agent_session(self, recovery_line: Optional[str] = None):
        """(Re)creates the Gemini Live session. Reuses the SAME LeadState so
        a reconnect never loses anything already learned. Called once from
        _on_start, and again from _on_agent_session_error on an unrecoverable
        session error — that error otherwise leaves the caller on a dead,
        unresponsive line (the session dies with no reconnect attempt)."""
        self._agent = MiraAgent(self.state)
        if settings.DISABLE_TOOLS_FOR_LATENCY_TEST:
            await self._agent.update_tools([])

        # Per-direction, dashboard-selectable — see core/model_config.py.
        # Recorded so compute_call_cost() prices this call against the
        # engine that actually handled it, not whichever is currently
        # selected — matters if the selection changes between calls.
        engine, model_name = get_engine_and_model(self.state.direction)
        self.state.call_metrics["engine"] = engine
        self.state.call_metrics["model"] = model_name
        model_settings = get_model_settings(engine, model_name)

        if engine == "gemini":
            # One-off comparison test: Gemini Live's native-audio model, same
            # static prompt, no tools (paired with DISABLE_TOOLS_FOR_LATENCY_TEST
            # above) — isolates raw conversational latency/quality from the
            # tool-calling defect that made us move to OpenAI originally.
            #
            # Gemini Live's function calls are non-blocking by design — the
            # model can keep talking while a tool runs in the background.
            # WHEN_IDLE ("add the result to context, prompt for output
            # without interrupting ongoing generation") is already the
            # plugin's default; the catalog sets it explicitly per model so
            # it's a deliberate choice, not an accident. INTERRUPT would cut
            # off a filler phrase mid-sentence to jump to the tool result —
            # the opposite of what TOOL-CALL FILLER PHRASES in agent/prompt.py
            # is asking the model to do.
            # NOT switched to FunctionResponseScheduling.SILENT despite it being
            # new_doc.txt's documented fix for the "duplicate spoken response
            # after a tool call" defect — tested it directly against the live
            # API first (side by side with WHEN_IDLE, same tool-calling
            # scenario): WHEN_IDLE correctly said the filler, then the real
            # answer, one clean turn. SILENT called the tool and then never
            # spoke again — the caller's actual question went unanswered
            # entirely, not just the duplicate. That's strictly worse than the
            # bug it claims to fix, so this stays on WHEN_IDLE.
            scheduling = (
                genai_types.FunctionResponseScheduling.INTERRUPT
                if model_settings.get("tool_response_scheduling") == "interrupt"
                else genai_types.FunctionResponseScheduling.WHEN_IDLE
            )
            # Context-window compression — new_doc.txt's other recommendation,
            # this one verified safe: smoke-tested directly against the live
            # API (connect, apply this exact config, run a full turn) with no
            # errors or behavior change on a short call. Gemini Live shows
            # zero prefix-caching benefit on repeated context in this app
            # (every Gemini row's text_input_cached is 0 in the DB, vs 86-93%
            # for OpenAI) — a call that runs long enough to cross
            # trigger_tokens reprocesses its ENTIRE accumulated history, in
            # full, uncached, on every subsequent turn, which is why later
            # turns visibly slow down as a call goes on (measured: turn
            # durations climbing from ~3s to 8-13s over one 5-minute call).
            # This caps that growth instead of leaving it unbounded — a
            # same-order-of-magnitude budget as this app's own (post-rewrite)
            # system prompt (~2,300-2,450 tokens), so a short call never
            # triggers it at all; only calls that run long enough to
            # meaningfully suffer from the uncapped-growth problem do.
            compression = genai_types.ContextWindowCompressionConfig(
                trigger_tokens=10_000,
                sliding_window=genai_types.SlidingWindow(target_tokens=2_000),
            )
            model = GeminiRealtimeModel(
                api_key=settings.GEMINI_API_KEY,
                model=model_name,
                voice=model_settings.get("voice", "Kore"),
                tool_response_scheduling=scheduling,
                context_window_compression=compression,
            )
        else:
            model = _build_openai_realtime_model(model_name, model_settings)
        self._agent_session = AgentSession(llm=model)

        self._audio_input = ExotelAudioInput()
        self._agent_session.input.audio = self._audio_input

        await self._finish_agent_session_setup(started=False, recovery_line=recovery_line)

    async def _finish_agent_session_setup(
        self, *, started: bool, recovery_line: Optional[str] = None
    ) -> None:
        """Shared tail for both the normal (cold) construction path above and
        the warm-handoff path (_try_use_warm_inbound_session) — output wiring,
        event handlers, starting the session if it isn't already, and firing
        the opening line. output.audio is safe to (re)assign here regardless
        of path: confirmed via the installed SDK's source that it's read
        fresh per-turn (self._session.output.audio), unlike input.audio,
        which is captured once by _forward_audio_task at session.start()
        time — that asymmetry is exactly why the warm pool has to reuse the
        SAME ExotelAudioInput instance a warm session was started with
        rather than swap in a new one, but can attach a brand new
        ExotelAudioOutput here with no such restriction."""
        audio_output = ExotelAudioOutput(
            send_frame=self._send_frame,
            get_sid=lambda: self._sid,
            on_segment_finished=self._on_segment_finished,
            on_frame=self._record_agent_frame,
        )
        self._agent_session.output.audio = audio_output
        self._agent_session.on("error", self._on_agent_session_error)
        self._agent_session.on("agent_state_changed", self._on_agent_state_changed)
        self._agent_session.on("metrics_collected", self._on_metrics_collected)

        if not started:
            await self._agent_session.start(self._agent)

        # Literal, deterministic line — not a meta-instruction (see
        # _build_opening_line's docstring for why that used to be silent).
        opening_line = recovery_line or _build_opening_line(self.state)
        self._greeting_confirmed = False
        asyncio.create_task(self._ensure_opening_line_spoken(opening_line))

    async def _try_use_warm_inbound_session(self) -> bool:
        """Inbound-only fast path (see voice/warm_pool.py): if a pre-connected,
        already-settled session is available, adopt it instead of building
        fresh — skips the connect-and-settle cost that's the actual
        bottleneck (verified: ~2.0s cold vs ~0.9s from a session given time
        to settle first, same code path, 20+ live trials). Returns False (do
        nothing) if the pool has nothing ready — caller falls back to the
        normal _start_agent_session() path unchanged. Never raises: any
        problem here just means "no warm session," not a broken call."""
        try:
            from voice.warm_pool import take_warm_inbound_session

            warm = take_warm_inbound_session()
            if warm is None:
                return False
            agent, session, audio_input, engine, model_name = warm

            # The warm agent's instructions were built from a disposable
            # placeholder LeadState — byte-identical to what a FIRST-TIME
            # inbound caller needs (agent/prompt.py's inbound instructions
            # only ever reference state.direction, never name/company/phone/
            # email, for a non-returning caller — see warm_pool.py's module
            # docstring), but the tool methods (save_lead_info/
            # search_knowledge_base) read/write self.lead_state, so that has
            # to point at THIS call's real, DB-backed state, not the
            # placeholder, regardless of returning-caller status.
            agent.lead_state = self.state
            self._agent = agent
            self._agent_session = session
            self._audio_input = audio_input
            self.state.call_metrics["engine"] = engine
            self.state.call_metrics["model"] = model_name

            # Returning-caller instructions (greet by name, prior BANT facts,
            # booking-gate override — see agent/prompt.py's
            # _returning_caller_block) are NOT byte-identical to the
            # placeholder's, since _apply_returning_caller_context (run
            # earlier in _on_start) only just resolved this from the DB.
            # update_instructions() is a real, awaitable SDK method that
            # works on a running OpenAI Realtime session (only Gemini-3.1
            # hard-blocks it, and that model never reaches this warm-pool
            # path — see core/model_config.py's standardization on
            # gpt-realtime) — this personalizes the already-warm, already-
            # settled connection in place, no reconnect, no new warm pool.
            # Skipped entirely for a non-returning caller: build_instructions
            # would just reproduce the placeholder's own text, so there's
            # nothing to gain from the extra realtime session.update() round
            # trip.
            if self.state.is_returning_caller:
                try:
                    await agent.update_instructions(build_instructions(self.state))
                except Exception:
                    logger.exception(
                        "Failed to personalize warm session instructions for returning caller — "
                        "continuing with the generic greeting instead"
                    )

            await self._finish_agent_session_setup(started=True)
            logger.info(f"Adopted warm inbound session for lead_id={self.state.lead_id}")
            return True
        except Exception:
            logger.exception("Warm inbound handoff failed — falling back to a cold start")
            return False

    def _on_agent_session_error(self, ev):
        logger.error(f"AgentSession error: {ev}")
        err = getattr(ev, "error", None)
        if getattr(err, "recoverable", True):
            if self._is_active_response_collision(err):
                asyncio.create_task(self._recover_dropped_turn())
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

    def _is_active_response_collision(self, err) -> bool:
        """True for OpenAI's conversation_already_has_active_response — a
        response.create (ours, or the server's own auto-triggered one via
        turn_detection=ServerVad(create_response=True)) colliding with a
        response that's still being generated. Recoverable at the session
        level, but left unhandled it silently drops whatever turn triggered
        it — either our own greeting/reprompt/hangup line, or the caller's
        own utterance if it was the server's auto-reply that lost the race.
        `err` is the RealtimeModelError; `err.error` is the wrapped APIError
        whose `.body` is the RealtimeError carrying `.code` — matches the
        exact shape seen in production logs."""
        inner = getattr(err, "error", None)
        body = getattr(inner, "body", None)
        return getattr(body, "code", None) == _ACTIVE_RESPONSE_COLLISION_CODE

    async def _recover_dropped_turn(self):
        """Nudges the model to respond to whatever's already in conversation
        history once its current generation actually finishes — recovers
        the turn a conversation_already_has_active_response collision would
        otherwise just drop on the floor (see _is_active_response_collision).
        No forced instructions here: unlike _speak_literal's scripted lines,
        this just lets the model react naturally to what's already in
        context (e.g. the caller's own "hello" that got dropped). Bounded
        wait plus a cooldown so a run of these errors can't become a retry
        storm."""
        now = time.monotonic()
        if now - self._last_collision_recovery_at < RESPONSE_COLLISION_RECOVERY_COOLDOWN_S:
            return
        self._last_collision_recovery_at = now

        waited = 0.0
        while waited < RESPONSE_COLLISION_RECOVERY_MAX_WAIT_S:
            if self._ended or not self._agent_session:
                return
            if self._agent_session.agent_state not in _GENERATION_ACTIVE_STATES:
                break
            await asyncio.sleep(1.0)
            waited += 1.0

        if self._ended or not self._agent_session:
            return
        try:
            self._agent_session.generate_reply()
        except Exception:
            logger.exception("_recover_dropped_turn's generate_reply failed")

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

    async def _ensure_opening_line_spoken(self, opening_line: str):
        """Drives the opening greeting to a *confirmed* spoken result, retrying
        on failure — replaces the old fire-once-and-hope approach that made
        every engine except gemini-3.1 silently fail to greet first.

        Root cause (traced into the installed livekit-agents/livekit-plugins-
        openai source, not assumed): AgentSession.generate_reply() returns a
        SpeechHandle immediately and resolves it as soon as the realtime API
        creates a response — but on a just-opened realtime session, that
        response can come back completely empty (zero audio/text tokens,
        confirmed via realtime_model.py: ttft=-1 means `first_token_timestamp`
        was never set). This is a real, observed race, not a hypothesis: the
        production log shows `ttft=-1.000s duration=0.000s` as the very first
        metrics line of nearly every call, on both OpenAI and Gemini-2.5. The
        old code never checked whether generate_reply() actually produced
        anything — it fired once and considered the job done — so this empty
        first response went completely unnoticed. The only safety net
        (_greeting_confirmed, set by ANY agent audio reaching the caller) was
        blind to the difference between "Mira greeted first" and "the caller
        said hello into dead air and the server's own auto-reply picked that
        up" — which is exactly what was happening (the timing of the
        following "real" turn in the logs is way too fast to be this
        watchdog's own retry, and matches a caller reacting to silence).

        For engines that support generate_reply(), each attempt is now
        confirmed via the SpeechHandle itself (see _speak_literal) — after it
        reports done, we know for certain whether real content came back —
        and retried up to OPENING_LINE_MAX_ATTEMPTS times, back-to-back, so a
        transient empty response gets caught and corrected in well under a
        second rather than silently standing in for a greeting that never
        happened. gemini-3.1 has no such handle to confirm against (the
        speech-trigger path doesn't go through generate_reply() at all), so it
        keeps the previous state-and-timer-based watchdog as its only
        available safety net."""
        metrics = self.state.call_metrics if self.state else {}
        engine = metrics.get("engine", "openai")
        model = metrics.get("model")

        if not _generate_reply_supported(engine, model):
            await self._speak_literal(opening_line, is_pre_conversation=True)
            await self._gemini_greeting_watchdog(opening_line)
            return

        for attempt in range(1, OPENING_LINE_MAX_ATTEMPTS + 1):
            if self._ended or not self._agent_session:
                return
            if self._greeting_confirmed:
                # Real agent audio already reached the caller — either this
                # loop's own confirmed attempt, or (rarer with the fix above,
                # but possible) the caller spoke first and got a genuine
                # reply. Either way there's a real greeting on the line now;
                # forcing another one on top would be a confusing duplicate.
                return
            ok = await self._speak_literal(
                opening_line, is_pre_conversation=True, playout_timeout=OPENING_LINE_PLAYOUT_TIMEOUT_S
            )
            if ok:
                self._greeting_confirmed = True
                return
            if self._ended:
                return
            logger.warning(
                f"Opening line attempt {attempt}/{OPENING_LINE_MAX_ATTEMPTS} produced no "
                f"spoken content — retrying"
            )
            await asyncio.sleep(OPENING_LINE_RETRY_BACKOFF_S)

        logger.error(
            f"Opening line never confirmed after {OPENING_LINE_MAX_ATTEMPTS} attempts — "
            "giving up; caller may be met with silence until they speak first"
        )

    async def _gemini_greeting_watchdog(self, opening_line: str):
        """gemini-3.1-only fallback (see _ensure_opening_line_spoken): the
        speech-trigger path has no SpeechHandle to confirm against, so this
        keeps the previous state-and-timer heuristic as the sole safety net
        for that one model. State-aware, NOT a blind timer. After the initial
        GREETING_CONFIRM_TIMEOUT_S, if agent_state shows a generation is
        plausibly still in flight (_GENERATION_ACTIVE_STATES), retrying here
        would collide with it — this is exactly what produced OpenAI's
        conversation_already_has_active_response error in production (this
        retry firing while the original opening-line response was still
        being generated). So instead of retrying blind, keep re-checking at
        GREETING_WATCHDOG_POLL_S intervals, up to GREETING_WATCHDOG_MAX_WAIT_S
        total, and only retry once the state has genuinely settled (idle)
        while still unconfirmed — or, past the max wait, retry anyway as a
        last resort rather than stay silent forever on a session that looks
        wedged."""
        waited = GREETING_CONFIRM_TIMEOUT_S
        await asyncio.sleep(GREETING_CONFIRM_TIMEOUT_S)
        while True:
            if self._ended or self._greeting_confirmed or not self._agent_session:
                return
            if self._agent_session.agent_state not in _GENERATION_ACTIVE_STATES:
                break
            if waited >= GREETING_WATCHDOG_MAX_WAIT_S:
                logger.warning(
                    f"Greeting still unconfirmed after {waited:.0f}s and agent_state="
                    f"{self._agent_session.agent_state!r} — retrying anyway as a last resort"
                )
                break
            await asyncio.sleep(GREETING_WATCHDOG_POLL_S)
            waited += GREETING_WATCHDOG_POLL_S

        if self._ended or self._greeting_confirmed or not self._agent_session:
            return
        logger.warning(f"No agent audio {waited:.0f}s after session start — retrying opening line")
        await self._speak_literal(opening_line, is_pre_conversation=True)

    def _raw_gemini_session(self):
        """Reaches past livekit-plugins-google's generate_reply()/
        update_chat_ctx() gating (both hard no-ops for any Gemini model with
        "3.1" in its name — `mutable = "3.1" not in model` in the plugin's
        own source) to the underlying raw google.genai Live `AsyncSession`
        the plugin itself talks to. That gate only blocks the plugin's OWN
        convenience wrappers, not the session underneath — the session's
        `send_client_content()` is not gated at all. Private SDK internals
        (AgentSession._activity._rt_session._active_session), not a public
        accessor — there isn't one. Every call site guards for None/attribute
        errors and falls back to the WAV-based trigger, so an SDK upgrade
        that renames/removes any of these can't take the greeting down with
        it, just slow it back down to the old path. Returns None if
        anything in the chain isn't there (including simply not being
        connected yet)."""
        try:
            rt_session = getattr(self._agent_session._activity, "_rt_session", None)
            return getattr(rt_session, "_active_session", None)
        except Exception:
            return None

    async def _trigger_gemini_via_synthetic_turn(self) -> bool:
        """Fast path for gemini-3.1 (and usable for gemini-2.5 too, though
        that model already has generate_reply()): send a synthetic user
        turn — "[SESSION_STARTED]", turn_complete=True — directly to the raw
        session, exactly as documented for the BidiGenerateContent protocol
        (the server passively waits in IDLE for an incoming clientContent/
        realtimeInput turn frame; it doesn't auto-greet on setupComplete
        alone). The model then composes its own reply from the system
        prompt, same as the WAV trigger, just via text instead of ~4
        seconds of recorded audio.

        This directly contradicts _play_speech_trigger's own prior
        docstring, which claimed text injection "produced zero audio no
        matter the wording" even when bypassing the plugin. Verified fresh,
        standalone, directly against the live API (independent of this
        app's pipeline, both gemini-3.1-flash-live-preview and
        gemini-2.5-flash-native-audio-preview-12-2025): connect-to-first-
        audio-chunk landed in 1.4s and 3.1s respectively, both with a real,
        on-topic spoken reply. Either the earlier test had a construction
        bug (e.g. missing turn_complete) or this model's behavior changed
        since that conclusion was written — Live API previews move fast.
        Kept as a fast path with the WAV trigger as an automatic fallback
        rather than a full replacement, specifically because the earlier
        conclusion existed at all — if the live pipeline (full Exotel audio
        path, our actual system prompt/config) behaves differently than
        this isolated verification, calls still get a working greeting.

        Polls briefly for the raw session to appear (same
        _active_session-non-None signal _play_speech_trigger's docstring
        already documented, just checked directly instead of blindly slept
        past) rather than assuming it's instantly ready.
        """
        deadline = time.monotonic() + GEMINI_RAW_SESSION_POLL_TIMEOUT_S
        session = None
        while time.monotonic() < deadline:
            session = self._raw_gemini_session()
            if session is not None:
                break
            await asyncio.sleep(0.1)
        if session is None:
            return False
        try:
            await session.send_client_content(
                turns=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text="[SESSION_STARTED]")],
                ),
                turn_complete=True,
            )
            return True
        except Exception:
            logger.exception("Raw Gemini synthetic-turn trigger failed")
            return False

    async def _play_speech_trigger(self):
        """Fallback for gemini-3.1 when _trigger_gemini_via_synthetic_turn
        isn't available (raw session never appeared, or the SDK's internal
        attribute chain changed). Feeds assets/gemini_speech_trigger.wav
        into the same audio-input queue real caller audio arrives through
        (NOT into the call recording — this never happened on the actual
        line, so it doesn't belong in it), padded with silence so Gemini's
        server-side VAD sees a clean start/end-of-speech boundary. Slower
        (~4-8s end to end) but was, prior to the synthetic-turn path above,
        the only mechanism confirmed to make gemini-3.1-flash-live-preview
        produce a spoken response at all: synthetic TTS audio (both a
        robotic espeak voice and Sarvam's neural bulbul:v3, at two different
        lengths) was silently rejected — only genuine recorded human speech
        got a real response through this specific (audio-injection) path."""
        pcm = _load_speech_trigger()
        if pcm is None or not self._audio_input:
            return

        # The underlying WebSocket connection object appears (RealtimeSession
        #._active_session becomes non-None) well under a second after
        # session.start() returns, but that's the TCP/WS handshake finishing —
        # not proof Gemini's own protocol-level setup on top of it is done.
        # Tested directly: polling for _active_session alone and pushing the
        # instant it appeared produced silence; a flat settle delay here is
        # what actually made the trigger land reliably in testing. Not
        # elegant, but empirically what works, and it's paid for once per
        # call, in parallel with nothing else waiting on it.
        await asyncio.sleep(SPEECH_TRIGGER_SETTLE_S)

        padding = np.zeros(int(SPEECH_TRIGGER_PADDING_S * SPEECH_TRIGGER_SAMPLE_RATE), dtype="<i2")
        full = np.concatenate([padding, pcm, padding])
        frame_samples = SPEECH_TRIGGER_SAMPLE_RATE * FRAME_MS // 1000
        next_frame_time = time.monotonic()
        for i in range(0, len(full) - frame_samples, frame_samples):
            chunk = full[i:i + frame_samples]
            self._audio_input.push(rtc.AudioFrame(
                data=chunk.tobytes(), sample_rate=SPEECH_TRIGGER_SAMPLE_RATE,
                num_channels=1, samples_per_channel=len(chunk),
            ))
            next_frame_time += FRAME_MS / 1000
            sleep_for = next_frame_time - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def _speak_literal(
        self,
        line: str,
        is_pre_conversation: bool = False,
        playout_timeout: float = GENERATE_REPLY_PLAYOUT_TIMEOUT_S,
    ) -> bool:
        """Speak a literal, deterministic line. Returns whether it was
        *confirmed* to have actually produced spoken content this attempt —
        not merely accepted by the SDK without raising. Two paths:

        1. Models the livekit plugin supports generate_reply() for (OpenAI;
           any Gemini model without "3.1" in its name) — use it normally.
           `line` is spoken verbatim. generate_reply() returns a SpeechHandle
           immediately, but that only means the request was accepted, not
           that it produced anything — on a freshly-opened realtime session
           the response can come back completely empty (see
           _ensure_opening_line_spoken's docstring for the confirmed root
           cause). So this awaits the handle's own completion
           (wait_for_playout()) and checks its outcome — exception() for a
           hard failure (e.g. the SDK's own 10s internal timeout), chat_items
           for whether anything was actually produced — before reporting
           success. This is what makes retries in
           _ensure_opening_line_spoken meaningful instead of blind.

        2. Any Gemini model with "3.1" in its name, where generate_reply()
           and update_chat_ctx() are both hard no-ops (the plugin's own
           `mutable = "3.1" not in model` check). When is_pre_conversation is
           True (the opening greeting and its retry — nothing has been said
           yet either way, so there's no real conversation history to
           contradict), first tries a synthetic-turn trigger sent directly
           to the raw session (_trigger_gemini_via_synthetic_turn) — fast
           (~1-3s), confirmed against the live API directly. Falls back to
           the older, slower (~4-8s) recorded-speech-trigger path
           (_play_speech_trigger) only if that raw-session reach-in isn't
           available this call. Either way the model composes its own reply
           guided by the system prompt, NOT the same as speaking `line`
           verbatim. There's no SpeechHandle for either sub-path to confirm
           against, so both report "true" once sent/pushed — actual
           confirmation still comes from _greeting_confirmed (real agent
           audio reaching the caller), tracked separately by
           _gemini_greeting_watchdog. When is_pre_conversation is False
           (mid-conversation reprompt/hangup lines, where real history
           already exists and an unrelated trigger would be a confusing
           non-sequitur), there's currently no working mechanism for this
           model — logged and skipped. The hangup timer that calls this for
           SILENCE_HANGUP_MS still ends the call regardless of whether this
           line lands (see _watchdog_loop), so the call reliably ends either
           way; only the spoken line itself may be missing on this specific
           model.
        """
        if not self._agent_session:
            return False
        metrics = self.state.call_metrics if self.state else {}
        engine = metrics.get("engine", "openai")
        model = metrics.get("model")

        if _generate_reply_supported(engine, model):
            if self._agent_session.agent_state in _GENERATION_ACTIVE_STATES:
                # A generation is plausibly still in flight — firing another
                # one now is exactly the collision that produces OpenAI's
                # conversation_already_has_active_response (see this
                # module's constants). Skip this cycle rather than risk it;
                # the caller (greeting retry loop / next watchdog tick)
                # re-evaluates.
                logger.info(
                    f"_speak_literal: agent_state={self._agent_session.agent_state!r} — "
                    f"skipping generate_reply to avoid an active-response collision: {line!r}"
                )
                return False
            try:
                handle = self._agent_session.generate_reply(instructions=_say_exactly(line))
            except Exception:
                logger.exception("generate_reply failed")
                return False
            try:
                await asyncio.wait_for(handle.wait_for_playout(), timeout=playout_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    f"_speak_literal: {line!r} did not finish playing out within "
                    f"{playout_timeout}s"
                )
                return False
            err = handle.exception()
            if err is not None:
                logger.warning(f"_speak_literal: generate_reply for {line!r} failed: {err!r}")
                return False
            if not handle.chat_items:
                # Accepted by the SDK, resolved cleanly, but produced nothing —
                # the confirmed empty-response race. Not an error, just not a
                # spoken line.
                logger.warning(
                    f"_speak_literal: generate_reply for {line!r} produced no spoken "
                    "content (empty response)"
                )
                return False
            return True

        if engine != "gemini":
            logger.warning(f"_speak_literal: no fallback path for engine={engine!r}; staying silent")
            return False

        if not is_pre_conversation:
            logger.warning(
                f"_speak_literal: '{model}' supports neither generate_reply() nor text injection, "
                f"and the speech-trigger fallback only applies pre-conversation — staying silent for: {line!r}"
            )
            return False

        if await self._trigger_gemini_via_synthetic_turn():
            return True

        try:
            await self._play_speech_trigger()
        except Exception:
            logger.exception("_play_speech_trigger failed")
            return False
        return True

    def _record_agent_frame(self, chunk: bytes):
        """Called for every 20ms Exotel-bound agent audio chunk — feeds the
        self-recorded call audio (silence-padded to stay aligned with the
        caller channel's continuous timeline) and doubles as the "has the
        agent said anything yet" signal for _ensure_opening_line_spoken/
        _gemini_greeting_watchdog."""
        self._greeting_confirmed = True
        if self._recording_start_t is None:
            return
        elapsed = time.monotonic() - self._recording_start_t
        target_len = int(elapsed * EXOTEL_SAMPLE_RATE) * 2
        if len(self._agent_buf) < target_len:
            self._agent_buf.extend(b"\x00" * (target_len - len(self._agent_buf)))
        self._agent_buf.extend(chunk)

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
        self._caller_buf.extend(pcm8k.tobytes())

        rms = float(np.sqrt(np.mean(pcm8k.astype(np.float32) ** 2))) if pcm8k.size else 0.0
        if rms > CALLER_ACTIVITY_RMS_FLOOR:
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

    def _last_assistant_turn_was_a_statement(self) -> bool:
        """True when the agent's most recent utterance did NOT end in a
        question — this app's own CLOSING prompt instruction always ends a
        finished call with a plain statement ("Thank you for your time...")
        never a question, so this is how _watchdog_loop tells "conversation
        genuinely concluded" apart from "caller is just thinking about the
        question I just asked them." Tool-independent — works whether or not
        save_lead_info is available this call."""
        if not self._agent_session:
            return False
        try:
            messages = [
                m for m in self._agent_session.history.messages()
                if m.role == "assistant" and m.text_content
            ]
            if not messages:
                return False
            return "?" not in messages[-1].text_content.strip()
        except Exception:
            logger.exception("Failed to inspect last assistant turn")
            return False

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
            self._non_listening_since = None
        elif self._non_listening_since is None:
            self._non_listening_since = time.monotonic()

    def _on_metrics_collected(self, ev):
        """Accumulates OpenAI Realtime token usage for the dashboard's
        per-call cost estimate (core/costs.py), and logs per-response
        ttft/duration — the actual model latency numbers, isolated from
        conversation content (unlike the tool-call timestamps elsewhere in
        this log, which include however long the caller spent talking).
        Valid ttft values (time-to-first-audio-token) are also accumulated
        for the dashboard's avg-latency-per-turn figure — see _end()."""
        if not self.state:
            return
        m = getattr(ev, "metrics", ev)
        ttft = getattr(m, "ttft", None)
        duration = getattr(m, "duration", None)
        if ttft is not None:
            logger.info(f"realtime turn latency: ttft={ttft:.3f}s duration={duration:.3f}s")
            if ttft >= 0:
                self.state.call_metrics.setdefault("latencies", []).append(float(ttft))
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
            if not self._agent_session:
                continue

            listening = self._agent_session.agent_state == "listening"
            stuck_ms = (
                (time.monotonic() - self._non_listening_since) * 1000
                if not listening and self._non_listening_since is not None
                else 0
            )
            idle_ms = (time.monotonic() - self._last_activity_at) * 1000

            # Tool-independent graceful-end detection (see
            # _last_assistant_turn_was_a_statement docstring). Normally only
            # meaningful while genuinely "listening" — checked before, and
            # separately from, the mid-conversation reprompt/hangup timers
            # below, which are unchanged and still apply whenever the agent's
            # last turn WAS a question (i.e. the caller is just thinking, not
            # done with the call). ALSO checked once agent_state has been
            # stuck off "listening" for STUCK_TURN_STATE_MS — that's the
            # defense-in-depth fallback for when a response collision (see
            # _ACTIVE_RESPONSE_COLLISION_CODE) wedges the SDK's own turn-
            # taking state, which used to silently suppress hangup detection
            # for the rest of the call. Not a call-duration policy — this
            # still only ends the call from content (transcript + idle time),
            # never from elapsed call length.
            if (listening or stuck_ms >= STUCK_TURN_STATE_MS) and idle_ms >= GRACEFUL_END_SILENCE_MS \
                    and self._last_assistant_turn_was_a_statement():
                logger.info(
                    "Agent's last utterance wasn't a question and the caller has gone quiet — "
                    "treating the call as concluded and hanging up."
                    + ("" if listening else f" (agent_state stuck non-listening for {stuck_ms:.0f}ms)")
                )
                # NOT `await self._end()` — this runs inside _watchdog_task itself, and
                # _end() cancels _watchdog_task as one of its first steps. Cancelling a
                # task from within its own currently-running coroutine throws
                # CancelledError at _end()'s very next await (update_lead_state), aborting
                # the DB persist/session-close/booking before they run, while _ended is
                # already latched True — silently killing the call's teardown forever
                # (confirmed live: leads 73/74 lost all data and their calls never hung
                # up this way). create_task() runs _end() as an independent task instead,
                # exactly like _on_segment_finished's (already-correct) call_complete path.
                asyncio.create_task(self._end())
                return

            if not listening:
                continue

            if not self._reprompt_fired and idle_ms >= SILENCE_REPROMPT_MS:
                self._reprompt_fired = True
                await self._speak_literal(_SILENCE_REPROMPT_LINE)
            elif self._reprompt_fired and idle_ms >= SILENCE_HANGUP_MS:
                if self.state:
                    self.state.classification = self.state.classification or "Cold"
                await self._speak_literal(_SILENCE_HANGUP_LINE)
                await asyncio.sleep(3.0)
                # See the create_task note above — same self-cancellation hazard.
                asyncio.create_task(self._end())
                return

    # ── Teardown ─────────────────────────────────────────────────────────────

    def _write_recording(self) -> Optional[str]:
        """Mixes the caller and agent 8kHz mono buffers into a single stereo
        WAV (left=caller, right=agent) and writes it to RECORDINGS_DIR. Self-
        recorded rather than relying on Exotel's own recording feature — no
        extra Exotel cost/config, and we already have both raw PCM streams
        in this process. Returns the file path, or None if there's nothing
        to write (e.g. the call ended before any audio arrived)."""
        if not self._caller_buf and not self._agent_buf:
            return None
        try:
            n = max(len(self._caller_buf), len(self._agent_buf))
            n -= n % 2  # keep it a whole number of int16 samples
            caller = bytes(self._caller_buf).ljust(n, b"\x00")[:n]
            agent = bytes(self._agent_buf).ljust(n, b"\x00")[:n]
            caller_arr = np.frombuffer(caller, dtype="<i2")
            agent_arr = np.frombuffer(agent, dtype="<i2")
            stereo = np.empty(caller_arr.size * 2, dtype="<i2")
            stereo[0::2] = caller_arr
            stereo[1::2] = agent_arr

            lead_id = self.state.lead_id if self.state else "unknown"
            path = os.path.join(RECORDINGS_DIR, f"{lead_id}.wav")
            with wave.open(path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(EXOTEL_SAMPLE_RATE)
                wf.writeframes(stereo.tobytes())
            return path
        except Exception:
            logger.exception("Failed to write call recording")
            return None

    async def _end(self):
        if self._ended:
            return
        self._ended = True

        if self._watchdog_task:
            self._watchdog_task.cancel()

        s = self.state
        if s and s.lead_id:
            s.call_metrics["call_duration_s"] = round(time.monotonic() - self._session_start_t, 1)

            latencies = s.call_metrics.get("latencies") or []
            if latencies:
                s.call_metrics["avg_latency_s"] = round(sum(latencies) / len(latencies), 3)

            recording_path = self._write_recording()
            if recording_path:
                s.call_metrics["recording_path"] = recording_path

            s.call_metrics.update(compute_call_cost(
                s.call_metrics.get("usage", {}),
                engine=s.call_metrics.get("engine", "openai"),
                model=s.call_metrics.get("model"),
                call_duration_s=s.call_metrics["call_duration_s"],
                direction=s.direction,
            ))

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

            # Busy/callback: if the lead was flagged as wanting a callback but
            # never gave (or the model never captured) a specific time,
            # default to the same time the next day.
            if s.callback_requested and not s.callback_time:
                fallback = self._session_start_wall + timedelta(days=1)
                s.callback_time = fallback.isoformat()
                logger.info(f"Callback requested with no time given — defaulting to {s.callback_time}")

            if s.classification is None:
                if s.out_of_scope:
                    s.classification = "Cold"
                elif s.discovery_call_agreed:
                    s.classification = "Hot"
                elif s.budget or s.timeline or s.decision_maker_status or s.callback_requested:
                    s.classification = "Warm"

            try:
                await update_lead_state(s.lead_id, s.to_dict(), s.classification, transcript=transcript)
            except Exception:
                # A DB hiccup here must never skip closing the session/WS or the
                # discovery-call booking below — losing the persisted row is bad
                # enough without also leaving the caller's line hanging open.
                logger.exception(f"Failed to persist lead {s.lead_id} state at call end")

        if self._agent_session:
            try:
                await self._agent_session.aclose()
            except Exception:
                pass

        try:
            await self._ws.close()
        except Exception:
            pass

        # Post-call summarizer — a real network round trip (same reasoning
        # as book_discovery_call below: runs after the WS/session are already
        # closed so it never delays hanging up on the caller). Feeds
        # agent/prompt.py's RETURNING CALLER block on this same lead's NEXT
        # call (see _apply_returning_caller_context). Failure here is
        # logged inside summarize_call itself and never raises.
        if s and s.lead_id:
            summary = await la.summarize_call(transcript)
            if summary:
                try:
                    await update_call_summary(s.lead_id, summary)
                except Exception:
                    logger.exception(f"Failed to persist call_summary for lead {s.lead_id}")

        if s and s.discovery_call_agreed and s.email_id:
            # Returning caller with an existing booking (see
            # _apply_returning_caller_context) → move that exact event
            # instead of creating a duplicate. Falls back to a fresh booking
            # if the reschedule fails (e.g. the original event was deleted
            # out-of-band) — never leaves the lead with nothing booked just
            # because the old event is gone.
            existing_event_id = (s.previous_call or {}).get("calendar_event_id")
            try:
                result = None
                if existing_event_id:
                    result = await reschedule_discovery_call(
                        event_id=existing_event_id,
                        organizer_email=settings.CALENDAR_ORGANIZER_EMAIL,
                        preferred_slot=s.preferred_slot,
                    )
                    if not result:
                        logger.warning(
                            f"Reschedule failed for event_id={existing_event_id!r} — "
                            "falling back to booking a fresh discovery call"
                        )
                if not result:
                    result = await book_discovery_call(
                        lead_name=s.name,
                        lead_email=s.email_id,
                        organizer_email=settings.CALENDAR_ORGANIZER_EMAIL,
                        preferred_slot=s.preferred_slot,
                    )
                if result and s.lead_id:
                    meet_link, event_id = result
                    s.meeting_link = meet_link
                    s.discovery_call_scheduled = True
                    await save_meeting_link(s.lead_id, meet_link, event_id)
            except Exception:
                logger.exception("book/reschedule discovery_call failed")


@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    await CallSession(ws).run()
