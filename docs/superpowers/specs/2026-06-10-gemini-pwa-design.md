# Gemini Live PWA — Design

**Date:** 2026-06-10
**Status:** Approved direction (option A of three packaging routes)

## Goal

Mature CC-Caller into a single polished, publicly shippable feature: a **free, browser-based voice loop with Claude Code**, powered by Gemini Live with proper tool-calling, packaged as an installable PWA-serving CLI.

Target audience: (B) casual visitors from a HN/Reddit/X post who give a repo 5 minutes — free and instant matters more than maximum call quality; (C) showcase value — the README demo is part of the product.

Headline story: *"Talk to Claude Code from your phone. Free."* — requires only a free Google AI Studio key. No VAPI, no Twilio, no Linphone for the default path.

## Why this route

The three VAPI transports (SIP, PWA, Twilio) already share one brain; the Gemini path is the outlier with a second, weaker brain. Its glitchiness has an identified architectural cause, not an inherent one: `gemini_bridge.py` drives the conversation with raw text injection (`realtimeInput.text`) plus a fragile transcript heuristic (1.5s debounce buffer gated on a mic flag). The VAPI agent behaves well because it gets results through a structured server tool (`askCodingAgent`). Gemini Live supports function calling too — so the fix is porting the proven tool-based pattern, not endless prompt tuning.

## Architecture

```
Browser PWA (mic/speaker, captions, push)
   │  WebSocket (token-authenticated, via cloudflared tunnel)
   ▼
FastAPI server ── GeminiLiveSession (tool-calling) ── Gemini Live WS API
   │                      │ toolCall: askCodingAgent(task)
   │                      ▼
   │              Claude worker (claude -p --resume <uuid>, sandboxed)
   └── Web Push (VAPID) / ntfy for away-from-desk callback
```

Topology stays server-side bridge (browser ↔ local FastAPI WS ↔ Gemini Live WS): tool calls must execute `claude` on the host machine, and the API key never reaches the browser.

## Package restructure

The repo becomes an installable package. The current `cc-caller` shell wrapper hardcodes an absolute path and cannot ship.

```
cc_caller/
  __init__.py
  cli.py            # arg parsing, mode dispatch; `cc-caller` console script
  claude_worker.py  # extracted from cc_caller.py: run_claude, name_to_uuid,
                    #   session auto-recovery, WORKER_SYSTEM_PROMPT, DISALLOWED_FILES,
                    #   clean_transcript, check_needs_input, is_termination, log_interaction
  gemini_live.py    # NEW: GeminiLiveSession — setup, tool declarations, toolCall
                    #   dispatch, audio relay, reconnect context
  server.py         # FastAPI app: /ws audio bridge, /api/config, /api/push-subscribe,
                    #   static file serving, sw.js; token middleware
  push.py           # VAPID keygen + pywebpush send (extracted)
  tunnel.py         # cloudflared (default) / ngrok / --tunnel-url (extracted)
  notify.py         # ntfy (extracted)
  vapi/             # existing vapi_client.py + SIP/Twilio/phone-loop glue, relocated
static/             # real files: index.html, app.js, audio-worklet.js, styles.css,
                    #   sw.js, manifest.json, icons
experiments/        # livekit_server.py, livekit_audio_bridge.py (preserved, not shipped paths)
tests/
pyproject.toml      # console script entry point; Python >= 3.9
```

Battle-tested logic moves, it is not rewritten: Claude worker, push, tunnel, ntfy, summarizer, VAPI client. The two rebuilt layers are the Gemini session and the PWA frontend.

## Gemini Live session (`gemini_live.py`)

**Setup message:**
- Model from `GEMINI_LIVE_MODEL` env var, default `models/gemini-3.1-flash-live-preview` (preview models churn; one-line config change).
- `responseModalities: ["AUDIO"]`, voice `Kore`, temperature 0.1 (carried over from current bridge).
- `automaticActivityDetection`: current values carried over (silence 2000ms, prefix 500ms), overridable via env.
- Input + output transcription enabled — **for UI captions only, never control flow**.
- System prompt: relay persona — acknowledge the task, call `askCodingAgent`, never read code verbatim, keep replies voice-friendly and short; answer follow-ups about prior results from injected context without re-calling the tool.

**Declared tools:**

| Tool | Behavior | Purpose |
|------|----------|---------|
| `askCodingAgent(task: string)` | `NON_BLOCKING` | Send a task to Claude; ack now, result later |
| `checkStatus()` | blocking | Report whether a task is running and elapsed time |
| `endSession()` | blocking | User is done; close the session gracefully |

