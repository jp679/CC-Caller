"""LiveKit + Gemini Live agent for CC-Caller.

Joins a LiveKit room, uses Gemini Live for voice, and communicates
with the cc-caller orchestrator via queues.
"""

import asyncio
import logging
import os
import queue
import threading
from typing import Optional

from livekit import rtc
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.agents.voice import MetricsCollectedEvent
from livekit.plugins import google

logger = logging.getLogger("cc-caller-agent")


class CCCallerAgent:
    """Wraps the LiveKit Gemini agent with queues for cc-caller integration."""

    def __init__(
        self,
        transcript_queue: queue.Queue,
        gemini_api_key: str,
        system_prompt: str,
    ):
        self.transcript_queue = transcript_queue
        self.gemini_api_key = gemini_api_key
        self.system_prompt = system_prompt
        self._inject_queue: asyncio.Queue = asyncio.Queue()
        self._session: Optional[AgentSession] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def inject_text(self, text: str) -> None:
        """Thread-safe: inject text into the Gemini conversation."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._inject_queue.put_nowait, text)

    async def _injection_loop(self):
        """Background task: reads from inject queue, sends to Gemini."""
        while True:
            text = await self._inject_queue.get()
            if self._session:
                try:
                    await self._session.say(text, allow_interruptions=True)
                except Exception as e:
                    logger.error(f"Injection error: {e}")

    async def run(self, room: rtc.Room):
        """Main agent loop — called after joining the room."""
        self._loop = asyncio.get_event_loop()

        model = google.realtime.RealtimeModel(
            api_key=self.gemini_api_key,
            voice="Kore",
            temperature=0.7,
            instructions=self.system_prompt,
        )

        self._session = AgentSession(llm=model)

        # Listen for final transcripts from the user
        @self._session.on("user_input_transcribed")
        def on_transcript(text: str):
            if text.strip():
                logger.info(f"User said: {text}")
                self.transcript_queue.put(text)

        # Start injection loop
        asyncio.create_task(self._injection_loop())

        # Start the session
        await self._session.start(
            room=room,
            agent=Agent(instructions=self.system_prompt),
        )

        # Keep running until room closes
        disconnect_event = asyncio.Event()

        @room.on("disconnected")
        def on_disconnect():
            disconnect_event.set()

        await disconnect_event.wait()

    async def close(self):
        if self._session:
            await self._session.close()
