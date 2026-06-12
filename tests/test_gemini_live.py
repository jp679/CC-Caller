import asyncio
import threading

import pytest

from cc_caller.gemini_live import GeminiLiveSession
from tests.fake_gemini import FakeGemini


class StubTM:
    def __init__(self, accept=True, queue_accepts=True):
        self.accept = accept
        self.queue_accepts = queue_accepts
        self.submitted = []
        self.queued = []
        self.busy = False
        self.elapsed = None
        self._cancel_called = False

    def submit(self, task, meta=None):
        self.submitted.append((task, meta))
        return self.accept

    def queue_next(self, task, meta=None):
        self.queued.append((task, meta))
        return self.queue_accepts

    def cancel(self):
        self._cancel_called = True
        return self.busy  # True only if busy


class Harness:
    def __init__(self, fake, tm):
        self.to_browser = []
        self.queue = asyncio.Queue()
        self.session = GeminiLiveSession(
            api_key="test-key", system_prompt="PROMPT", task_manager=tm,
            send_to_browser=self._send, ws_url=fake.url,
        )
        self.run_task = None

    async def _send(self, msg):
        self.to_browser.append(msg)

    async def _browser_messages(self):
        while True:
            msg = await self.queue.get()
            if msg is None:
                return
            yield msg

    def start(self):
        self.run_task = asyncio.ensure_future(self.session.run(self._browser_messages()))

    async def stop(self):
        await self.queue.put(None)
        try:
            await asyncio.wait_for(self.run_task, timeout=3)
        except asyncio.TimeoutError:
            self.run_task.cancel()


async def wait_until(cond, timeout=3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in {}s".format(timeout))
        await asyncio.sleep(0.02)


async def test_handshake_declares_non_blocking_tools():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        setup = fake.received_of("setup")[0]["setup"]
        decls = setup["tools"][0]["functionDeclarations"]
        names = [d["name"] for d in decls]
        assert names == [
            "askCodingAgent", "checkStatus", "cancelTask", "rememberNote",
            "listSessions", "switchSession", "endSession",
        ]
        assert decls[0]["behavior"] == "NON_BLOCKING"
        assert setup["systemInstruction"]["parts"][0]["text"] == "PROMPT"
        assert h.session.async_tools is True
        await h.stop()


async def test_fallback_when_non_blocking_rejected():
    async with FakeGemini(reject_non_blocking=True) as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        assert fake.setup_count == 2
        second = fake.received_of("setup")[1]["setup"]
        assert "behavior" not in second["tools"][0]["functionDeclarations"][0]
        assert h.session.async_tools is False
        await h.stop()


async def test_browser_audio_forwarded_to_gemini():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await h.queue.put({"type": "audio", "data": "QUJD"})
        await wait_until(lambda: fake.received_of("realtimeInput"))
        ri = fake.received_of("realtimeInput")[0]["realtimeInput"]
        assert ri["audio"]["data"] == "QUJD"
        assert ri["audio"]["mimeType"] == "audio/pcm;rate=16000"
        await h.stop()


async def test_gemini_audio_and_captions_forwarded_to_browser():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"serverContent": {
            "inputTranscription": {"text": "hello"},
            "modelTurn": {"parts": [{"inlineData": {"data": "UENN"}}]},
        }})
        await wait_until(lambda: any(m.get("type") == "audio" for m in h.to_browser))
        assert {"type": "caption", "role": "user", "text": "hello"} in h.to_browser
        assert {"type": "audio", "data": "UENN"} in h.to_browser
        await h.stop()


async def test_tool_call_acks_interim_then_delivers_interrupt():
    tm = StubTM(accept=True)
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "fix the bug"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        assert tm.submitted == [("fix the bug", {"fc_id": "f1"})]
        interim = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert interim["id"] == "f1"
        assert interim["willContinue"] is True
        assert interim["response"]["status"] == "started"
        assert any(m.get("type") == "status" and m.get("state") == "working"
                   for m in h.to_browser)

        # deliver from a foreign thread, like the worker does
        ok = await asyncio.get_event_loop().run_in_executor(
            None, h.session.deliver_result, "all fixed")
        assert ok is True
        await wait_until(lambda: len(fake.received_of("toolResponse")) >= 2)
        final = fake.received_of("toolResponse")[1]["toolResponse"]["functionResponses"][0]
        assert final["id"] == "f1"
        assert final["scheduling"] == "INTERRUPT"
        assert final["response"]["result"] == "all fixed"
        await h.stop()