**askCodingAgent flow:**
1. `toolCall` arrives → immediately send an interim `FunctionResponse` (`{"status": "started"}`, `willContinue: true`) so the agent verbally confirms and the conversation stays alive.
2. Handler acquires the task lock (see Concurrency), runs `clean_transcript(task)` then `run_claude(...)` in an executor thread.
3. On completion: `summarize_output(...)` for a voice-friendly version; full output appended to `.cc-caller-log` (existing `log_interaction`).
4. Send the final `FunctionResponse` for the same call id with `scheduling: INTERRUPT` — the agent speaks the result the moment it is ready, even minutes later. This removes VAPI's ~40s tool-timeout dance entirely. The exact interim/final field shapes are verified against the live API during implementation; if they differ, the fallback below absorbs it.

**Fallback (must be implemented, not deferred):** if the configured model rejects `NON_BLOCKING` declarations or `scheduling`, the session degrades to quick-ack delivery — the late result is injected as a `clientContent` user turn ("[SYSTEM] Task finished. Tell the user: …"). Delivery is one pluggable function (`deliver_result`), chosen at session start based on the setup response.

**Deleted:** `inject_text`/`realtimeInput.text` control flow, the 1.5s transcript debounce, the `mic_active` gating heuristic, and the `transcript_queue` dependency for this path.

## Concurrency and sessions

- One task at a time, enforced by the existing `tool_lock` pattern (`acquire(timeout=1)`); a duplicate `askCodingAgent` while busy returns "Still working on the previous request."
- Claude session identity unchanged: deterministic UUID via `name_to_uuid(session_name)`, auto-recovery to a fresh session on resume errors. The Claude session is the durable memory.
- Gemini sessions are ephemeral per WebSocket connection. Cross-call context: server keeps `conversation_history` (task/result pairs); the last 5 (results truncated to 500 chars) are appended to the system prompt of each new session — same pattern as today's `build_pwa_config`.

## Away-from-desk flow (hybrid callback)

1. User gives a task and closes the tab / locks the phone → browser WS drops → Gemini session ends → **Claude keeps running** (executor thread is independent of the WS).
2. On completion with no live session: store `pending_result`, send a Web Push via existing VAPID/pywebpush plumbing ("Claude finished — tap to hear the result"). ntfy notification sent additionally if `NTFY_TOPIC` is set (optional fallback).
3. Notification click → service worker opens the PWA with `?callback=1` → page auto-connects → new Gemini session's system prompt includes the pending result with an instruction to open the call by reading it → `pending_result` cleared after the session confirms setup.

No cross-transport fallback: the Gemini PWA path calls back only via push/PWA (established project rule).

## PWA frontend (`static/`)

Real static files served by FastAPI — the inline-HTML pages in `webhook.py` for this path are removed.

- **One screen:** connect/disconnect button, live captions (user + agent, from transcription events), task-status strip ("idle" / "Claude working — 2m 14s" driven by WS status messages), session name display.
- **Audio:** AudioWorklet mic capture → 16kHz PCM base64 frames up the WS; playback queue for 24kHz PCM frames down the WS (replaces ScriptProcessor-era code).
- **PWA shell:** existing `manifest.json` + `sw.js` carried over (push handler, notification click → `?callback=1`); Wake Lock while connected; install prompt and push-permission request on first connect.
- **Status messages** over the same WS: `ready`, `task_started`, `task_done`, `error` — drives the status strip.

## Security (required before publishing)

Today anyone who discovers the tunnel URL can trigger Claude execution on the host. The Gemini path needs no open inbound webhooks (unlike VAPI), so:

- On startup, generate `secrets.token_urlsafe(32)`; print the PWA URL with `?token=…` and render it as a terminal QR code (`qrcode` package, ASCII output).
- The WS endpoint and all API/push endpoints require the token (query param or header), compared with `hmac.compare_digest`. Static assets stay public; nothing sensitive is in them.
- Token persists for the process lifetime so push-notification reopens keep working; the service worker stores it from the initial URL.

## CLI and onboarding

- `cc-caller` (no args) → default mode: Gemini PWA. Serve + tunnel + print URL/QR. Optional positional instruction starts Claude on a task immediately, with the result delivered on first connect.
- `cc-caller setup` → prompts for `GEMINI_API_KEY` (free AI Studio key), writes `~/.config/cc-caller/.env`. Config load order: `~/.config/cc-caller/.env`, then `./.env` overrides (repo-dir `.env` keeps working for the developer).
- Legacy transports kept under explicit flags, unchanged behavior: `--sip`, `--mode always` phone loop, VAPI `--pwa` etc., documented as "advanced transports" requiring VAPI credentials.
- Quickstart is three commands: `pipx install git+https://github.com/jp679/CC-Caller` (or `uvx --from git+https://github.com/jp679/CC-Caller cc-caller` to try without installing) → `cc-caller setup` → `cc-caller`. The same `pyproject.toml` powers pipx, uvx, and editable installs; PyPI remains a stretch goal.
- Python 3.9+ (no `match`, no `X | Y` unions).

