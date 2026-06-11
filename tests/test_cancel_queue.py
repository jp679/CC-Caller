"""Batch B-lite: cancel + queue by voice — TDD test suite.

Three test sections:
  1. claude_worker: WorkerCancelled exception, cancel_event polling
  2. tasks: cancel mid-task, idle cancel, queue_next, cancel clears queue
  3. cli_wiring: cancelled result routing
"""
import asyncio
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

# ── 1. claude_worker ──────────────────────────────────────────────────────────

def test_worker_cancelled_exception_is_runtime_error():
    from cc_caller.claude_worker import WorkerCancelled
    exc = WorkerCancelled("cancelled by user")
    assert isinstance(exc, RuntimeError)
    assert "cancelled" in str(exc)


def test_run_claude_raises_worker_cancelled_when_event_set():
    """A slow query is cancelled by a threading.Event set from another thread;
    run_claude must raise WorkerCancelled (not return a result) within 2 s."""
    from cc_caller.claude_worker import WorkerCancelled

    cancel_event = threading.Event()

    async def slow_query(prompt, options):
        """Yields init then loops forever until cancelled."""
        from claude_agent_sdk import SystemMessage
        yield SystemMessage(subtype="init", data={"session_id": "slow-1"})
        # simulate an infinite stream from the SDK
        while True:
            await asyncio.sleep(0.05)

    # Set the cancel_event from a timer so the loop has time to start
    def _set_after():
        time.sleep(0.15)
        cancel_event.set()

    threading.Thread(target=_set_after, daemon=True).start()

    with patch("cc_caller.claude_worker.query", new=slow_query):
        with pytest.raises(WorkerCancelled):
            from cc_caller.claude_worker import run_claude
            run_claude("long task", None, cancel_event=cancel_event)


def test_run_claude_cancel_during_resume_raises_not_retries():
    """If cancel_event is set before the call, the resume attempt is cancelled;
    WorkerCancelled must propagate — NOT fall back to a fresh session.
    We verify this by tracking how many distinct resume values were used:
    a fresh-session fallback would call query with resume=None."""
    from cc_caller.claude_worker import WorkerCancelled

    cancel_event = threading.Event()
    cancel_event.set()   # pre-set: cancel immediately

    resume_args = []

    async def instant_cancel_query(prompt, options):
        resume_args.append(options.resume)
        from claude_agent_sdk import SystemMessage
        yield SystemMessage(subtype="init", data={"session_id": "r-1"})
        # Park here so cancel can be detected in the watcher loop
        await asyncio.sleep(10)

    with patch("cc_caller.claude_worker.query", new=instant_cancel_query):
        with pytest.raises(WorkerCancelled):
            from cc_caller.claude_worker import run_claude
            run_claude("task", "some-session-id", cancel_event=cancel_event)

    # If fallback happened, resume_args would contain None (fresh session).
    # Only the resume attempt should have been tried — no fresh-session call.
    assert None not in resume_args, (
        "run_claude fell back to a fresh session instead of re-raising WorkerCancelled"
    )


# ── 2. tasks ──────────────────────────────────────────────────────────────────

def _patches():
    return (
        patch("cc_caller.tasks.clean_transcript", side_effect=lambda t: t),
        patch("cc_caller.tasks.run_claude", return_value=("full output", "sid-1")),
        patch("cc_caller.tasks.summarize_output",
              return_value={"summary": "did the thing", "detail": "full output"}),
        patch("cc_caller.tasks.log_interaction"),
    )


def test_task_manager_has_cancel_event(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        assert hasattr(tm, "_cancel_event")
        import threading as _t
        assert isinstance(tm._cancel_event, _t.Event)


def test_task_manager_has_queued_slot(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        assert hasattr(tm, "_queued")
        assert tm._queued is None


def test_cancel_when_idle_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        assert tm.cancel() is False


def test_cancel_sets_event_and_returns_true(monkeypatch, tmp_path):
    """cancel() while busy sets the cancel_event and returns True."""
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    release = threading.Event()
    started = threading.Event()

    def slow_run(*a, **kw):
        started.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        assert tm.submit("long task") is True
        assert started.wait(timeout=5)
        result = tm.cancel()
        release.set()   # unblock the fake run
        done.wait(timeout=5)

    assert result is True


def test_cancel_mid_task_produces_cancelled_result(monkeypatch, tmp_path):
    """When cancel_event is set during run_claude, the task produces a result
    with cancelled=True; pending stays None; history unchanged."""
    from cc_caller.claude_worker import WorkerCancelled
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))

    started = threading.Event()
    results = []
    done = threading.Event()

    def raises_cancelled(*a, **kw):
        started.set()
        raise WorkerCancelled("cancelled by user")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=raises_cancelled), p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        tm.on_complete = lambda r: (results.append(r), done.set())
        tm.submit("task to cancel")
        assert done.wait(timeout=5)

    assert results[0]["cancelled"] is True
    assert results[0]["summary"] == "Task cancelled."
    assert tm.pending is None
    assert len(tm.history) == 0   # cancelled task not recorded


