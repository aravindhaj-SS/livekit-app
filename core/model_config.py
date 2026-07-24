"""Per-direction realtime-model selection, changeable from the dashboard
without shell access to the deployment.

Every entry in MODEL_CATALOG is a real, currently-listed model ID for its
provider, re-verified against OpenAI's and Google's own docs/pricing pages
— not guessed. Excluded deliberately:
  - gpt-4o-audio-preview: a Chat Completions audio-modality model, not a
    Realtime API model. It's request/response, not a persistent WebSocket
    session, so it isn't a drop-in swap for RealtimeModel — a different
    integration entirely, out of scope for a model-name switch.
  - gpt-realtime-2025-08-28: OpenAI's own docs mark this snapshot deprecated.
  - gemini-3.5-live-translate-preview: a real Gemini Live model, but purpose-
    built for speech-to-speech translation, not general conversation with
    tool-calling — would not run this app's BANT/booking flow correctly.

Selection is stored in runtime_model_config.json (gitignored, like leads.db/
.env) — not in .env — because this is meant to be changed at runtime,
picked up by the very next call with no restart needed (get_engine_and_model
re-reads this file fresh on every call, no caching); .env is read once at
process start by pydantic-settings and isn't a great fit for "the app
rewrites its own config".
"""

import json
import logging
from pathlib import Path
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

RUNTIME_CONFIG_PATH = Path("runtime_model_config.json")

# (engine, model) -> display metadata + tuning. Order here is the order
# shown in the dashboard's model picker.
#
# "settings" carries the per-model tuning knobs voice/gemini_bridge.py's
# _start_agent_session() used to hardcode identically for every model of an
# engine, regardless of which specific one was actually selected. Kept
# engine-appropriate:
#   - OpenAI: voice, speed, reasoning_effort (None where NOT confirmed
#     supported — reasoning is a gpt-realtime-2.1+ feature; gpt-4o-realtime-
#     preview predates it entirely, and gpt-realtime's own support for the
#     request param specifically is unconfirmed, so both stay conservative),
#     and turn_detection (silence/prefix window + threshold — threshold is
#     OpenAI's server-side VAD's own audio-activity sensitivity, 0.0-1.0,
#     raised from their ~0.5 default to 0.6 on request, to cut down on
#     background noise/line static being misread as the caller starting to
#     speak. This is the actual lever against that failure mode — it's a
#     property of the model's own turn detection, not something the RMS
#     floor in voice/gemini_bridge.py's _on_media can influence; that check
#     only ever gated this app's own silence-watchdog bookkeeping).
#   - Gemini: voice, and tool_response_scheduling as a plain string
#     ("when_idle"/"interrupt") rather than the google.genai enum directly —
#     keeps this module free of SDK-specific imports; gemini_bridge.py maps
#     the string to genai_types.FunctionResponseScheduling itself.
MODEL_CATALOG = [
    {
        "engine": "openai", "model": "gpt-realtime",
        "label": "GPT Realtime", "provider": "OpenAI",
        "note": "Flagship GA realtime model. Now the only model in active use (both directions).",
        "settings": {
            # speed was 1.15 (15% faster than normal) — dropped to a
            # deliberately slightly-slow-of-normal pace on request. 1.0 is
            # OpenAI's own baseline; this is a modest, audible step below
            # it, not an extreme slowdown.
            "voice": "marin", "speed": 1.05, "reasoning_effort": None,
            "turn_detection": {"silence_duration_ms": 350, "prefix_padding_ms": 300, "threshold": 0.6},
            # Filters input audio before it reaches VAD/transcription at all.
            # "far_field" fits phone-line audio (background/line noise)
            # better than "near_field" (meant for close-talking headset
            # mics). Never set before this; OpenAI's own docs describe it as
            # improving "VAD and turn detection accuracy (reducing false
            # positives)" — directly targets background noise being
            # misheard as speech.
            "noise_reduction": "far_field",
        },
    },
    {
        "engine": "openai", "model": "gpt-realtime-2.1",
        "label": "GPT Realtime 2.1", "provider": "OpenAI",
        "note": "Current default — async tool calls, spoken preambles while a tool runs.",
        "settings": {
            "voice": "marin", "speed": 1.05, "reasoning_effort": "minimal",
            "turn_detection": {"silence_duration_ms": 350, "prefix_padding_ms": 300, "threshold": 0.6},
            # Filters input audio before it reaches VAD/transcription at all.
            # "far_field" fits phone-line audio (background/line noise)
            # better than "near_field" (meant for close-talking headset
            # mics). Never set before this; OpenAI's own docs describe it as
            # improving "VAD and turn detection accuracy (reducing false
            # positives)" — directly targets background noise being
            # misheard as speech.
            "noise_reduction": "far_field",
        },
    },
    {
        "engine": "openai", "model": "gpt-realtime-2.1-mini",
        "label": "GPT Realtime 2.1 Mini", "provider": "OpenAI",
        "note": "Cheaper, faster — lighter reasoning than the full 2.1.",
        "settings": {
            "voice": "marin", "speed": 1.05, "reasoning_effort": "minimal",
            "turn_detection": {"silence_duration_ms": 350, "prefix_padding_ms": 300, "threshold": 0.6},
            # Filters input audio before it reaches VAD/transcription at all.
            # "far_field" fits phone-line audio (background/line noise)
            # better than "near_field" (meant for close-talking headset
            # mics). Never set before this; OpenAI's own docs describe it as
            # improving "VAD and turn detection accuracy (reducing false
            # positives)" — directly targets background noise being
            # misheard as speech.
            "noise_reduction": "far_field",
        },
    },
    {
        "engine": "openai", "model": "gpt-4o-realtime-preview",
        "label": "GPT-4o Realtime (legacy)", "provider": "OpenAI",
        "note": "Predecessor to gpt-realtime — kept for comparison/rollback.",
        "settings": {
            "voice": "marin", "speed": 1.05, "reasoning_effort": None,
            "turn_detection": {"silence_duration_ms": 350, "prefix_padding_ms": 300, "threshold": 0.6},
            # Filters input audio before it reaches VAD/transcription at all.
            # "far_field" fits phone-line audio (background/line noise)
            # better than "near_field" (meant for close-talking headset
            # mics). Never set before this; OpenAI's own docs describe it as
            # improving "VAD and turn detection accuracy (reducing false
            # positives)" — directly targets background noise being
            # misheard as speech.
            "noise_reduction": "far_field",
        },
    },
    {
        "engine": "gemini", "model": "gemini-2.5-flash-native-audio-preview-12-2025",
        "label": "Gemini 2.5 Flash (native audio)", "provider": "Gemini",
        "note": "Greets first reliably (generate_reply works) — higher per-turn latency (~2-5s).",
        "settings": {"voice": "Kore", "tool_response_scheduling": "when_idle"},
    },
    {
        "engine": "gemini", "model": "gemini-3.1-flash-live-preview",
        "label": "Gemini 3.1 Flash Live", "provider": "Gemini",
        "note": "Lower latency (~0.4-0.7s) — greet-first uses the recorded speech-trigger workaround, not a literal script.",
        "settings": {"voice": "Kore", "tool_response_scheduling": "when_idle"},
    },
]

