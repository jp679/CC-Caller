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


def test_build_base_prompt_without_calibration(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    from cc_caller.cli import RELAY_SYSTEM_PROMPT, build_base_prompt
    assert build_base_prompt() == RELAY_SYSTEM_PROMPT


def test_build_base_prompt_appends_calibration(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "prompt.md").write_text("Address the user as JP. Be terse.")
    from cc_caller.cli import RELAY_SYSTEM_PROMPT, build_base_prompt
    prompt = build_base_prompt()
    assert prompt.startswith(RELAY_SYSTEM_PROMPT)
    assert "USER CALIBRATION" in prompt
    assert "Address the user as JP. Be terse." in prompt


def test_show_exchange_flag_parsing(monkeypatch):
    from cc_caller.cli import show_exchange_enabled
    monkeypatch.delenv("CC_SHOW_EXCHANGE", raising=False)
    assert show_exchange_enabled() is True
    for off in ("0", "false", "no", "off", "False"):
        monkeypatch.setenv("CC_SHOW_EXCHANGE", off)
        assert show_exchange_enabled() is False
    monkeypatch.setenv("CC_SHOW_EXCHANGE", "1")
    assert show_exchange_enabled() is True


def test_resolve_token_random_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CC_TOKEN", raising=False)
    monkeypatch.delenv("CC_PERSIST_TOKEN", raising=False)
    from cc_caller.cli import resolve_token
    t1, t2 = resolve_token(), resolve_token()
    assert t1 != t2
    assert len(t1) > 20
    assert not (tmp_path / ".env").exists()


def test_resolve_token_honors_explicit_cc_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CC_TOKEN", "my-fixed-token")
    from cc_caller.cli import resolve_token
    assert resolve_token() == "my-fixed-token"


def test_resolve_token_persists_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CC_TOKEN", raising=False)
    monkeypatch.setenv("CC_PERSIST_TOKEN", "1")
    from cc_caller.cli import resolve_token
    t1 = resolve_token()
    assert 'CC_TOKEN="{}"'.format(t1) in (tmp_path / ".env").read_text()
    # second call returns the same token (now in os.environ via save_config_values)
    assert resolve_token() == t1
