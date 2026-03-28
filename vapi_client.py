import requests

VAPI_CALL_URL = "https://api.vapi.ai/call"
VAPI_PHONE_NUMBER_URL = "https://api.vapi.ai/phone-number"
DETAIL_MAX_CHARS = 10000

INBOUND_SYSTEM_PROMPT = (
    "You are a task intake assistant for a coding agent.\n"
    "The user is calling to give you a task. Your job:\n"
    "1) Greet them briefly and ask what they'd like the coding assistant to work on.\n"
    "2) Listen to their task description.\n"
    "3) Confirm what you heard back to them in one sentence.\n"
    "4) Say 'Got it, starting now.' then use the endCall tool to hang up.\n"
    "Keep it short and natural. No filler."
)

SYSTEM_PROMPT_TEMPLATE = (
    "You are a voice relay for a coding assistant.\n"
    "After your greeting, read the SUMMARY below. The user can interrupt you at any time.\n"
    "Rules:\n"
    "- Read ONLY the summary. Do NOT read the detail unless the user asks.\n"
    "- If they ask for more detail, read from the DETAIL section.\n"
    "- Collect any instructions they give.\n"
    "- After collecting instructions, ask: 'Should I keep working and call you back, or are we done for now?'\n"
    "- If they want to continue, say 'On it, I'll call back when done.' then use the endCall tool to hang up.\n"
    "- If they want to stop, say 'Got it, ending session.' then use the endCall tool to hang up.\n"
    "Do NOT answer coding questions yourself. Keep responses short. No filler.\n\n"
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
            "speed": 1.2,
        },
        "endCallPhrases": ["go ahead", "that's all", "stop", "we're done"],
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


def configure_inbound_number(
    api_key: str,
    phone_number_id: str,
    assistant_config: dict,
) -> dict:
    response = requests.patch(
        f"{VAPI_PHONE_NUMBER_URL}/{phone_number_id}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "assistantId": None,
            "assistant": assistant_config,
        },
    )
    if not response.ok:
        print(f"VAPI error {response.status_code}: {response.text}")
    response.raise_for_status()
    return response.json()


def clear_inbound_number(
    api_key: str,
    phone_number_id: str,
) -> dict:
    response = requests.patch(
        f"{VAPI_PHONE_NUMBER_URL}/{phone_number_id}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "assistantId": None,
            "assistant": None,
        },
    )
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
