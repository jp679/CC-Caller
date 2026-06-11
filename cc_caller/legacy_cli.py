import argparse
import os
import queue
import threading
import time
import uuid
from enum import Enum

import uvicorn
from dotenv import load_dotenv

from cc_caller.summarizer import summarize_output
from cc_caller.vapi.client import (
    build_assistant_config,
    build_inbound_assistant_config,
    configure_inbound_number,
    clear_inbound_number,
    create_call,
)
from cc_caller.vapi.webhook import create_app

import pathlib
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")

from cc_caller.claude_worker import (
    name_to_uuid, run_claude, clean_transcript, check_needs_input,
    is_termination, log_interaction,
)
from cc_caller.push import ensure_vapid_keys, send_web_push
from cc_caller.notify import send_notification
from cc_caller.tunnel import start_tunnel


class CallMode(Enum):
    ALWAYS = "always"
    ON_NEED = "on-need"
    INTERVAL = "interval"


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



def main():
    parser = argparse.ArgumentParser(description="CC-Caller: Voice-driven Claude Code loop")
    parser.add_argument("instruction", nargs="?", default=None, help="Initial instruction for Claude (omit for inbound mode)")
    parser.add_argument("--mode", choices=["always", "on-need", "interval"], default="always")
    parser.add_argument("--inbound", action="store_true", help="Wait for an inbound call instead of starting with an instruction")
    parser.add_argument("--pwa", action="store_true", help="Serve a PWA — browser voice call with push notifications, no app install")
    parser.add_argument("--sip", action="store_true", help="Use SIP for inbound calls (native phone ring via Linphone/Zoiper)")
    parser.add_argument("--tunnel", choices=["cloudflare", "ngrok"], default="cloudflare", help="Tunnel provider (default: cloudflare, free)")
    parser.add_argument("--tunnel-url", type=str, default=None, help="Fixed public URL (skip tunnel, e.g. https://cc-caller.yourdomain.com)")
    parser.add_argument("--session-id", type=str, default="caller", help="Claude session ID (default: 'caller', persists across restarts)")
    parser.add_argument("--new-session", action="store_true", help="Start a fresh Claude session instead of resuming")
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--port", type=int, default=int(os.getenv("WEBHOOK_PORT", "8765")))
    args = parser.parse_args()

    if args.pwa:
        args.inbound = True  # --pwa implies inbound
    if not args.inbound and not args.instruction:
        parser.error("Either provide an instruction or use --inbound")

    mode = CallMode(args.mode)
    api_key = os.getenv("VAPI_API_KEY", "")
    public_key = os.getenv("VAPI_PUBLIC_KEY", "")
    phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID", "")
    sip_phone_number_id = os.getenv("VAPI_SIP_PHONE_NUMBER_ID", "")
    customer_number = os.getenv("USER_PHONE_NUMBER", "")
    sip_uri = os.getenv("VAPI_SIP_URI", "sip:cc-caller@sip.vapi.ai")

    if args.sip and not (api_key and sip_phone_number_id):
        parser.error("--sip requires VAPI_API_KEY and VAPI_SIP_PHONE_NUMBER_ID in .env")
    if args.pwa and not public_key:
        parser.error("--pwa requires VAPI_PUBLIC_KEY in .env")
    if not args.sip and not api_key:
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

    # Start tunnel (or use fixed URL)
    if args.tunnel_url:
        public_url = args.tunnel_url.rstrip("/")
        cleanup_tunnel = lambda: None
        print(f"Using fixed URL: {public_url}")
    else:
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

    if args.pwa:
        from cc_caller.vapi.client import build_persistent_sip_config

        vapid_priv, vapid_pub = ensure_vapid_keys()

        # Conversation history for cross-call context
        conversation_history = []  # list of {"task": str, "result": str}

        def build_pwa_config():
            """Build assistant config with recent conversation context injected."""
            config = build_persistent_sip_config(webhook_url)
            if conversation_history:
                context = "\n\nRECENT CONVERSATION (you said this in previous calls — use it to answer follow-ups, repeats, and clarifications WITHOUT calling askCodingAgent):\n"
                for entry in conversation_history[-5:]:
                    context += f"\nUser asked: {entry['task']}\nYou responded: {entry['result'][:500]}\n"
                config["model"]["messages"][0]["content"] += context
            return config

        # Store config for PWA page to fetch
        app.state.pwa_config = {
            "assistantConfig": build_pwa_config(),
            "publicKey": public_key,
            "vapidPublicKey": vapid_pub,
        }

        # Track call state for hybrid mode
        call_active = threading.Event()
        call_active.set()
        pending_result = {"output": None}
        tool_lock = threading.Lock()
        task_in_progress = threading.Event()

        def on_webhook_event(event_type):
            if event_type == "end-of-call-report":
                print("[pwa] Call ended")
                call_active.clear()
                if task_in_progress.is_set():
                    print("[pwa] Task still in progress — will push when done")

        app.state.on_webhook_event = on_webhook_event

        def handle_tool_call(task: str) -> str:
            nonlocal session_id, first_run
            # Tool call from VAPI means a call is active
            call_active.set()
            if not tool_lock.acquire(timeout=1):
                print("[tool] Already processing a task, skipping duplicate")
                return "Still working on the previous request. Please wait."
            try:
                print(f"\n[tool] Task: {task}")
                task_in_progress.set()
                instruction = clean_transcript(task)
                print(f"[tool] Cleaned: {instruction}")
                output, session_id = run_claude(instruction, session_id, session_name=session_name, is_first_run=first_run)
                first_run = False
                print(f"[tool] Claude output: {len(output)} chars")
                task_in_progress.clear()
            except Exception as e:
                task_in_progress.clear()
                tool_lock.release()
                raise e

            truncated = output[:2000] if len(output) > 2000 else output

            # Log and track for cross-call context
            log_interaction(task, truncated)
            conversation_history.append({"task": task, "result": truncated})
            if len(conversation_history) > 10:
                del conversation_history[:-10]
            # Refresh config with updated context
            app.state.pwa_config["assistantConfig"] = build_pwa_config()

            if call_active.is_set():
                tool_lock.release()
                return truncated
            else:
                print("[pwa] Call dropped during task — will push notification")
                pending_result["output"] = truncated
                tool_lock.release()
                return truncated

        app.state.tool_call_handler = handle_tool_call

        pwa_url = f"{public_url}/pwa"
        print(f"PWA: {pwa_url}")
        send_notification(
            title="CC-Caller PWA Ready",
            message="Open to start voice session",
            url=pwa_url,
        )

        try:
            print("\nPWA session active. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)

                # Check for pending result to push
                if pending_result["output"] and not call_active.is_set() and not task_in_progress.is_set():
                    output = pending_result["output"]
                    pending_result["output"] = None
                    print(f"\n[pwa] Pushing result ({len(output)} chars)...")

                    summary_data = summarize_output(output)

                    # Build callback assistant so user hears the result
                    callback_config = build_assistant_config(
                        summary=summary_data["summary"],
                        detail=summary_data["detail"],
                        webhook_url=webhook_url,
                    )

                    # Temporarily swap config so PWA page gets callback assistant
                    app.state.pwa_config["assistantConfig"] = callback_config

                    # Web Push notification
                    if app.state.push_subscriptions:
                        send_web_push(
                            app.state.push_subscriptions,
                            title="CC-Caller Result Ready",
                            body=summary_data["summary"][:200],
                            url=f"{pwa_url}?callback=1",
                            vapid_private_key=vapid_priv,
                        )
                        print("[pwa] Web Push sent")

                    # Also ntfy as fallback
                    send_notification(
                        title="CC-Caller Result Ready",
                        message=summary_data["summary"][:200],
                        url=f"{pwa_url}?callback=1",
                    )

                    # Restore persistent config after a delay
                    def restore_config():
                        time.sleep(60)
                        app.state.pwa_config["assistantConfig"] = build_pwa_config()
                        print("[pwa] Config restored to persistent mode")

                    threading.Thread(target=restore_config, daemon=True).start()

        except KeyboardInterrupt:
            print("\nExiting.")
        finally:
            cleanup_tunnel()
        return

    elif args.sip:
        from cc_caller.vapi.client import build_persistent_sip_config

        api_key = os.environ["VAPI_API_KEY"]
        sip_phone_number_id = os.environ["VAPI_SIP_PHONE_NUMBER_ID"]
        phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID", "")
        customer_number = os.getenv("USER_PHONE_NUMBER", "")
        sip_uri = os.getenv("VAPI_SIP_URI", "sip:cc-caller@sip.vapi.ai")

        # Track call state for hybrid mode
        call_active = threading.Event()
        call_active.set()  # Assume active initially
        pending_result = {"output": None}  # Stores result if call drops mid-task
        tool_lock = threading.Lock()  # Prevent concurrent tool calls

        # Build assistant with askCodingAgent tool
        sip_config = build_persistent_sip_config(webhook_url)
        configure_inbound_number(api_key, sip_phone_number_id, sip_config)

        # Track call lifecycle via webhook events
        original_call_active = True

        def on_webhook_event(event_type):
            if event_type == "end-of-call-report":
                print("[hybrid] Call ended")
                call_active.clear()
                # If Claude is still working, wait for it to finish
                if task_in_progress.is_set():
                    print("[hybrid] Task still in progress — will call back when done")
                    def wait_and_callback():
                        task_in_progress.wait(timeout=60)
                        time.sleep(2)  # Let the handler store the result
                        # pending_result should now be set
                    threading.Thread(target=wait_and_callback, daemon=True).start()

        app.state.on_webhook_event = on_webhook_event

        # Tool-call handler with hybrid support
        task_in_progress = threading.Event()

        def handle_tool_call(task: str) -> str:
            nonlocal session_id, first_run
            # Tool call from VAPI means a call is active
            call_active.set()
            if not tool_lock.acquire(timeout=1):
                print("[tool] Already processing a task, skipping duplicate")
                return "Still working on the previous request. Please wait."
            try:
                print(f"\n[tool] Task: {task}")
                task_in_progress.set()
                instruction = clean_transcript(task)
                print(f"[tool] Cleaned: {instruction}")
                output, session_id = run_claude(instruction, session_id, session_name=session_name, is_first_run=first_run)
                first_run = False
                print(f"[tool] Claude output: {len(output)} chars")
                task_in_progress.clear()
            except Exception as e:
                task_in_progress.clear()
                tool_lock.release()
                raise e

            truncated = output[:2000] if len(output) > 2000 else output
            log_interaction(task, truncated)

            # Check if call is still active
            if call_active.is_set():
                tool_lock.release()
                return truncated
            else:
                print("[hybrid] Call dropped during task — will call back")
                pending_result["output"] = truncated
                tool_lock.release()
                return truncated

        app.state.tool_call_handler = handle_tool_call

        print(f"SIP URI: {sip_uri}")
        print("Dial from Linphone — hybrid persistent session")
        send_notification(
            title="CC-Caller Live Ready",
            message="Dial from Linphone",
            url=sip_uri,
        )

        # Main loop — handles callbacks when call drops mid-task
        try:
            print("\nHybrid session active. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)

                # Check if there's a pending result to call back with
                if pending_result["output"] and not call_active.is_set() and not task_in_progress.is_set():
                    output = pending_result["output"]
                    pending_result["output"] = None
                    print(f"\n[hybrid] Calling back with result ({len(output)} chars)...")

                    # Summarize for callback
                    summary_data = summarize_output(output)

                    # Build callback assistant config
                    callback_config = build_assistant_config(
                        summary=summary_data["summary"],
                        detail=summary_data["detail"],
                        webhook_url=webhook_url,
                    )

                    # Configure SIP number with result so redial works
                    configure_inbound_number(api_key, sip_phone_number_id, callback_config)

                    # Send ntfy with SIP deep link
                    send_notification(
                        title="CC-Caller Result Ready",
                        message=summary_data["summary"][:200],
                        url=sip_uri,
                    )

                    print("[hybrid] Result ready — redial from Linphone")

                    # Restore persistent tool config for next inbound call
                    time.sleep(5)
                    configure_inbound_number(api_key, sip_phone_number_id, sip_config)
                    print("[hybrid] SIP number restored to persistent mode")

        except KeyboardInterrupt:
            print("\nExiting.")
        finally:
            clear_inbound_number(api_key, sip_phone_number_id)
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
            log_interaction(instruction, output)

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

            if args.sip:
                print("Preparing SIP callback...")
                configure_inbound_number(api_key, sip_phone_number_id, assistant_config)
                print(f"Dial {sip_uri} from Linphone for the update")
                send_notification(
                    title="CC-Caller Update",
                    message=summary_data["summary"][:200],
                    url=sip_uri,
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
                if args.sip:
                    send_notification(
                        title="CC-Caller: Still waiting",
                        message="Tap to connect",
                        url=sip_uri,
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
