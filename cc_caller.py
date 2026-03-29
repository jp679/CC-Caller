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

import re

import uvicorn
from dotenv import load_dotenv

from summarizer import summarize_output
import requests as http_requests
from vapi_client import (
    build_assistant_config,
    build_inbound_assistant_config,
    configure_inbound_number,
    clear_inbound_number,
    create_call,
    create_web_call,
)
from webhook import create_app

import pathlib
load_dotenv(pathlib.Path(__file__).parent / ".env")


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


def name_to_uuid(name: str) -> str:
    """Convert a human-friendly session name to a deterministic UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"cc-caller.{name}"))


WORKER_SYSTEM_PROMPT = (
    "You are a coding assistant being orchestrated by cc-caller. "
    "Do your task and report what you did. "
    "NEVER run cc-caller, cc_caller.py, or any voice/phone/VAPI related commands. "
    "NEVER read or use .env files for making calls. "
    "NEVER attempt to call, phone, or contact the user — the orchestrator handles that. "
    "Just do the coding work and output your results."
)

# Files the worker should never touch
DISALLOWED_FILES = [
    "cc_caller.py", "vapi_client.py", "webhook.py", "summarizer.py",
    ".env", ".env.example", "cc-caller",
]


CLEAN_TRANSCRIPT_PROMPT = (
    "You are a transcript cleaner. Clean up the raw voice transcript below. "
    "Remove filler words, false starts, and repetitions. "
    "Preserve the user's EXACT intent — don't add, remove, or judge anything. "
    "If the user asked a question, keep it as a question. "
    "If the user said something short like 'did you get it?' or 'yes', keep it as-is. "
    "NEVER add commentary like 'no actionable instruction'. NEVER filter out messages. "
    "Output ONLY the cleaned text, nothing else."
)


def clean_transcript(raw_transcript: str) -> str:
    prompt = f"{CLEAN_TRANSCRIPT_PROMPT}\n\n---\n\n{raw_transcript}"
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
    )
    cleaned = result.stdout.strip()
    if not cleaned or result.returncode != 0:
        return raw_transcript
    return cleaned


def run_claude(instruction: str, session_id: str, session_name: str = "caller", is_first_run: bool = False) -> Tuple[str, str]:
    base_cmd = [
        "claude", "-p", "--output-format", "text",
        "--append-system-prompt", WORKER_SYSTEM_PROMPT,
        "--disallowedTools", "Bash(cc-caller*) Bash(python*cc_caller*) Bash(python*vapi*) Bash(curl*vapi*) Bash(curl*twilio*)",
        "--name", session_name,
    ]
    def _is_error(r):
        combined = (r.stdout + r.stderr).lower()
        return r.returncode != 0 or "api error: 400" in combined or "concurrency" in combined

    if is_first_run:
        cmd = base_cmd + ["--resume", session_id, instruction]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if _is_error(result):
            session_id = str(uuid.uuid4())
            cmd = base_cmd + ["--session-id", session_id, instruction]
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(f"New session: {session_id}")
    else:
        cmd = base_cmd + ["--resume", session_id, instruction]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if _is_error(result):
            print("Session error, starting fresh...")
            session_id = str(uuid.uuid4())
            cmd = base_cmd + ["--session-id", session_id, instruction]
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(f"New session: {session_id}")
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


def send_notification(title: str, message: str, url: str = "") -> None:
    ntfy_topic = os.getenv("NTFY_TOPIC", "cc-caller")
    headers = {"Title": title, "Priority": "urgent", "Tags": "phone"}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open Call, {url}"
    try:
        http_requests.post(
            f"https://ntfy.sh/{ntfy_topic}",
            data=message,
            headers=headers,
            timeout=5,
        )
    except Exception as e:
        print(f"Notification failed: {e}")


def start_tunnel(port: int, method: str) -> tuple:
    """Start a tunnel and return (public_url, cleanup_fn)."""
    if method == "cloudflare":
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
            stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        start = time.time()
        while time.time() - start < 15:
            line = proc.stderr.readline()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                return url, lambda: proc.terminate()
        proc.terminate()
        raise RuntimeError("Cloudflare tunnel failed to start")
    else:
        from pyngrok import ngrok
        tunnel = ngrok.connect(port, "http")
        url = tunnel.public_url
        return url, lambda: ngrok.disconnect(url)


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
    parser.add_argument("instruction", nargs="?", default=None, help="Initial instruction for Claude (omit for inbound mode)")
    parser.add_argument("--mode", choices=["always", "on-need", "interval"], default="always")
    parser.add_argument("--inbound", action="store_true", help="Wait for an inbound call instead of starting with an instruction")
    parser.add_argument("--web", action="store_true", help="Use VAPI web-based voice calls instead of phone")
    parser.add_argument("--gemini", action="store_true", help="Use Gemini Live for voice (free, no VAPI needed)")
    parser.add_argument("--live", action="store_true", help="Persistent Gemini voice session — no hang-up/call-back loop")
    parser.add_argument("--livekit", action="store_true", help="LiveKit + Gemini persistent session — server-side, SIP + browser")
    parser.add_argument("--sip", action="store_true", help="Use SIP for inbound calls (native phone ring via Linphone/Zoiper)")
    parser.add_argument("--tunnel", choices=["cloudflare", "ngrok"], default="cloudflare", help="Tunnel provider (default: cloudflare, free)")
    parser.add_argument("--session-id", type=str, default="caller", help="Claude session ID (default: 'caller', persists across restarts)")
    parser.add_argument("--new-session", action="store_true", help="Start a fresh Claude session instead of resuming")
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--port", type=int, default=int(os.getenv("WEBHOOK_PORT", "8765")))
    args = parser.parse_args()

    if args.live or args.livekit:
        args.inbound = True  # --live/--livekit imply inbound
    if not args.inbound and not args.instruction:
        parser.error("Either provide an instruction or use --inbound")

    mode = CallMode(args.mode)
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    api_key = os.getenv("VAPI_API_KEY", "")
    public_key = os.getenv("VAPI_PUBLIC_KEY", "")
    phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID", "")
    sip_phone_number_id = os.getenv("VAPI_SIP_PHONE_NUMBER_ID", "")
    customer_number = os.getenv("USER_PHONE_NUMBER", "")
    sip_uri = os.getenv("VAPI_SIP_URI", "sip:cc-caller@sip.vapi.ai")

    if args.gemini and not gemini_key:
        parser.error("--gemini requires GEMINI_API_KEY in .env")
    if args.sip and not (api_key and sip_phone_number_id):
        parser.error("--sip requires VAPI_API_KEY and VAPI_SIP_PHONE_NUMBER_ID in .env")
    if args.web and not public_key:
        parser.error("--web requires VAPI_PUBLIC_KEY in .env")
    if not args.gemini and not args.web and not args.sip and not api_key:
        parser.error("Phone mode requires VAPI_API_KEY in .env")

    # Clear stale assistants from previous crashed sessions
    if api_key:
        if sip_phone_number_id:
            try:
                clear_inbound_number(api_key, sip_phone_number_id)
            except Exception:
                pass
        if phone_number_id:
            try:
                clear_inbound_number(api_key, phone_number_id)
            except Exception:
                pass
        print("Cleared stale assistant configs")

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

    # Start tunnel
    public_url, cleanup_tunnel = start_tunnel(args.port, args.tunnel)
    webhook_url = f"{public_url}/webhook"
    print(f"Webhook listening at {webhook_url}")

    session_name = args.session_id
    if args.new_session:
        session_id = str(uuid.uuid4())
        print(f"Fresh Claude session: {session_id}")
    else:
        session_id = name_to_uuid(session_name)
        print(f"Claude session: {session_name} ({session_id})")
    instruction = args.instruction
    last_call_time = 0.0
    first_run = True

    if args.livekit:
        import asyncio as aio
        import livekit_agent
        from livekit_server import create_room, generate_participant_token

        gemini_key = os.getenv("GEMINI_API_KEY", "")
        livekit_url = os.environ["LIVEKIT_URL"]

        if not gemini_key:
            print("ERROR: --livekit requires GEMINI_API_KEY")
            return

        room_name = "cc-caller"

        # Set shared queue on the agent module
        livekit_agent.transcript_queue = transcript_queue

        # Create room and generate tokens
        loop = aio.new_event_loop()
        room_info = loop.run_until_complete(create_room(room_name))
        print(f"LiveKit room created: {room_info}")

        user_token = generate_participant_token(room_name, "user")
        agent_token = generate_participant_token(room_name, "agent")
        call_url = f"{public_url}/call-livekit?token={user_token}&url={livekit_url}"
        print(f"Browser join: {call_url}")
        send_notification(
            title="CC-Caller LiveKit Ready",
            message="Tap to join voice session",
            url=call_url,
        )
        loop.close()

        # Start the agent in a background thread
        def start_agent():
            loop = aio.new_event_loop()
            aio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    livekit_agent.run_agent(livekit_url, agent_token, gemini_key)
                )
            except Exception as e:
                print(f"Agent error: {e}")
                import traceback
                traceback.print_exc()

        agent_thread = threading.Thread(target=start_agent, daemon=True)
        agent_thread.start()
        print("LiveKit agent started — waiting for you to join")

        # Main loop: wait for transcripts, run Claude, inject results
        try:
            while True:
                print("\nWaiting for your voice input...")
                try:
                    transcript = transcript_queue.get(timeout=7200)
                except queue.Empty:
                    print("Session idle timeout.")
                    break

                print(f"Raw transcript: {transcript}")

                if is_termination(transcript):
                    print("Termination signal received.")
                    break

                print("Cleaning transcript...")
                instruction = clean_transcript(transcript)
                print(f"Instruction: {instruction}")

                # Progress pings while Claude works
                progress_stop = threading.Event()
                def push_progress():
                    while not progress_stop.is_set():
                        progress_stop.wait(15)
                        if not progress_stop.is_set() and livekit_agent.inject_fn:
                            livekit_agent.inject_fn("Still working on that...")
                            print("  [progress ping]")

                progress_thread = threading.Thread(target=push_progress, daemon=True)
                progress_thread.start()

                print("--- Running Claude ---")
                output, session_id = run_claude(instruction, session_id, session_name=session_name, is_first_run=first_run)
                first_run = False
                progress_stop.set()
                print(f"Output length: {len(output)} chars")

                # Inject result into Gemini conversation
                if livekit_agent.inject_fn:
                    livekit_agent.inject_fn(f"Here is what was done: {output}. What would you like to do next?")
                    print("Result injected into LiveKit session")
                else:
                    print("WARNING: inject_fn not available")

        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            cleanup_tunnel()
        return

    elif args.live:
        print("\n--- Live mode (persistent Gemini session) ---")
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            print("ERROR: --live requires GEMINI_API_KEY in .env")
            cleanup_tunnel()
            return

        with app.state.web_call_lock:
            app.state.live_gemini_config = {
                "geminiKey": gemini_key,
                "model": "gemini-3.1-flash-live-preview",
            }
        call_url = f"{public_url}/call-gemini-live"
        print(f"Open {call_url} to start a live session")
        send_notification(
            title="CC-Caller Live Ready",
            message="Tap to start a live voice session",
            url=call_url,
        )

        # Live loop: wait for transcript → run claude → push result via SSE → repeat
        try:
            while True:
                print("\nWaiting for your voice input...")
                try:
                    transcript = transcript_queue.get(timeout=7200)  # 2hr timeout
                except queue.Empty:
                    print("Session idle timeout. Exiting.")
                    break

                print(f"Raw transcript: {transcript}")

                if is_termination(transcript):
                    print("Termination signal received. Exiting.")
                    break

                print("Cleaning transcript...")
                instruction = clean_transcript(transcript)
                print(f"Instruction: {instruction}")

                # Push progress updates while Claude works
                progress_stop = threading.Event()
                def push_progress():
                    while not progress_stop.is_set():
                        progress_stop.wait(15)
                        if not progress_stop.is_set():
                            app.state.live_msg_counter += 1
                            app.state.live_messages.append({"type": "progress", "message": "Still working on that...", "id": app.state.live_msg_counter})
                            print("  [progress ping]")

                progress_thread = threading.Thread(target=push_progress, daemon=True)
                progress_thread.start()

                print("--- Running Claude ---")
                output, session_id = run_claude(instruction, session_id, session_name=session_name, is_first_run=first_run)
                first_run = False
                progress_stop.set()
                print(f"Output length: {len(output)} chars")

                # Push result to Gemini via SSE
                app.state.live_msg_counter += 1
                app.state.live_messages.append({"type": "result", "message": output, "id": app.state.live_msg_counter})
                # Keep only last 20 messages
                if len(app.state.live_messages) > 20:
                    app.state.live_messages = app.state.live_messages[-20:]
                print("Result pushed to live session")

        except KeyboardInterrupt:
            print("\nInterrupted. Exiting.")
        finally:
            cleanup_tunnel()
        return

    elif args.inbound and args.gemini:
        print("\n--- Gemini Live inbound mode ---")
        gemini_system = (
            "You are a task intake assistant for a coding agent.\n"
            "The user is calling to give you a task. Your job:\n"
            "1) Greet them briefly and ask what they'd like the coding assistant to work on.\n"
            "2) Listen to their task description.\n"
            "3) Confirm what you heard back to them in one sentence.\n"
            "4) Say 'Got it, starting now.' and stop talking.\n"
            "Keep it short and natural."
        )
        with app.state.web_call_lock:
            app.state.pending_gemini_call = {
                "systemPrompt": gemini_system,
                "geminiKey": gemini_key,
                "model": "gemini-3.1-flash-live-preview",
            }
        call_url = f"{public_url}/call-gemini"
        print(f"Open {call_url} to start a task")
        send_notification(
            title="CC-Caller Ready",
            message="Tap to connect and give your task",
            url=call_url,
        )
        try:
            instruction = transcript_queue.get()
        except KeyboardInterrupt:
            print("\nInterrupted while waiting. Exiting.")
            cleanup_tunnel()
            return
        print(f"You said: {instruction}")
        if is_termination(instruction):
            print("Termination signal received. Exiting.")
            cleanup_tunnel()
            return

    elif args.inbound and args.sip:
        print("\n--- SIP inbound mode ---")
        inbound_config = build_inbound_assistant_config(webhook_url)
        configure_inbound_number(api_key, sip_phone_number_id, inbound_config)
        print(f"Dial {sip_uri} from Linphone/Zoiper to start a task. Waiting...")
        send_notification(
            title="CC-Caller Ready",
            message="Tap to connect",
            url=sip_uri,
        )
        try:
            instruction = transcript_queue.get()
        except KeyboardInterrupt:
            print("\nInterrupted while waiting. Exiting.")
            clear_inbound_number(api_key, sip_phone_number_id)
            cleanup_tunnel()
            return
        print(f"You said: {instruction}")
        if is_termination(instruction):
            print("Termination signal received. Exiting.")
            clear_inbound_number(api_key, sip_phone_number_id)
            cleanup_tunnel()
            return

    elif args.inbound and args.web:
        print("\n--- Web inbound mode ---")
        inbound_config = build_inbound_assistant_config(webhook_url)
        with app.state.web_call_lock:
            app.state.pending_web_call = {
                "assistantConfig": inbound_config,
                "publicKey": public_key,
            }
        call_url = f"{public_url}/call"
        print(f"Open {call_url} to start a task")
        send_notification(
            title="CC-Caller Ready",
            message="Tap to connect and give your task",
            url=call_url,
        )
        try:
            instruction = transcript_queue.get()
        except KeyboardInterrupt:
            print("\nInterrupted while waiting. Exiting.")
            cleanup_tunnel()
            return
        print(f"You said: {instruction}")
        if is_termination(instruction):
            print("Termination signal received. Exiting.")
            cleanup_tunnel()
            return

    elif args.inbound:
        print("\n--- Inbound mode: configuring phone number ---")
        inbound_config = build_inbound_assistant_config(webhook_url)
        configure_inbound_number(api_key, phone_number_id, inbound_config)
        print(f"Call your VAPI number to start a task. Waiting...")
        try:
            instruction = transcript_queue.get()
        except KeyboardInterrupt:
            print("\nInterrupted while waiting. Exiting.")
            clear_inbound_number(api_key, phone_number_id)
            cleanup_tunnel()
            return
        print(f"You said: {instruction}")
        if is_termination(instruction):
            print("Termination signal received. Exiting.")
            clear_inbound_number(api_key, phone_number_id)
            cleanup_tunnel()
            return

    # Clean the initial instruction if it came from voice (inbound)
    if args.inbound and instruction:
        print("Cleaning transcript...")
        instruction = clean_transcript(instruction)
        print(f"Instruction: {instruction}")

    try:
        while True:
            print(f"\n--- Running Claude ---")
            print(f"Instruction: {instruction[:100]}...")
            output, session_id = run_claude(instruction, session_id, session_name=session_name, is_first_run=first_run)
            first_run = False
            print(f"Output length: {len(output)} chars")

            if not should_call(mode, output, last_call_time, args.interval_minutes):
                print("No call needed, continuing autonomously...")
                instruction = "Continue working."
                continue

            print("Summarizing for voice call...")
            summary_data = summarize_output(output)

            assistant_config = build_assistant_config(
                summary=summary_data["summary"],
                detail=summary_data["detail"],
                webhook_url=webhook_url,
            )

            if args.gemini:
                gemini_system = (
                    "You are a voice relay for a coding assistant.\n"
                    "Read the SUMMARY below to the user. They can interrupt anytime.\n"
                    "Rules:\n"
                    "- Read ONLY the summary. Do NOT read the detail unless asked.\n"
                    "- If they ask for more detail, read from the DETAIL section.\n"
                    "- Collect any instructions they give.\n"
                    "- After collecting instructions, ask: 'Should I keep working and call you back, or are we done?'\n"
                    "- If they want to continue, say 'On it, I'll call back when done.' and stop talking.\n"
                    "- If they want to stop, say 'Got it, ending session.' and stop talking.\n"
                    "Do NOT answer coding questions yourself. Keep responses short.\n\n"
                    f"SUMMARY:\n{summary_data['summary']}\n\n"
                    f"DETAIL:\n{summary_data['detail']}"
                )
                print("Preparing Gemini call...")
                with app.state.web_call_lock:
                    app.state.pending_gemini_call = {
                        "systemPrompt": gemini_system,
                        "geminiKey": gemini_key,
                        "model": "gemini-3.1-flash-live-preview",
                    }
                call_url = f"{public_url}/call-gemini"
                print(f"Gemini call ready at {call_url}")
                send_notification(
                    title="CC-Caller Update",
                    message=summary_data["summary"][:200],
                    url=call_url,
                )
            elif args.sip:
                print("Preparing SIP callback...")
                configure_inbound_number(api_key, sip_phone_number_id, assistant_config)
                print(f"Dial {sip_uri} from Linphone for the update")
                send_notification(
                    title="CC-Caller Update",
                    message=summary_data["summary"][:200],
                    url=sip_uri,
                )
            elif args.web:
                print("Preparing web call...")
                with app.state.web_call_lock:
                    app.state.pending_web_call = {
                        "assistantConfig": assistant_config,
                        "publicKey": public_key,
                    }
                call_url = f"{public_url}/call"
                print(f"Web call ready at {call_url}")
                send_notification(
                    title="CC-Caller Update",
                    message=summary_data["summary"][:200],
                    url=call_url,
                )
            else:
                print(f"Calling {customer_number}...")
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
                print("No response after 10 minutes. Retrying...")
                if args.gemini:
                    send_notification(
                        title="CC-Caller: Still waiting",
                        message="Tap to connect",
                        url=f"{public_url}/call-gemini",
                    )
                elif args.sip:
                    send_notification(
                        title="CC-Caller: Still waiting",
                        message="Tap to connect",
                        url=sip_uri,
                    )
                elif args.web:
                    send_notification(
                        title="CC-Caller: Still waiting",
                        message="Tap to connect",
                        url=f"{public_url}/call",
                    )
                else:
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

            print(f"Raw transcript: {transcript}")

            if is_termination(transcript):
                print("Termination signal received. Exiting.")
                break

            print("Cleaning transcript...")
            instruction = clean_transcript(transcript)
            print(f"Instruction: {instruction}")

    except KeyboardInterrupt:
        print("\nInterrupted. Exiting.")
    finally:
        if args.inbound or args.sip:
            print("Clearing inbound config...")
            try:
                clear_inbound_number(api_key, phone_number_id)
            except Exception:
                pass
            if args.sip:
                try:
                    clear_inbound_number(api_key, sip_phone_number_id)
                except Exception:
                    pass
        cleanup_tunnel()


if __name__ == "__main__":
    main()
