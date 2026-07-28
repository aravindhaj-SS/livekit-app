"""Pre-warms one OpenAI Realtime connection for INBOUND calls, ahead of any
call actually arriving, so the greeting doesn't have to pay the full
connect-and-settle cost on the caller's own time.

Root cause this targets (see CLAUDE.md / chat history for the full trace):
measured directly against the live API, with this app's own production code
(real MiraAgent, real prompt, real RealtimeModel construction, 20+ trials),
that AgentSession.start() itself returns in single-digit milliseconds — the
underlying WebSocket connects in a background task, not something we block
on — but a generate_reply() fired immediately after start() still takes
~1.8-4.5s (median ~2.05s) to produce audio, because the connection hasn't
actually finished connecting-and-settling server-side yet. Giving that same
connection ~5s to rest BEFORE firing anything cuts that to ~0.8-1.4s (median
~0.92s across 10 trials) — same code, same live API, only difference is
timing. This module exists to spend that ~5s before the caller is even on
the line, instead of after.

Scoped to inbound only, deliberately: agent/prompt.py's build_instructions,
when state.direction == "inbound", never references caller-specific fields
(name/company/interest_area/email) — only state.direction — so instructions
built from a placeholder LeadState are byte-identical to what any real
inbound call needs. Outbound instructions DO embed the specific lead being
called (name/company/interest_area), so a generically pre-warmed session's
baked-in instructions would be wrong for whichever lead ends up connected —
outbound is intentionally untouched, unchanged cold-start via
CallSession._start_agent_session().

Also scoped to the OpenAI path specifically, since that's the only engine
this app now runs in production (see core/model_config.py's standardization
on gpt-realtime) — if inbound is ever pointed back at Gemini, this pool
simply builds nothing (see _build_entry) rather than warming a path that
wouldn't be used.

Failure mode by design: every public entrypoint here is wrapped so a
problem building or maintaining a warm session degrades to "no warm session
available" — never an exception that could take a real call down. The
normal cold-start path in voice/gemini_bridge.py is completely unchanged and
always available as the fallback.
"""
import asyncio
import logging
import time
from typing import Optional

from livekit.agents import AgentSession

from agent.lead_state import LeadState
from core.model_config import get_engine_and_model, get_model_settings

logger = logging.getLogger(__name__)

# How long a freshly-connected session must rest before being handed to a
# real call. Verified empirically against the live API (see module
# docstring): 5s of settle time gave a tight, reliable ~0.9s median with a
# 1.43s worst case across 10 trials; 1.5s only gave partial, inconsistent
# benefit (some trials still took ~2.8s — barely better than cold). 6s adds
# a little margin above the tested 5s.
WARM_SETTLE_S = 6.0

# If a warm session sits unused this long, discard and rebuild it rather
# than risk handing off a connection that's been idle long enough to be
# stale/degraded server-side or hit some provider-side idle limit. Just a
# safety net — not tuned against any specific observed failure, comfortably
# above any realistic gap between inbound calls on this line.
WARM_MAX_AGE_S = 600.0

# One warm slot: this app's own call volume (per logs reviewed this session)
# is one inbound call at a time, not concurrent bursts. If a second call
# arrives before the first warm session is replaced, take() just returns
# None for it and CallSession falls back to its normal cold start — never
# blocks, never errors, strictly a bonus when available.
_POOL_SIZE = 1

# How often the maintenance loop checks whether its slot needs (re)filling.
# Short enough that a just-claimed or just-aged-out slot gets refilled
# promptly rather than sitting empty for a long fixed interval.
_MAINTENANCE_TICK_S = 1.0


class _WarmEntry:
    __slots__ = ("agent", "session", "audio_input", "engine", "model_name", "created_at", "ready_at")

    def __init__(self, agent, session, audio_input, engine: str, model_name: str):
        self.agent = agent
        self.session = session
        self.audio_input = audio_input
        self.engine = engine
        self.model_name = model_name
        self.created_at = time.monotonic()
        self.ready_at = self.created_at + WARM_SETTLE_S


async def _close_entry(entry: "_WarmEntry") -> None:
    try:
        await entry.session.aclose()
    except Exception:
        pass


