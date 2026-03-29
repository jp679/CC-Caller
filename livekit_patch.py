"""Monkey-patch LiveKit Google plugin to use 'audio' instead of deprecated 'media_chunks'.

LiveKit google plugin v1.3.6 sends media_chunks which Gemini 2.5+/3.1 rejects.
This patch replaces the push_audio method to use the new audio field.

Import this module BEFORE creating the RealtimeModel.
"""

from google.genai import types
from livekit.plugins.google.realtime.realtime_api import RealtimeSession

INPUT_AUDIO_SAMPLE_RATE = 16000

_original_push_audio = RealtimeSession.push_audio


def _patched_push_audio(self, frame):
    for f in self._resample_audio(frame):
        for nf in self._bstream.write(f.data.tobytes()):
            realtime_input = types.LiveClientRealtimeInput(
                audio=types.Blob(
                    data=nf.data.tobytes(),
                    mime_type=f"audio/pcm;rate={INPUT_AUDIO_SAMPLE_RATE}",
                )
            )
            self._send_client_event(realtime_input)


RealtimeSession.push_audio = _patched_push_audio
print("[patch] LiveKit Google plugin patched: media_chunks → audio")
