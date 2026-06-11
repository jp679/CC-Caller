"""Claude Code worker subprocess layer: sandboxed runs, sessions, judge prompts."""
import pathlib
import subprocess
import uuid
from typing import Tuple


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
    )
    cleaned = result.stdout.strip()
    if not cleaned or result.returncode != 0:
        return raw_transcript
    return cleaned


def run_claude(instruction: str, session_id: str, session_name: str = "caller", is_first_run: bool = False) -> Tuple[str, str]:
    base_cmd = [
        "claude", "-p", "--output-format", "text",
        "--append-system-prompt", WORKER_SYSTEM_PROMPT,
        "--disallowedTools", "Bash(cc-caller*) Bash(python*cc_caller*) Bash(python*vapi*) Bash(curl*vapi*) Bash(curl*twilio*)",
        "--name", session_name,
    ]
    def _is_error(r):
        combined = (r.stdout + r.stderr).lower()
        return r.returncode != 0 or "api error: 400" in combined or "concurrency" in combined

    if is_first_run:
        cmd = base_cmd + ["--resume", session_id, instruction]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if _is_error(result):
            session_id = str(uuid.uuid4())
            cmd = base_cmd + ["--session-id", session_id, instruction]
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(f"New session: {session_id}")
    else:
        cmd = base_cmd + ["--resume", session_id, instruction]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if _is_error(result):
            print("Session error, starting fresh...")
            session_id = str(uuid.uuid4())
            cmd = base_cmd + ["--session-id", session_id, instruction]
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(f"New session: {session_id}")
    return result.stdout, session_id


def check_needs_input(claude_output: str) -> bool:
    result = subprocess.run(
        ["claude", "-p", NEED_INPUT_PROMPT],
        input=claude_output,
        capture_output=True,
        text=True,
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
