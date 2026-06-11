# Caller Memory & Reconnect Experience — Implementation Plan

> **For agentic workers:** execute task-by-task with review gates. Compact plan: schemas and seams are normative; implementers write the code TDD-style.

**Goal:** (0) stop utility `claude -p` calls polluting project transcripts; (1) caller speaks first on reconnect when a result is pending; (2) per-Claude-session persistent caller state (1:1 shared memory across restarts/switches); (3) voice-conversation memory distilled at hang-up and injected on reconnect.

**Architecture:** new `cc_caller/callermem.py` owns a per-session state file; TaskManager loads/saves through it; the server builds an opener + injects memory blocks; GeminiLiveSession accumulates voice transcriptions and reports them at session end for distillation.

## Normative decisions

**State file:** `~/.config/cc-caller/sessions/<session-uuid>.json`, atomic write at 0600 (same tmp+`os.replace` pattern as config.py). Schema:
```json
{
  "history": [{"task": "...", "summary": "..."}],
  "pending": {"task": "...", "summary": "...", "detail": "...", "meta": {}},
  "voice_notes": ["2026-06-12 01:30 — discussed pasta recipe pot size; user postponed README work"]
}
```
`history` capped at 50, `voice_notes` capped at 10 (oldest dropped). `pending` may be null/absent. Corrupt/missing file → empty defaults (logged, like push.load_subscriptions).

**Write ownership:** ALL file writes go through `callermem.save(session_id, data)`; TaskManager serializes its own writes under `_state_lock` (data assembled inside the lock, write outside is fine since save is atomic). The voice-note append happens on a background thread at session end — it must re-load, append, save (read-modify-write race with a concurrent task completion is acceptable: worst case one voice note or one history entry written last wins; both writers re-load first to minimize loss).

**Opener rule:** built by the server ONLY when `tm.pending` exists at WS connect: text = `"[SYSTEM] The user just reconnected after stepping away. Greet them briefly, then tell them this finished result right away: <summary>"`. Passed to `GeminiLiveSession(opening=...)`; injected as a `clientContent` user turn (turnComplete true) immediately after the ready frame + `on_ready()` (so take_pending has already consumed/persisted). No opener otherwise — no chatty greetings.

**Voice memory:** GeminiLiveSession accumulates coalesced transcription lines (`user:`/`agent:`, consecutive same-role fragments joined) into `self.voice_log`. In `run()`'s finally, if ≥2 lines, call `self.on_session_end(self.voice_log)` (callback, set by server) — non-blocking: server wiring runs distillation on a daemon thread: `summarizer.summarize_conversation(text)` (new fn: `claude -p` one-liner summary prompt, returns "" on failure) → timestamped note → callermem append (cap 10). Injection: `build_system_prompt` adds a `PREVIOUS CALLS (your own memory of earlier conversations on this session)` block from voice_notes (after history, before pending).

**Pollution fix (0):** `clean_transcript`, `check_needs_input` (claude_worker.py), `summarize_output` and new `summarize_conversation` (summarizer.py) run their subprocess with `cwd=tempfile.gettempdir()` — junk sessions land in /tmp's transcript dir, not the project's. Belt-and-braces: `sessions.recent_sessions` and `recent_messages` skip entries whose text starts with any known utility-prompt prefix (module constant `UTILITY_PREFIXES`: "You are a transcript cleaner", "Summarize this coding assistant output", "Read this output and answer", "Read this transcript from a phone call"). `run_claude` (the real worker) keeps the caller's cwd.

**TaskManager integration:** on `__init__` and on a real `switch_session`, load state via callermem (history/pending replace in-memory); `_run` completion and `take_pending` persist. `new_session` starts empty (file created on first save). The PROCESS-LOCAL semantics stay identical when the file is empty.

## Tasks (each: TDD, suite green, commit; reviews per established pattern)

1. **Pollution fix** — cwd=tempdir for the four utility calls; UTILITY_PREFIXES filter in sessions.py; tests: subprocess cwd asserted via mock call_args, filter test with a cleaner-prompt session present. Commit `fix: utility claude calls run outside the project; filter their sessions from pickers`.
2. **callermem.py** — `load(session_id) -> dict` (defaults, corrupt-safe), `save(session_id, data)` (atomic 0600, caps applied), `append_voice_note(session_id, note)` (load-modify-save). Tests incl. cap enforcement + 0600 + corrupt file. Commit `feat: callermem — per-session persistent caller state`.
3. **TaskManager persistence** — load on bind/switch; persist on completion/take_pending; tests: state survives a simulated restart (new TaskManager same session_id sees history+pending); switch to other session loads ITS file; same-id no-op doesn't reload. Commit `feat: TaskManager state persists per Claude session`.
4. **Proactive opener** — `GeminiLiveSession(opening=None)` injects clientContent after ready/on_ready; server builds opener from pending before construction; fake-gemini test asserts the clientContent turn arrives after setup and contains the summary; no-pending test asserts no injection. Commit `feat: caller speaks first on reconnect with a pending result`.
5. **Voice memory** — voice_log accumulation (coalescing) in _pump_gemini; on_session_end callback in run() finally; server wiring thread → `summarize_conversation` (cwd=tempdir) → append_voice_note; `build_system_prompt` PREVIOUS CALLS block (cap ~1500 chars). Tests: coalescing, callback firing with log, prompt block, summarize_conversation mocked-subprocess. Commit `feat: voice-conversation memory distilled at hang-up, injected on reconnect`.
6. **Docs + ship** — README (reconnect behavior + memory note + where state lives), CLAUDE.md bullets, suite, push.

Out of scope: PWA UI changes (none needed), Gemini session resumption API, multi-user.
