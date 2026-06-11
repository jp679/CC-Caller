import queue
from unittest.mock import patch, MagicMock, call
from cc_caller.claude_worker import run_claude, name_to_uuid
from cc_caller.legacy_cli import should_call, CallMode


def test_name_to_uuid_is_deterministic():
    a = name_to_uuid("caller")
    b = name_to_uuid("caller")
    assert a == b
    # Must be a valid UUID format
    assert len(a) == 36 and a.count("-") == 4


def test_name_to_uuid_different_names():
    assert name_to_uuid("caller") != name_to_uuid("myapp")


def test_run_claude_first_run_tries_resume_then_new():
    sid = name_to_uuid("caller")
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=1),
            MagicMock(stdout="I refactored the module.\n", returncode=0),
        ]
        output, session_id = run_claude("Fix the bug", session_id=sid, is_first_run=True)

    assert output == "I refactored the module.\n"
    # Session ID changes when resume fails (new UUID generated)
    assert session_id != sid
    call_args = mock_run.call_args_list[1][0][0]
    assert "--session-id" in call_args


def test_run_claude_subsequent_iteration_resumes():
    sid = name_to_uuid("caller")
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="Done with tests.\n",
            returncode=0,
        )
        output, session_id = run_claude("Add tests", session_id=sid)

    call_args = mock_run.call_args[0][0]
    assert "--resume" in call_args
    assert sid in call_args


def test_should_call_always_mode():
    assert should_call(CallMode.ALWAYS, "any output", last_call_time=0, interval_minutes=0) is True


def test_should_call_on_need_mode_yes():
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="YES", returncode=0)
        assert should_call(CallMode.ON_NEED, "I need your input on X", last_call_time=0, interval_minutes=0) is True


def test_should_call_on_need_mode_no():
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="NO", returncode=0)
        assert should_call(CallMode.ON_NEED, "All tests pass.", last_call_time=0, interval_minutes=0) is False


def test_run_claude_omits_name_when_none():
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_claude("do it", "sid-1", session_name=None, is_first_run=False)
    cmd = mock_run.call_args[0][0]
    assert "--name" not in cmd