async def test_busy_manager_queues_task_when_queue_available():
    """When submit returns False (busy) and queue_next accepts, response is 'queued'."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM(accept=False, queue_accepts=True))
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f9", "name": "askCodingAgent", "args": {"task": "another"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["status"] == "queued"
        assert "willContinue" not in resp
        await h.stop()


async def test_busy_manager_returns_busy_when_queue_unavailable():
    """When submit returns False and queue_next also returns False, response is 'busy'."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM(accept=False, queue_accepts=False))
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f9", "name": "askCodingAgent", "args": {"task": "another"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["status"] == "busy"
        assert "willContinue" not in resp
        await h.stop()


async def test_cancellation_falls_back_to_client_content():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "t"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        await fake.send({"toolCallCancellation": {"ids": ["f1"]}})
        await wait_until(lambda: "f1" in h.session._cancelled)
        ok = await asyncio.get_event_loop().run_in_executor(
            None, h.session.deliver_result, "late result")
        assert ok is True
        await wait_until(lambda: fake.received_of("clientContent"))
        turn = fake.received_of("clientContent")[0]["clientContent"]["turns"][0]
        assert "late result" in turn["parts"][0]["text"]
        await h.stop()


async def test_check_status_and_end_session():
    tm = StubTM()
    tm.busy, tm.elapsed = True, 42.5
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "s1", "name": "checkStatus", "args": {}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        status = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert status["response"] == {"working": True, "elapsedSeconds": 42}

        await fake.send({"toolCall": {"functionCalls": [
            {"id": "e1", "name": "endSession", "args": {}}]}})
        await wait_until(lambda: len(fake.received_of("toolResponse")) >= 2)
        await fake.send({"serverContent": {"turnComplete": True}})
        await asyncio.wait_for(h.run_task, timeout=3)
        assert h.session.alive is False
        assert h.session.ended is True


async def test_remember_note_saved():
    saved = []

    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.session.on_remember = saved.append
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "r1", "name": "rememberNote", "args": {"note": "Use black coffee."}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["id"] == "r1"
        assert resp["response"] == {"saved": True}
        assert saved == ["Use black coffee."]
        await h.stop()


async def test_remember_note_empty_note_yields_saved_false():
    saved = []

    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.session.on_remember = saved.append
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "r2", "name": "rememberNote", "args": {"note": "   "}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"] == {"saved": False}
        assert saved == []
        await h.stop()


async def test_remember_note_without_callback_yields_saved_false():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "r3", "name": "rememberNote", "args": {"note": "Remember this."}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"] == {"saved": False}
        await h.stop()


async def test_instant_completion_does_not_overtake_interim_ack():
    ref = {}

    class InstantTM(StubTM):
        def submit(self, task, meta=None):
            super().submit(task, meta)
            self.thread = threading.Thread(
                target=ref["s"].deliver_result, args=("instant",))
            self.thread.start()
            return True

    async with FakeGemini() as fake:
        tm = InstantTM()
        h = Harness(fake, tm)
        ref["s"] = h.session
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "quick"}}]}})
        await wait_until(lambda: len(fake.received_of("toolResponse")) >= 2)
        first = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        second = fake.received_of("toolResponse")[1]["toolResponse"]["functionResponses"][0]
        assert first["willContinue"] is True
        assert first["response"]["status"] == "started"
        assert second["scheduling"] == "INTERRUPT"
        assert second["response"]["result"] == "instant"
        tm.thread.join(timeout=5)
        await h.stop()


async def test_result_ready_during_pre_ack_await_still_ordered():
    """Deterministic overtake repro: send_to_browser suspends (like a real
    browser socket) and the worker finishes before the interim ack is sent.
    Without the ack gate, the final INTERRUPT reaches the wire first."""
    import time
    ref = {}

    class BlockingInstantTM(StubTM):
        def submit(self, task, meta=None):
            super().submit(task, meta)
            self.thread = threading.Thread(
                target=ref["s"].deliver_result, args=("instant",))
            self.thread.start()
            time.sleep(0.05)  # loop thread blocked: _deliver is now scheduled
            return True

    async with FakeGemini() as fake:
        tm = BlockingInstantTM()
        h = Harness(fake, tm)

        async def yielding_send(msg):
            await asyncio.sleep(0.01)  # a real browser socket suspends here
            h.to_browser.append(msg)

        h.session.send_to_browser = yielding_send
        ref["s"] = h.session
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "quick"}}]}})
        await wait_until(lambda: len(fake.received_of("toolResponse")) >= 2)
        first = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        second = fake.received_of("toolResponse")[1]["toolResponse"]["functionResponses"][0]
        assert first["response"].get("status") == "started", \
            "final overtook interim: {}".format(first)
        assert first["willContinue"] is True
        assert second.get("scheduling") == "INTERRUPT"
        assert second["response"]["result"] == "instant"
        # No blocking join on the loop thread: _deliver still needs the loop
        # for its final send_to_browser before the worker's future resolves.
        await wait_until(lambda: not tm.thread.is_alive())
        await h.stop()


