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
