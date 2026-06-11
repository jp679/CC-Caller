"""Per-Claude-session persistent caller state: task history, pending result, voice notes."""
import json
import os
import threading

from cc_caller import config

HISTORY_CAP = 50
VOICE_NOTES_CAP = 10

_LOCK = threading.Lock()    # serializes all read-modify-write cycles
_UNSET = object()           # "field not provided": preserve the on-disk value


def _validate_session_id(session_id):
    if not session_id or not session_id.strip():
        raise ValueError("invalid session id")
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise ValueError("invalid session id")


def _state_file(session_id):
    return config.config_dir() / "sessions" / "{}.json".format(session_id)


def _read(session_id):
    """Raw disk state with defaults on missing/corrupt. No id validation."""
    defaults = {"history": [], "pending": None, "voice_notes": [], "title": None}
    f = _state_file(session_id)
    if not f.exists():
        return defaults
    try:
        data = json.loads(f.read_text())
        return {
            "history": list(data.get("history") or []),
            "pending": data.get("pending", None),
            "voice_notes": list(data.get("voice_notes") or []),
            "title": data.get("title", None),
        }
    except (ValueError, OSError) as e:
        print("[callermem] Could not read session file {}: {}".format(f, e))
        return defaults


def load(session_id):
    """State dict with keys history/pending/voice_notes. Empty defaults on missing/corrupt."""
    _validate_session_id(session_id)
    return _read(session_id)


def _write_overlay(session_id, history, pending, voice_notes, title):
    """Overlay the provided (non-_UNSET) fields onto disk state and atomically
    write (0600, tmp+os.replace). Caller must hold _LOCK. Fields left _UNSET
    keep their on-disk values -- this is what lets the task manager own
    history/pending while the distiller owns voice_notes."""
    state = _read(session_id)
    if history is not _UNSET:
        state["history"] = list(history or [])
    if pending is not _UNSET:
        state["pending"] = pending
    if voice_notes is not _UNSET:
        state["voice_notes"] = list(voice_notes or [])
    if title is not _UNSET:
        state["title"] = title
    state["history"] = state["history"][-HISTORY_CAP:]
    state["voice_notes"] = state["voice_notes"][-VOICE_NOTES_CAP:]
    f = _state_file(session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.parent / (f.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(state))
        os.replace(str(tmp), str(f))
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def save(session_id, history=_UNSET, pending=_UNSET, voice_notes=_UNSET, title=_UNSET):
    """Persist only the provided fields; omitted fields keep their disk values.
    Serialized by a module write lock so concurrent writers can't clobber
    each other's fields. Caps applied: history last HISTORY_CAP, voice_notes
    last VOICE_NOTES_CAP."""
    _validate_session_id(session_id)
    with _LOCK:
        _write_overlay(session_id, history, pending, voice_notes, title)


def append_voice_note(session_id, note):
    """Load-append-save a single note (used from the distiller thread).
    Touches ONLY voice_notes; runs entirely under the write lock."""
    _validate_session_id(session_id)
    with _LOCK:
        notes = _read(session_id)["voice_notes"] + [note]
        _write_overlay(session_id, _UNSET, _UNSET, notes, _UNSET)