async def test_show_exchange_sends_browser_messages():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.session.show_exchange = True
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "fix it"}}]}})
        await wait_until(lambda: any(m.get("type") == "exchange" for m in h.to_browser))
        assert {"type": "exchange", "role": "task", "text": "fix it"} in h.to_browser
        ok = await asyncio.get_event_loop().run_in_executor(
            None, h.session.deliver_result, "all good")
        assert ok is True
        await wait_until(lambda: {"type": "exchange", "role": "result", "text": "all good"}
                         in h.to_browser)
        await h.stop()


async def test_no_exchange_messages_by_default():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "x"}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        assert not any(m.get("type") == "exchange" for m in h.to_browser)
        await h.stop()


async def test_connect_failure_raises_runtime_error():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listening on this port now

    async def no_messages():
        return
        yield  # pragma: no cover

    session = GeminiLiveSession(
        api_key="k", system_prompt="P", task_manager=StubTM(),
        send_to_browser=lambda m: None, ws_url="ws://127.0.0.1:{}".format(port),
    )
    with pytest.raises(RuntimeError):
        await session.run(no_messages())


async def test_ready_frame_includes_session_identity():
    tm = StubTM()
    tm.session_id = "abc-12345678"
    tm.session_name = "myproj"
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        ready = [m for m in h.to_browser if m.get("type") == "ready"][0]
        assert ready["session"] == {"id": "abc-12345678", "name": "myproj"}
        await h.stop()


async def test_opening_injected_after_ready():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.session.opening = "[SYSTEM] Greet and report: all tests pass"
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await wait_until(lambda: fake.received_of("clientContent"))
        turn = fake.received_of("clientContent")[0]["clientContent"]["turns"][0]
        assert "all tests pass" in turn["parts"][0]["text"]
        assert fake.received_of("clientContent")[0]["clientContent"]["turnComplete"] is True
        await h.stop()


async def test_no_opening_no_client_content():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await asyncio.sleep(0.1)
        assert not fake.received_of("clientContent")
        await h.stop()


async def test_voice_log_coalesces_and_fires_on_session_end():
    ended = {}
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.session.on_session_end = lambda log: ended.update({"log": log})
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"serverContent": {"inputTranscription": {"text": "hel"}}})
        await fake.send({"serverContent": {"inputTranscription": {"text": "lo there"}}})
        await fake.send({"serverContent": {"outputTranscription": {"text": "hi JP"}}})
        await wait_until(lambda: len(h.session.voice_log) == 2)
        assert h.session.voice_log[0] == ("user", "hello there")
        await h.stop()
    assert ended["log"] == [("user", "hello there"), ("agent", "hi JP")]


async def test_no_session_end_callback_for_trivial_log():
    ended = {}
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.session.on_session_end = lambda log: ended.update({"log": log})
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"serverContent": {"inputTranscription": {"text": "hi"}}})
        await h.stop()
    assert "log" not in ended


async def test_check_status_includes_activity_when_present():
    tm = StubTM()
    tm.busy, tm.elapsed = True, 10.0
    tm.current_activity = "Edit cc_caller/server.py"
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "s1", "name": "checkStatus", "args": {}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["activity"] == "Edit cc_caller/server.py"
        assert resp["response"]["working"] is True
        await h.stop()


async def test_check_status_omits_activity_when_absent():
    tm = StubTM()
    tm.busy, tm.elapsed = True, 10.0
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "s1", "name": "checkStatus", "args": {}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert "activity" not in resp["response"]
        await h.stop()


async def test_notify_activity_sends_status_from_foreign_thread():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await asyncio.get_event_loop().run_in_executor(
            None, h.session.notify_activity, "Bash pytest -q")
        await wait_until(lambda: any(
            m.get("type") == "status" and m.get("activity") == "Bash pytest -q"
            for m in h.to_browser))
        msg = [m for m in h.to_browser
               if m.get("type") == "status" and m.get("activity") == "Bash pytest -q"][0]
        assert msg == {"type": "status", "state": "working", "activity": "Bash pytest -q"}
        await h.stop()


async def test_notify_activity_noop_when_dead():
    session = GeminiLiveSession(
        api_key="k", system_prompt="P", task_manager=StubTM(),
        send_to_browser=lambda m: None,
    )
    session.notify_activity("anything")


async def test_cancel_task_when_busy_sends_cancelled_true_and_status_done():
    """cancelTask while tm.busy=True: response has cancelled=True, browser gets status done."""
    tm = StubTM()
    tm.busy = True  # pretend busy so cancel() returns True
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "c1", "name": "cancelTask", "args": {}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["cancelled"] is True
        assert "message" not in resp["response"]
        assert any(m.get("type") == "status" and m.get("state") == "done"
                   for m in h.to_browser)
        assert tm._cancel_called is True
        await h.stop()


