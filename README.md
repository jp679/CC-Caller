# CC-Caller

Voice-driven Claude Code feedback loop. Start a task, walk away, get voice updates and respond by speaking. Three transport options, one unified VAPI brain.

## Architecture

```
Phone (Twilio)  ─┐
SIP (Linphone)  ─┤── VAPI (persistent tool-based session) ── Claude Code
PWA (browser)   ─┘
```

All transports use VAPI's `askCodingAgent` server tool for mid-call Claude results. Calls are persistent — you stay on the line while Claude works. If you hang up mid-task, Claude finishes and calls you back.

## Setup

```bash
pip install -r requirements.txt
brew install cloudflared
cp .env.example .env
```

Fill in your credentials:

```
VAPI_API_KEY=your-private-key
VAPI_PUBLIC_KEY=your-public-key
VAPI_ACCOUNT_ID=your-account-id
VAPI_PHONE_NUMBER_ID=your-phone-number-id
VAPI_SIP_PHONE_NUMBER_ID=your-sip-number-id
USER_PHONE_NUMBER=+1234567890
GEMINI_API_KEY=your-google-ai-key
NTFY_TOPIC=cc-caller
```

Install [ntfy](https://ntfy.sh) on your phone and subscribe to your topic for push notifications.

## Usage

### SIP — Persistent session via Linphone (recommended)

Native phone call UI. Screen can lock. Persistent session with mid-call Claude results.

```bash
cc-caller --bridge --sip
```

Then dial `sip:cc-caller@sip.vapi.ai` from Linphone.

**Linphone setup:** Add account with username `cc-caller`, domain `sip.vapi.ai`, password = your VAPI API key.

### Browser — Persistent session via web

Same persistent session, no app install needed. Requires screen to stay on.

```bash
cc-caller --bridge
```

Open the URL from the ntfy notification.

### Phone — Twilio calls

Real phone calls. Requires a Twilio number imported into VAPI.

```bash
# Claude calls your phone
cc-caller --mode always "Refactor the auth module"

# You call the VAPI number
cc-caller --mode always --inbound
```

### Call modes

```bash
# Always — call after every Claude response
cc-caller --mode always --sip --inbound

# On-need — only call when Claude needs input
cc-caller --mode on-need --sip --inbound

# Interval — check in every N minutes
cc-caller --mode interval --interval-minutes 15 --sip --inbound
```

### Run from any project

```bash
cd ~/Dev/MyProject
cc-caller --bridge --sip
```

Claude works in your current directory.

### Background mode

```bash
nohup cc-caller --bridge --sip &
```

## During a call

- **Give a task** — speak naturally, the agent sends it to Claude
- **Wait for results** — the agent says "checking now" then reads Claude's response
- **Ask follow-ups** — stay on the line, ask more questions
- **Hang up anytime** — Claude keeps working, calls you back when done
- **End session** — say "end session" or "goodbye"

## Key files

| File | Purpose |
|------|---------|
| `cc_caller.py` | Main orchestrator, CLI, all modes |
| `webhook.py` | FastAPI server — webhooks, tool calls, web pages |
| `vapi_client.py` | VAPI API — assistant configs, call creation |
| `gemini_bridge.py` | Server-side Gemini WebSocket bridge |
| `summarizer.py` | Voice-friendly summaries via claude -p |
| `cc-caller` | Shell wrapper for global CLI access |
