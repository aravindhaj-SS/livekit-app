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

    # ── Gemini Live — kept configured but no longer used live (see below) ─────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash-native-audio-preview-12-2025"

    # ── OpenAI Realtime (active speech-to-speech engine) ───────────────────────
    # Switched from Gemini Live: real test calls showed 30-50s silent stalls
    # around tool calls, matching a documented, currently-open issue with
    # Gemini's native-audio model's function-calling latency (LiveKit
    # agents#4554; Google's own dev forum). gpt-realtime-2.1 specifically
    # targets that failure mode — a spoken preamble the moment it decides to
    # call a tool, plus asynchronous function calling so a slow tool call no
    # longer freezes the session.
    OPEN_AI_API_KEY: str = ""
    OPENAI_REALTIME_MODEL: str = "gpt-realtime-2.1"

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

    # ── Dashboard login (/login, /dashboard) ──────────────────────────────────
    DASHBOARD_ADMIN_USERNAME: str = "admin"
    DASHBOARD_ADMIN_PASSWORD: str = "admin_123"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
