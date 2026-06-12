"""cc-caller entry point.

Default mode: Gemini Live PWA. Legacy VAPI transports stay reachable via
their original flags and delegate to legacy_cli unchanged.
"""
import warnings

# macOS system Python links LibreSSL; urllib3 v2 warns about it on import.
# Harmless for us — silence it before anything imports requests/urllib3.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

import argparse
import os
import secrets
import shutil
import socket
import sys
import threading
import time

import requests
import uvicorn

from cc_caller import config
from cc_caller import legacy_cli
from cc_caller import notify, push
from cc_caller.server import AppState, create_app
from cc_caller.tasks import TaskManager
from cc_caller.tunnel import start_tunnel

def show_exchange_enabled():
    """CC_SHOW_EXCHANGE config flag: show the caller<->Claude exchange. On unless disabled."""
    return os.getenv("CC_SHOW_EXCHANGE", "1").strip().lower() not in ("0", "false", "no", "off")


def resolve_token():
    """Per-run random token by default. CC_TOKEN in the CONFIG-DIR .env fixes it
    (project-local .env files are deliberately ignored for auth material);
    CC_PERSIST_TOKEN=1 generates one and stores it there. Long-lived bearer --
    pair it with a stable tunnel domain."""
    explicit = config.config_dir_value("CC_TOKEN")
    if explicit:
        return explicit
    if os.getenv("CC_PERSIST_TOKEN", "0").strip().lower() not in ("0", "false", "no", "off", ""):
        token = secrets.token_urlsafe(32)
        config.save_config_values(CC_TOKEN=token)
        print("Generated persistent token (saved to {})".format(config.config_dir() / ".env"))
        return token
    return secrets.token_urlsafe(32)


LEGACY_TRIGGERS = {
    "--sip", "--pwa", "--vapi-pwa", "--phone", "--inbound",
    "--mode", "--interval-minutes",
}


def _is_legacy(argv):
    return any(a.split("=", 1)[0] in LEGACY_TRIGGERS for a in argv)


def run_setup():
    print("CC-Caller setup — you need a free Gemini API key from https://aistudio.google.com/apikey")
    key = input("Paste your GEMINI_API_KEY: ").strip()
    if not key:
        print("No key entered.")
        return 1
    try:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models?key={}".format(key),
            timeout=15,
        )
    except requests.RequestException as e:
        print("Could not reach Google to validate the key ({}). "
              "Check your connection and try again.".format(type(e).__name__))
        return 1
    if resp.status_code != 200:
        print("Key validation failed (HTTP {}). Not saved.".format(resp.status_code))
        return 1
    config.save_config_values(GEMINI_API_KEY=key)
    print("Saved to {}. Run `cc-caller` to start.".format(config.config_dir() / ".env"))
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="cc-caller",
        description="Talk to Claude Code from your phone. Free.",
    )
    parser.add_argument("instruction", nargs="?", default=None,
                        help="Optional task to start Claude on immediately")
    parser.add_argument("--session-id", type=str, default="caller", dest="session")
    parser.add_argument("--new-session", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--tunnel", choices=["cloudflare", "ngrok"], default="cloudflare")
    parser.add_argument("--tunnel-url", type=str, default=None)
    parser.add_argument("--model", type=str, default=None,
                        help="Override GEMINI_LIVE_MODEL")
    return parser.parse_args(argv)


RELAY_SYSTEM_PROMPT = (
    "You are CC-Caller, a voice assistant relaying between the user and Claude "
    "Code, a coding agent working on the user's machine.\n"
    "- When the user gives a coding task, question, or instruction, call "
    "askCodingAgent with the request phrased completely and faithfully, then "
    "acknowledge briefly (e.g. 'On it -- sending that to Claude').\n"
    "- When a result arrives, read the summary conversationally. Never read "
    "code, file paths, or stack traces aloud verbatim.\n"
    "- Never invent or assume a task result. If you don't have the result in "
    "your context, say so plainly and offer to check: use checkStatus, or ask "
    "Claude again. Results can belong to a different session than this one.\n"
    "- Use checkStatus if the user asks how the task is going.\n"
    "- If the user says stop, cancel, or never mind about a running task, call cancelTask.\n"
    "- When the user asks you to remember something, call rememberNote with it.\n"
    "- If the user asks to switch to or resume a different session, call listSessions, "
    "confirm which one they mean, then switchSession with its id.\n"
    "- If the conversation context below already answers a follow-up, answer "
    "directly without calling askCodingAgent again.\n"
    "- When the user says they are done ('goodbye', 'end session'), call "
    "endSession and give a short goodbye.\n"
    "- Keep every reply short and speakable. You are on a voice call."
)


def build_base_prompt():
    """Relay prompt plus any user calibration from <config_dir>/prompt.md."""
    extra = config.prompt_extra()
    if not extra:
        return RELAY_SYSTEM_PROMPT
    return (RELAY_SYSTEM_PROMPT
            + "\n\nUSER CALIBRATION (style and behavior preferences -- follow these "
              "as long as they don't conflict with the tool instructions above):\n"
            + extra)


def make_on_complete(state, task_manager, public_url, vapid_priv):
    """Route a finished task: live session first, push + ntfy otherwise."""
    def on_complete(result):
        session = state.session_holder.get("session")
        live = session is not None and getattr(session, "alive", False)
        if result.get("cancelled"):
            if live:
                session.deliver_result(result["summary"])
            return
        url = "{}/?callback=1&token={}".format(public_url, state.token)
        if live and session.deliver_result(result["summary"]):
            task_manager.take_pending()
            return
        print("[notify] no live session — push to {} subscription(s); ntfy topic: {}".format(
            len(state.subscriptions), os.getenv("NTFY_TOPIC", "cc-caller")))
        push.send_web_push(
            state.subscriptions, "Claude finished",
            result["summary"][:160], url, vapid_priv,
        )
        push.save_subscriptions(state.subscriptions)
        notify.send_notification("Claude finished", result["summary"][:300], url)
    return on_complete


def make_on_activity(state):
    def on_activity(text):
        session = state.session_holder.get("session")
        if session is not None and getattr(session, "alive", False):
            session.notify_activity(text)
    return on_activity


def print_qr(url):
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)



