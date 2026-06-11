import json

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

    def take_pending(self):
        p, self.pending = self.pending, None
        return p


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
