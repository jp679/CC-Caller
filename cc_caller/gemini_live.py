"""Gemini Live session with tool-calling.

Replaces the old text-injection bridge. Claude results are delivered through
declared functions: askCodingAgent is NON_BLOCKING -- an interim response
("started") keeps the conversation alive, and the final FunctionResponse with
scheduling INTERRUPT makes the agent speak the result the moment it is ready.
If the model rejects NON_BLOCKING declarations, the session reconnects without
them and late results are injected as a clientContent turn instead.
"""
import asyncio
import json
import os

import websockets

GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
DEFAULT_MODEL = "models/gemini-3.1-flash-live-preview"

ASK_CODING_AGENT = {
    "name": "askCodingAgent",
    "description": (
        "Send a coding task, question, or instruction to Claude Code, the coding "
        "agent on the user's machine. Returns an acknowledgement immediately; "
        "the result is announced when Claude finishes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {"type": "STRING",
                     "description": "The user's request, phrased completely and faithfully."},
        },
        "required": ["task"],
    },
    "behavior": "NON_BLOCKING",
}

CHECK_STATUS = {
    "name": "checkStatus",
    "description": "Check whether Claude is still working on a task and for how long.",
    "parameters": {"type": "OBJECT", "properties": {}},
}

END_SESSION = {
    "name": "endSession",
    "description": "End the voice session when the user says they are done.",
    "parameters": {"type": "OBJECT", "properties": {}},
}


