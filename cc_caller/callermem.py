"""Per-Claude-session persistent caller state: task history, pending result, voice notes."""
import json
import os

from cc_caller import config

HISTORY_CAP = 50
VOICE_NOTES_CAP = 10


def _validate_session_id(session_id):
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise ValueError("invalid session id")


def _state_file(session_id):
    return config.config_dir() / "sessions" / "{}.json".format(session_id)


def load(session_id):
    """State dict with keys history/pending/voice_notes. Empty defaults on missing/corrupt."""
    _validate_session_id(session_id)
    defaults = {"history": [], "pending": None, "voice_notes": []}
    f = _state_file(session_id)
    if not f.exists():
        return defaults
    try:
        data = json.loads(f.read_text())
        return {
            "history": list(data.get("history") or []),
            "pending": data.get("pending", None),
            "voice_notes": list(data.get("voice_notes") or []),
        }
    except (ValueError, OSError) as e:
        print("[callermem] Could not read session file {}: {}".format(f, e))
        return defaults


def save(session_id, history=None, pending=None, voice_notes=None):
    """Atomic 0600 write (same tmp+os.replace pattern as config.save_config_values).
    Caps applied here: history last HISTORY_CAP, voice_notes last VOICE_NOTES_CAP."""
    _validate_session_id(session_id)
    history = list(history or [])[-HISTORY_CAP:]
    voice_notes = list(voice_notes or [])[-VOICE_NOTES_CAP:]
    data = {
        "history": history,
        "pending": pending,
        "voice_notes": voice_notes,
    }
    f = _state_file(session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.parent / (f.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(data))
        os.replace(str(tmp), str(f))
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def append_voice_note(session_id, note):
    """Load-modify-save a single note (used from the distiller thread)."""
    state = load(session_id)
    state["voice_notes"].append(note)
    save(session_id,
         history=state["history"],
         pending=state["pending"],
         voice_notes=state["voice_notes"])
