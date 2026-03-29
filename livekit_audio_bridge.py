"""LiveKit audio bridge — pipes SIP audio ↔ Gemini bridge.

Joins a LiveKit room, subscribes to the user's audio track,
forwards it to the Gemini WebSocket, and plays Gemini's audio
back into the room.
"""

import asyncio
import base64
import json
import logging
import os
import queue
from typing import Optional

import websockets
from livekit import rtc

logger = logging.getLogger("livekit-audio-bridge")

GEMINI_WS_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"


class LiveKitAudioBridge:
    """Bridges LiveKit room audio ↔ Gemini Live WebSocket."""

    def __init__(self, gemini_api_key: str, system_prompt: str, transcript_queue: queue.Queue):
        self.gemini_api_key = gemini_api_key
        self.system_prompt = system_prompt
        self.transcript_queue = transcript_queue
        self._gemini_ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._audio_source: Optional[rtc.AudioSource] = None

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

    async def run(self, livekit_url: str, token: str):
        """Join room and bridge audio."""
        self._loop = asyncio.get_running_loop()

        # Connect to LiveKit room
        room = rtc.Room()
        await room.connect(livekit_url, token)
        logger.info(f"Joined LiveKit room: {room.name}")

        # Create audio source for playing Gemini audio back to room
        self._audio_source = rtc.AudioSource(24000, 1)  # Gemini outputs 24kHz mono
        track = rtc.LocalAudioTrack.create_audio_track("gemini-voice", self._audio_source)
        await room.local_participant.publish_track(track)
        logger.info("Published audio track to room")

        # Wait for SIP participant to join
        participant_audio = asyncio.Event()
        user_audio_stream = None

        @room.on("track_subscribed")
        def on_track(track: rtc.Track, publication, participant):
            nonlocal user_audio_stream
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                user_audio_stream = rtc.AudioStream(track)
                logger.info(f"Subscribed to audio from {participant.identity}")
                participant_audio.set()

        # Check if participant is already in room
        for p in room.remote_participants.values():
            for pub in p.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    user_audio_stream = rtc.AudioStream(pub.track)
                    participant_audio.set()

        print("Waiting for SIP caller to join...")
        await participant_audio.wait()
        print("SIP caller connected! Starting Gemini...")

        # Connect to Gemini
        gemini_url = f"{GEMINI_WS_URL}?key={self.gemini_api_key}"
        async with websockets.connect(gemini_url) as gemini_ws:
            self._gemini_ws = gemini_ws

            # Setup Gemini
            await gemini_ws.send(json.dumps({
                "setup": {
                    "model": "models/gemini-3.1-flash-live-preview",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "temperature": 0.1,
                        "topP": 0.1,
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": "Kore"}
                            }
                        }
                    },
                    "realtimeInputConfig": {
                        "automaticActivityDetection": {
                            "silenceDurationMs": 2000,
                            "prefixPaddingMs": 500
                        }
                    },
                    "systemInstruction": {
                        "parts": [{"text": self.system_prompt}]
                    },
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {}
                }
            }))

            msg = await gemini_ws.recv()
            if isinstance(msg, bytes):
                msg = msg.decode()
            data = json.loads(msg)
            if "setupComplete" in data:
                print("Gemini session ready!")

            # Run audio forwarding tasks
            await asyncio.gather(
                self._user_to_gemini(user_audio_stream, gemini_ws),
                self._gemini_to_user(gemini_ws),
                return_exceptions=True,
            )

        self._gemini_ws = None
        await room.disconnect()

    async def _user_to_gemini(self, audio_stream, gemini_ws):
        """Forward user's audio from LiveKit to Gemini."""
        try:
            async for frame_event in audio_stream:
                frame = frame_event.frame
                # Convert to base64 PCM
                pcm_bytes = frame.data.tobytes()
                b64 = base64.b64encode(pcm_bytes).decode()
                await gemini_ws.send(json.dumps({
                    "realtimeInput": {
                        "audio": {
                            "data": b64,
                            "mimeType": f"audio/pcm;rate={frame.sample_rate}"
                        }
                    }
                }))
        except Exception as e:
            logger.error(f"User→Gemini error: {e}")

    async def _gemini_to_user(self, gemini_ws):
        """Forward Gemini's audio to LiveKit room + capture transcripts."""
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

                # Flush user transcript on turn complete or agent speaking
                if sc.get("outputTranscription", {}).get("text"):
                    if user_buf.strip():
                        self.transcript_queue.put(user_buf.strip())
                        logger.info(f"Transcript: {user_buf.strip()[:80]}")
                        user_buf = ""

                # Audio → play into LiveKit room
                if sc.get("modelTurn", {}).get("parts"):
                    for part in sc["modelTurn"]["parts"]:
                        if part.get("inlineData", {}).get("data"):
                            audio_b64 = part["inlineData"]["data"]
                            audio_bytes = base64.b64decode(audio_b64)
                            # Create audio frame (24kHz mono 16-bit PCM)
                            frame = rtc.AudioFrame(
                                data=audio_bytes,
                                sample_rate=24000,
                                num_channels=1,
                                samples_per_channel=len(audio_bytes) // 2,
                            )
                            await self._audio_source.capture_frame(frame)

                if sc.get("turnComplete"):
                    if user_buf.strip():
                        self.transcript_queue.put(user_buf.strip())
                        logger.info(f"Transcript: {user_buf.strip()[:80]}")
                        user_buf = ""

        except Exception as e:
            logger.error(f"Gemini→User error: {e}")
            if user_buf.strip():
                self.transcript_queue.put(user_buf.strip())
