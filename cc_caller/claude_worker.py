"""Claude Code worker layer: sandboxed Agent SDK runs, sessions, judge prompts."""
import pathlib
import subprocess
import tempfile
import uuid

from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, SystemMessage, ResultMessage,
    TextBlock, ToolUseBlock,
)


NEED_INPUT_PROMPT = (
    "Read this output and answer with ONLY 'YES' or 'NO': "
    "does this require user input, a decision, or clarification to continue?"
)

TERMINATION_CHECK_PROMPT = (
    "Read this transcript from a phone call and answer with ONLY 'YES' or 'NO': "
    "is the user signaling they want to END the session and stop receiving calls? "
    "Examples of YES: 'stop', 'we're done', 'that's it for today', 'I'm finished', "
    "'stop calling', 'end session', 'the task is finished'. "
    "Examples of NO: 'go ahead', 'continue', 'work on X next', 'sounds good'."
)


def name_to_uuid(name: str) -> str:
    """Convert a human-friendly session name to a deterministic UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"cc-caller.{name}"))


WORKER_SYSTEM_PROMPT = (
    "You are a coding assistant being orchestrated by cc-caller. "
    "Do your task and report what you did. "
    "NEVER run cc-caller, cc_caller.py, or any voice/phone/VAPI related commands. "
    "NEVER read or use .env files for making calls. "
    "NEVER attempt to call, phone, or contact the user — the orchestrator handles that. "
    "Just do the coding work and output your results."
)

# Files the worker should never touch
DISALLOWED_FILES = [
    "cc_caller.py", "vapi_client.py", "webhook.py", "summarizer.py",
    ".env", ".env.example", "cc-caller",
]


def log_interaction(task: str, result: str) -> None:
    """Append a task + result entry to .cc-caller-log in the current directory."""
    from datetime import datetime
    log_path = pathlib.Path.cwd() / ".cc-caller-log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n--- {timestamp} ---\nTask: {task}\nResult: {result}\n"
    try:
        with open(log_path, "a") as f:
            f.write(entry)
    except Exception as e:
        print(f"[log] Failed to write: {e}")


CLEAN_TRANSCRIPT_PROMPT = (
    "You are a transcript cleaner. Clean up the raw voice transcript below. "
    "Remove filler words, false starts, and repetitions. "
    "Preserve the user's EXACT intent — don't add, remove, or judge anything. "
    "If the user asked a question, keep it as a question. "
    "If the user said something short like 'did you get it?' or 'yes', keep it as-is. "
    "NEVER add commentary like 'no actionable instruction'. NEVER filter out messages. "
    "Output ONLY the cleaned text, nothing else."
)


def clean_transcript(raw_transcript: str) -> str:
    prompt = f"{CLEAN_TRANSCRIPT_PROMPT}\n\n---\n\n{raw_transcript}"
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
    )
    cleaned = result.stdout.strip()
    if not cleaned or result.returncode != 0:
        return raw_transcript
    return cleaned


# Tool-use patterns the worker is never allowed to run (split from the old
# single-string --disallowedTools value; same patterns, SDK list form).
DISALLOWED_TOOL_PATTERNS = [
    "Bash(cc-caller*)", "Bash(python*cc_caller*)", "Bash(python*vapi*)",
    "Bash(curl*vapi*)", "Bash(curl*twilio*)",
]


class WorkerTaskError(RuntimeError):
    """A task run that completed with an error result."""


def _describe_tool_use(block):
    """Short human string for an activity display: 'Edit cc_caller/server.py'."""
    target = ""
    if isinstance(block.input, dict):
        for key in ("file_path", "path", "pattern", "command", "url", "query"):
            if block.input.get(key):
                target = str(block.input[key])
                break
    text = "{} {}".format(block.name, target).strip()
    return text[:80]


async def _run_task(instruction, resume_id, on_activity, cwd):
    options = ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": WORKER_SYSTEM_PROMPT},
        disallowed_tools=DISALLOWED_TOOL_PATTERNS,
        resume=resume_id,
        cwd=str(cwd) if cwd else None,
    )
    session_id = resume_id
    texts = []
    result_text = None
    async for message in query(prompt=instruction, options=options):
        if isinstance(message, SystemMessage):
            if message.subtype == "init" and message.data.get("session_id"):
                session_id = message.data["session_id"]
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text:
                    texts.append(block.text)
                elif isinstance(block, ToolUseBlock) and on_activity:
                    on_activity(_describe_tool_use(block))
        elif isinstance(message, ResultMessage):
            session_id = message.session_id or session_id
            if message.is_error:
                raise WorkerTaskError(
                    message.result or "task failed ({})".format(message.subtype))
            result_text = message.result
    return (result_text or "\n".join(texts) or ""), session_id


def run_claude(instruction, session_id, session_name=None, is_first_run=False,
               on_activity=None, cwd=None):
    """Run one task against a (resumable) Claude session via the Agent SDK.

    Returns (output_text, session_id). session_name is retained for
    compatibility; the SDK has no session-naming concept.

    Resume semantics: when session_id is given we attempt a resume; if the SDK
    raises for any reason (process/connection error, or a WorkerTaskError from
    an error result such as "no conversation found") we fall back once to a
    fresh session and adopt its new id. A fresh-session error is NOT retried --
    the WorkerTaskError propagates so TaskManager turns it into a spoken
    failure.
    """
    import asyncio
    if session_id:
        try:
            return asyncio.run(_run_task(instruction, session_id, on_activity, cwd))
        except Exception as e:
            print("[worker] resume of {} failed ({}); starting a fresh session".format(
                session_id[:8], type(e).__name__))
    output, new_id = asyncio.run(_run_task(instruction, None, on_activity, cwd))
    if session_id and new_id != session_id:
        print("New session: {}".format(new_id))
    return output, new_id


def check_needs_input(claude_output: str) -> bool:
    result = subprocess.run(
        ["claude", "-p", NEED_INPUT_PROMPT],
        input=claude_output,
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
    )
    return result.stdout.strip().upper().startswith("YES")


TERMINATION_PHRASES = [
    "end session", "we're done", "done for now", "stop session",
    "that's all", "goodbye", "finish session", "close session",
    "terminar", "terminemos",
]


def is_termination(transcript: str) -> bool:
    lower = transcript.strip().lower()
    # Only match if the transcript is short (likely a command, not mid-sentence)
    if len(lower) > 50:
        return False
    return any(phrase in lower for phrase in TERMINATION_PHRASES)
