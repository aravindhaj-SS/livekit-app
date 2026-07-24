# Mira — Voice Sales Agent (Swaran Soft)

Voice AI sales/support agent. Handles inbound + outbound phone calls over Exotel telephony. Talks BANT qualification, books discovery calls, hangs up when done. Runs on Gemini Live or OpenAI Realtime — swappable per direction, live, from dashboard.

No real LiveKit server/room. `livekit-agents` SDK reused roomless — just `AgentSession` + `RealtimeModel` classes, wired straight to Exotel's WebSocket audio.

## Stack

- FastAPI + uvicorn, single process, port 8090.
- Exotel bidirectional WS (`start`/`media`/`stop` events) — 8kHz mono PCM16.
- Realtime brain: Gemini Live (`livekit-plugins-google`) or OpenAI Realtime (`livekit-plugins-openai`). Picked per-call via `core/model_config.py`.
- Postgres (asyncpg) — was SQLite, migrated.
- Google Calendar API — books discovery-call Meet links.
- Local RAG microservice (separate process) — knowledge base lookups.
- systemd unit, `Restart=on-failure` — app can self-restart on model switch (see below).
- Cloudflare tunnel for public exposure.

## Call Flow

1. Outbound: lead submits form (`static/index.html`) → `POST /api/callback` → Exotel dials → `voice/gemini_bridge.py` picks up the WS media stream.
2. Inbound: caller dials Exotel number → App Bazaar Voicebot applet → same WS media stream, no pre-registered lead.
3. `CallSession` (in `gemini_bridge.py`) owns one call end-to-end: builds prompt, starts `AgentSession`, feeds/drains audio, watches for hangup, writes recording, persists to DB.
4. Exotel flow now has a Hangup applet after the Voicebot applet — closing our WS actually ends the PSTN call (didn't used to).

## File Map

### `main.py` — app entrypoint
- `lifespan(app)` — init DB on start, close DB/RAG client on shutdown.
- `NoCacheStaticFiles` — StaticFiles subclass, forces `Cache-Control: no-cache` on `/static/*` so dashboard JS/CSS never goes stale in browser cache after a deploy.
- `root()` — serves lead-intake form.
- `health()` — `{"status": "ok"}`, used by dashboard to detect app back up after restart.
- `login_page()` / `login_submit()` / `logout()` — dashboard auth cookie flow.
- `dashboard_page()` / `dashboard_inbound_page()` — serve the two dashboards, cookie-gated.

### `voice/gemini_bridge.py` — the whole call engine (biggest file, read this first)
- `ExotelAudioInput` / `ExotelAudioOutput` — bridge Exotel PCM frames ↔ `AgentSession`'s audio I/O interfaces.
- `MiraAgent(Agent)` — holds prompt + tools.
  - `save_lead_info(...)` — tool, model calls this to report name/budget/timeline/etc, has docstring now (see Fixes) so model gets per-field guidance straight from tool schema.
  - `search_knowledge_base(query)` — tool, hits local RAG service.
- `CallSession` — one instance per call.
  - `run()` — main WS receive loop.
  - `_on_start()` — resolves lead (pre-registered outbound row, or fresh inbound row), builds `LeadState`.
  - `_start_agent_session()` — builds `GeminiRealtimeModel`/`RealtimeModel` from `core/model_config.py`'s per-model settings, starts `AgentSession`, fires opening line.
  - `_speak_literal(line)` — force model to say an exact line (greeting/reprompt/hangup lines). Two paths: `generate_reply()` for models that support it, recorded speech-trigger WAV for Gemini 3.1 (see Fixes).
  - `_greeting_watchdog()` — retries opening line if agent stays silent. State-aware now, not blind timer.
  - `_on_agent_session_error()` / `_is_active_response_collision()` / `_recover_dropped_turn()` — catches OpenAI's "already has active response" error, nudges model to answer the dropped turn.
  - `_watchdog_loop()` — silence/reprompt/hangup timers + graceful-end detection (agent's last line wasn't a question + caller gone quiet = call over).
  - `_last_assistant_turn_was_a_statement()` — tool-independent "is call over" signal, reads transcript not tool calls.
  - `_end()` — teardown: write recording, compute cost, reconcile transcript gaps, persist to DB, close session/WS, book calendar if discovery call agreed.
  - `_write_recording()` — self-recorded stereo WAV (left=caller, right=agent), not Exotel's own recording feature.
  - `_reconnect()` — rebuilds session on Gemini session death, keeps same `LeadState`.
- `media_stream(ws)` — the actual `/media-stream` WS route, owns a `CallSession`.

### `agent/prompt.py` — system prompt (one static prompt, no per-model branching)
- `build_instructions(state)` — assembles full prompt: identity/opening, language auto-detect, company facts, guardrails, voice style, tool-call filler phrases, busy/callback handling, BANT qualification gate, booking sequence, closing gate, reporting rule.
- `_identity_block(state)` — branches inbound (ask name first) vs outbound (already know who/why).
- Branches only on `state.direction`. Never branches on engine/model.

### `agent/lead_agent.py` — deterministic backup extractors + form pre-filter
- Regex/heuristic extractors: email (incl. spelled-out), budget, timeline, decision-maker status, preferred slot/date, discovery-call intent.
- `reconcile_transcript(transcript)` — post-call safety net, fills gaps the model's own tool-calls missed. Never overrides live data.
- `classify_in_scope_form(interest_area)` — Gemini-flash call, filters junk/out-of-scope form submissions before dialing. Fails open.

### `agent/lead_state.py`
- `LeadState` — per-call in-memory record (identity, BANT fields, email/slot, callback, classification, `call_metrics` dict).

### `core/model_config.py` — model catalog + per-direction runtime switch
- `MODEL_CATALOG` — 6 entries (4 OpenAI, 2 Gemini), each with display metadata + a `settings` dict (voice, speed, reasoning effort, VAD window / tool_response_scheduling).
- `get_engine_and_model(direction)` — reads `runtime_model_config.json`, falls back to `.env`.
- `get_model_settings(engine, model)` — per-model tuning lookup, falls back gracefully if catalog entry missing.
- `set_engine_and_model(direction, engine, model)` — validates + writes selection to disk.
- `request_restart()` — `os._exit(1)`, relies on systemd `Restart=on-failure` to bring it back. No sudo, no manual process kill needed.

### `core/database.py` — Postgres data layer (asyncpg)
- `init_db()` / `close_db()` — pool + schema setup, idempotent `ALTER TABLE` migrations.
- `save_lead()` — new outbound lead (pre-call, from form).
- `create_inbound_lead(phone_number)` — bare inbound row at call-connect (name/company unknown yet).
- `update_lead_state()` — final persist at call end.
- `save_meeting_link()` — post-booking update.
- `get_lead()` / `get_all_leads(direction=None)` — reads, feed the dashboards.

### `core/config.py`
- `Settings` — all env-backed config: Exotel creds, `DATABASE_URL`, Gemini/OpenAI keys+models, `REALTIME_ENGINE` toggle, `DISABLE_TOOLS_FOR_LATENCY_TEST`, calendar/RAG config, cost rates, dashboard admin creds.

### `core/costs.py`
- `compute_call_cost_usd()` — AI token cost from usage dict.
- `compute_exotel_cost_inr()` — telephony cost, whole-minute pulses, rate depends on direction.
- `compute_call_cost()` — combined breakdown dict, feeds dashboard.

### `core/calendar.py`
- `book_discovery_call()` — creates Calendar event + Meet link using `token.pkl` OAuth creds. Returns None on any failure (never blocks call end).

### `core/pending_calls.py`
- `register(phone, lead_id)` / `consume(phone)` — TTL cache (120s) linking an outbound dial to its lead_id when the WS stream connects.

### `core/dashboard_auth.py`
- `dash_token()` / `dash_token_valid()` — single-account HMAC cookie auth, process-lifetime secret (deploy = everyone logged out).

### `core/rag.py`
- `ask(query, ...)` — hits local RAG microservice, fails open (empty context) on any error.

### `api/routes.py`
- `POST /api/callback` — new outbound lead, scope-filter, dial via Exotel.
- `GET /api/calls` — feeds both dashboards (direction filter).
- `GET /api/models` / `POST /api/models/{direction}` — model catalog + switch-and-restart.
- `GET /api/recordings/{lead_id}` — streams self-recorded WAV.

### `static/` — dashboards
- `dashboard.html` (outbound) / `dashboard_inbound.html` (inbound) — same design system, direction-specific KPIs/columns.
- `dashboard-common.js` — shared render helpers (icons, KPI strip, sparkline, transcript/cost panel) + model-picker modal (fetch catalog, POST switch, poll `/health` till app's back, reload).
- `dashboard.css` — shared theme (warm charcoal/gold, not default AI-dashboard navy).

### `assets/gemini_speech_trigger.wav`
Real recorded human "Hello" clip, 16kHz mono. Fed into Gemini 3.1's audio-input path to force it to speak first (see Fixes — text injection doesn't work on this model, only real speech does).

### `scripts/`
- `auth_setup.py` — one-time interactive Google OAuth, writes `token.pkl`.
- `migrate_sqlite_to_postgres.py` — one-time, idempotent, SQLite → Postgres data migration (preserves ids).

### `deploy/`
- `livekit-app.service` — systemd unit, `Restart=on-failure`, runs the app.
- `cloudflared-livekit-app.service` — tunnel unit.

---

## Issues Faced + Fixes (chronological, condensed)

**Agent silent on pickup (didn't greet first).**
Cause: relying on freeform "compose an opener" instruction → model sometimes returned zero audio. Fix: literal deterministic opening line via `generate_reply(instructions=say_exactly(line))`, not a meta-instruction.

**Gemini 3.1 never speaks first, ever.**
Cause: `livekit-plugins-google` hard-disables `generate_reply()`/`update_chat_ctx()` for any model with "3.1" in name (`mutable = "3.1" not in model`). Confirmed via raw SDK test: even bypassing plugin, text/synthetic-TTS injection produces zero audio on this model — Gemini server itself won't trigger a turn from injected text. Only real recorded human speech works. Fix: play a real recorded "Hello" clip into the same audio-input path as caller audio (speech-trigger), model's own VAD picks it up, composes its own greeting per prompt. Not a literal script — model-composed. ~10-15s slower than other engines, not first-attempt-instant (server quirk on fresh connections, unresolved).

**Agent not hanging up after call ends.**
Two causes: (1) code only had `save_lead_info(call_complete=true)` as hangup signal — tool-dependent, skippable. (2) Exotel: closing our WS didn't actually hang up the underlying PSTN call, App Bazaar flow just advanced past Voicebot applet. Fixes: added tool-independent "last agent line wasn't a question + caller silent N sec = call over" detection (`_last_assistant_turn_was_a_statement`); user added a Hangup applet after Voicebot applet in Exotel flow.

**"conversation_already_has_active_response" (OpenAI) → agent goes silent/dead mid-call.**
Cause: `_greeting_watchdog` retried `generate_reply()` blind on a flat 6s timer with zero check for an in-flight generation. OpenAI's own `turn_detection=ServerVad(create_response=True)` also auto-fires a response server-side whenever its VAD detects caller done talking — collides with our own retry, or with the caller's own utterance racing our opening line. OpenAI plugin never guards against double `response.create`. Confirmed: Gemini's plugin does self-supersede on this, OpenAI's doesn't. Fix: watchdog + `_speak_literal` now check `agent_state` (public SDK property) before firing `generate_reply()` — skip if `"thinking"`/`"speaking"`. Added `_is_active_response_collision()` + `_recover_dropped_turn()` — detects this exact error code, waits for the in-flight generation to clear, nudges model to answer whatever got dropped.

**Only one call ever hung up cleanly.**
Root cause: same as above — a response collision could leave `agent_state` stuck off `"listening"` forever, and the ENTIRE watchdog loop (hangup included) was gated on `agent_state == "listening"`. Fix: decoupled the graceful-end check from that gate — now also fires if state's been stuck non-listening past `STUCK_TURN_STATE_MS` (25s, well past any real turn). Explicitly NOT a call-duration timer — still purely content+idle based, only a dead-man's-switch for a wedged SDK state.

**Agent inventing caller names ("Vijay", "Rajesh") on inbound calls that never gave one.**
Cause: pure model hallucination, not a code bug — traced full pipeline (`LeadState` default, DB insert, opening line, prompt) and confirmed nothing seeds/guesses a name anywhere in code. Real cause: no guardrail in prompt telling model what to do when name's unclear, AND `save_lead_info` tool had zero docstring so `name` param reached model with no schema description at all. Fix: added explicit "never guess a name" rule to prompt (OPENING + GUARDRAILS), added full docstring to `save_lead_info` with per-field guidance (verified via schema introspection — description now present, previously `None`).

**BANT questions (budget/timeline/decision-maker) asked all bundled in one turn.**
Cause: "ask one at a time" was a single trailing sentence in prompt, much weaker than the numbered BOOKING SEQUENCE's structure. Fix: restructured into a numbered, hard-gated, one-question-per-turn sequence matching BOOKING SEQUENCE's emphasis.

**Every model forced through identical hardcoded per-engine settings.**
Cause: `_start_agent_session` only branched on engine (openai/gemini), not on which specific model was picked. Fix: `MODEL_CATALOG` entries now carry per-model `settings` (voice/speed/reasoning effort/VAD window), `reasoning_effort` only set for confirmed reasoning-capable models (`gpt-realtime-2.1`, its mini), left unset for `gpt-realtime` (unconfirmed) and legacy `gpt-4o-realtime-preview` (predates the feature) — avoids passing an unsupported param.

**Model switcher (dashboard button) not doing anything after clicking / icons still huge after fix deployed + service restart.**
Cause: NOT a server bug — confirmed via curl that server was serving updated files correctly the whole time. Browser cached old `dashboard.css`/`dashboard-common.js` (Starlette's `StaticFiles` sends no `Cache-Control` header at all → browser heuristic-caches indefinitely). Restarting the systemd service does nothing to a browser's cache. Fix: `NoCacheStaticFiles` subclass forces `Cache-Control: no-cache` on all `/static/*` (future-proof), plus `?v=2` cache-busting query param on the CSS/JS `<link>`/`<script>` tags (fixes it immediately for already-stale browsers).

**Icons rendering huge across dashboard.**
Cause: CSS sized the wrapping `<span>` (from JS `icon()` helper), not the nested bare `<svg>` inside it — an unsized `<svg>` defaults to ~300x150px intrinsic size. Fix: added `.kicon svg`/`.ico svg`/`.ticon svg` descendant selectors with explicit width/height, matching the pattern already used correctly elsewhere in the same stylesheet.

**Gemini 2.5 repeating same response on real inbound call.**
Cause: known Gemini Live defect — "server cancelled tool calls" after a `save_lead_info` call, correlated exactly with abnormal 6-7s generation stall + duplicate tool call + duplicate transcript line. Pre-existing/documented issue, not new code.

**Language auto-detect not switching to Tamil/Hindi mid-call, hangs on spoken times in Tamil.**
Fix: prompt LANGUAGE section — detect + switch automatically every turn, no explicit request needed; added TOOL-CALL LANGUAGE rule — always report budget/timeline/availability/callback_time to tools in English day/time phrasing regardless of conversation language (tools/internal systems only understand English phrasing).

**Pricing bug — `gpt-realtime-2.1` text_output rate wrong in cost table.**
Was $16/1M, actual (verified against OpenAI pricing page) is $24/1M. Fixed in `core/costs.py`.

**SQLite → Postgres migration.**
Full schema move via asyncpg, preserves row ids (`ON CONFLICT DO NOTHING` + sequence reset), `scripts/migrate_sqlite_to_postgres.py` is one-time/idempotent/safe to re-run.

**Dashboard redesign.**
Old dashboard was default-AI-generic (navy/indigo). Redesigned around warm charcoal + gold ("Swaran" = gold in Sanskrit), added KPI strip, call-volume sparkline, token-usage breakdown, priced-against-model tag — kept every existing field, added new ones only.

## Gotchas / Rules for future work

- **Never manage the process yourself** (start/stop/restart/kill) — tell user, they run it. Established rule, not a suggestion.
- `DISABLE_TOOLS_FOR_LATENCY_TEST` toggle in `core/config.py` — keep it, used for isolating raw conversational latency from tool-calling defects. Don't remove.
- `runtime_model_config.json` is gitignored — runtime state, not source. Don't commit it.
- Any static file edit (`dashboard.css`/`dashboard-common.js`) needs its cache-busting version bumped in the HTML `<link>`/`<script>` tags, or browsers may not see the change.
- Model switch via dashboard just writes `runtime_model_config.json` — no process restart. Removed the old restart-on-switch behavior (`request_restart()`/`os._exit(1)`): `get_engine_and_model()` already re-reads that file fresh on every call with no caching, so a restart was never actually needed to make a switch take effect, and it had a real cost — it dropped any call in progress the instant systemd relaunched the process. Applies to the next call for that direction; in-flight calls are unaffected.
- Prompt is ONE static string, no per-engine branching. If a future model needs different phrasing, that's a deliberate architecture change, not a quick patch.
- `agent_state` (`"initializing"|"idle"|"listening"|"thinking"|"speaking"`) is the load-bearing signal for both greet-first and hangup logic. Any future change to turn-detection/VAD config should re-check both mechanisms still work.
