from unittest.mock import patch
from cc_caller.claude_worker import (
    run_claude, name_to_uuid, WORKER_SYSTEM_PROMPT, DISALLOWED_TOOL_PATTERNS,
)
from cc_caller.legacy_cli import should_call, CallMode


def test_name_to_uuid_is_deterministic():
    a = name_to_uuid("caller")
    b = name_to_uuid("caller")
    assert a == b
    # Must be a valid UUID format
    assert len(a) == 36 and a.count("-") == 4


def test_name_to_uuid_different_names():
    assert name_to_uuid("caller") != name_to_uuid("myapp")


def _fake_messages(session_id="new-sid", result="all done", is_error=False,
                   tool_uses=(), error_on_resume=False):
    from claude_agent_sdk import (AssistantMessage, ResultMessage, SystemMessage,
                                  TextBlock, ToolUseBlock)

    def make_query(prompt, options):
        async def gen():
            if error_on_resume and options.resume:
                raise RuntimeError("No conversation found with session ID")
            yield SystemMessage(subtype="init", data={"session_id": session_id})
            blocks = [TextBlock(text="working on it")]
            for name, tool_input in tool_uses:
                blocks.append(ToolUseBlock(id="t1", name=name, input=tool_input))
            yield AssistantMessage(content=blocks, model="m")
            yield ResultMessage(subtype="success" if not is_error else "error_during_execution",
                                duration_ms=1, duration_api_ms=1, is_error=is_error,
                                num_turns=1, session_id=session_id,
                                result=None if is_error else result)
        return gen()

    return make_query


def test_run_claude_returns_result_and_session():
    with patch("cc_caller.claude_worker.query", new=_fake_messages(session_id="abc-123")):
        out, sid = run_claude("do it", "abc-123", session_name=None)
    assert out == "all done"
    assert sid == "abc-123"


def test_run_claude_resume_failure_falls_back_to_fresh():
    with patch("cc_caller.claude_worker.query",
               new=_fake_messages(session_id="fresh-456", error_on_resume=True)):
        out, sid = run_claude("do it", "dead-session", session_name=None)
    assert out == "all done"
    assert sid == "fresh-456"


def test_run_claude_activity_callback():
    seen = []
    fake = _fake_messages(tool_uses=[("Edit", {"file_path": "cc_caller/server.py"}),
                                     ("Bash", {"command": "pytest -q"})])
    with patch("cc_caller.claude_worker.query", new=fake):
        run_claude("do it", None, on_activity=seen.append)
    assert "Edit cc_caller/server.py" in seen
    assert any(s.startswith("Bash pytest") for s in seen)


def test_run_claude_fresh_error_raises():
    import pytest as _pytest
    from cc_caller.claude_worker import WorkerTaskError
    with patch("cc_caller.claude_worker.query", new=_fake_messages(is_error=True)):
        with _pytest.raises(WorkerTaskError):
            run_claude("do it", None)


def test_run_claude_passes_sandbox_options():
    captured = {}

    def spy_query(prompt, options):
        captured["options"] = options

        async def gen():
            from claude_agent_sdk import ResultMessage, SystemMessage
            yield SystemMessage(subtype="init", data={"session_id": "s"})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="s", result="ok")
        return gen()

    with patch("cc_caller.claude_worker.query", new=spy_query):
        run_claude("do it", None, cwd="/tmp/somewhere")
    opts = captured["options"]
    assert opts.system_prompt == {"type": "preset", "preset": "claude_code",
                                  "append": WORKER_SYSTEM_PROMPT}
    assert opts.disallowed_tools == DISALLOWED_TOOL_PATTERNS
    assert opts.cwd == "/tmp/somewhere"


def test_should_call_always_mode():
    assert should_call(CallMode.ALWAYS, "any output", last_call_time=0, interval_minutes=0) is True


def test_should_call_on_need_mode_yes():
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        from unittest.mock import MagicMock
        mock_run.return_value = MagicMock(stdout="YES", returncode=0)
        assert should_call(CallMode.ON_NEED, "I need your input on X", last_call_time=0, interval_minutes=0) is True


def test_should_call_on_need_mode_no():
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        from unittest.mock import MagicMock
        mock_run.return_value = MagicMock(stdout="NO", returncode=0)
        assert should_call(CallMode.ON_NEED, "All tests pass.", last_call_time=0, interval_minutes=0) is False


def test_clean_transcript_runs_outside_project(tmp_path):
    import tempfile
    from unittest.mock import MagicMock
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="cleaned", stderr="")
        from cc_caller.claude_worker import clean_transcript
        clean_transcript("raw text")
    assert mock_run.call_args[1].get("cwd") == tempfile.gettempdir()


def test_check_needs_input_runs_outside_project(tmp_path):
    import tempfile
    from unittest.mock import MagicMock
    with patch("cc_caller.claude_worker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="NO", stderr="")
        from cc_caller.claude_worker import check_needs_input
        check_needs_input("some output")
    assert mock_run.call_args[1].get("cwd") == tempfile.gettempdir()


def test_run_claude_fresh_create_uses_provided_id():
    captured = {}

    def spy_query(prompt, options):
        captured.setdefault("calls", []).append(
            {"resume": options.resume, "session_id": options.session_id})

        async def gen():
            from claude_agent_sdk import ResultMessage, SystemMessage
            if options.resume:
                raise RuntimeError("No conversation found with session ID")
            yield SystemMessage(subtype="init",
                                data={"session_id": options.session_id or "anon-1"})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1,
                                session_id=options.session_id or "anon-1", result="ok")
        return gen()

    with patch("cc_caller.claude_worker.query", new=spy_query):
        out, sid = run_claude("do it", "name-uuid-123", fresh_session_id="name-uuid-123")
    assert sid == "name-uuid-123"
    assert captured["calls"][0] == {"resume": "name-uuid-123", "session_id": None}
    assert captured["calls"][1] == {"resume": None, "session_id": "name-uuid-123"}


