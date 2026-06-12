import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cc_caller.server import AppState, create_app, build_system_prompt


class StubTM:
    def __init__(self):
        self.history = []
        self.pending = None
        self.busy = False
        self.elapsed = None
        self.session_id = None
        self.session_name = "caller"

    def take_pending(self):
        p, self.pending = self.pending, None
        return p

    def switch_session(self, **kwargs):
        return True


def make_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    return AppState(
        token="sekrit", task_manager=StubTM(), api_key="gk",
        model=None, vapid_public_key="VPK",
        base_system_prompt="BASE PROMPT", subscriptions=[],
    )


def test_api_config_requires_token(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config?token=wrong").status_code == 401
    resp = client.get("/api/config?token=sekrit")
    assert resp.status_code == 200
    assert resp.json()["vapidPublicKey"] == "VPK"


def test_push_subscribe_requires_token_and_persists(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    client = TestClient(create_app(state))
    sub = {"endpoint": "https://e/x", "keys": {"auth": "a", "p256dh": "b"}}
    assert client.post("/api/push-subscribe", json=sub).status_code == 401
    resp = client.post("/api/push-subscribe?token=sekrit", json=sub)
    assert resp.status_code == 200
    assert state.subscriptions == [sub]
    saved = json.loads((tmp_path / "subscriptions.json").read_text())
    assert saved == [sub]


def test_index_and_sw_are_public(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.headers["service-worker-allowed"] == "/"


def test_ws_rejects_bad_token(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=wrong"):
            pass


def test_ws_accepts_good_token_and_runs_session(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)

    class StubSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, browser_messages):
            await self.kwargs["send_to_browser"]({"type": "ready", "asyncTools": True})
            async for _ in browser_messages:
                return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws?token=sekrit") as ws:
        assert ws.receive_json() == {"type": "ready", "asyncTools": True}
        assert state.session_holder["session"] is not None
        ws.send_json({"type": "end"})


def test_ws_session_failure_sends_error_frame(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)

    class ExplodingSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, browser_messages):
            raise RuntimeError("Gemini Live setup failed")

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", ExplodingSession)
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws?token=sekrit") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "Gemini Live setup failed" in msg["message"]


def test_ws_passes_show_exchange_to_session(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.show_exchange = True
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert captured.get("show_exchange") is True


def test_system_prompt_includes_history_and_pending(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.history = [{"task": "fix auth", "summary": "auth fixed"}]
    state.task_manager.pending = {"task": "t2", "summary": "tests added", "detail": "", "meta": {}}
    prompt = build_system_prompt(state)
    assert prompt.startswith("BASE PROMPT")
    assert "fix auth" in prompt and "auth fixed" in prompt
    assert "tests added" in prompt
    assert "PENDING RESULT" in prompt


def test_manifest_embeds_token_when_presented(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    resp = client.get("/manifest.json?token=sekrit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["start_url"] == "/?callback=0&token=sekrit"


def test_manifest_plain_without_valid_token(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    for url in ("/manifest.json", "/manifest.json?token=wrong"):
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert "sekrit" not in resp.text
        assert data["start_url"] == "/?callback=0"


def test_index_links_tokened_manifest_when_presented(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    resp = client.get("/?token=sekrit")
    assert resp.status_code == 200
    assert 'href="/manifest.json?token=sekrit"' in resp.text
    resp = client.get("/")
    assert 'href="/manifest.json"' in resp.text
    assert "sekrit" not in resp.text


def test_api_sessions_token_gated_and_shaped(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.session_id = "deadbeef-0000-0000-0000-000000000000"
    state.task_manager.session_name = "caller"
    fake = [{"session_id": "aaa", "label": "fix auth", "age": "5m ago"}]
    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod.sessions, "recent_sessions", lambda limit=5: fake)
    client = TestClient(create_app(state))
    assert client.get("/api/sessions").status_code == 401
    resp = client.get("/api/sessions?token=sekrit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions"] == fake
    assert data["current"]["id"] == "deadbeef-0000-0000-0000-000000000000"
    assert data["current"]["name"] == "caller"


def test_api_sessions_overlays_titles_from_callermem(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.session_id = "current-sid"
    fake = [
        {"session_id": "aaa", "label": "from transcript", "age": "5m ago"},
        {"session_id": "bbb", "label": "keep me", "age": "6m ago"},
    ]

    class StubCallerMem:
        @staticmethod
        def load(session_id):
            if session_id == "aaa":
                return {"title": "Better title", "history": [], "pending": None, "voice_notes": []}
            if session_id == "current-sid":
                return {"title": "Current Title", "history": [], "pending": None, "voice_notes": []}
            return {"title": None, "history": [], "pending": None, "voice_notes": []}

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod.sessions, "recent_sessions", lambda limit=5: list(fake))
    monkeypatch.setattr(server_mod, "callermem", StubCallerMem)
    client = TestClient(create_app(state))
    resp = client.get("/api/sessions?token=sekrit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions"][0]["label"] == "Better title"
    assert data["sessions"][1]["label"] == "keep me"
    assert data["current"]["title"] == "Current Title"


def test_ws_session_param_switches_before_session_build(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    calls = []
    state.task_manager.switch_session = lambda **kw: (calls.append(kw), True)[1]

    class StubSession:
        def __init__(self, **kwargs):
            pass

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit&session=id:abc-123"):
            pass
    except WebSocketDisconnect:
        pass
    assert calls == [{"session_id": "abc-123"}]

    calls.clear()
    try:
        with client.websocket_connect("/ws?token=sekrit&session=name:myproj"):
            pass
    except WebSocketDisconnect:
        pass
    assert calls == [{"session_name": "myproj"}]


def test_ws_session_switch_refused_sends_error_frame(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.switch_session = lambda **kw: False

    class StubSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, browser_messages):
            await self.kwargs["send_to_browser"]({"type": "ready", "asyncTools": True})
            async for _ in browser_messages:
                return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws?token=sekrit&session=id:abc") as ws:
        first = ws.receive_json()
        assert first["type"] == "error"
        assert "task is still running" in first["message"]
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "end"})


def test_build_system_prompt_includes_resumed_messages(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    resumed = [{"role": "user", "text": "fix the login bug"},
               {"role": "assistant", "text": "done, tests pass"}]
    prompt = build_system_prompt(state, resumed=resumed)
    assert "RESUMED CLAUDE SESSION" in prompt
    assert "fix the login bug" in prompt
    assert "done, tests pass" in prompt
    assert build_system_prompt(state) == build_system_prompt(state, resumed=[])


def test_ws_builds_opener_from_pending(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.pending = {"task": "t", "summary": "the tests now pass",
                                  "detail": "", "meta": {}}
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert "the tests now pass" in captured["opening"]
    assert captured["opening"].startswith("[SYSTEM]")

    captured.clear()
    state.task_manager.pending = None
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert captured["opening"] is None


def test_ws_builds_fresh_opener_from_pending_ts(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.pending = {"task": "t", "summary": "fresh result",
                                  "detail": "", "meta": {}, "ts": time.time()}
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert "right away" in captured["opening"]
    assert "fresh result" in captured["opening"]


def test_ws_builds_stale_opener_from_pending_ts(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.pending = {"task": "t", "summary": "stale result",
                                  "detail": "", "meta": {},
                                  "ts": time.time() - 3 * 86400}
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert "days ago" in captured["opening"]
    assert "stale result" in captured["opening"]


def test_ws_suppresses_pending_block_when_opener_carries_it(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.pending = {"task": "t", "summary": "the tests now pass",
                                  "detail": "", "meta": {}}
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert "the tests now pass" in captured["opening"]
    assert "PENDING RESULT" not in captured["system_prompt"]

    # without an opener the prompt still carries the pending block
    prompt = build_system_prompt(state)
    assert "PENDING RESULT" in prompt
    assert "PENDING RESULT" not in build_system_prompt(state, suppress_pending=True)


def test_system_prompt_includes_voice_notes(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.voice_notes = ["2026-06-12 01:00 -- discussed pasta recipe"]
    prompt = build_system_prompt(state)
    assert "PREVIOUS CALLS" in prompt
    assert "discussed pasta recipe" in prompt


def test_ws_passes_session_end_callback(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert callable(captured.get("on_session_end"))


def test_ws_passes_remember_callback(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert callable(captured.get("on_remember"))


def test_ws_passes_on_list_sessions_callback(tmp_path, monkeypatch):
    """ws_bridge passes a callable on_list_sessions to GeminiLiveSession."""
    state = make_state(tmp_path, monkeypatch)
    captured = {}

    class StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, browser_messages):
            return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    try:
        with client.websocket_connect("/ws?token=sekrit"):
            pass
    except WebSocketDisconnect:
        pass
    assert callable(captured.get("on_list_sessions"))


def test_session_listing_helper_overlays_titles(tmp_path, monkeypatch):
    """session_listing() returns recent sessions with callermem titles overlaid."""
    from cc_caller.server import session_listing

    fake_entries = [
        {"session_id": "aaa", "label": "from transcript", "age": "5m ago"},
        {"session_id": "bbb", "label": "keep me", "age": "6m ago"},
    ]

    class StubCallerMem:
        @staticmethod
        def load(session_id):
            if session_id == "aaa":
                return {"title": "Better title", "history": [], "pending": None, "voice_notes": []}
            return {"title": None, "history": [], "pending": None, "voice_notes": []}

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod.sessions, "recent_sessions", lambda limit=5: list(fake_entries))
    monkeypatch.setattr(server_mod, "callermem", StubCallerMem)

    result = session_listing(None)
    assert result[0]["label"] == "Better title"
    assert result[1]["label"] == "keep me"


def test_ws_sends_transcript_frames_before_ready(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    fake_msgs = [{"role": "user", "text": "old question"},
                 {"role": "assistant", "text": "old answer"}]
    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod.sessions, "recent_messages",
                        lambda sid, limit=12: fake_msgs)

    class StubSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, browser_messages):
            await self.kwargs["send_to_browser"]({"type": "ready", "asyncTools": True})
            async for _ in browser_messages:
                return

    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws?token=sekrit") as ws:
        frames = [ws.receive_json(), ws.receive_json(), ws.receive_json()]
        assert frames[0] == {"type": "transcript", "role": "user", "text": "old question"}
        assert frames[1] == {"type": "transcript", "role": "assistant", "text": "old answer"}
        assert frames[2]["type"] == "ready"
        ws.send_json({"type": "end"})
