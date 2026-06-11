import threading
from unittest.mock import patch

from cc_caller.tasks import TaskManager


def _patches():
    return (
        patch("cc_caller.tasks.clean_transcript", side_effect=lambda t: t),
        patch("cc_caller.tasks.run_claude", return_value=("full output", "sid-1")),
        patch("cc_caller.tasks.summarize_output",
              return_value={"summary": "did the thing", "detail": "full output"}),
        patch("cc_caller.tasks.log_interaction"),
    )


def test_submit_runs_task_and_reports_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    done = threading.Event()
    results = []
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="t1")
        tm.on_complete = lambda r: (results.append(r), done.set())
        assert tm.submit("fix the bug", meta={"fc_id": "f1"}) is True
        assert done.wait(timeout=5)
    assert results[0]["summary"] == "did the thing"
    assert results[0]["task"] == "fix the bug"
    assert results[0]["meta"] == {"fc_id": "f1"}
    assert tm.history[-1]["task"] == "fix the bug"
    assert tm.pending is not None
    assert tm.take_pending()["summary"] == "did the thing"
    assert tm.pending is None


def test_second_submit_while_busy_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    release = threading.Event()
    started = threading.Event()

    def slow_run(*a, **kw):
        started.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        assert tm.submit("task one") is True
        assert started.wait(timeout=5)
        assert tm.busy is True
        assert tm.submit("task two") is False
        release.set()
        assert done.wait(timeout=5)
    assert tm.busy is False


def test_completion_callback_errors_do_not_break_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        def boom(r):
            done.set()
            raise RuntimeError("listener died")
        tm.on_complete = boom
        tm.submit("task")
        assert done.wait(timeout=5)
        # lock must have been released despite the callback raising
        assert tm.submit("next task") is True


def test_take_pending_when_empty_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        assert tm.take_pending() is None
        assert tm.pending is None


