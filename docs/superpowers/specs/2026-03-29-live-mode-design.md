# CC-Caller `--live` Mode: Persistent Gemini Voice Session

## Overview

A persistent voice session where the user connects once via Gemini Live and stays on the line. Claude's output is read aloud as it arrives. No hang-up/call-back loop.

## Flag

```bash
cc-caller --live --inbound
```

`--live` implies Gemini (no VAPI). Can combine with `--mode` for call decision logic, but the voice session stays open regardless.

## Flow

1. User connects (ntfy notification → tap → `/call-gemini-live` page)
2. Gemini: "Hey, what would you like me to work on?"
3. User speaks task
4. Gemini: "On it."
5. Browser sends transcript to server via `/gemini-transcript`
6. Server runs `claude -p --resume <session>` with the instruction
7. Every 15 seconds while Claude works, server pushes "Still working..." via SSE
8. When Claude finishes, server pushes the full output via SSE
9. Browser injects output as text into Gemini: "Here's what was done: {output}"
10. Gemini reads it conversationally to the user
11. User responds with next instruction or says "end session"
12. Repeat from step 5

## Architecture

```
Browser (Gemini WebSocket) ←→ Gemini Live API
    ↕ (SSE)
FastAPI Server (/live-stream, /gemini-transcript)
    ↕ (subprocess)
claude -p --resume <session>
```

### Server → Browser Communication

SSE (Server-Sent Events) on `/live-stream` endpoint. Three event types:

- `progress` — "Still working on that..." (every 15 seconds)
- `result` — Claude's full output (when done)
- `status` — "ready" (waiting for next task)

### Browser → Server Communication

POST `/gemini-transcript` — same as current Gemini mode. Sends user's transcribed words.

### Browser → Gemini Communication

Browser receives SSE events and injects them into Gemini via `realtimeInput.text`:
- Progress: sends "Say to the user: Still working on that..."
- Result: sends "Read this update to the user: {output}. Then ask what they'd like to do next."

## Page: `/call-gemini-live`

Same as current `/call-gemini` but:
- Listens to SSE stream from `/live-stream`
- Forwards progress/result messages to Gemini as text input
- Stays open after each exchange (no auto-close on call end)
- Collects transcripts continuously and POSTs them when user pauses

## Progress Pings

When `claude -p` is running (blocking subprocess), a background thread:
1. Sleeps 15 seconds
2. Pushes `progress` event via SSE
3. Repeats until Claude finishes

## Session End

- Voice: client-side phrase detection ("end session", "we're done", "stop") closes WebSocket
- Timeout: 2-hour idle auto-disconnect
- Both trigger transcript flush + SSE close

## No Summarizer

Unlike other modes, `--live` does NOT run the summarizer. Instead, the raw Claude output is sent to Gemini with the instruction "Read this update to the user." Gemini summarizes conversationally in real-time — leveraging its native ability to digest text and speak naturally.

## Files Changed

- `cc_caller.py` — add `--live` flag, SSE queue, progress thread, live loop logic
- `webhook.py` — add `/live-stream` SSE endpoint, `/call-gemini-live` page

## Environment

No new env vars needed. Uses existing `GEMINI_API_KEY`.
