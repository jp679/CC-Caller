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
    "Always respond in English."
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

PERSISTENT_SIP_SYSTEM_PROMPT = (
    "You are a voice relay between the user and a coding agent.\n"
    "You have ZERO knowledge of any code, project, or files.\n"
    "RULES:\n"
    "- Greet: 'Hey, what would you like me to work on?'\n"
    "- When the user gives ANY task, question, or instruction about code/files/project: "
    "IMMEDIATELY call the askCodingAgent tool with their request. Say 'Let me check on that.' while waiting.\n"
    "- When askCodingAgent returns: read the result to the user, then ask 'What next?'\n"
    "- NEVER answer coding questions yourself. ALWAYS use the tool.\n"
    "- If user says 'end session': say 'Goodbye.' and use endCall.\n"
    "Be brief. English only."
)


def build_persistent_sip_config(webhook_url: str) -> dict:
    return {
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en",
            "smartFormat": True,
            "keywords": [
                "JSON:2", "API:2", "deploy:2", "compile:2", "TypeScript:2",
                "JavaScript:2", "Python:2", "React:2", "Node:2", "npm:2",
                "git:2", "commit:2", "endpoint:2", "webhook:2", "database:2",
                "schema:2", "Docker:2", "frontend:2", "backend:2",
                "HTML:2", "CSS:2", "localhost:2", "Claude:2",
            ],
        },
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": PERSISTENT_SIP_SYSTEM_PROMPT}
            ],
            "tools": [
                {"type": "endCall"},
                {
                    "type": "function",
                    "function": {
                        "name": "askCodingAgent",
                        "description": "Send a task or question to the coding agent. Use this for ANY request about code, files, projects, or technical topics.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": "The user's task or question to send to the coding agent"
                                }
                            },
                            "required": ["task"]
                        }
                    },
                    "server": {
                        "url": f"{webhook_url.replace('/webhook', '')}/tool-call"
                    }
                }
            ],
        },
        "firstMessage": "Hey, what would you like me to work on?",
        "voice": {
            "provider": "11labs",
            "voiceId": "21m00Tcm4TlvDq8ikWAM",
            "model": "eleven_turbo_v2_5",
            "speed": 1.2,
        },
        "endCallPhrases": ["end session", "goodbye", "we're done"],
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.2,
            "backoffSeconds": 1,
        },
        "backgroundSound": "off",
        "serverUrl": webhook_url,
    }


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
            "language": "en",
            "smartFormat": True,
            "keywords": [
                "JSON:2", "API:2", "deploy:2", "compile:2", "TypeScript:2",
                "JavaScript:2", "Python:2", "React:2", "Node:2", "npm:2",
                "git:2", "commit:2", "push:2", "merge:2", "branch:2",
                "endpoint:2", "webhook:2", "REST:2", "GraphQL:2", "SQL:2",
                "database:2", "schema:2", "Docker:2", "Kubernetes:2",
                "AWS:2", "CICD:2", "pipeline:2", "lint:2", "refactor:2",
                "debug:2", "localhost:2", "frontend:2", "backend:2",
                "component:2", "module:2", "package:2", "HTML:2", "CSS:2",
                "Tailwind:2", "NextJS:2", "Vite:2", "FastAPI:2", "Flask:2",
                "Express:2", "MongoDB:2", "Postgres:2", "Redis:2",
                "Cloudflare:2", "ngrok:2", "VAPI:2", "Gemini:2", "Claude:2",
                "SIP:2", "WebRTC:2", "WebSocket:2", "OAuth:2", "JWT:2",
                "async:2", "await:2",
            ],
        },
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
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
            "model": "eleven_turbo_v2_5",
            "speed": 1.2,
        },
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.2,
            "backoffSeconds": 1,
        },
        "endCallPhrases": ["go ahead", "that's all", "stop", "we're done"],
        "backgroundSound": "off",
        "serverUrl": webhook_url,
    }


def build_inbound_assistant_config(webhook_url: str) -> dict:
    return {
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en",
            "smartFormat": True,
            "keywords": [
                "JSON:2", "API:2", "deploy:2", "compile:2", "TypeScript:2",
                "JavaScript:2", "Python:2", "React:2", "Node:2", "npm:2",
                "git:2", "commit:2", "push:2", "merge:2", "branch:2",
                "endpoint:2", "webhook:2", "REST:2", "GraphQL:2", "SQL:2",
                "database:2", "schema:2", "Docker:2", "Kubernetes:2",
                "AWS:2", "CICD:2", "pipeline:2", "lint:2", "refactor:2",
                "debug:2", "localhost:2", "frontend:2", "backend:2",
                "component:2", "module:2", "package:2", "HTML:2", "CSS:2",
                "Tailwind:2", "NextJS:2", "Vite:2", "FastAPI:2", "Flask:2",
                "Express:2", "MongoDB:2", "Postgres:2", "Redis:2",
                "Cloudflare:2", "ngrok:2", "VAPI:2", "Gemini:2", "Claude:2",
                "SIP:2", "WebRTC:2", "WebSocket:2", "OAuth:2", "JWT:2",
                "async:2", "await:2",
            ],
        },
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
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
            "model": "eleven_turbo_v2_5",
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