_CATALOG_KEYS = {(m["engine"], m["model"]) for m in MODEL_CATALOG}

_DEFAULTS = {
    "inbound": {"engine": settings.REALTIME_ENGINE, "model": settings.GEMINI_MODEL if settings.REALTIME_ENGINE == "gemini" else settings.OPENAI_REALTIME_MODEL},
    "outbound": {"engine": settings.REALTIME_ENGINE, "model": settings.GEMINI_MODEL if settings.REALTIME_ENGINE == "gemini" else settings.OPENAI_REALTIME_MODEL},
}


def _read() -> dict:
    if RUNTIME_CONFIG_PATH.exists():
        try:
            with open(RUNTIME_CONFIG_PATH) as f:
                data = json.load(f)
            if "inbound" in data and "outbound" in data:
                return data
        except Exception:
            logger.exception(f"Failed to read {RUNTIME_CONFIG_PATH} — falling back to .env defaults")
    return {k: dict(v) for k, v in _DEFAULTS.items()}


def get_engine_and_model(direction: str) -> tuple[str, str]:
    """Called once per call, from voice/gemini_bridge.py, to decide which
    engine/model handles THIS call based on its direction. Falls back to
    .env's REALTIME_ENGINE/model settings if no runtime selection has ever
    been saved (first boot after this feature shipped)."""
    cfg = _read().get(direction) or _DEFAULTS[direction]
    return cfg["engine"], cfg["model"]


def get_model_settings(engine: str, model: str) -> dict:
    """Per-model tuning knobs (voice, speed, reasoning effort, VAD window,
    etc. — see MODEL_CATALOG) for the exact (engine, model) actually
    selected. Falls back to another entry for the same engine if the exact
    pair isn't in the catalog (e.g. a runtime_model_config.json left over
    from before a catalog entry was renamed/removed) rather than raising
    mid-call — a missing settings dict would otherwise take a live call
    down."""
    for m in MODEL_CATALOG:
        if m["engine"] == engine and m["model"] == model:
            return m["settings"]
    for m in MODEL_CATALOG:
        if m["engine"] == engine:
            logger.warning(
                f"No exact MODEL_CATALOG entry for {engine}/{model!r} — "
                f"falling back to {m['model']!r}'s settings"
            )
            return m["settings"]
    raise ValueError(f"No MODEL_CATALOG entry for engine {engine!r}")


def set_engine_and_model(direction: str, engine: str, model: str) -> None:
    if direction not in ("inbound", "outbound"):
        raise ValueError(f"direction must be 'inbound' or 'outbound', got {direction!r}")
    if (engine, model) not in _CATALOG_KEYS:
        raise ValueError(f"{engine}/{model} is not in MODEL_CATALOG")

    data = _read()
    data[direction] = {"engine": engine, "model": model}
    with open(RUNTIME_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Model selection saved — direction={direction} engine={engine} model={model}")


def current_selection() -> dict:
    """Returns {"inbound": {...}, "outbound": {...}} for the dashboard to
    display and pre-select in its model picker."""
    return _read()