def test_show_exchange_prints_task_and_result(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(show_exchange=True)
        tm.on_complete = lambda r: done.set()
        assert tm.submit("fix the bug") is True
        assert done.wait(timeout=5)
    out = capsys.readouterr().out
    assert "[task] -> fix the bug" in out
    assert "did the thing" in out


def test_no_prints_without_show_exchange(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        tm.on_complete = lambda r: done.set()
        tm.submit("quiet task")
        assert done.wait(timeout=5)
    assert "[task]" not in capsys.readouterr().out


def test_explicit_session_id_overrides_name(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="x", session_id="abc-123")
        assert tm.session_id == "abc-123"


def test_switch_session_by_id_clears_context(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="orig")
        tm.history.append({"task": "t", "summary": "s"})
        tm.pending = {"task": "t", "summary": "s", "detail": "", "meta": {}}
        assert tm.switch_session(session_id="abc-123") is True
    assert tm.session_id == "abc-123"
    assert tm.session_name is None
    assert tm.first_run is True
    assert tm.history == []
    assert tm.pending is None


def test_switch_session_same_id_is_noop_preserving_context(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="orig")
        tm.history.append({"task": "t", "summary": "s"})
        tm.pending = {"task": "t", "summary": "s", "detail": "", "meta": {}}
        same = tm.session_id
        assert tm.switch_session(session_id=same) is True
    assert tm.history != []
    assert tm.pending is not None


def test_switch_session_by_name_uses_deterministic_uuid(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    from cc_caller.claude_worker import name_to_uuid
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        assert tm.switch_session(session_name="myproj") is True
    assert tm.session_id == name_to_uuid("myproj")
    assert tm.session_name == "myproj"


def test_switch_session_refused_while_busy(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    release = threading.Event()
    started = threading.Event()

    def slow_run(*a, **kw):
        started.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        tm.submit("task")
        assert started.wait(timeout=5)
        assert tm.switch_session(session_id="other") is False
        release.set()
        assert done.wait(timeout=5)


def test_state_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    from cc_caller.claude_worker import name_to_uuid
    persist_sid = name_to_uuid("persist-test")
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    # Override run_claude to return the same session_id so session_id stays stable
    p2_stable = patch("cc_caller.tasks.run_claude",
                      return_value=("full output", persist_sid))
    with p1, p2_stable, p3, p4:
        tm1 = TaskManager(session_name="persist-test")
        tm1.on_complete = lambda r: done.set()
        tm1.submit("build the thing")
        assert done.wait(timeout=5)
        sid = tm1.session_id
    with p1, p2_stable, p3, p4:
        tm2 = TaskManager(session_name="persist-test")
    assert tm2.session_id == sid
    assert tm2.history[-1]["task"] == "build the thing"
    assert tm2.pending is not None
    assert tm2.pending["summary"] == "did the thing"


def test_take_pending_clears_persisted_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    from cc_caller.claude_worker import name_to_uuid
    persist_sid = name_to_uuid("persist-test")
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    p2_stable = patch("cc_caller.tasks.run_claude",
                      return_value=("full output", persist_sid))
    with p1, p2_stable, p3, p4:
        tm1 = TaskManager(session_name="persist-test")
        tm1.on_complete = lambda r: done.set()
        tm1.submit("task")
        assert done.wait(timeout=5)
        tm1.take_pending()
        tm2 = TaskManager(session_name="persist-test")
    assert tm2.pending is None
    assert tm2.history != []


def test_switch_session_restores_target_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    from cc_caller import callermem
    callermem.save("target-session", history=[{"task": "old work", "summary": "done"}],
                   voice_notes=["we discussed pasta"])
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="elsewhere")
        assert tm.switch_session(session_id="target-session") is True
    assert tm.history == [{"task": "old work", "summary": "done"}]
    assert tm.voice_notes == ["we discussed pasta"]


def test_activity_visible_during_task_and_cleared_after(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    release = threading.Event()
    mid_task = threading.Event()
    seen = {}

    def slow_run(instruction, session_id, session_name=None, is_first_run=False,
                 on_activity=None, cwd=None, fresh_session_id=None, cancel_event=None):
        on_activity("Edit cc_caller/server.py")
        seen["cwd"] = cwd
        mid_task.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        tm.submit("task")
        assert mid_task.wait(timeout=5)
        assert tm.current_activity == "Edit cc_caller/server.py"
        release.set()
        assert done.wait(timeout=5)
    assert tm.current_activity is None
    import os
    assert seen["cwd"] == os.getcwd()


def test_on_activity_callback_invoked_and_errors_contained(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    done = threading.Event()
    seen = []

    def spy_run(instruction, session_id, session_name=None, is_first_run=False,
                on_activity=None, cwd=None, fresh_session_id=None, cancel_event=None):
        on_activity("Edit a.py")
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=spy_run), p3, p4:
        tm = TaskManager()

        def on_activity(text):
            seen.append(text)
            raise RuntimeError("listener died")

        tm.on_activity = on_activity
        tm.on_complete = lambda r: done.set()
        assert tm.submit("task") is True
        assert done.wait(timeout=5)

    assert seen == ["Edit a.py"]
    assert "[tasks] on_activity error" in capsys.readouterr().out


def test_workdir_pinned_at_init(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        import os
        tm = TaskManager()
        assert tm.workdir == os.getcwd()


def test_name_bound_manager_passes_fresh_session_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    from cc_caller.claude_worker import name_to_uuid
    seen = {}

    def spy_run(instruction, session_id, session_name=None, is_first_run=False,
                on_activity=None, cwd=None, fresh_session_id=None, cancel_event=None):
        seen["fresh"] = fresh_session_id
        return ("out", session_id or "sid")

    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=spy_run), p3, p4:
        tm = TaskManager(session_name="myproj")
        tm.on_complete = lambda r: done.set()
        tm.submit("task")
        assert done.wait(timeout=5)
    assert seen["fresh"] == name_to_uuid("myproj")


def test_picked_session_does_not_pass_fresh_session_id(monkeypatch, tmp_path):
    # TaskManager(session_name="x", session_id="picked-raw-id"):
    # session_name is truthy but session_id != name_to_uuid("x") → fresh_id must be None
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    seen = {}

    def spy_run(instruction, session_id, session_name=None, is_first_run=False,
                on_activity=None, cwd=None, fresh_session_id=None, cancel_event=None):
        seen["fresh"] = fresh_session_id
        return ("out", session_id or "sid")

    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=spy_run), p3, p4:
        tm = TaskManager(session_name="x", session_id="picked-raw-id")
        tm.on_complete = lambda r: done.set()
        tm.submit("task")
        assert done.wait(timeout=5)
    assert seen["fresh"] is None
