"""Per-call cost estimation for the dashboard — token pricing for whichever
realtime engine actually handled the call. Estimates only, not a
billing-reconciled figure.

OpenAI: developers.openai.com/api/docs/models/gpt-realtime, verified 2026-07-21.
Gemini: ai.google.dev/gemini-api/docs/pricing, verified 2026-07-21 — that page
lists no separate cached-token rate for the Live API models, so cached tokens
are billed at the same rate as uncached for Gemini (no discount to apply).
"""

# USD per 1,000,000 tokens, keyed by (engine, model).
_RATES = {
    ("openai", "gpt-realtime-2.1"): {
        "text_input": 4.00, "text_input_cached": 0.40, "text_output": 16.00,
        "audio_input": 32.00, "audio_input_cached": 0.40, "audio_output": 64.00,
    },
    ("gemini", "gemini-3.1-flash-live-preview"): {
        "text_input": 0.75, "text_input_cached": 0.75, "text_output": 4.50,
        "audio_input": 3.00, "audio_input_cached": 3.00, "audio_output": 12.00,
    },
    ("gemini", "gemini-2.5-flash-native-audio-preview-12-2025"): {
        "text_input": 0.50, "text_input_cached": 0.50, "text_output": 2.00,
        "audio_input": 3.00, "audio_input_cached": 3.00, "audio_output": 12.00,
    },
    ("gemini", "gemini-live-2.5-flash-native-audio"): {
        "text_input": 0.50, "text_input_cached": 0.50, "text_output": 2.00,
        "audio_input": 3.00, "audio_input_cached": 3.00, "audio_output": 12.00,
    },
}

# Fallback when the exact model string isn't in the table above (e.g. a new
# Gemini Live model gets configured before its rates are added here).
_ENGINE_DEFAULT_MODEL = {
    "openai": "gpt-realtime-2.1",
    "gemini": "gemini-3.1-flash-live-preview",
}


def compute_call_cost_usd(usage: dict, engine: str = "openai", model: str | None = None) -> float:
    """usage keys: text_input, text_input_cached, text_output,
    audio_input, audio_input_cached, audio_output — all raw token counts,
    uncached-vs-cached already split out. Missing keys default to 0."""
    rates = _RATES.get((engine, model))
    if rates is None:
        rates = _RATES[(engine, _ENGINE_DEFAULT_MODEL.get(engine, "gpt-realtime-2.1"))]

    def toks(key: str) -> int:
        return int(usage.get(key) or 0)

    text_input_uncached = max(toks("text_input") - toks("text_input_cached"), 0)
    audio_input_uncached = max(toks("audio_input") - toks("audio_input_cached"), 0)

    cost = (
        text_input_uncached / 1_000_000 * rates["text_input"]
        + toks("text_input_cached") / 1_000_000 * rates["text_input_cached"]
        + toks("text_output") / 1_000_000 * rates["text_output"]
        + audio_input_uncached / 1_000_000 * rates["audio_input"]
        + toks("audio_input_cached") / 1_000_000 * rates["audio_input_cached"]
        + toks("audio_output") / 1_000_000 * rates["audio_output"]
    )
    return round(cost, 6)
