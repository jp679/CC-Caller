import json
import subprocess
import tempfile

SUMMARIZE_PROMPT = (
    "Summarize this coding assistant output for a phone call. "
    "Return JSON with two keys: "
    "'summary' (under 30 seconds spoken, what was done + what's needed) "
    "and 'detail' (full specifics). "
    "No markdown, plain spoken English."
)

FALLBACK_SUMMARY = "Claude finished working but I couldn't generate a summary. Call back for details."

CONVERSATION_PROMPT = (
    "Summarize this voice call between a user and their coding assistant. "
    "Reply with EXACTLY two lines:\n"
    "TITLE: a 3-6 word name for what this session is about\n"
    "SUMMARY: 1-2 sentences -- decisions made, tasks discussed, anything postponed. "
    "Plain English, no markdown."
)


def summarize_conversation(voice_log_text):
    """Compact memory of a voice call. Returns {"title","note"} or empty strings on failure."""
    result = subprocess.run(
        ["claude", "-p", CONVERSATION_PROMPT + "\n\n---\n\n" + voice_log_text],
        capture_output=True, text=True, cwd=tempfile.gettempdir(),
    )
    if result.returncode != 0:
        return {"title": "", "note": ""}
    raw = result.stdout.strip()
    if not raw:
        return {"title": "", "note": ""}
    title = ""
    note = ""
    for line in raw.splitlines():
        lower = line.lower()
        if lower.startswith("title:"):
            title = line.split(":", 1)[1].strip()
        elif lower.startswith("summary:"):
            note = line.split(":", 1)[1].strip()
    if title or note:
        return {"title": title, "note": note}
    return {"title": "", "note": raw}


def summarize_output(claude_output: str) -> dict:
    prompt = f"{SUMMARIZE_PROMPT}\n\n---\n\n{claude_output}"
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
    )

    if result.returncode != 0 or not result.stdout.strip():
        return {"summary": FALLBACK_SUMMARY, "detail": ""}

    raw = result.stdout.strip()

    try:
        parsed = json.loads(raw)
        return {
            "summary": parsed.get("summary", raw),
            "detail": parsed.get("detail", raw),
        }
    except json.JSONDecodeError:
        return {"summary": raw, "detail": raw}
