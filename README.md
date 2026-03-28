# CC-Caller

Voice-driven Claude Code feedback loop. Start a task, walk away, get voice updates and respond by speaking.

## Setup

```bash
pip install -r requirements.txt
brew install cloudflared
```

Create `.env` from the template:

```bash
cp .env.example .env
```

Fill in your VAPI credentials (get from [dashboard.vapi.ai](https://dashboard.vapi.ai)):

```
VAPI_API_KEY=your-private-key
VAPI_PUBLIC_KEY=your-public-key
VAPI_ACCOUNT_ID=your-account-id
VAPI_PHONE_NUMBER_ID=your-phone-number-id
USER_PHONE_NUMBER=+1234567890
NTFY_TOPIC=cc-caller
```

For push notifications, install [ntfy](https://ntfy.sh) on your phone and subscribe to your topic.

## Usage

### Web mode (free, recommended)

No Twilio costs. Uses browser-based voice calls + push notifications.

```bash
# You give the task upfront, Claude calls you back via web
cc-caller --mode always --web "Refactor the auth module"

# You initiate — tap the notification to speak your task
cc-caller --mode always --web --inbound
```

### Phone mode (requires Twilio)

Real phone calls. Requires a Twilio number imported into VAPI.

```bash
# Claude calls your phone after each response
cc-caller --mode always "Refactor the auth module"

# You call the VAPI number to start a task
cc-caller --mode always --inbound
```

### Call modes

```bash
# Always — call after every Claude response
cc-caller --mode always --web "task"

# On-need — only call when Claude needs your input
cc-caller --mode on-need --web "task"

# Interval — call every N minutes with status updates
cc-caller --mode interval --interval-minutes 15 --web "task"
```

### Run from any project

`cc-caller` runs Claude in your current directory:

```bash
cd ~/Dev/MyProject
cc-caller --mode always --web --inbound
```

### Background mode

Keep it running after closing the terminal:

```bash
nohup cc-caller --mode always --web --inbound &
```

### Tunnel options

Cloudflare Tunnel is the default (free, no account needed):

```bash
# Cloudflare (default)
cc-caller --mode always --web "task"

# ngrok (requires account + auth token)
cc-caller --mode always --web --tunnel ngrok "task"
```

## During a call

- **Interrupt anytime** — just start talking
- **Ask for detail** — say "give me more detail"
- **Continue working** — say "go ahead"
- **End session** — say "we're done" or "stop"

## Architecture

```
cc-caller (Python) ──> claude -p ──> Claude Code CLI
       │
       ├──> summarizer (claude -p) ──> voice-friendly summary
       │
       ├──> VAPI API ──> phone call or web call
       │
       └──> webhook server <── VAPI end-of-call-report
                │
                └──> your transcript ──> loops back to Claude
```
