from typing import Optional

import requests

VAPI_CALL_URL = "https://api.vapi.ai/call"
VAPI_WEB_CALL_URL = "https://api.vapi.ai/call/web"
VAPI_PHONE_NUMBER_URL = "https://api.vapi.ai/phone-number"
DETAIL_MAX_CHARS = 10000

INBOUND_SYSTEM_PROMPT = (
    "You are the voice interface for a coding assistant. The user IS talking to the assistant — you ARE the assistant.\n"
    "Your job:\n"
    "1) Greet them: 'Hey, what would you like me to work on?'\n"
    "2) Listen to their task.\n"
    "3) Repeat it back briefly to confirm.\n"
    "4) Say 'Got it, starting now.' then use the endCall tool.\n"
    "Speak as 'I' — never refer to 'the agent' or 'the assistant' as a third party. YOU are the assistant.\n"
    "Keep it short and natural.\n"
    "IMPORTANT: Always respond in the same language the user is speaking."
)

SYSTEM_PROMPT_TEMPLATE = (
    "You are the voice interface for a coding assistant. The user IS talking to the assistant — you ARE the assistant.\n"
    "Read the SUMMARY below to the user. They can interrupt you at any time.\n"
    "Rules:\n"
    "- Read ONLY the summary. Do NOT read the detail unless the user asks.\n"
    "- If they ask for more detail, read from the DETAIL section.\n"
    "- Collect any instructions they give. Their words will be sent directly as your next task.\n"
    "- After collecting instructions, ask: 'Should I keep working and call you back, or are we done?'\n"
    "- If they want to continue, say 'On it, I'll call back when done.' then use the endCall tool.\n"
    "- If they want to stop, say 'Got it, ending session.' then use the endCall tool.\n"
    "Speak as 'I' — never say 'the agent' or 'the coding assistant'. YOU are the assistant.\n"
    "Do NOT add your own interpretation to what the user says. Just collect their exact words.\n"
    "Keep responses short. No filler.\n"
    "IMPORTANT: Always respond in the same language the user is speaking.\n\n"
    "SUMMARY:\n{summary}\n\n"
    "DETAIL:\n{detail}"
)


def build_assistant_config(
    summary: str,
    detail: str,
    webhook_url: str,
) -> dict:
    truncated_detail = detail[:DETAIL_MAX_CHARS]
    system_content = SYSTEM_PROMPT_TEMPLATE.format(
        summary=summary, detail=truncated_detail
    )

    return {
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "multi",
            "smartFormat": True,
        },
        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "system", "content": system_content}
            ],
            "tools": [
                {"type": "endCall"}
            ],
        },
        "firstMessage": "Hey, got an update for you.",
        "voice": {
            "provider": "11labs",
            "voiceId": "21m00Tcm4TlvDq8ikWAM",
            "model": "eleven_multilingual_v2",
            "speed": 1.2,
        },
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.2,
            "backoffSeconds": 1,
        },
        "backgroundSound": "off",
        "serverUrl": webhook_url,
    }


def build_inbound_assistant_config(webhook_url: str) -> dict:
    return {
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "multi",
            "smartFormat": True,
        },
        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "system", "content": INBOUND_SYSTEM_PROMPT}
            ],
            "tools": [
                {"type": "endCall"}
            ],
        },
        "firstMessage": "Hey, what would you like me to work on?",
        "voice": {
            "provider": "11labs",
            "voiceId": "21m00Tcm4TlvDq8ikWAM",
            "model": "eleven_multilingual_v2",
            "speed": 1.2,
        },
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.2,
            "backoffSeconds": 1,
        },
        "backgroundSound": "off",
        "serverUrl": webhook_url,
    }


VAPI_ASSISTANT_URL = "https://api.vapi.ai/assistant"
INBOUND_ASSISTANT_NAME = "CC-Caller Inbound"


def _find_inbound_assistant(api_key: str) -> Optional[str]:
    response = requests.get(
        VAPI_ASSISTANT_URL,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    response.raise_for_status()
    for assistant in response.json():
        if assistant.get("name") == INBOUND_ASSISTANT_NAME:
            return assistant["id"]
    return None


def configure_inbound_number(
    api_key: str,
    phone_number_id: str,
    assistant_config: dict,
) -> str:
    assistant_config["name"] = INBOUND_ASSISTANT_NAME

    # Update existing or create new saved assistant
    assistant_id = _find_inbound_assistant(api_key)
    if assistant_id:
        response = requests.patch(
            f"{VAPI_ASSISTANT_URL}/{assistant_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=assistant_config,
        )
    else:
        response = requests.post(
            VAPI_ASSISTANT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=assistant_config,
        )
    if not response.ok:
        print(f"VAPI error {response.status_code}: {response.text}")
    response.raise_for_status()
    assistant_id = response.json()["id"]

    # Link assistant to phone number
    response = requests.patch(
        f"{VAPI_PHONE_NUMBER_URL}/{phone_number_id}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"assistantId": assistant_id},
    )
    if not response.ok:
        print(f"VAPI error {response.status_code}: {response.text}")
    response.raise_for_status()
    return assistant_id


def clear_inbound_number(
    api_key: str,
    phone_number_id: str,
) -> None:
    requests.patch(
        f"{VAPI_PHONE_NUMBER_URL}/{phone_number_id}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"assistantId": None},
    )


def create_web_call(
    public_key: str,
    assistant_config: dict,
) -> dict:
    response = requests.post(
        VAPI_WEB_CALL_URL,
        headers={
            "Authorization": f"Bearer {public_key}",
            "Content-Type": "application/json",
        },
        json={
            "assistant": assistant_config,
        },
    )
    if not response.ok:
        print(f"VAPI error {response.status_code}: {response.text}")
    response.raise_for_status()
    return response.json()


def create_call(
    api_key: str,
    phone_number_id: str,
    customer_number: str,
    assistant_config: dict,
) -> dict:
    response = requests.post(
        VAPI_CALL_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "phoneNumberId": phone_number_id,
            "customer": {"number": customer_number},
            "assistant": assistant_config,
        },
    )
    if not response.ok:
        print(f"VAPI error {response.status_code}: {response.text}")
    response.raise_for_status()
    return response.json()
