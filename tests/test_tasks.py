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


def test_submit_runs_task_and_reports_completion():
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


def test_second_submit_while_busy_is_rejected():
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


def test_completion_callback_errors_do_not_break_manager():
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


def test_take_pending_when_empty_returns_none():
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        assert tm.take_pending() is None
        assert tm.pending is None


def test_show_exchange_prints_task_and_result(capsys):
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


def test_no_prints_without_show_exchange(capsys):
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        tm.on_complete = lambda r: done.set()
        tm.submit("quiet task")
        assert done.wait(timeout=5)
    assert "[task]" not in capsys.readouterr().out


def test_explicit_session_id_overrides_name():
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="x", session_id="abc-123")
        assert tm.session_id == "abc-123"
