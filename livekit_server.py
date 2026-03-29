"""LiveKit room and token management for CC-Caller."""

import os
from livekit.api import LiveKitAPI, CreateRoomRequest, AccessToken, VideoGrants


def get_livekit_config():
    return {
        "url": os.environ["LIVEKIT_URL"],
        "api_key": os.environ["LIVEKIT_API_KEY"],
        "api_secret": os.environ["LIVEKIT_API_SECRET"],
    }


async def create_room(room_name: str = "cc-caller") -> dict:
    config = get_livekit_config()
    api = LiveKitAPI(
        url=config["url"],
        api_key=config["api_key"],
        api_secret=config["api_secret"],
    )
    try:
        room = await api.room.create_room(
            CreateRoomRequest(name=room_name, empty_timeout=7200)
        )
        return {"name": room.name, "sid": room.sid}
    finally:
        await api.aclose()


def generate_participant_token(
    room_name: str = "cc-caller",
    participant_name: str = "user",
) -> str:
    config = get_livekit_config()
    token = AccessToken(
        api_key=config["api_key"],
        api_secret=config["api_secret"],
    )
    token.with_identity(participant_name)
    token.with_name(participant_name)
    token.with_grants(VideoGrants(
        room_join=True,
        room=room_name,
    ))
    return token.to_jwt()
