import queue
from unittest.mock import patch, MagicMock, call
from cc_caller import run_claude, should_call, CallMode


def test_run_claude_first_iteration_no_resume():
    with patch("cc_caller.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="I refactored the module.\n",
            returncode=0,
        )
        output, session_id = run_claude("Fix the bug", session_id=None)

    assert output == "I refactored the module.\n"
    call_args = mock_run.call_args[0][0]
    assert "claude" in call_args
    assert "-p" in call_args
    assert "--resume" not in call_args


def test_run_claude_subsequent_iteration_resumes():
    with patch("cc_caller.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="Done with tests.\n",
            returncode=0,
        )
        output, session_id = run_claude("Add tests", session_id="sess-abc")

    call_args = mock_run.call_args[0][0]
    assert "--resume" in call_args
    assert "sess-abc" in call_args


def test_should_call_always_mode():
    assert should_call(CallMode.ALWAYS, "any output", last_call_time=0, interval_minutes=0) is True


def test_should_call_on_need_mode_yes():
    with patch("cc_caller.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="YES", returncode=0)
        assert should_call(CallMode.ON_NEED, "I need your input on X", last_call_time=0, interval_minutes=0) is True


def test_should_call_on_need_mode_no():
    with patch("cc_caller.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="NO", returncode=0)
        assert should_call(CallMode.ON_NEED, "All tests pass.", last_call_time=0, interval_minutes=0) is False
