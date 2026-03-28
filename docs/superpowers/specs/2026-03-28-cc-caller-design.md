# CC-Caller: Voice-Driven Claude Code Feedback Loop

## Overview

CC-Caller is a Python orchestrator that creates a voice feedback loop between Claude Code and the user via phone calls through VAPI. The user starts a task, walks away from the desk, and receives phone calls with status updates. They respond by voice, and Claude continues working.

## Architecture

```
┌─────────────┐     claude -p      ┌─────────────┐
│  cc-caller   │ ──────────────────▶ │ Claude Code  │
│  (Python)    │ ◀────────────────── │   CLI        │
│              │     stdout          └─────────────┘
│              │
│              │     claude -p       ┌─────────────┐
│  summarize   │ ──────────────────▶ │ Claude Code  │
│              │ ◀────────────────── │  (summary)   │
│              │     summary text    └─────────────┘
│              │
│              │     POST /call      ┌─────────────┐
│  call user   │ ──────────────────▶ │   VAPI API   │
│              │                     │              │──── rings your phone
│              │                     └─────────────┘
│              │
│              │     POST /webhook   ┌─────────────┐
│  receive     │ ◀────────────────── │ VAPI webhook │
│  transcript  │     end-of-call     └─────────────┘
└─────────────┘
        │
        └──── loops back to top with your transcript as next instruction
```

### Components

- **cc_caller.py** — Main orchestrator loop and CLI entry point
- **webhook.py** — FastAPI server receiving VAPI end-of-call-report (runs in a thread)
- **vapi_client.py** — VAPI API interactions (create call, build transient assistant config)
- **summarizer.py** — Generates voice-friendly summaries via `claude -p`
- **requirements.txt** — fastapi, uvicorn, requests, pyngrok
- **.env** — VAPI_API_KEY, VAPI_ACCOUNT_ID, phone numbers

## The Loop

### Startup

```bash
python cc_caller.py --mode always "Refactor the auth module and add tests"
```

The user provides an initial instruction and a call mode.

### Each Iteration

1. **Execute** — Runs `claude -p --resume <session-id> "<instruction>"` and captures the full output. First iteration starts a new session (no `--resume`).

2. **Decide whether to call** — Based on the active mode:
   - `always` — call every time
   - `on-need` — runs a quick `claude -p` classifier: "Does this output require user input? YES or NO." Only calls on YES. Otherwise sends "Continue working." and loops silently.
   - `interval` — tracks time since last call. Calls if enough time has elapsed OR if the on-need check says YES. Otherwise continues silently.

3. **Summarize** — Runs `claude -p` with the full output piped to stdin. The prompt requests JSON output with two fields:
   - `summary` — concise, under 30 seconds spoken, leads with what was done then what's needed
   - `detail` — longer version with specifics, available on demand during the call
   - Prompt: `"Summarize this coding assistant output for a phone call. Return JSON with two keys: 'summary' (under 30 seconds spoken, what was done + what's needed) and 'detail' (full specifics). No markdown, plain spoken English."`
   - Output is parsed as JSON to extract both fields

4. **Call** — POSTs to `https://api.vapi.ai/call` with a transient assistant (see Call Configuration below).

5. **Wait for transcript** — Webhook server receives the end-of-call-report, extracts user messages from `artifact.messages`, puts them in a thread-safe `queue.Queue`.

6. **Loop** — The user's transcript becomes the next instruction. Go to step 1.

### Termination

- User says "stop" or "we're done" on a call — clean exit
- Ctrl+C on the terminal — clean exit
- Claude outputs completion signal — final confirmation call, then exit

## Call Configuration

Each call uses a transient assistant so fresh context is injected every time.

```json
{
  "phoneNumberId": "054b2cd8-7a07-440f-9ba2-9ebbd7be44eb",
  "customer": {
    "number": "+16504510611"
  },
  "assistant": {
    "model": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5-20250514",
      "messages": [
        {
          "role": "system",
          "content": "You are a voice relay for a coding assistant. Your job: 1) Read the summary to the user. 2) If they ask for more detail, provide it from the DETAIL section below. 3) Collect any instructions they give. 4) When they say 'go ahead', 'that's all', or hang up, say 'On it, I'll call back when done.' and end the call. Do NOT attempt to answer coding questions yourself. Stay concise and natural.\n\nDETAIL:\n{detail_text}"
        }
      ]
    },
    "firstMessage": "{summary_text}",
    "voice": {
      "provider": "11labs",
      "voiceId": "21m00Tcm4TlvDq8ikWAM"
    },
    "endCallPhrases": ["go ahead", "that's all", "stop", "we're done"],
    "serverUrl": "{ngrok_public_url}/webhook"
  }
}
```

**Choices:**
- **Relay model:** Sonnet — cheap, only needs to follow simple relay instructions
- **Voice:** ElevenLabs Rachel (default). Configurable.
- **endCallPhrases:** VAPI auto-ends the call on these phrases, triggering the webhook immediately
- **firstMessage:** Summary is spoken immediately on pickup — no filler

The transient assistant also receives `serverUrl` pointing to the ngrok-exposed webhook.

## Webhook Server

- FastAPI app running on local port 8765
- Exposed via pyngrok (auto-starts tunnel, grabs public URL programmatically)
- Public URL injected as `serverUrl` on each transient assistant
- Receives `end-of-call-report` POST from VAPI
- Extracts user messages only: `[msg["message"] for msg in artifact["messages"] if msg["role"] == "user"]`
- Joins them into a single transcript string
- Puts transcript on a `queue.Queue` shared with the main loop
- Main loop blocks on `queue.get()` — simple, thread-safe, no polling

**Timeout:** If no call report arrives within 10 minutes (user didn't pick up), retries the call once, then pauses.

## Call Modes

| Mode | Calls when | Best for |
|------|-----------|----------|
| `always` | Every Claude response | Short tasks, close collaboration |
| `on-need` | Claude asks a question or hits a blocker | Long autonomous work sessions |
| `interval` | Every N minutes OR when Claude needs input | Monitoring long tasks with periodic check-ins |

### CLI Interface

```bash
# Always call
python cc_caller.py --mode always "Refactor the auth module"

# Only when needed
python cc_caller.py --mode on-need "Fix all failing tests in src/"

# Every 15 minutes
python cc_caller.py --mode interval --interval-minutes 15 "Build the new API endpoints"
```

### on-need Detection

```
claude -p "Read this output and answer with ONLY 'YES' or 'NO': does this require user input, a decision, or clarification to continue?" <<< "$output"
```

### interval Logic

Tracks `last_call_time`. After each Claude response:
- If `now - last_call_time >= interval` → call with status update
- If on-need check says YES → call regardless of timer
- Otherwise → send "Continue working." and loop

## Environment & Dependencies

**Python packages:** fastapi, uvicorn, requests, pyngrok, python-dotenv

**Environment variables (.env):**
- `VAPI_API_KEY` — VAPI API key
- `VAPI_ACCOUNT_ID` — VAPI account ID
- `VAPI_PHONE_NUMBER_ID` — The VAPI phone number ID for outbound calls
- `USER_PHONE_NUMBER` — The user's phone number to call

**External requirements:**
- Python 3.10+
- Claude Code CLI installed and authenticated
- ngrok account (free tier works) with auth token configured
