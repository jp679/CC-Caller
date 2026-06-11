# Claude Agent SDK Migration — Implementation Plan

> **For agentic workers:** execute task-by-task with the established review gates (implementer → spec review → quality review). Normative decisions are binding; where exact SDK bindings are marked VERIFY, check them against the installed `claude-agent-sdk` before writing code and report adaptations.

**Goal:** Replace the `claude -p` subprocess worker with the Claude Agent SDK — same behavior, same sessions, same billing (subscription) — establishing the foundation for live progress, cancel/steer, and voice-approved permissions.

**Architecture:** `cc_caller/claude_worker.py` keeps its public contract (`run_claude(...) -> (output, session_id)`) but is reimplemented on `claude_agent_sdk.query()` running inside `asyncio.run()` on the existing TaskManager worker thread. The threading model, TaskManager semantics, callermem persistence, Gemini session, server, and PWA are untouched except for a new optional activity surface. Judge calls (`clean_transcript`, `check_needs_input`, `summarize_output`, `summarize_conversation`) stay as cheap `claude -p` one-offs in /tmp — consolidating them is explicitly out of scope.

**Tech stack change:** Python floor moves from 3.9 to **3.10** (SDK requirement). New dependency `claude-agent-sdk`.

## Normative decisions

**Parity contract (the migration's definition of done):** a task spoken through the PWA behaves identically to today — same session resumed (existing UUIDs work; the SDK shares the CLI's session store), same sandbox (worker prompt + disallowed tools), same cwd semantics (worker operates in the launch directory), same `.cc-caller-log`, same summary spoken, same subscription billing (no `ANTHROPIC_API_KEY` anywhere; the SDK inherits the CLI's login).

**SDK call shape (VERIFY bindings against installed SDK and `code.claude.com/docs/en/agent-sdk/python` before coding):**
```python
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(
    system_prompt={"type": "preset", "preset": "claude_code", "append": WORKER_SYSTEM_PROMPT},
    disallowed_tools=DISALLOWED_TOOL_PATTERNS,   # list — split from today's single string
    resume=session_id_or_None,
    cwd=str(workdir),
)
```
- `system_prompt` MUST use the `claude_code` preset with `append` — the SDK's default system prompt is empty, which would silently change worker behavior vs `claude -p`.
- `setting_sources`: leave at default so `~/.claude` + project `.claude` + CLAUDE.md load like the CLI does (parity).
- `permission_mode`: leave at default to match `claude -p`. If the manual run shows a permission regression vs today (e.g. file writes denied), set the minimal mode that restores parity and document it.
- Messages consumed from the iterator: a `SystemMessage` with `subtype == "init"` carries the real `session_id` (capture it — it's the resume handle we persist); `AssistantMessage` content blocks (`TextBlock`, `ToolUseBlock`) feed activity; the final `ResultMessage` carries `result` text and error state. Exact class/field names per the SDK — VERIFY, adapt minimally, report.

**Resume/fallback semantics (preserve today's):** attempt with `resume=session_id`; if the SDK raises (process/connection error) or the result is an error consistent with "no such session", retry once without `resume` (fresh session) and adopt the new init `session_id`. Print the same "New session: <id>" style notice.

**Activity surface (the one new capability in scope):** `run_claude` gains `on_activity=None` — called with short human strings derived from tool-use blocks ("Edit cc_caller/server.py", "Bash: pytest", "Read README.md" — tool name + primary input arg, truncated ~80 chars). TaskManager stores the latest on `self.current_activity` (cleared on task end) and passes a callback; `checkStatus` in gemini_live adds `"activity"` to its response when present, so the voice agent can say *what* Claude is doing. No PWA UI changes in this plan (that's the post-migration progress feature).

**Async-in-thread rule:** `run_claude` wraps the async query in `asyncio.run()` — one event loop per task, owned by the TaskManager worker thread, fully independent of the server's loop. No shared loop, no cross-loop calls.

**Python floor:** pyproject `requires-python = ">=3.10"`; README prereq line updated; the dev wrapper `cc-caller` picks the newest available interpreter (`python3.13`→`python3.10`, falling back to `python3` with a version check and a clear error). JP's machine needs a brew Python before anything else runs.

## Tasks

1. **Toolchain + dependency floor.** Verify/install Python ≥3.10 on this machine (`brew install python@3.12` if absent; report which interpreter won). pyproject: `requires-python = ">=3.10"`, add `claude-agent-sdk` to dependencies. Update the `cc-caller` wrapper with interpreter detection + friendly version error. Install dev deps under the new interpreter and run the FULL suite with it (expect 128 passing unchanged — the code is 3.10-compatible already). Update README (Python 3.10+) and CLAUDE.md (commands note). Commit.

2. **Worker reimplementation (TDD).** Rewrite `run_claude` internals on the SDK per the call shape above, keeping the signature `(instruction, session_id, session_name=None, is_first_run=False, on_activity=None, cwd=None)` — `session_name` becomes advisory/unused by the SDK path (keep the param for compatibility; note it). `DISALLOWED_FILES`/`WORKER_SYSTEM_PROMPT` unchanged; add `DISALLOWED_TOOL_PATTERNS` list derived from the current single string. Judges untouched. Tests: replace the subprocess mocks for run_claude with a fake `query` async generator (patch `cc_caller.claude_worker.query`) yielding init→assistant(tool_use+text)→result message stubs; cover: happy path returns (result_text, captured_session_id); resume-failure fallback retries without resume; on_activity receives tool-derived strings; error result produces the legacy-style error return (define: on is_error, return the result/error text so TaskManager's summarize-and-speak flow still works). Judge tests (subprocess) stay green untouched. Commit.

3. **TaskManager activity plumbing (TDD).** `current_activity` attribute (None default, set via callback during `_run`, cleared in finally, exposed alongside `busy`/`elapsed`); pass `on_activity` and `cwd=os.getcwd()`-equivalent into run_claude (decide: capture cwd once at TaskManager init so a server-spawned thread can't drift). Tests with the fake worker assert activity visible mid-task and cleared after. Commit.

4. **checkStatus enrichment (TDD).** gemini_live `checkStatus` response gains `"activity": <string>` when `getattr(tm, "current_activity", None)` is set. Fake-gemini test asserts it; absent when None. Commit.

5. **Manual parity run + ship.** Human checklist against the running product: resume an existing session and ask "where did we leave off" (session store parity); give a file-creation task (permissions parity — if denied, apply the documented permission_mode fix); ask "how's it going" mid-task (activity in the voice answer); confirm `.cc-caller-log` entries and `[task]` console lines unchanged; confirm no ANTHROPIC_API_KEY in env and usage appears on the subscription. Fix-forward any SDK binding surprises (report exact errors). Full suite, push.

## Post-migration roadmap (the earlier proposals, annotated)

Sequenced batches; effort annotated as (before → after migration) where the SDK changes it.

**Batch A — call quality (do first):**
- **Live task progress in PWA** (Medium → **Small**): the activity stream now exists (Tasks 2–4); remaining work is plumbing activity into `exchange`/`status` WS messages + rendering in the taskbar strip.
- **Call-state indicators** (Small-medium, unchanged): listening pulse, agent-speaking cue in the PWA.
- **Mute / push-to-talk** (Small, unchanged): gate the mic worklet.

**Batch B — agent control (newly cheap):**
- **Cancel by voice** (Small → **Small, cleaner**): a `cancelTask` Gemini tool; SDK task cancellation replaces subprocess kill.
- **Queue a follow-up task** (Small, unchanged): one-slot queue in TaskManager.
- **Mid-task steering** (impossible → **Medium**): requires switching the worker from per-task `query()` to a persistent `ClaudeSDKClient` session object — do it as its own task when wanted; the Task 2 module boundary makes it a drop-in.
- **Voice-approved permissions** (Large → **Medium**): SDK `can_use_tool` callback → pending-approval state → Gemini `approveAction` tool round-trip → callback returns allow/deny. The killer feature; needs the persistent-client refactor above first.

**Batch C — memory polish (unchanged by migration):**
- **`rememberNote` voice tool** (Small): user-dictated callermem notes.
- **Stale-aware opener** (Tiny): timestamp pending; age-appropriate greeting.
- **Session titles** (Small): distiller also produces a 3-word title for picker/header.

**Batch D — PWA interaction (unchanged):**
- **Text-input fallback** (Small-medium): typed turns injected via clientContent.
- **Auto-reconnect with backoff** (Small).
- **Task cards** (Medium): collapsible per-task cards replacing raw exchange rows.
- **Voice session switching** (Small): `listSessions`/`switchSession` Gemini tools over the existing `switch_session`.

Out of scope for all of the above until explicitly picked up: judge-call consolidation onto the SDK, TypeScript SDK, Managed Agents.
