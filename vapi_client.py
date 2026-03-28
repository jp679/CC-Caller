import requests

VAPI_CALL_URL = "https://api.vapi.ai/call"
DETAIL_MAX_CHARS = 10000

SYSTEM_PROMPT_TEMPLATE = (
    "You are a voice relay for a coding assistant. Your job:\n"
    "1) Read the summary to the user.\n"
    "2) If they ask for more detail, provide it from the DETAIL section below.\n"
    "3) Collect any instructions they give.\n"
    "4) When they say 'go ahead', 'that's all', or hang up, "
    "say 'On it, I'll call back when done.' and end the call.\n"
    "Do NOT attempt to answer coding questions yourself. "
    "Stay concise and natural.\n\n"
    "DETAIL:\n{detail}"
)


def build_assistant_config(
    summary: str,
    detail: str,
    webhook_url: str,
) -> dict:
    truncated_detail = detail[:DETAIL_MAX_CHARS]
    system_content = SYSTEM_PROMPT_TEMPLATE.format(detail=truncated_detail)

    return {
        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-5-20250514",
            "messages": [
                {"role": "system", "content": system_content}
            ],
        },
        "firstMessage": summary,
        "voice": {
            "provider": "11labs",
            "voiceId": "21m00Tcm4TlvDq8ikWAM",
        },
        "endCallPhrases": ["go ahead", "that's all", "stop", "we're done"],
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
    response.raise_for_status()
    return response.json()
