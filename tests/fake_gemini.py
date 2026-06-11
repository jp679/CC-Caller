"""In-process fake of the Gemini Live WebSocket API for offline tests."""
import asyncio
import json

import websockets


class FakeGemini:
    def __init__(self, reject_non_blocking=False):
        self.reject_non_blocking = reject_non_blocking
        self.received = []        # parsed client->server messages
        self.setup_count = 0
        self.url = None
        self._server = None
        self._client = None

    async def __aenter__(self):
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = "ws://127.0.0.1:{}".format(port)
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws, path=None):  # path kwarg: websockets <13 compat
        async for raw in ws:
            data = json.loads(raw)
            self.received.append(data)
            if "setup" in data:
                self.setup_count += 1
                decls = data["setup"]["tools"][0]["functionDeclarations"]
                if self.reject_non_blocking and any("behavior" in d for d in decls):
                    await ws.close(code=1007, reason="NON_BLOCKING unsupported")
                    return
                self._client = ws
                await ws.send(json.dumps({"setupComplete": {}}))

    async def send(self, obj):
        await self._client.send(json.dumps(obj))

    def received_of(self, key):
        return [m for m in self.received if key in m]
