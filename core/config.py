from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Exotel (outbound calling + WSS bidirectional voice streaming) ─────────
    # Account SID + API key/token from the Exotel dashboard (Settings > API
    # Settings). EXOTEL_SUBDOMAIN selects the API region: api.exotel.com
    # (Singapore/Global) or api.in.exotel.com (Mumbai).
    EXOTEL_API_KEY: str = ""
    EXOTEL_API_TOKEN: str = ""
    EXOTEL_ACCOUNT_SID: str = ""
    EXOTEL_SUBDOMAIN: str = "api.exotel.com"
    # App Bazaar Flow ID containing the Voicebot Applet configured with this
    # app's wss:// media-stream URL. Referenced via the Url param on the
    # outbound Calls/connect API as my.exotel.com/{sid}/exoml/start_voice/{id}.
    EXOTEL_APP_ID: str = ""
    # The ExoPhone (virtual number) calls are placed from — passed as CallerId.
    EXOTEL_CALLER_ID: str = ""

    BASE_URL: str = "http://localhost:8000"

    # ── Postgres (lead/call storage — replaces the old leads.db SQLite file) ──
    DATABASE_URL: str = "postgresql://livekit_app:changeme@localhost:5432/livekit_leads"

    # ── Gemini Live — kept configured but no longer used live (see below) ─────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash-native-audio-preview-12-2025"

    # ── OpenAI Realtime (active speech-to-speech engine) ───────────────────────
    # Switched from Gemini Live: real test calls showed 30-50s silent stalls
    # around tool calls, matching a documented, currently-open issue with
    # Gemini's native-audio model's function-calling latency (LiveKit
    # agents#4554; Google's own dev forum).
    #
    # Standardized on plain "gpt-realtime" (not "-2.1") for both directions —
    # a deliberate operational decision, not a placeholder. This is the
    # fallback default only (used if runtime_model_config.json is ever
    # missing, e.g. a fresh deploy); the live per-direction selection lives
    # in that file and is what actually governs real calls.
    OPEN_AI_API_KEY: str = ""
    OPENAI_REALTIME_MODEL: str = "gpt-realtime"

    # Diagnostic-only toggle: "openai" (default, production) or "gemini" — a
    # one-off comparison test against Gemini Live's native-audio model, using
    # the SAME static prompt/no-tools path as the OpenAI latency test so the
    # ttft/duration numbers are comparable apples-to-apples. Pair with
    # DISABLE_TOOLS_FOR_LATENCY_TEST=true, since Gemini Live's own tool-calling
    # latency defect (the reason we moved to OpenAI in the first place) only
    # shows up when tools are in play.
    REALTIME_ENGINE: str = "openai"

    # Diagnostic-only toggle: when true, MiraAgent starts with NO tools at all
    # (no save_lead_info, no search_knowledge_base) so a test call runs purely
    # off the static system prompt. Used to isolate whether tool-calling round
    # trips are contributing to per-turn latency, separate from the reasoning-
    # effort/turn-detection settings. Live BANT capture is lost for the
    # duration of the test (reconcile_transcript still recovers it after the
    # call from the transcript). Turn back off for normal operation.
    DISABLE_TOOLS_FOR_LATENCY_TEST: bool = False

    # ── Google Calendar (discovery call booking via OAuth installed-app flow) ──
    CALENDAR_ORGANIZER_EMAIL: str = "info@swaransoft.com"

    # Local RAG service (optional). If no server is listening on this port,
    # core/rag.ask() fails open (returns empty context) — the agent then
    # answers from the model's general knowledge. Nothing to run for it to work.
    RAG_PORT: int = 8006

    # ── Telephony (Exotel) per-minute billing — dashboard cost estimate ────────
    # Flat per-minute rates from the Exotel account's own rate card, billed in
    # whole-minute pulses (rounded up), same as a phone bill. These are separate
    # from, and additive with, the Gemini/OpenAI per-token cost in core/costs.py.
    EXOTEL_COST_PER_MIN_INBOUND_INR: float = 0.22
    EXOTEL_COST_PER_MIN_OUTBOUND_INR: float = 0.60

    # Approximate, manually-set conversion used ONLY to combine the USD model
    # cost with the INR telephony cost into one dashboard "Total" figure — not
    # a live FX rate, not billing-accurate. Update as needed.
    USD_TO_INR: float = 87.5

    # ── Dashboard login (/login, /dashboard) ──────────────────────────────────
    DASHBOARD_ADMIN_USERNAME: str = "admin"
    DASHBOARD_ADMIN_PASSWORD: str = "admin_123"

    # ── Local model for dashboard AI insights (core/ollama_client.py) ──────────
    # Mira's OWN dedicated Ollama instance (deploy/ollama-mira.service) — NOT
    # the machine's shared ollama.service (port 11434), which is tuned
    # OLLAMA_NUM_PARALLEL=1 (globally single-request) for a different,
    # latency-critical voice system. Routing dashboard-insight traffic through
    # that shared instance would contend for its one inference slot and could
    # stall somebody else's live call, so this app runs its own isolated
    # instance on a different port instead. See core/ollama_client.py.
    OLLAMA_MIRA_HOST: str = "http://127.0.0.1:11435"
    OLLAMA_MIRA_MODEL: str = "llama3.1:8b"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