def test_run_claude_fresh_without_id_stays_anonymous():
    captured = {}

    def spy_query(prompt, options):
        captured.setdefault("calls", []).append(
            {"resume": options.resume, "session_id": options.session_id})

        async def gen():
            from claude_agent_sdk import ResultMessage, SystemMessage
            if options.resume:
                raise RuntimeError("No conversation found with session ID")
            yield SystemMessage(subtype="init", data={"session_id": "anon-1"})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="anon-1", result="ok")
        return gen()

    with patch("cc_caller.claude_worker.query", new=spy_query):
        out, sid = run_claude("do it", "dead-session")
    # fresh attempt must not specify a session_id (anonymous)
    assert captured["calls"][1] == {"resume": None, "session_id": None}


# ---------------------------------------------------------------------------
# Item 3: richer error detail (errors list appended)
# ---------------------------------------------------------------------------

def _fake_messages_with_errors(errors_list):
    """Fake query that yields a ResultMessage with is_error=True and errors list."""
    from claude_agent_sdk import (ResultMessage, SystemMessage)

    def make_query(prompt, options):
        async def gen():
            yield SystemMessage(subtype="init", data={"session_id": "err-sid"})
            yield ResultMessage(
                subtype="error_during_execution",
                duration_ms=1, duration_api_ms=1,
                is_error=True,
                num_turns=2, session_id="err-sid",
                result="task failed (error_during_execution)",
                errors=errors_list,
            )
        return gen()

    return make_query


def test_error_detail_includes_errors_list():
    import pytest as _pytest
    from cc_caller.claude_worker import WorkerTaskError
    fake = _fake_messages_with_errors(["turn 2 boom"])
    with patch("cc_caller.claude_worker.query", new=fake):
        with _pytest.raises(WorkerTaskError) as exc_info:
            run_claude("do it", None)
    assert "turn 2 boom" in str(exc_info.value)


def test_error_detail_no_errors_field_is_unchanged():
    """When errors is None the message is unchanged (no ' -- ' appended)."""
    import pytest as _pytest
    from cc_caller.claude_worker import WorkerTaskError
    fake = _fake_messages_with_errors(None)
    with patch("cc_caller.claude_worker.query", new=fake):
        with _pytest.raises(WorkerTaskError) as exc_info:
            run_claude("do it", None)
    assert " -- " not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Item 4: rate-limit event surfacing
# ---------------------------------------------------------------------------

def test_rate_limit_event_triggers_on_activity():
    """RateLimitEvent yields an on_activity call with 'Rate limit:' prefix."""
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo

    seen_activity = []

    def make_query(prompt, options):
        async def gen():
            from claude_agent_sdk import SystemMessage, ResultMessage
            yield SystemMessage(subtype="init", data={"session_id": "rl-sid"})
            yield RateLimitEvent(
                rate_limit_info=RateLimitInfo(status="allowed_warning", raw={}),
                uuid="uuid-1",
                session_id="rl-sid",
            )
            yield ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id="rl-sid", result="done",
            )
        return gen()

    with patch("cc_caller.claude_worker.query", new=make_query):
        run_claude("do it", None, on_activity=seen_activity.append)

    assert any("Rate limit:" in s for s in seen_activity)


# ---------------------------------------------------------------------------
# Item 5: CC_MAX_TURNS env var wires to options.max_turns
# ---------------------------------------------------------------------------

def test_max_turns_env_wires_to_options(monkeypatch):
    """CC_MAX_TURNS=3 → ClaudeAgentOptions.max_turns == 3."""
    monkeypatch.setenv("CC_MAX_TURNS", "3")
    captured = {}

    def spy_query(prompt, options):
        captured["max_turns"] = options.max_turns

        async def gen():
            from claude_agent_sdk import ResultMessage, SystemMessage
            yield SystemMessage(subtype="init", data={"session_id": "mt-sid"})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="mt-sid", result="ok")
        return gen()

    with patch("cc_caller.claude_worker.query", new=spy_query):
        run_claude("do it", None)
    assert captured["max_turns"] == 3


def test_max_turns_unset_is_none(monkeypatch):
    """CC_MAX_TURNS absent → options.max_turns is None."""
    monkeypatch.delenv("CC_MAX_TURNS", raising=False)
    captured = {}

    def spy_query(prompt, options):
        captured["max_turns"] = options.max_turns

        async def gen():
            from claude_agent_sdk import ResultMessage, SystemMessage
            yield SystemMessage(subtype="init", data={"session_id": "mt-sid"})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="mt-sid", result="ok")
        return gen()

    with patch("cc_caller.claude_worker.query", new=spy_query):
        run_claude("do it", None)
    assert captured["max_turns"] is None


def test_max_turns_invalid_value_is_none(monkeypatch):
    """CC_MAX_TURNS with non-digit value → options.max_turns is None."""
    monkeypatch.setenv("CC_MAX_TURNS", "abc")
    captured = {}

    def spy_query(prompt, options):
        captured["max_turns"] = options.max_turns

        async def gen():
            from claude_agent_sdk import ResultMessage, SystemMessage
            yield SystemMessage(subtype="init", data={"session_id": "mt-sid"})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="mt-sid", result="ok")
        return gen()

    with patch("cc_caller.claude_worker.query", new=spy_query):
        run_claude("do it", None)
    assert captured["max_turns"] is None
