import json
from unittest.mock import patch, MagicMock
from summarizer import summarize_output


def test_summarize_output_returns_summary_and_detail():
    fake_json = json.dumps({
        "summary": "I fixed the login bug. All tests pass. What's next?",
        "detail": "Changed auth.py line 42 to use bcrypt. Updated 3 test files. All 15 tests green."
    })

    with patch("summarizer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout=fake_json,
            returncode=0,
        )
        result = summarize_output("Full claude output here...")

    assert result["summary"] == "I fixed the login bug. All tests pass. What's next?"
    assert "bcrypt" in result["detail"]

    # Output should be embedded in the prompt argument
    prompt_arg = mock_run.call_args[0][0][2]  # ["claude", "-p", prompt]
    assert "Full claude output here..." in prompt_arg


def test_summarize_output_handles_non_json_gracefully():
    with patch("summarizer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="This is not JSON, just plain text summary.",
            returncode=0,
        )
        result = summarize_output("Some output")

    assert result["summary"] == "This is not JSON, just plain text summary."
    assert result["detail"] == "This is not JSON, just plain text summary."


def test_summarize_output_handles_claude_failure():
    with patch("summarizer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="",
            returncode=1,
        )
        result = summarize_output("Some output")

    assert result["summary"] == "Claude finished working but I couldn't generate a summary. Call back for details."
    assert result["detail"] == ""