class InboundWarmPool:
    """Maintains _POOL_SIZE pre-connected, resting AgentSessions for inbound
    calls. take() hands one off if a settled one is available, else returns
    None so the caller falls back to its normal cold-start path unchanged."""

    def __init__(self):
        self._slots: list[Optional[_WarmEntry]] = [None] * _POOL_SIZE
        self._locks = [asyncio.Lock() for _ in range(_POOL_SIZE)]
        self._maintain_tasks: list[asyncio.Task] = []
        self._closed = False

    def start(self) -> None:
        self._closed = False
        self._maintain_tasks = [
            asyncio.create_task(self._maintain_slot(i), name=f"warm_pool_slot_{i}")
            for i in range(_POOL_SIZE)
        ]

    async def aclose(self) -> None:
        self._closed = True
        for t in self._maintain_tasks:
            t.cancel()
        for t in self._maintain_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._maintain_tasks = []
        for i in range(_POOL_SIZE):
            async with self._locks[i]:
                entry, self._slots[i] = self._slots[i], None
            if entry is not None:
                await _close_entry(entry)

    def take(self):
        """Non-blocking. Returns (agent, session, audio_input, engine,
        model_name) if a settled warm session is available, else None."""
        try:
            now = time.monotonic()
            for i in range(_POOL_SIZE):
                entry = self._slots[i]
                if entry is not None and now >= entry.ready_at:
                    self._slots[i] = None  # claimed — _maintain_slot notices and refills
                    return entry.agent, entry.session, entry.audio_input, entry.engine, entry.model_name
        except Exception:
            logger.exception("InboundWarmPool.take failed — falling back to cold start")
        return None

    async def _maintain_slot(self, slot_index: int) -> None:
        while not self._closed:
            try:
                if self._slots[slot_index] is None:
                    async with self._locks[slot_index]:
                        if self._slots[slot_index] is None:
                            self._slots[slot_index] = await _build_entry()
                else:
                    entry = self._slots[slot_index]
                    if entry is not None and (time.monotonic() - entry.created_at) > WARM_MAX_AGE_S:
                        async with self._locks[slot_index]:
                            stale, self._slots[slot_index] = self._slots[slot_index], None
                        if stale is not None:
                            await _close_entry(stale)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"warm_pool slot {slot_index} maintenance tick failed")
            await asyncio.sleep(_MAINTENANCE_TICK_S)


async def _build_entry() -> Optional["_WarmEntry"]:
    try:
        # Lazy import: voice.gemini_bridge doesn't import this module until
        # a function actually needs it either (see take_warm_inbound_session
        # call sites) — avoids a real circular import at module-load time
        # while still reusing its exact construction logic, not a
        # hand-copied approximation that could drift out of sync.
        from voice.gemini_bridge import ExotelAudioInput, MiraAgent, _build_openai_realtime_model

        engine, model_name = get_engine_and_model("inbound")
        if engine != "openai":
            logger.info(f"Inbound is on {engine!r}, not openai — warm pool builds nothing")
            return None

        model_settings = get_model_settings(engine, model_name)
        state = LeadState(direction="inbound")
        agent = MiraAgent(state)
        model = _build_openai_realtime_model(model_name, model_settings)
        session = AgentSession(llm=model)

        audio_input = ExotelAudioInput()
        session.input.audio = audio_input
        # output.audio deliberately left unset here — confirmed via the
        # installed SDK's source that it's resolved fresh per-turn
        # (self._session.output.audio), not captured once at start() time
        # the way input.audio is, so the real call's own ExotelAudioOutput
        # gets attached at handoff time instead (see gemini_bridge.py's
        # _finish_agent_session_setup).

        await session.start(agent)
        return _WarmEntry(agent, session, audio_input, engine, model_name)
    except Exception:
        logger.exception("Failed to build a warm inbound session — will retry next tick")
        return None


_pool: Optional[InboundWarmPool] = None


def start_inbound_warm_pool() -> None:
    global _pool
    _pool = InboundWarmPool()
    _pool.start()
    logger.info("Inbound warm pool started")


async def stop_inbound_warm_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    logger.info("Inbound warm pool stopped")


def take_warm_inbound_session():
    """Returns (agent, session, audio_input, engine, model_name) if
    available, else None. Safe to call even if the pool was never started —
    just returns None, same as an empty pool."""
    if _pool is None:
        return None
    return _pool.take()
