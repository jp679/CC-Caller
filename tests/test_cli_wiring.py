from unittest.mock import MagicMock, patch

from cc_caller.cli import make_on_complete


def _state(session):
    state = MagicMock()
    state.session_holder = {"session": session}
    state.subscriptions = [{"endpoint": "e"}]
    return state


def test_live_session_gets_interrupt_delivery():
    session = MagicMock()
    session.alive = True
    session.deliver_result.return_value = True
    tm = MagicMock()
    state = _state(session)
    cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
    cb({"task": "t", "summary": "done!", "detail": "", "meta": {}})
    session.deliver_result.assert_called_once_with("done!")
    tm.take_pending.assert_called_once()


def test_no_session_falls_back_to_push_and_ntfy():
    tm = MagicMock()
    state = _state(None)
    with patch("cc_caller.cli.push") as mock_push, patch("cc_caller.cli.notify") as mock_notify:
        cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        cb({"task": "t", "summary": "done!", "detail": "", "meta": {}})
    mock_push.send_web_push.assert_called_once()
    url = mock_push.send_web_push.call_args[0][3]
    assert url.startswith("https://x/?callback=1")
    mock_notify.send_notification.assert_called_once()
    tm.take_pending.assert_not_called()


def test_failed_live_delivery_falls_back_to_push():
    session = MagicMock()
    session.alive = True
    session.deliver_result.return_value = False
    tm = MagicMock()
    state = _state(session)
    with patch("cc_caller.cli.push") as mock_push, patch("cc_caller.cli.notify"):
        cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        cb({"task": "t", "summary": "done!", "detail": "", "meta": {}})
    mock_push.send_web_push.assert_called_once()
    tm.take_pending.assert_not_called()


def test_run_gemini_pwa_exits_on_busy_port(monkeypatch, tmp_path):
    import socket
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        from cc_caller import cli
        args = cli.parse_args(["--port", str(port)])
        with patch("cc_caller.cli.shutil.which", return_value="/usr/bin/stub"):
            rc = cli.run_gemini_pwa(args)
    finally:
        s.close()
    assert rc == 1


def test_real_taskmanager_pending_survives_push_fallback(monkeypatch, tmp_path):
    import threading
    from cc_caller.tasks import TaskManager
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    done = threading.Event()
    with patch("cc_caller.tasks.clean_transcript", side_effect=lambda t: t), \
         patch("cc_caller.tasks.run_claude", return_value=("out", "sid")), \
         patch("cc_caller.tasks.summarize_output",
               return_value={"summary": "real done", "detail": "out"}), \
         patch("cc_caller.tasks.log_interaction"), \
         patch("cc_caller.cli.push") as mock_push, \
         patch("cc_caller.cli.notify"):
        tm = TaskManager(session_name="wiring-it")
        state = MagicMock()
        state.session_holder = {"session": None}
        state.subscriptions = []
        state.token = "tok"
        inner = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        tm.on_complete = lambda r: (inner(r), done.set())
        assert tm.submit("do it") is True
        assert done.wait(timeout=5)
    mock_push.send_web_push.assert_called_once()
    assert tm.pending is not None
    assert tm.pending["summary"] == "real done"
