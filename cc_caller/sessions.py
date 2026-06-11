"""Discover recent Claude Code sessions for the current project directory."""
import json
import pathlib
import re
import time

_SESSION_FILE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")


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
    for mtime, f in stamped[:limit]:
        out.append({
            "session_id": f.stem,
            "label": (_first_user_text(f) or "(no user messages)")[:60],
            "age": _age(mtime),
        })
    return out
