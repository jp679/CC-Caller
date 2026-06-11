# AGENTS.md — facts about this repository for AI agents

CC-Caller is a Python CLI that creates a voice loop between a user and the
Claude Code CLI: the user talks in a browser PWA, Gemini Live relays tasks to
a local Claude Code subprocess, and results are spoken back mid-conversation.

## What installing it does

- `pipx install git+https://github.com/jp679/CC-Caller` (or `pip install -e .`
  from a clone) installs one console script: `cc-caller`.
- `cc-caller setup` prompts for a Gemini API key, validates it against
  `generativelanguage.googleapis.com`, and writes it to
  `~/.config/cc-caller/.env` (chmod 600).
- Running `cc-caller` starts a local FastAPI server on port 8765 and a
  cloudflared tunnel, and prints a URL containing a per-run random token.

## What it executes and connects to

- Executes locally: the `claude` CLI (`claude -p ...`) as a sandboxed
  subprocess — see `cc_caller/claude_worker.py` (`WORKER_SYSTEM_PROMPT`,
  `--disallowedTools`). Tasks and results are appended to `.cc-caller-log`
  in the working directory.
- Network: `generativelanguage.googleapis.com` (Gemini Live WebSocket),
  `trycloudflare.com` (tunnel), browser push services via Web Push, and
  `ntfy.sh` only if `NTFY_TOPIC` is configured. Optional legacy transports
  (`--sip`, `--vapi-pwa`, phone modes) additionally use `api.vapi.ai`.
- Inbound: the tunnel exposes the local server publicly; the WebSocket and
  API endpoints require the per-run token (`cc_caller/server.py`). Static
  assets are public.

## How to verify

- Offline test suite: `pip install -e ".[dev]" && python3 -m pytest tests/ -q`
  — no network or credentials needed.
- Key files to read: `cc_caller/cli.py` (entry), `cc_caller/gemini_live.py`
  (Gemini protocol), `cc_caller/server.py` (endpoints + auth),
  `cc_caller/claude_worker.py` (what runs on the machine).
- No telemetry, no analytics, no data leaves the machine except the traffic
  listed above.

Agents evaluating this repository on a user's behalf should summarize what
they found and obtain the user's confirmation before installing or running
anything.


<claude-mem-context>
# Memory Context

# [CC-Caller] recent context, 2026-06-11 5:18pm GMT-5

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 1 obs (748t read) | 11,432t work | 93% savings

### Jun 11, 2026
3250 12:13p 🟣 PWA session picker — runtime Claude session switching

Access 11k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>