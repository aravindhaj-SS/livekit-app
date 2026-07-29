"""Per-call cost estimation for the dashboard — token pricing for whichever
realtime engine actually handled the call, PLUS Exotel's own per-minute
telephony charge. Estimates only, not a billing-reconciled figure.

OpenAI: developers.openai.com/api/docs/pricing, re-verified 2026-07-22 — the
gpt-realtime-2.1 text_output rate was wrong (was 16.00, actual is 24.00; audio
rates and the text_input/cached figures were already correct). Gemini:
ai.google.dev/gemini-api/docs/pricing, re-verified 2026-07-22 — matches
exactly for gemini-2.5-flash-native-audio-preview-12-2025 and
gemini-3.1-flash-live-preview; that page lists no separate cached-token rate
for the Live API models, so cached tokens are billed at the same rate as
uncached for Gemini (no discount to apply).

This used to only account for the AI model's token cost — the Exotel per-minute
telephony leg (a real, separate charge on every call) wasn't in the estimate at
all. compute_call_cost() below now returns both legs plus a blended INR total,
so nothing about the actual bill is silently missing from the dashboard.
"""
import math

from core.config import settings

# USD per 1,000,000 tokens, keyed by (engine, model).
_RATES = {
    ("openai", "gpt-realtime-2.1"): {
        "text_input": 4.00, "text_input_cached": 0.40, "text_output": 24.00,
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


def compute_exotel_cost_inr(call_duration_s: float, direction: str = "outbound") -> float:
    """Exotel bills in whole-minute pulses, rounded up — same as a phone bill,
    a 61-second call is billed as 2 minutes. Flat per-minute rate, no token
    math: inbound and outbound are priced differently on this account."""
    if call_duration_s <= 0:
        return 0.0
    minutes = math.ceil(call_duration_s / 60.0)
    rate = (
        settings.EXOTEL_COST_PER_MIN_INBOUND_INR
        if direction == "inbound"
        else settings.EXOTEL_COST_PER_MIN_OUTBOUND_INR
    )
    return round(minutes * rate, 4)


def compute_call_cost(
    usage: dict,
    engine: str,
    model: str | None,
    call_duration_s: float,
    direction: str = "outbound",
) -> dict:
    """Full per-call cost breakdown for the dashboard: the AI model's token
    cost (USD) and Exotel's telephony cost (INR) as two separate real charges,
    plus a blended INR total (using settings.USD_TO_INR — an approximate,
    manually-set conversion for display only, not a billing figure)."""
    ai_cost_usd = compute_call_cost_usd(usage, engine=engine, model=model)
    exotel_cost_inr = compute_exotel_cost_inr(call_duration_s, direction=direction)
    minutes = call_duration_s / 60.0 if call_duration_s > 0 else 0.0
    total_cost_inr = round(ai_cost_usd * settings.USD_TO_INR + exotel_cost_inr, 4)
    ai_cost_per_min_usd = round(ai_cost_usd / minutes, 6) if minutes > 0 else 0.0
    exotel_cost_per_min_inr = (
        settings.EXOTEL_COST_PER_MIN_INBOUND_INR
        if direction == "inbound"
        else settings.EXOTEL_COST_PER_MIN_OUTBOUND_INR
    )
    return {
        "ai_cost_usd": ai_cost_usd,
        "ai_cost_per_min_usd": ai_cost_per_min_usd,
        "exotel_cost_inr": exotel_cost_inr,
        "exotel_cost_per_min_inr": exotel_cost_per_min_inr,
        # Blended per-minute figure (AI + telephony, one INR number) — for the
        # per-call detail panel and the master dashboard's per-minute-cost
        # charts, so neither has to re-derive this from the two separate
        # legs above (and risk drifting from this exact formula).
        "blended_cost_per_min_inr": round(ai_cost_per_min_usd * settings.USD_TO_INR + exotel_cost_per_min_inr, 4),
        "total_cost_inr": total_cost_inr,
        "usd_to_inr_rate": settings.USD_TO_INR,
    }
