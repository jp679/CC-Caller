"""cc-caller entry point.

Default mode: Gemini Live PWA. Legacy VAPI transports stay reachable via
their original flags and delegate to legacy_cli unchanged.
"""
import argparse
import sys

import requests

from cc_caller import config
from cc_caller import legacy_cli

LEGACY_TRIGGERS = {
    "--sip", "--web", "--pwa", "--vapi-pwa", "--phone", "--inbound",
    "--mode", "--interval-minutes", "--gemini", "--live", "--bridge",
}


def _is_legacy(argv):
    return any(a.split("=", 1)[0] in LEGACY_TRIGGERS for a in argv)


def run_setup():
    print("CC-Caller setup — you need a free Gemini API key from https://aistudio.google.com/apikey")
    key = input("Paste your GEMINI_API_KEY: ").strip()
    if not key:
        print("No key entered.")
        return 1
    resp = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models?key={}".format(key),
        timeout=15,
    )
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


def run_gemini_pwa(args):
    raise SystemExit("Gemini PWA mode lands in Task 10.")  # replaced in Task 10


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
