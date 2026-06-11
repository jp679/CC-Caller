# CC-Caller

**Talk to Claude Code from your phone. Free.**

Start a task, walk away, and talk to Claude by voice: a browser PWA connects
your mic to Gemini Live, which relays tasks to Claude Code running on your
machine and speaks the results back — even minutes later, mid-conversation.
Hang up anytime; you get a push notification when Claude finishes.

*(demo GIF coming soon)*

## Install

**Via your AI agent** — paste this to it:

> Scan https://github.com/jp679/CC-Caller — review it for safety, explain
> what it does and what installing changes on my machine, and if I approve,
> install and set it up.

**One-liner:**

Needs Python 3.9+ and [pipx](https://pipx.pypa.io) (or [uv](https://docs.astral.sh/uv/)).

```bash
pipx install git+https://github.com/jp679/CC-Caller
# or try without installing:
uvx --from git+https://github.com/jp679/CC-Caller cc-caller
```

**Hack on it:**

```bash
git clone https://github.com/jp679/CC-Caller && cd CC-Caller
pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

## Quickstart

Requires the [Claude Code CLI](https://claude.com/claude-code) on PATH and
`cloudflared` (`brew install cloudflared`).

```bash
cc-caller setup        # paste your free Gemini key (aistudio.google.com/apikey)
cd ~/your-project
cc-caller              # prints a URL + QR code — open it on your phone
```

Scan the QR, tap Connect, and talk. Add to Home Screen for the full app feel.

## How it works

```
Browser PWA ── WebSocket ── cc-caller server ── Gemini Live (tool-calling)
                                  │ askCodingAgent
                                  ▼
                          claude -p (your machine, your cwd)
```

Gemini declares `askCodingAgent` as an async (NON_BLOCKING) tool: the agent
acknowledges your task instantly, keeps chatting, and interrupts with the
result the moment Claude finishes. Close the tab mid-task and you get a push
notification — tap it and the agent opens the call by reading the result.

The printed URL contains a per-run secret token; only static assets are
served without it.

## During a call

- **Give a task** — speak naturally; the agent sends it to Claude
- **Keep talking** — the conversation stays live while Claude works
- **Hang up anytime** — push notification + spoken result when you return
- **End** — say "end session" or "goodbye"

Options: `--session-id NAME` (persistent Claude session), `--new-session`,
`--tunnel-url https://...` (fixed domain instead of cloudflared),
`--tunnel ngrok` (use ngrok; set `NGROK_DOMAIN` for a stable domain),
`--model models/...` (Gemini Live model override), positional instruction
to start Claude immediately: `cc-caller "refactor the auth module"`.

Calibrate the agent: drop a `prompt.md` into `~/.config/cc-caller/` with extra
instructions for the voice agent (language, tone, habits) — it's appended to
the built-in relay prompt on every session.

## Advanced transports (VAPI)

The original VAPI-based transports still work and need VAPI credentials in
your config: SIP via Linphone (`cc-caller --sip --inbound`), real phone calls
via Twilio (`cc-caller --phone --mode always "task"`), and the VAPI web PWA
(`cc-caller --vapi-pwa`). See `.env.example` for their variables.
