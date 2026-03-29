"""LiveKit + Gemini Live agent for CC-Caller.

Manually connects to a LiveKit room and sets up Gemini via AgentSession.
Waits for a user participant, then starts the voice session.
"""

import asyncio
import logging
import os
import queue
from typing import Optional

# Monkey-patch LiveKit plugin BEFORE importing google plugin
import livekit_patch  # noqa: F401

from livekit import rtc
from livekit.agents import AgentSession, Agent
from livekit.plugins import google

logger = logging.getLogger("cc-caller-agent")

# Shared state (set from cc_caller.py)
transcript_queue: Optional[queue.Queue] = None
inject_fn = None

SYSTEM_PROMPT = (
    "You are a voice relay between a user and a coding agent that runs in the background.\n"
    "You do NOT write code or answer technical questions yourself. You are a messenger.\n"
    "Your job:\n"
    "1) Greet the user and ask what they'd like to work on.\n"
    "2) When the user gives a task, say 'Sending that to the agent now.' and wait.\n"
    "3) When you are told to say something, read it to the user exactly.\n"
    "4) After reading a response, ask 'What would you like to do next?'\n"
    "5) If the user asks a coding question, say 'Let me ask the agent.'\n"
    "6) If the user says 'end session', say 'Ending session.' and stop.\n"
    "NEVER make up information about code or the project.\n"
    "Always respond in English. Keep responses short."
)


async def run_agent(livekit_url: str, agent_token: str, gemini_api_key: str):
    """Connect to room, wait for user, start Gemini session."""
    global inject_fn

    room = rtc.Room()
    await room.connect(livekit_url, agent_token)
    logger.info(f"Agent connected to room: {room.name}")

    # Wait for a user participant to join
    user_joined = asyncio.Event()

    @room.on("participant_connected")
    def on_participant(participant: rtc.RemoteParticipant):
        logger.info(f"Participant joined: {participant.identity}")
        user_joined.set()

    # Check if someone is already in the room
    if len(room.remote_participants) > 0:
        user_joined.set()

    print("Waiting for user to join room...")
    await user_joined.wait()
    print("User joined! Starting Gemini session...")

    # Small delay to let tracks publish
    await asyncio.sleep(2)

    model = google.realtime.RealtimeModel(
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        api_key=gemini_api_key,
        voice="Kore",
        temperature=0.7,
        instructions=SYSTEM_PROMPT,
    )

    session = AgentSession(llm=model)

    # Capture user transcripts
    @session.on("user_input_transcribed")
    def on_transcript(ev):
        text = ev.transcript if hasattr(ev, 'transcript') else str(ev)
        if text.strip() and transcript_queue:
            logger.info(f"User said: {text}")
            transcript_queue.put(text)

    # Set up injection function
    loop = asyncio.get_running_loop()

    async def _say(text: str):
        try:
            await session.say(text, allow_interruptions=True)
        except Exception as e:
            logger.error(f"Say error: {e}")

    def _inject(text: str):
        asyncio.run_coroutine_threadsafe(_say(text), loop)

    inject_fn = _inject

    # Start the agent session in the room
    await session.start(
        room=room,
        agent=Agent(instructions=SYSTEM_PROMPT),
    )
    print("Gemini session active!")

    # Wait until room disconnects
    disconnect = asyncio.Event()

    @room.on("disconnected")
    def on_dc():
        disconnect.set()

    await disconnect.wait()
    await room.disconnect()