class GeminiLiveSession:
    """One Gemini Live conversation, bridged to one browser connection.

    send_to_browser: async callable(dict). browser messages arrive through the
    async iterator passed to run(). deliver_result() is thread-safe.
    """

    def __init__(self, api_key, system_prompt, task_manager, send_to_browser,
                 model=None, ws_url=None, on_ready=None, show_exchange=False):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.tm = task_manager
        self.send_to_browser = send_to_browser
        self.model = model or os.getenv("GEMINI_LIVE_MODEL", DEFAULT_MODEL)
        self.ws_url = ws_url or GEMINI_WS_URL
        self.on_ready = on_ready
        self.show_exchange = show_exchange
        self.async_tools = True
        self.alive = False
        self.ended = False
        self._ws = None
        self._loop = None
        self._current_fc = None      # {"id", "name"} of the in-flight askCodingAgent
        self._ack_sent = None        # Event: interim ack on the wire; gates _deliver
        self._cancelled = set()

    # -- setup ---------------------------------------------------------------

    def _setup_msg(self):
        ask = dict(ASK_CODING_AGENT)
        if not self.async_tools:
            ask.pop("behavior", None)
        return {"setup": {
            "model": self.model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "temperature": 0.1,
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}
                },
            },
            "systemInstruction": {"parts": [{"text": self.system_prompt}]},
            "tools": [{"functionDeclarations": [ask, CHECK_STATUS, END_SESSION]}],
            "realtimeInputConfig": {"automaticActivityDetection": {
                "silenceDurationMs": int(os.getenv("GEMINI_VAD_SILENCE_MS", "2000")),
                "prefixPaddingMs": 500,
            }},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}

    async def _connect(self):
        url = "{}?key={}".format(self.ws_url, self.api_key)
        for non_blocking in (True, False):
            self.async_tools = non_blocking
            try:
                ws = await websockets.connect(url, max_size=None)
                await ws.send(json.dumps(self._setup_msg()))
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode()
                if "setupComplete" in json.loads(raw):
                    return ws
                await ws.close()
            except (websockets.ConnectionClosed, websockets.exceptions.InvalidHandshake, OSError):
                continue
        raise RuntimeError("Gemini Live setup failed with and without NON_BLOCKING tools")

    # -- main loop -----------------------------------------------------------

    async def run(self, browser_messages):
        self._loop = asyncio.get_event_loop()
        self._ws = await self._connect()
        self.alive = True
        await self.send_to_browser({"type": "ready", "asyncTools": self.async_tools})
        if self.on_ready:
            self.on_ready()
        browser_task = asyncio.ensure_future(self._pump_browser(browser_messages))
        gemini_task = asyncio.ensure_future(self._pump_gemini())
        try:
            # FIRST_COMPLETED: if either side dies (browser tab closed, Gemini
            # socket dropped, endSession), the other pump must not keep run()
            # alive -- the server cleans up on return.
            done, pending = await asyncio.wait(
                {browser_task, gemini_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, websockets.ConnectionClosed):
                    print("[gemini] pump error: {!r}".format(exc))
        finally:
            self.alive = False
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _pump_browser(self, browser_messages):
        async for msg in browser_messages:
            if msg.get("type") == "audio" and msg.get("data"):
                await self._ws.send(json.dumps({"realtimeInput": {"audio": {
                    "data": msg["data"], "mimeType": "audio/pcm;rate=16000",
                }}}))
            elif msg.get("type") == "end":
                break
        await self._ws.close()

    async def _pump_gemini(self):
        async for raw in self._ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)

            if "toolCall" in data:
                for fc in data["toolCall"].get("functionCalls", []):
                    await self._handle_tool(fc)

            if "toolCallCancellation" in data:
                self._cancelled.update(data["toolCallCancellation"].get("ids", []))

            sc = data.get("serverContent")
            if sc:
                text = sc.get("inputTranscription", {}).get("text")
                if text:
                    await self.send_to_browser({"type": "caption", "role": "user", "text": text})
                text = sc.get("outputTranscription", {}).get("text")
                if text:
                    await self.send_to_browser({"type": "caption", "role": "agent", "text": text})
                for part in sc.get("modelTurn", {}).get("parts", []):
                    blob = part.get("inlineData", {}).get("data")
                    if blob:
                        await self.send_to_browser({"type": "audio", "data": blob})
                if sc.get("turnComplete") and self.ended:
                    await self._ws.close()
                    return

    # -- tools ---------------------------------------------------------------

    async def _handle_tool(self, fc):
        name, fc_id = fc.get("name"), fc.get("id")
        args = fc.get("args") or {}
        if name == "askCodingAgent":
            task = args.get("task", "")
            # Gate set up BEFORE submit: an instantly-completing worker may call
            # deliver_result during any await below; _deliver waits on _ack_sent
            # so the final response can never overtake the interim ack.
            prev_fc, prev_ack = self._current_fc, self._ack_sent
            self._current_fc = {"id": fc_id, "name": name}
            self._ack_sent = asyncio.Event()
            if not self.tm.submit(task, meta={"fc_id": fc_id}):
                self._current_fc, self._ack_sent = prev_fc, prev_ack
                await self._respond(fc_id, name, {
                    "status": "busy",
                    "message": "Still working on the previous task. Ask checkStatus for progress.",
                })
                return
            await self.send_to_browser({"type": "status", "state": "working", "task": task})
            if self.show_exchange:
                await self.send_to_browser({"type": "exchange", "role": "task", "text": task})
            if self.async_tools:
                await self._respond(fc_id, name, {"status": "started"}, will_continue=True)
            else:
                await self._respond(fc_id, name, {
                    "status": "started",
                    "note": "The result will be announced as soon as it is ready.",
                })
            self._ack_sent.set()
        elif name == "checkStatus":
            if self.tm.busy:
                await self._respond(fc_id, name, {
                    "working": True, "elapsedSeconds": int(self.tm.elapsed or 0),
                })
            else:
                await self._respond(fc_id, name, {"working": False})
        elif name == "endSession":
            self.ended = True
            await self._respond(fc_id, name, {"status": "ending"})
            await self.send_to_browser({"type": "status", "state": "ended"})
        else:
            await self._respond(fc_id, name, {"error": "unknown tool"})

    async def _respond(self, fc_id, name, response, will_continue=False, scheduling=None):
        fr = {"id": fc_id, "name": name, "response": response}
        if will_continue:
            fr["willContinue"] = True
        if scheduling:
            fr["scheduling"] = scheduling
        await self._ws.send(json.dumps({"toolResponse": {"functionResponses": [fr]}}))

    # -- result delivery (thread-safe) ----------------------------------------

    def deliver_result(self, summary):
        """Deliver a finished task's summary into the live conversation.
        Called from worker threads. Returns True if delivered into the live
        conversation. Returns False if the session or socket is unavailable --
        the caller MUST treat False as undelivered and fall back to
        pending/push delivery."""
        if not (self.alive and self._loop):
            return False
        future = asyncio.run_coroutine_threadsafe(self._deliver(summary), self._loop)
        try:
            return future.result(timeout=10)
        except Exception:
            return False

    async def _deliver(self, summary):
        ack = self._ack_sent
        if ack is not None:
            try:
                await asyncio.wait_for(ack.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        fc, self._current_fc = self._current_fc, None
        try:
            if self.async_tools and fc and fc["id"] not in self._cancelled:
                await self._respond(fc["id"], fc["name"], {"result": summary},
                                    scheduling="INTERRUPT")
            else:
                await self._ws.send(json.dumps({"clientContent": {
                    "turns": [{"role": "user", "parts": [{"text":
                        "[SYSTEM] The coding task just finished. "
                        "Tell the user this result now: " + summary}]}],
                    "turnComplete": True,
                }}))
            if self.show_exchange:
                await self.send_to_browser({"type": "exchange", "role": "result", "text": summary})
            await self.send_to_browser({"type": "status", "state": "done"})
            return True
        except Exception as e:
            print("[gemini] deliver failed: {}".format(e))
            self._current_fc = fc
            return False
