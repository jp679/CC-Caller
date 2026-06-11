import json
import os
import time

from cc_caller import sessions


def _write_session(d, uuid, text, mtime_offset, content_as_list=False):
    content = [{"type": "text", "text": text}] if content_as_list else text
    line = json.dumps({"type": "user", "message": {"role": "user", "content": content}})
    f = d / "{}.jsonl".format(uuid)
    f.write_text(json.dumps({"type": "summary", "summary": "x"}) + "\n" + line + "\n")
    t = time.time() - mtime_offset
    os.utime(f, (t, t))
    return f


def test_recent_sessions_orders_and_labels(tmp_path, monkeypatch):
    proj_dir = tmp_path / "claude-projects" / "-Users-x-proj"
    proj_dir.mkdir(parents=True)
    monkeypatch.setattr(sessions, "project_transcript_dir", lambda cwd=None: proj_dir)
    _write_session(proj_dir, "11111111-1111-1111-1111-111111111111", "fix the auth bug", 3600)
    _write_session(proj_dir, "22222222-2222-2222-2222-222222222222", "add dark mode", 60,
                   content_as_list=True)
    (proj_dir / "not-a-session.txt").write_text("ignore me")
    result = sessions.recent_sessions(limit=5)
    assert len(result) == 2
    assert result[0]["session_id"] == "22222222-2222-2222-2222-222222222222"
    assert result[0]["label"] == "add dark mode"
    assert "m ago" in result[0]["age"] or "h ago" in result[0]["age"]
    assert result[1]["label"] == "fix the auth bug"


def test_recent_sessions_limit(tmp_path, monkeypatch):
    proj_dir = tmp_path / "p"
    proj_dir.mkdir()
    monkeypatch.setattr(sessions, "project_transcript_dir", lambda cwd=None: proj_dir)
    for i in range(7):
        _write_session(proj_dir, "{:08d}-0000-0000-0000-000000000000".format(i), "task {}".format(i), i * 10)
    assert len(sessions.recent_sessions(limit=5)) == 5


def test_recent_sessions_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "project_transcript_dir", lambda cwd=None: tmp_path / "nope")
    assert sessions.recent_sessions() == []


def test_project_transcript_dir_munges_path():
    d = sessions.project_transcript_dir("/Users/JP_1/Dev/CC-Caller")
    assert d.name == "-Users-JP-1-Dev-CC-Caller"


def test_recent_sessions_survives_vanishing_file(tmp_path, monkeypatch):
    proj_dir = tmp_path / "p"
    proj_dir.mkdir()
    monkeypatch.setattr(sessions, "project_transcript_dir", lambda cwd=None: proj_dir)
    f = _write_session(proj_dir, "33333333-3333-3333-3333-333333333333", "still here", 60)
    ghost = proj_dir / "44444444-4444-4444-4444-444444444444.jsonl"
    ghost.write_text("{}")
    real_iterdir = type(proj_dir).iterdir
    def racy_iterdir(self):
        files = list(real_iterdir(self))
        ghost.unlink()  # vanishes after listing, before stat
        return iter(files)
    monkeypatch.setattr(type(proj_dir), "iterdir", racy_iterdir)
    result = sessions.recent_sessions(limit=5)
    assert [s["session_id"] for s in result] == ["33333333-3333-3333-3333-333333333333"]