## Install tiers and agent-assisted install

The README offers three install tiers, in this order:

1. **Via your AI agent** — a copy-paste prompt the *user* gives their own agent: "Scan https://github.com/jp679/CC-Caller — review it for safety, explain what it does and what installing changes on my machine, and if I approve, install and set it up." The instruction is user-issued; the repo never directs agents to install anything.
2. **One-liner** — pipx/uvx from GitHub (above).
3. **Hack on it** — `git clone` + `pip install -e .`.

To support tier 1, an `AGENTS.md` at the repo root provides factual orientation for agents evaluating or working in the repo. Content rules (these keep it from pattern-matching prompt injection):

- Descriptive statements only — what the tool does, what installation changes (console script on PATH, config at `~/.config/cc-caller/`), what network endpoints it contacts (Gemini API, cloudflared tunnel, push services, optional ntfy), what it executes locally (the `claude` CLI as a sandboxed subprocess), where the auth token logic lives, and how to verify (offline test suite, key files to read).
- No imperatives addressed to the agent, no requests to skip or shortcut review.
- One explicit deference line: agents should summarize findings and obtain their user's confirmation before installing or running anything.

## Subsumed and relocated code

- Removed (capability replaced by the new mode, not lost): `--gemini`, `--live`, `--web`, and the old text-injection `--bridge` — four experimental variants of the same idea — plus their inline HTML pages (`/call-gemini`, `/call-gemini-live`, `/call`, `/call-bridge`) and `gemini_bridge.py`.
- Relocated, preserved: `livekit_server.py`, `livekit_audio_bridge.py`, `/call-livekit` page → `experiments/` (kept for the future SIP idea, not a shipped path).
- Untouched: VAPI assistant configs, SIP/Twilio handlers, summarizer, worker sandboxing (`WORKER_SYSTEM_PROMPT`, `DISALLOWED_FILES`, `--disallowedTools`).

## Error handling

- Gemini WS drop mid-conversation: browser shows "reconnecting", auto-reconnects with fresh session + injected context; an in-flight task is unaffected and lands as `pending_result` if no session is live when it finishes.
- Claude failure: existing auto-recovery (fresh session UUID, retry); if it still fails, the tool response says so plainly and the full stderr goes to the log file.
- Tunnel death: process exits with a clear message (current behavior), push subscriptions are persisted to config dir so a restart can still notify.
- `setup` validates the key with a one-shot `models.list` call before writing config.

## Testing

Offline-mocked throughout, matching the existing suite:

- **Fake Gemini WS fixture**: an in-process websocket server speaking the Live protocol — drives `GeminiLiveSession` tests: setup handshake, toolCall → ack + `INTERRUPT`-scheduled response, fallback delivery when setup rejects NON_BLOCKING, audio frame relay both directions.
- **Server tests** (FastAPI TestClient): token required on WS/API (401 without, accept with), push-subscribe persistence, config endpoint.
- **Worker tests**: carried over from `tests/test_cc_caller.py` (mocked `subprocess.run`), plus pending-result flow (task finishes with no live session → push sent, result stored, cleared on next session).
- **Existing** summarizer and VAPI client tests carry over unchanged.

## Out of scope

- Rewriting or improving the VAPI SIP/Twilio paths (kept as-is).
- Reviving LiveKit.
- Multi-user / multi-device auth (single-user token is the model).
- Voice quality work beyond Gemini's built-in voices.
- Publishing to PyPI is a stretch goal; the spec targets "pipx install from GitHub" working.

## Success criteria

1. Fresh machine with only a Gemini key: quickstart to a working voice call in under 5 minutes.
2. Mid-call task → agent acks, conversation stays responsive, result spoken automatically on completion (no "check again" prompting).
3. Close tab mid-task → push notification → tap → agent reads the result.
4. `python3 -m pytest tests/ -q` passes offline.
5. README leads with a demo GIF and the three install tiers (agent prompt, pipx/uvx one-liner, clone); advanced transports documented below the fold.
6. `AGENTS.md` present, purely factual, with the user-confirmation deference line.
