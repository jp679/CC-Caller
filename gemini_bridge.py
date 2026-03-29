"""Server-side Gemini Live bridge.

Manages the Gemini WebSocket connection from Python.
Browser connects via a local WebSocket for mic/speaker audio.
Text injection happens directly — no SSE, no polling.
"""

import asyncio
import json
import logging
import os
import queue
import base64
from typing import Optional

import websockets

logger = logging.getLogger("gemini-bridge")

GEMINI_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


class GeminiBridge:
    """Bridges browser audio ↔ Gemini Live, with text injection."""

    def __init__(self, gemini_api_key: str, system_prompt: str, transcript_queue: queue.Queue):
        self.gemini_api_key = gemini_api_key
        self.system_prompt = system_prompt
        self.transcript_queue = transcript_queue
        self._gemini_ws = None
        self._browser_ws = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def inject_text(self, text: str) -> None:
        """Thread-safe: inject text into the Gemini conversation."""
        if self._loop and self._gemini_ws:
            asyncio.run_coroutine_threadsafe(self._send_text(text), self._loop)

    async def _send_text(self, text: str):
        if self._gemini_ws:
            await self._gemini_ws.send(json.dumps({
                "realtimeInput": {"text": text}
            }))
            logger.info(f"Injected text ({len(text)} chars)")

    async def handle_browser_ws(self, websocket, path=None):
        """Handle a browser WebSocket connection."""
        self._browser_ws = websocket
        self._loop = asyncio.get_event_loop()
        logger.info("Browser connected")

        # Connect to Gemini
        gemini_url = f"{GEMINI_WS_URL}?key={self.gemini_api_key}"
        async with websockets.connect(gemini_url) as gemini_ws:
            self._gemini_ws = gemini_ws
            self._running = True

            # Send setup
            await gemini_ws.send(json.dumps({
                "setup": {
                    "model": "models/gemini-3.1-flash-live-preview",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": "Kore"}
                            }
                        }
                    },
                    "systemInstruction": {
                        "parts": [{"text": self.system_prompt}]
                    },
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {}
                }
            }))

            # Wait for setupComplete
            msg = await gemini_ws.recv()
            if isinstance(msg, bytes):
                msg = msg.decode()
            data = json.loads(msg)
            if "setupComplete" in data:
                await websocket.send(json.dumps({"type": "ready"}))
                logger.info("Gemini session ready")

            # Run two tasks: browser→gemini and gemini→browser
            await asyncio.gather(
                self._browser_to_gemini(websocket, gemini_ws),
                self._gemini_to_browser(gemini_ws, websocket),
            )

        self._gemini_ws = None
        self._running = False
        logger.info("Gemini session closed")

    async def _browser_to_gemini(self, browser_ws, gemini_ws):
        """Forward browser mic audio to Gemini."""
        try:
            async for msg in browser_ws:
                data = json.loads(msg)
                if data.get("type") == "audio" and data.get("data"):
                    # Forward PCM audio to Gemini
                    await gemini_ws.send(json.dumps({
                        "realtimeInput": {
                            "audio": {
                                "data": data["data"],
                                "mimeType": "audio/pcm;rate=16000"
                            }
                        }
                    }))
        except websockets.ConnectionClosed:
            pass

    async def _gemini_to_browser(self, gemini_ws, browser_ws):
        """Forward Gemini audio + transcripts to browser."""
        user_buf = ""
        user_timer = None

        async def flush_user():
            nonlocal user_buf
            if user_buf.strip():
                self.transcript_queue.put(user_buf.strip())
                logger.info(f"Transcript: {user_buf.strip()[:80]}...")
            user_buf = ""

        try:
            async for msg in gemini_ws:
                if isinstance(msg, bytes):
                    msg = msg.decode()
                data = json.loads(msg)

                if "serverContent" not in data:
                    continue

                sc = data["serverContent"]

                # User transcript
                if sc.get("inputTranscription", {}).get("text"):
                    text = sc["inputTranscription"]["text"]
                    user_buf += " " + text
                    if user_timer:
                        user_timer.cancel()
                    user_timer = asyncio.get_event_loop().call_later(
                        1.5, lambda: asyncio.ensure_future(flush_user())
                    )

                # Agent transcript
                if sc.get("outputTranscription", {}).get("text"):
                    text = sc["outputTranscription"]["text"]
                    try:
                        await browser_ws.send(json.dumps({
                            "type": "transcript",
                            "role": "agent",
                            "text": text
                        }))
                    except:
                        pass

                # Audio chunks → forward to browser
                if sc.get("modelTurn", {}).get("parts"):
                    for part in sc["modelTurn"]["parts"]:
                        if part.get("inlineData", {}).get("data"):
                            try:
                                await browser_ws.send(json.dumps({
                                    "type": "audio",
                                    "data": part["inlineData"]["data"]
                                }))
                            except:
                                pass

                # Turn complete
                if sc.get("turnComplete"):
                    if user_buf.strip():
                        await flush_user()

        except websockets.ConnectionClosed:
            if user_buf.strip():
                await flush_user()
