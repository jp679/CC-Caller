import argparse
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from enum import Enum
from typing import Optional, Tuple

import uvicorn
from dotenv import load_dotenv
from pyngrok import ngrok

from summarizer import summarize_output
from vapi_client import build_assistant_config, create_call
from webhook import create_app

load_dotenv()


class CallMode(Enum):
    ALWAYS = "always"
    ON_NEED = "on-need"
    INTERVAL = "interval"


NEED_INPUT_PROMPT = (
    "Read this output and answer with ONLY 'YES' or 'NO': "
    "does this require user input, a decision, or clarification to continue?"
)

TERMINATION_CHECK_PROMPT = (
    "Read this transcript from a phone call and answer with ONLY 'YES' or 'NO': "
    "is the user signaling they want to END the session and stop receiving calls? "
    "Examples of YES: 'stop', 'we're done', 'that's it for today', 'I'm finished', "
    "'stop calling', 'end session', 'the task is finished'. "
    "Examples of NO: 'go ahead', 'continue', 'work on X next', 'sounds good'."
)


def run_claude(instruction: str, session_id: Optional[str]) -> Tuple[str, str]:
    cmd = ["claude", "-p", "--output-format", "text"]
    if session_id:
        cmd.extend(["--resume", session_id])
    else:
        session_id = str(uuid.uuid4())
        cmd.extend(["--session-id", session_id])
    cmd.append(instruction)

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout, session_id


def check_needs_input(claude_output: str) -> bool:
    result = subprocess.run(
        ["claude", "-p", NEED_INPUT_PROMPT],
        input=claude_output,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().upper().startswith("YES")


def should_call(
    mode: CallMode,
    claude_output: str,
    last_call_time: float,
    interval_minutes: int,
) -> bool:
    if mode == CallMode.ALWAYS:
        return True

    if mode == CallMode.ON_NEED:
        return check_needs_input(claude_output)

    if mode == CallMode.INTERVAL:
        elapsed = time.time() - last_call_time
        if elapsed >= interval_minutes * 60:
            return True
        return check_needs_input(claude_output)

    return True


def is_termination(transcript: str) -> bool:
    result = subprocess.run(
        ["claude", "-p", TERMINATION_CHECK_PROMPT],
        input=transcript,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().upper().startswith("YES")


def main():
    parser = argparse.ArgumentParser(description="CC-Caller: Voice-driven Claude Code loop")
    parser.add_argument("instruction", help="Initial instruction for Claude")
    parser.add_argument("--mode", choices=["always", "on-need", "interval"], default="always")
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--port", type=int, default=int(os.getenv("WEBHOOK_PORT", "8765")))
    args = parser.parse_args()

    mode = CallMode(args.mode)
    api_key = os.environ["VAPI_API_KEY"]
    phone_number_id = os.environ["VAPI_PHONE_NUMBER_ID"]
    customer_number = os.environ["USER_PHONE_NUMBER"]

    # Start webhook server
    transcript_queue = queue.Queue()
    app = create_app(transcript_queue)

    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": args.port, "log_level": "info"},
        daemon=True,
    )
    server_thread.start()

    # Start ngrok tunnel
    public_url = ngrok.connect(args.port, "http").public_url
    webhook_url = f"{public_url}/webhook"
    print(f"Webhook listening at {webhook_url}")

    session_id = None
    instruction = args.instruction
    last_call_time = 0.0

    try:
        while True:
            print(f"\n--- Running Claude ---")
            print(f"Instruction: {instruction[:100]}...")
            output, session_id = run_claude(instruction, session_id)
            print(f"Output length: {len(output)} chars")

            if not should_call(mode, output, last_call_time, args.interval_minutes):
                print("No call needed, continuing autonomously...")
                instruction = "Continue working."
                continue

            print("Summarizing for voice call...")
            summary_data = summarize_output(output)

            print(f"Calling {customer_number}...")
            assistant_config = build_assistant_config(
                summary=summary_data["summary"],
                detail=summary_data["detail"],
                webhook_url=webhook_url,
            )
            create_call(
                api_key=api_key,
                phone_number_id=phone_number_id,
                customer_number=customer_number,
                assistant_config=assistant_config,
            )
            last_call_time = time.time()

            print("Waiting for your response...")
            try:
                transcript = transcript_queue.get(timeout=600)
            except queue.Empty:
                print("No response after 10 minutes. Retrying call once...")
                create_call(
                    api_key=api_key,
                    phone_number_id=phone_number_id,
                    customer_number=customer_number,
                    assistant_config=assistant_config,
                )
                try:
                    transcript = transcript_queue.get(timeout=600)
                except queue.Empty:
                    print("Still no response. Pausing. Restart to continue.")
                    break

            print(f"You said: {transcript}")

            if is_termination(transcript):
                print("Termination signal received. Exiting.")
                break

            instruction = transcript

    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
    finally:
        ngrok.disconnect(public_url)


if __name__ == "__main__":
    main()
