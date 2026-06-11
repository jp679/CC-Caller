# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CC-Caller is a voice feedback loop for Claude Code: the user starts a task, walks away, and talks to Claude by phone/SIP/browser. Three transports share one VAPI brain:

```
Phone (Twilio)  ─┐
SIP (Linphone)  ─┤── VAPI assistant (persistent, tool-based) ── claude -p subprocess
PWA (browser)   ─┘      askCodingAgent tool → /tool-call webhook → result read mid-call
```

## Commands

```bash
python3 -m pytest tests/ -q                 # run all tests (fast, fully mocked — no network/credentials needed)
python3 -m pytest tests/test_webhook.py -q  # one file
python3 -m pytest tests/test_webhook.py::test_end_of_call_report_extracts_user_messages  # one test

./cc-caller --bridge --sip                  # run the app (best mode: persistent SIP session via Linphone)
./cc-caller --pwa                           # browser PWA mode
./cc-caller --mode always "task"            # Twilio outbound phone call mode
```

Target Python is 3.9 — no `match`, no `X | Y` type unions. Requires `claude` CLI on PATH and `cloudflared` (default tunnel) for live runs. Credentials live in `.env` (loaded from this repo's directory by `cc-caller` wrapper, so the CLI works from any project directory — Claude then works in *that* cwd).

## Architecture

**`cc_caller.py`** — orchestrator and CLI entry point. `main()` parses flags, starts the FastAPI server in a daemon thread, opens a tunnel (cloudflared/ngrok), then branches into one large per-mode block (`--pwa`, `--sip`, `--bridge`, outbound loop). Each mode block wires its own closures (`handle_tool_call`, `on_webhook_event`) into `app.state`. Also owns the Claude worker subprocess logic:
- `run_claude()` shells out to `claude -p --resume <session-uuid>`; on error it auto-recovers with a fresh session. Session IDs are deterministic UUIDs derived from a name (`name_to_uuid`), so sessions persist across restarts.
- The worker is sandboxed: `WORKER_SYSTEM_PROMPT` + `--disallowedTools` prevent the worker Claude from invoking cc-caller/VAPI itself (a real bug that happened).
- Small `claude -p` judge calls classify things: `check_needs_input()`, `is_termination()`, `clean_transcript()`.

**`webhook.py`** — `create_app(transcript_queue)` builds the FastAPI app. Two endpoints matter most:
- `POST /tool-call` — VAPI's `askCodingAgent` server tool lands here; it calls `app.state.handle_tool_call` (set by the active mode in cc_caller.py) via `run_in_executor` so webhook events aren't blocked while Claude works. VAPI's tool timeout is ~40s; long tasks return "still working" and deliver results via the hybrid callback path.
- `POST /webhook` — VAPI lifecycle events. End-of-call reports push the joined user transcript onto `transcript_queue` (consumed by the outbound call loop) and fire `app.state.on_webhook_event` so hybrid mode detects hang-ups mid-task.
- The rest of the file is mostly inline HTML pages for the browser modes (`/call`, `/call-bridge`, `/pwa`, etc.) plus PWA plumbing (`/sw.js`, `/push-subscribe`).

**`vapi_client.py`** — pure VAPI REST helpers: assistant config builders (`build_persistent_sip_config` is the tool-based persistent assistant; gpt-4o-mini relay + `askCodingAgent`/`endCall` tools, Deepgram transcriber, ElevenLabs voice), inbound number configure/clear, call creation. Stale assistants are cleared on every startup because crashes used to leave numbers pointing at dead webhooks.

**Hybrid callback flow** (the trickiest cross-file behavior): user gives a task and hangs up → `end-of-call-report` clears `call_active` while `task_in_progress` is set → when Claude finishes, the result is stored as `pending_result` and the user is notified (ntfy + SIP number reconfigured with a callback assistant for SIP; web push for PWA) → user redials and the result is read immediately. A `tool_lock` serializes tool calls — concurrent `claude --resume` calls on the same session corrupt it.

**Secondary/legacy paths**: `gemini_bridge.py` (server-side Gemini Live WebSocket bridge, `--bridge` without `--sip`), `livekit_*.py` (abandoned for SIP due to NAT issues, kept for future use), `summarizer.py` (voice-friendly summaries via `claude -p`).

## Constraints worth knowing

- VAPI cannot make outbound SIP calls — SIP callback is ntfy-notification + user redials; don't "fix" this with an outbound SIP attempt.
- Callbacks must stay on the transport the user used (no cross-transport fallback) — this was a deliberate fix.
- Worker-protected files: `DISALLOWED_FILES` in cc_caller.py lists files the worker Claude must never touch.
- Tasks and results are appended to `.cc-caller-log` in the cwd where cc-caller runs.
- Tests mock all subprocess/HTTP boundaries (`unittest.mock.patch`, FastAPI `TestClient`) — keep new tests offline-safe.