async def test_cancel_task_when_idle_sends_cancelled_false():
    """cancelTask while idle: response has cancelled=False with a message."""
    tm = StubTM()
    tm.busy = False  # idle
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "c2", "name": "cancelTask", "args": {}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["cancelled"] is False
        assert "message" in resp["response"]
        # No status:done sent when idle
        assert not any(m.get("type") == "status" and m.get("state") == "done"
                       for m in h.to_browser)
        await h.stop()


# ---------------------------------------------------------------------------
# Batch D round 1: session switching + typed input
# ---------------------------------------------------------------------------

async def test_handshake_declares_seven_tools():
    """After adding listSessions and switchSession there are 7 declared tools."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        setup = fake.received_of("setup")[0]["setup"]
        decls = setup["tools"][0]["functionDeclarations"]
        names = [d["name"] for d in decls]
        assert names == [
            "askCodingAgent", "checkStatus", "cancelTask", "rememberNote",
            "listSessions", "switchSession", "endSession",
        ]
        await h.stop()


async def test_list_sessions_returns_wired_data():
    """listSessions toolCall invokes on_list_sessions and returns sessions list + current."""
    sessions_data = [{"session_id": "aaa", "label": "Pasta tweaks", "age": "5m ago"}]

    async with FakeGemini() as fake:
        tm = StubTM()
        tm.session_id = "current-id"
        tm.session_name = "my-proj"
        h = Harness(fake, tm)
        h.session.on_list_sessions = lambda: sessions_data
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "ls1", "name": "listSessions", "args": {}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["id"] == "ls1"
        body = resp["response"]
        assert body["current"] == {"id": "current-id", "name": "my-proj"}
        assert len(body["sessions"]) == 1
        assert body["sessions"][0]["session_id"] == "aaa"
        assert body["sessions"][0]["label"] == "Pasta tweaks"
        assert body["sessions"][0]["age"] == "5m ago"
        await h.stop()


async def test_list_sessions_without_callback_returns_empty_list():
    """listSessions with no on_list_sessions set returns empty list, no crash."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        # on_list_sessions not set (None)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "ls2", "name": "listSessions", "args": {}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["sessions"] == []
        await h.stop()


async def test_switch_session_ok_sends_browser_frame_and_switched_true():
    """switchSession with a valid session_id: browser gets {type:session}, response switched True."""
    async with FakeGemini() as fake:
        tm = StubTM()
        tm.switch_session = lambda **kw: True
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "sw1", "name": "switchSession", "args": {"session_id": "aaa"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["switched"] is True
        session_frames = [m for m in h.to_browser if m.get("type") == "session"]
        assert len(session_frames) == 1
        assert session_frames[0]["session"]["id"] == "aaa"
        await h.stop()


async def test_switch_session_refused_returns_switched_false():
    """switchSession when tm.switch_session returns False: switched False, no browser frame."""
    async with FakeGemini() as fake:
        tm = StubTM()
        tm.switch_session = lambda **kw: False
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "sw2", "name": "switchSession", "args": {"session_id": "bbb"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["switched"] is False
        assert "message" in resp["response"]
        assert not any(m.get("type") == "session" for m in h.to_browser)
        await h.stop()


async def test_server_interrupted_forwarded_to_browser():
    """serverContent.interrupted true -> browser receives {type:interrupted}."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"serverContent": {"interrupted": True}})
        await wait_until(lambda: any(m.get("type") == "interrupted" for m in h.to_browser))
        assert {"type": "interrupted"} in h.to_browser
        await h.stop()


async def test_server_content_without_interrupted_sends_no_interrupted_frame():
    """A serverContent frame with no interrupted flag must not emit interrupted."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"serverContent": {
            "outputTranscription": {"text": "hi"},
            "modelTurn": {"parts": [{"inlineData": {"data": "UENN"}}]},
        }})
        await wait_until(lambda: any(m.get("type") == "audio" for m in h.to_browser))
        assert not any(m.get("type") == "interrupted" for m in h.to_browser)
        await h.stop()


async def test_typed_text_browser_message_forwarded_as_client_content():
    """Browser sends {type:text, text:'hello'}: fake Gemini receives a clientContent turn."""
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await h.queue.put({"type": "text", "text": "hello from keyboard"})
        await wait_until(lambda: fake.received_of("clientContent"))
        cc = fake.received_of("clientContent")[0]["clientContent"]
        assert cc["turnComplete"] is True
        assert cc["turns"][0]["role"] == "user"
        assert cc["turns"][0]["parts"][0]["text"] == "hello from keyboard"
        await h.stop()
