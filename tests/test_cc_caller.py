import queue
from unittest.mock import patch, MagicMock, call
from cc_caller import run_claude, should_call, CallMode


def test_run_claude_first_run_tries_resume_then_new():
    with patch("cc_caller.subprocess.run") as mock_run:
        # First call (resume) fails, second call (new session) succeeds
        mock_run.side_effect = [
            MagicMock(stdout="", returncode=1, stderr="session not found"),
            MagicMock(stdout="I refactored the module.\n", returncode=0),
        ]
        output, session_id = run_claude("Fix the bug", session_id="caller", is_first_run=True)

    assert output == "I refactored the module.\n"
    assert session_id == "caller"
    # Second call should use --session-id
    call_args = mock_run.call_args_list[1][0][0]
    assert "--session-id" in call_args
    assert "caller" in call_args


def test_run_claude_subsequent_iteration_resumes():
    with patch("cc_caller.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="Done with tests.\n",
            returncode=0,
        )
        output, session_id = run_claude("Add tests", session_id="caller")

    call_args = mock_run.call_args[0][0]
    assert "--resume" in call_args
    assert "caller" in call_args


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