def test_cancel_clears_queue(monkeypatch, tmp_path):
    """cancel() drops both running task and queued task."""
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    release = threading.Event()
    started = threading.Event()

    def slow_run(*a, **kw):
        started.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        assert tm.submit("first") is True
        assert started.wait(timeout=5)
        # Queue a follow-up
        assert tm.queue_next("second") is True
        # Now cancel — should clear the queue
        assert tm.cancel() is True
        release.set()
        done.wait(timeout=5)

    assert tm._queued is None


def test_queue_next_when_idle_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        assert tm.queue_next("task") is False


def test_queue_next_while_busy_returns_true(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    release = threading.Event()
    started = threading.Event()

    def slow_run(*a, **kw):
        started.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        assert tm.submit("first") is True
        assert started.wait(timeout=5)
        result = tm.queue_next("second")
        release.set()
        done.wait(timeout=5)

    assert result is True


def test_queue_next_auto_submits_after_completion(monkeypatch, tmp_path):
    """After the first task finishes, the queued task is auto-submitted;
    both instructions are passed to run_claude in order."""
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))

    calls = []
    barrier = threading.Barrier(2)  # sync test thread + worker thread
    first_done = threading.Event()
    both_done = threading.Event()
    results = []

    def spy_run(instruction, session_id, *a, **kw):
        calls.append(instruction)
        if instruction == "first task":
            # signal test thread that we're in the first call so it can queue
            barrier.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=spy_run), p3, p4:
        from cc_caller.tasks import TaskManager
        tm = TaskManager()

        complete_count = [0]
        def on_complete(r):
            results.append(r)
            complete_count[0] += 1
            if complete_count[0] == 2:
                both_done.set()

        tm.on_complete = on_complete
        assert tm.submit("first task") is True
        barrier.wait(timeout=5)   # wait until run_claude("first task") is executing
        assert tm.queue_next("second task") is True
        assert both_done.wait(timeout=10)

    assert calls == ["first task", "second task"]
    # Second task result should NOT be cancelled
    assert not any(r.get("cancelled") for r in results)


# ── 3. cli wiring: cancelled result ───────────────────────────────────────────

def test_cancelled_result_delivers_live_and_returns_early():
    """on_complete with cancelled=True and a live session: deliver_result is
    called, take_pending is NOT called, push is NOT called."""
    from cc_caller.cli import make_on_complete
    from unittest.mock import MagicMock, patch

    session = MagicMock()
    session.alive = True

    state = MagicMock()
    state.session_holder = {"session": session}
    state.subscriptions = []

    tm = MagicMock()

    with patch("cc_caller.cli.push") as mock_push, \
         patch("cc_caller.cli.notify"):
        cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        cb({"task": "t", "summary": "Task cancelled.", "detail": "", "meta": {},
             "cancelled": True})

    session.deliver_result.assert_called_once_with("Task cancelled.")
    tm.take_pending.assert_not_called()
    mock_push.send_web_push.assert_not_called()


def test_cancelled_result_no_session_returns_early_no_push():
    """on_complete with cancelled=True and NO live session: neither push nor
    ntfy is called — cancelled tasks are silent when there's no live session."""
    from cc_caller.cli import make_on_complete
    from unittest.mock import MagicMock, patch

    state = MagicMock()
    state.session_holder = {"session": None}
    state.subscriptions = []

    tm = MagicMock()

    with patch("cc_caller.cli.push") as mock_push, \
         patch("cc_caller.cli.notify") as mock_notify:
        cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        cb({"task": "t", "summary": "Task cancelled.", "detail": "", "meta": {},
             "cancelled": True})

    tm.take_pending.assert_not_called()
    mock_push.send_web_push.assert_not_called()
    mock_notify.send_notification.assert_not_called()


# ── 4. gemini_live: cancelTask + queue ────────────────────────────────────────
# These are kept with the gemini_live tests logically but are here to keep
# failing-test discovery in one file until we update test_gemini_live.py.
# (We update test_gemini_live.py directly because of the handshake name assertion.)
