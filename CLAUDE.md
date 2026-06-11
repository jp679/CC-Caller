# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CC-Caller is a voice loop for Claude Code: talk to Claude from your phone. Default path:

```
Browser PWA ── WebSocket ── FastAPI server ── Gemini Live (tool-calling) ── claude -p subprocess
```

Gemini declares `askCodingAgent` as a NON_BLOCKING tool: an interim ack keeps the conversation alive, and the final FunctionResponse with `scheduling: INTERRUPT` speaks the result whenever Claude finishes. Legacy VAPI transports (SIP / Twilio / VAPI web PWA) remain under explicit flags.

## Commands

```bash
python3 -m pytest tests/ -q                       # full offline suite — no network or credentials
python3 -m pytest tests/test_gemini_live.py -q    # one file
python3 -m pytest "tests/test_tasks.py::test_submit_runs_task_and_reports_completion"  # one test

./cc-caller                   # default mode: Gemini PWA (dev wrapper; installs get the console script)
./cc-caller setup             # onboarding wizard — Gemini key → ~/.config/cc-caller/.env
./cc-caller --sip --inbound   # legacy VAPI SIP transport (needs VAPI credentials)
pip install -e ".[dev]"       # dev install; pyproject.toml is the single dependency source
```

Python 3.9 compatible — no `match`, no `X | Y` unions. Live runs need the `claude` CLI and `cloudflared` on PATH.

## Architecture

- `cc_caller/cli.py` — entry point. `setup` wizard; legacy-flag dispatch (`LEGACY_TRIGGERS`) to `legacy_cli`; default `run_gemini_pwa`: preflights → VAPID → per-run token → TaskManager → token-gated server in a uvicorn thread → tunnel → QR. `make_on_complete` routes finished tasks: live session via `deliver_result` (INTERRUPT), otherwise web push + ntfy with the result kept pending. Optional user calibration from `<config_dir>/prompt.md` is appended to the relay prompt (`build_base_prompt`). Session choice lives in the PWA (server's `/api/sessions` + WS `?session=` param → `TaskManager.switch_session`); `--session-id`/`--new-session` set the spawn default.
- `cc_caller/gemini_live.py` — `GeminiLiveSession`: the Gemini Live WS protocol. Three declared tools (`askCodingAgent` NON_BLOCKING, `checkStatus`, `endSession`), audio relay, captions, thread-safe `deliver_result`. The ack-gate (`_ack_sent` asyncio.Event) guarantees the interim ack hits the wire before the final INTERRUPT — do not reorder it. Falls back to `clientContent` injection if the model rejects NON_BLOCKING declarations. Accepts `opening=` (a clientContent turn injected after ready, BEFORE `on_ready` — order matters: pending must not be consumed until the opener is on the wire) and accumulates coalesced voice transcriptions into `voice_log`, reported via `on_session_end` at teardown.
- `cc_caller/server.py` — token-gated FastAPI: `/ws` bridge, `/api/config`, `/api/push-subscribe`; only static assets are public. `build_system_prompt` injects last-5 history + any pending result into each new session; `build_system_prompt` also injects recent transcript of a resumed session (`sessions.recent_messages`). `GET /api/sessions` + WS `?session=` param switch (`TaskManager.switch_session`, refused while busy). On reconnect with a pending result, builds an opener so the agent speaks first (and suppresses the prompt's PENDING block — single source of truth). At session end, a daemon thread distills the voice log via `summarizer.summarize_conversation` into a callermem voice note. Utility `claude -p` calls run with `cwd=tempfile.gettempdir()` so they don't pollute the project's transcript dir (`sessions.UTILITY_PREFIXES`, derived from the prompt constants, filters any stragglers).
- `cc_caller/tasks.py` — `TaskManager`: one Claude task at a time, worker thread, `pending` result consumed via `take_pending()` by whichever path actually delivers. `on_complete` fires after lock release. State (history/pending/voice_notes) loads from `callermem` on bind/switch and persists on completion/take_pending — `switch_session` RESTORES the target session's memory.
- `cc_caller/callermem.py` — per-Claude-session caller state at `<config_dir>/sessions/<uuid>.json` (atomic 0600). Field-ownership rule: TaskManager writes history/pending; the voice-note distiller writes voice_notes; `save()` overlays only provided fields under a module lock — preserve this split, it prevents cross-thread clobbering.
- `cc_caller/claude_worker.py` — sandboxed `claude -p` subprocess layer: deterministic session UUIDs, auto-recovery on session errors, judge prompts. `WORKER_SYSTEM_PROMPT` + `--disallowedTools` stop the worker Claude from invoking cc-caller/VAPI itself (a real bug that happened).
- `cc_caller/config.py`, `push.py`, `notify.py`, `tunnel.py` — config precedence (`~/.config/cc-caller/.env` → repo-checkout `.env` → cwd `.env`), Web Push + VAPID + `subscriptions.json` (written 0600, atomic), ntfy, cloudflared/ngrok tunnels.
- `cc_caller/legacy_cli.py` + `cc_caller/vapi/` — legacy VAPI transports (SIP via Linphone, Twilio phone loop, VAPI web PWA). `webhook.py` keeps `/webhook`, `/tool-call`, and the `/pwa` page.
- `cc_caller/static/` — the PWA: `app.js` (WS bridge, captions, wake lock, push), `audio-worklet.js` (16kHz PCM capture; playback is 24kHz), `sw.js`, manifest.
- `experiments/` — archived dead ends (pre-tool-calling Gemini bridge, LiveKit). Not shipped, not wired, need extra deps.

## Constraints worth knowing

- Tests are offline by design (`tests/fake_gemini.py` is an in-process Gemini Live WS fake) — keep new tests offline-safe.
- `deliver_result` returning False means UNDELIVERED — callers MUST fall back to pending/push (contract in its docstring). Only call `take_pending()` after confirmed delivery; pending results must survive the push-fallback path because the next session's opening prompt reads them.
- The async tool-call wire format (`willContinue`/`scheduling`) is verified against the real API only in manual testing; the `clientContent` fallback absorbs differences.
- The repo-checkout `.env` contains real credentials — never run legacy VAPI flags casually; the test suite never needs it.
- Tasks and results append to `.cc-caller-log` in the cwd where cc-caller runs.
