import json

import pytest

from cc_caller import callermem


def test_load_missing_returns_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    state = callermem.load("abc-123")
    assert state == {"history": [], "pending": None, "voice_notes": []}


def test_save_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    callermem.save("abc-123",
                   history=[{"task": "t", "summary": "s"}],
                   pending={"task": "t", "summary": "s", "detail": "", "meta": {}},
                   voice_notes=["note one"])
    state = callermem.load("abc-123")
    assert state["history"] == [{"task": "t", "summary": "s"}]
    assert state["pending"]["summary"] == "s"
    assert state["voice_notes"] == ["note one"]


def test_save_applies_caps(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    callermem.save("abc-123",
                   history=[{"task": str(i), "summary": ""} for i in range(60)],
                   voice_notes=["n{}".format(i) for i in range(15)])
    state = callermem.load("abc-123")
    assert len(state["history"]) == 50
    assert state["history"][-1]["task"] == "59"
    assert len(state["voice_notes"]) == 10
    assert state["voice_notes"][-1] == "n14"


def test_file_is_0600(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    callermem.save("abc-123", history=[])
    f = tmp_path / "sessions" / "abc-123.json"
    assert oct(f.stat().st_mode)[-3:] == "600"


def test_corrupt_file_returns_defaults(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "abc-123.json").write_text("{nope")
    state = callermem.load("abc-123")
    assert state == {"history": [], "pending": None, "voice_notes": []}
    assert "[callermem]" in capsys.readouterr().out


def test_append_voice_note_preserves_other_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    callermem.save("abc-123", history=[{"task": "t", "summary": "s"}], voice_notes=["old"])
    callermem.append_voice_note("abc-123", "new note")
    state = callermem.load("abc-123")
    assert state["voice_notes"] == ["old", "new note"]
    assert state["history"] == [{"task": "t", "summary": "s"}]


def test_rejects_path_traversal_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    for bad in ("../etc", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            callermem.load(bad)
        with pytest.raises(ValueError):
            callermem.save(bad, history=[])