def run_gemini_pwa(args):
    config.load_config()
    show_exchange = show_exchange_enabled()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("No GEMINI_API_KEY found. Run `cc-caller setup` first "
              "(free key: https://aistudio.google.com/apikey)")
        return 1

    if not shutil.which("claude"):
        print("The `claude` CLI is required: https://claude.com/claude-code")
        return 1
    if not args.tunnel_url and args.tunnel == "cloudflare" and not shutil.which("cloudflared"):
        print("cloudflared is required for the default tunnel:\n"
              "  macOS:  brew install cloudflared\n"
              "  Linux:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n"
              "Or use --tunnel ngrok / --tunnel-url https://your-domain")
        return 1

    port = args.port or int(os.getenv("WEBHOOK_PORT", "8765"))
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        print("Port {} is already in use — is another cc-caller running? Try --port.".format(port))
        return 1
    finally:
        probe.close()

    vapid_priv, vapid_pub = push.ensure_vapid_keys()
    token = resolve_token()
    task_manager = TaskManager(session_name=args.session, new_session=args.new_session,
                               show_exchange=show_exchange)
    state = AppState(
        token=token, task_manager=task_manager, api_key=api_key,
        model=args.model, vapid_public_key=vapid_pub,
        base_system_prompt=build_base_prompt(),
        subscriptions=push.load_subscriptions(),
        show_exchange=show_exchange,
    )
    app = create_app(state)

    threading.Thread(
        target=uvicorn.run, args=(app,),
        kwargs={"host": "0.0.0.0", "port": port, "log_level": "warning"},
        daemon=True,
    ).start()

    if args.tunnel_url:
        public_url, cleanup = args.tunnel_url.rstrip("/"), lambda: None
    else:
        try:
            public_url, cleanup = start_tunnel(port, args.tunnel)
        except RuntimeError as e:
            print("Tunnel failed: {}. Check cloudflared works "
                  "(`cloudflared tunnel --url http://localhost:{}`), "
                  "or use --tunnel ngrok / --tunnel-url.".format(e, port))
            return 1

    task_manager.on_complete = make_on_complete(state, task_manager, public_url, vapid_priv)
    task_manager.on_activity = make_on_activity(state)

    url = "{}/?token={}".format(public_url, token)
    print("\nCC-Caller is live. Open on your phone:\n\n  {}\n".format(url))
    print_qr(url)
    print("\nClaude session: {} | Ctrl-C to stop".format(args.session))

    if args.instruction:
        print("Starting Claude on: {}".format(args.instruction))
        task_manager.submit(args.instruction)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        cleanup()
        return 0


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["setup"]:
        return run_setup()
    if _is_legacy(argv):
        sys.argv = [sys.argv[0]] + [
            "--pwa" if a == "--vapi-pwa" else a for a in argv if a != "--phone"
        ]
        return legacy_cli.main()
    return run_gemini_pwa(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main() or 0)
