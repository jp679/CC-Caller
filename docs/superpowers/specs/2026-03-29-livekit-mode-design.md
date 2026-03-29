# CC-Caller `--livekit` Mode: LiveKit + Gemini Persistent Voice Session

## Overview

Persistent voice session using LiveKit as the real-time communication layer and Gemini Live as the voice engine. Server-side text injection — no browser middleman for delivering Claude's results.

## Flag

```bash
cc-caller --livekit
```

## Architecture

```
Linphone (SIP) ──▶ LiveKit SIP Trunk ──▶ LiveKit Room ◀── Gemini Agent (Python)
Browser ──────────▶ LiveKit Web SDK ──▶ LiveKit Room         │
                                                              │ injects results
                                                              │
                                                        cc_caller.py
                                                              │
                                                        claude -p
```

## Flow

1. cc-caller creates a LiveKit room named "cc-caller"
2. Gemini agent (Python process) joins the room
3. ntfy notification sent with browser join URL
4. User joins via browser (LiveKit Web SDK) or SIP (Linphone)
5. Gemini: "What would you like me to work on?"
6. User speaks → Gemini processes → agent captures final transcript
7. Agent puts transcript on a shared queue
8. cc-caller consumes from queue → cleans transcript → runs `claude -p`
9. While Claude works, cc-caller tells agent to say "Still working..."
10. Claude finishes → cc-caller passes output to agent
11. Agent injects output as text into Gemini conversation
12. Gemini reads it aloud to the user
13. User responds → repeat from 6
14. "End session" → room closes

## Components

### `livekit_agent.py`

The Gemini agent that joins the LiveKit room. Uses `livekit-agents[google]` plugin.

Responsibilities:
- Join room as a participant with Gemini Live as the LLM
- Capture user transcripts and put them on a queue
- Accept text injection from cc-caller (Claude's output)
- Handle progress pings ("Still working...")
- System prompt: relay role (collect tasks, read results, don't answer coding questions)

### `cc_caller.py` changes

New `--livekit` flag. The loop:
1. Start LiveKit agent in a subprocess/thread
2. Wait for transcript on queue
3. Clean transcript
4. Run `claude -p`
5. Push progress pings to agent while Claude works
6. Push result to agent when done
7. Repeat

### `webhook.py` changes

New `/call-livekit` page:
- Loads LiveKit Web SDK from CDN
- Connects to the room using a participant token
- Handles mic/speaker — LiveKit manages all audio transport
- Mic mute toggle button

### Communication

Shared `queue.Queue` between agent and cc-caller for transcripts.
Agent exposes methods: `inject_text(text)` and `say_progress()` called from cc-caller.

## Dependencies

```
livekit-agents[google]~=1.4
livekit-api~=0.8
```

## Environment Variables

```
LIVEKIT_URL=wss://cc-caller-sgv6gie5.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
GEMINI_API_KEY=...
```

## SIP Setup (one-time in LiveKit dashboard)

1. Create SIP Trunk (inbound) in LiveKit Cloud dashboard
2. Get the SIP URI assigned by LiveKit
3. Configure Linphone to dial that URI
4. LiveKit routes the call into the "cc-caller" room

## Session End

- Voice: user says "end session" → agent closes
- Timeout: 2 hours idle
- Ctrl+C: graceful shutdown
