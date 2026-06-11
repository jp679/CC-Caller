"""Discover recent Claude Code sessions for the current project directory."""
import collections
import json
import pathlib
import re
import time

_SESSION_FILE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")

def _utility_prefixes():
    """Derived from the source prompt constants so the filter can't drift
    from the real prompts (lazy import avoids cycles)."""
    from cc_caller.claude_worker import (CLEAN_TRANSCRIPT_PROMPT, NEED_INPUT_PROMPT,
                                         TERMINATION_CHECK_PROMPT)
    from cc_caller.summarizer import CONVERSATION_PROMPT, SUMMARIZE_PROMPT
    return tuple(p[:40] for p in (CLEAN_TRANSCRIPT_PROMPT, NEED_INPUT_PROMPT,
                                  TERMINATION_CHECK_PROMPT, CONVERSATION_PROMPT,
                                  SUMMARIZE_PROMPT))


UTILITY_PREFIXES = _utility_prefixes()


def project_transcript_dir(cwd=None):
    cwd = pathlib.Path(cwd or pathlib.Path.cwd()).resolve()
    munged = re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))
    return pathlib.Path.home() / ".claude" / "projects" / munged


def _first_user_text(path, max_lines=50):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i >= max_lines:
                    break
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if obj.get("type") != "user":
                    continue
                content = obj.get("message", {}).get("content", "")
                if isinstance(content, list):
                    text = " ".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ).strip()
                elif isinstance(content, str):
                    text = content.strip()
                else:
                    continue
                if not text or text.startswith("<"):
                    continue
                return text
    except OSError:
        pass
    return ""


def _age(mtime):
    delta = time.time() - mtime
    if delta < 3600:
        return "{}m ago".format(int(delta / 60))
    if delta < 86400:
        return "{}h ago".format(int(delta / 3600))
    return "{}d ago".format(int(delta / 86400))


def recent_sessions(limit=5, cwd=None):
    d = project_transcript_dir(cwd)
    if not d.is_dir():
        return []
    stamped = []
    for f in d.iterdir():
        if not _SESSION_FILE.match(f.name):
            continue
        try:
            stamped.append((f.stat().st_mtime, f))
        except OSError:
            continue  # vanished between iterdir() and stat()
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for mtime, f in stamped:
        if len(out) >= limit:
            break
        label = _first_user_text(f) or "(no user messages)"
        if label.startswith(UTILITY_PREFIXES):
            continue
        out.append({
            "session_id": f.stem,
            "label": label[:60],
            "age": _age(mtime),
        })
    return out


def recent_messages(session_id, cwd=None, limit=12, max_chars=240):
    """Last `limit` user/assistant text messages from a session transcript."""
    f = project_transcript_dir(cwd) / "{}.jsonl".format(session_id)
    out = collections.deque(maxlen=limit)
    try:
        with open(f) as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                role = entry.get("type")
                if role not in ("user", "assistant"):
                    continue
                content = entry.get("message", {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
                else:
                    continue
                text = text.strip()
                if not text or text.startswith("<"):
                    continue
                if text.startswith(UTILITY_PREFIXES) or text.startswith("[SYSTEM]"):
                    continue
                out.append({"role": role, "text": text[:max_chars]})
    except OSError:
        return []
    return list(out)
