"""LiveKit + Gemini Live agent for CC-Caller.

Uses LiveKit's proper agent dispatch pattern for reliable audio routing.
Communicates with cc-caller via shared queues.
"""

import asyncio
import logging
import os
import queue
import sys
from typing import Optional

# Monkey-patch LiveKit plugin BEFORE importing google plugin
import livekit_patch  # noqa: F401

from livekit.agents import (
    Agent,
    AgentSession,
    WorkerOptions,
    WorkerType,
    JobContext,
    cli,
)
from livekit.plugins import google

logger = logging.getLogger("cc-caller-agent")

# Shared state between agent and cc-caller (set from cc_caller.py)
transcript_queue: Optional[queue.Queue] = None
inject_fn = None  # Will be set to a function that injects text


SYSTEM_PROMPT = (
    "You are a voice relay between a user and a coding agent that runs in the background.\n"
    "You do NOT write code or answer technical questions yourself. You are a messenger.\n"
    "Your job:\n"
    "1) Collect what the user says.\n"
    "2) When the user gives a task, say 'Sending that to the agent now.' and wait.\n"
    "3) When you are told to say something, read it to the user exactly.\n"
    "4) After reading a response, ask 'What would you like to do next?'\n"
    "5) If the user asks a coding question, say 'Let me ask the agent.'\n"
    "6) If the user says 'end session', say 'Ending session.' and stop.\n"
    "NEVER make up information about code or the project.\n"
    "Always respond in English. Keep responses short."
)


async def entrypoint(ctx: JobContext):
    """Called by LiveKit when a user joins the room."""
    global inject_fn

    await ctx.connect()
    logger.info("Agent connected to room, waiting for participant...")

    model = google.realtime.RealtimeModel(
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        api_key=os.environ.get("GEMINI_API_KEY", ""),
        voice="Kore",
        temperature=0.7,
        instructions=SYSTEM_PROMPT,
    )

    session = AgentSession(llm=model)

    # Capture transcripts
    @session.on("user_input_transcribed")
    def on_transcript(ev):
        text = ev.transcript if hasattr(ev, 'transcript') else str(ev)
        if text.strip() and transcript_queue:
            logger.info(f"User said: {text}")
            transcript_queue.put(text)

    # Set up injection function
    async def _say(text: str):
        await session.say(text, allow_interruptions=True)

    inject_fn = lambda text: asyncio.run_coroutine_threadsafe(_say(text), asyncio.get_event_loop())

    agent = Agent(instructions=SYSTEM_PROMPT)
    await session.start(room=ctx.room, agent=agent)
    logger.info("Agent session started")


def run_worker():
    """Start the LiveKit agent worker."""
    opts = WorkerOptions(
        entrypoint_fnc=entrypoint,
        worker_type=WorkerType.ROOM,
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
        ws_url=os.environ["LIVEKIT_URL"],
    )
    cli.run_app(opts)
