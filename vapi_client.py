import requests

VAPI_CALL_URL = "https://api.vapi.ai/call"
DETAIL_MAX_CHARS = 10000

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
