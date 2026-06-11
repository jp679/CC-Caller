# Gemini Live PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild CC-Caller's free voice path on Gemini Live with proper tool-calling, packaged as an installable PWA-serving CLI, per `docs/superpowers/specs/2026-06-10-gemini-pwa-design.md`.

**Architecture:** Browser PWA ↔ FastAPI WebSocket ↔ Gemini Live (tool-calling: `askCodingAgent` NON_BLOCKING → final `FunctionResponse` with `scheduling: INTERRUPT`) ↔ Claude worker subprocess. Existing battle-tested code (worker, push, tunnel, ntfy, VAPI transports) is relocated into a `cc_caller/` package, not rewritten. A per-run token guards the WS and API endpoints.

**Tech Stack:** Python 3.9+ (no `match`, no `X | Y` unions), FastAPI/uvicorn, `websockets`, `pywebpush`, `qrcode`, pytest + pytest-asyncio. Frontend: vanilla JS, AudioWorklet, service worker.

**Conventions for every task:** run tests with `python3 -m pytest tests/ -q` from the repo root (`/Users/JP_1/Dev/CC-Caller`). Commit after each green task. One deviation from the spec, for packaging correctness: static assets live at `cc_caller/static/` (inside the package) so wheels include them — the spec's top-level `static/` moves there.

---

## Task 1: Package skeleton and file moves

Everything moves into a `cc_caller/` package. The old top-level module `cc_caller.py` becomes `cc_caller/legacy_cli.py`. Tests must stay green at the end of this task.

**Files:**
- Create: `pyproject.toml`, `cc_caller/__init__.py`, `cc_caller/vapi/__init__.py`
- Move: `cc_caller.py` → `cc_caller/legacy_cli.py`, `webhook.py` → `cc_caller/vapi/webhook.py`, `vapi_client.py` → `cc_caller/vapi/client.py`, `summarizer.py` → `cc_caller/summarizer.py`, `gemini_bridge.py` → `cc_caller/vapi/gemini_bridge.py`, `static/` → `cc_caller/static/`
- Modify: `cc-caller` (wrapper), `tests/test_cc_caller.py`, `tests/test_webhook.py`, `tests/test_summarizer.py`, `tests/test_vapi_client.py`

- [ ] **Step 1: Create the package and move files**

```bash
cd /Users/JP_1/Dev/CC-Caller
mkdir -p cc_caller/vapi
git mv cc_caller.py cc_caller/legacy_cli.py
git mv webhook.py cc_caller/vapi/webhook.py
git mv vapi_client.py cc_caller/vapi/client.py
git mv summarizer.py cc_caller/summarizer.py
git mv gemini_bridge.py cc_caller/vapi/gemini_bridge.py
git mv static cc_caller/static
```

- [ ] **Step 2: Create `cc_caller/__init__.py` and `cc_caller/vapi/__init__.py`**

`cc_caller/__init__.py`:
```python
__version__ = "0.1.0"
```

`cc_caller/vapi/__init__.py`:
```python
```
(empty file)

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "cc-caller"
version = "0.1.0"
description = "Talk to Claude Code from your phone. Free."
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "requests>=2.32.0",
    "python-dotenv>=1.0.1",
    "websockets>=12.0",
    "pywebpush>=2.0.0",
    "cryptography>=42.0",
    "qrcode>=7.4",
    "pyngrok>=7.2.0",
]

[project.scripts]
cc-caller = "cc_caller.cli:main"

[project.optional-dependencies]
dev = ["pytest>=8.3.0", "pytest-asyncio>=0.23", "httpx>=0.27.0"]

[tool.setuptools.packages.find]
include = ["cc_caller*"]

[tool.setuptools.package-data]
cc_caller = ["static/*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 4: Fix imports in `cc_caller/legacy_cli.py`**

At the top of `cc_caller/legacy_cli.py`, change:
```python
from summarizer import summarize_output
from vapi_client import (
```
to:
```python
from cc_caller.summarizer import summarize_output
from cc_caller.vapi.client import (
```
and:
```python
from webhook import create_app
```
to:
```python
from cc_caller.vapi.webhook import create_app
```
and the `.env` load (the file is now one level deeper):
```python
load_dotenv(pathlib.Path(__file__).parent / ".env")
```
to:
```python
load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
```
At line ~672, change `from gemini_bridge import GeminiBridge` to `from cc_caller.vapi.gemini_bridge import GeminiBridge`.
In `ensure_vapid_keys` (~line 236), change `env_path = pathlib.Path(__file__).parent / ".env"` to `env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"`.

- [ ] **Step 5: Fix the static path in `cc_caller/vapi/webhook.py`**

Change:
```python
STATIC_DIR = pathlib.Path(__file__).parent / "static"
```
to:
```python
STATIC_DIR = pathlib.Path(__file__).resolve().parents[1] / "static"
```
(`cc_caller/vapi/webhook.py` → parents[1] is `cc_caller/`, so this resolves to `cc_caller/static/`.)

- [ ] **Step 6: Update test imports**

In `tests/test_cc_caller.py`: `from cc_caller import run_claude, should_call, CallMode, name_to_uuid` → `from cc_caller.legacy_cli import run_claude, should_call, CallMode, name_to_uuid`, and every `patch("cc_caller.subprocess.run")` → `patch("cc_caller.legacy_cli.subprocess.run")`.

In `tests/test_webhook.py`: `from webhook import create_app` → `from cc_caller.vapi.webhook import create_app`.

In `tests/test_summarizer.py`: `from summarizer import summarize_output` → `from cc_caller.summarizer import summarize_output`, and `patch("summarizer.subprocess.run")` → `patch("cc_caller.summarizer.subprocess.run")`.

In `tests/test_vapi_client.py`: `from vapi_client import ...` → `from cc_caller.vapi.client import ...`, and any `patch("vapi_client.` / `monkeypatch.setattr` targets referencing `vapi_client` → `cc_caller.vapi.client`.

- [ ] **Step 7: Update the `cc-caller` wrapper**

Replace the contents of `cc-caller` with:
```bash
#!/bin/bash
# CC-Caller dev wrapper. Installed users get the `cc-caller` console script instead.
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$DIR:$PYTHONPATH" python3 -m cc_caller.legacy_cli "$@"
```

- [ ] **Step 8: Run tests, verify all pass**

Run: `python3 -m pytest tests/ -q`
Expected: 17 passed. If an import error surfaces, the failing file names the stale module path — fix per Steps 4–6 patterns.

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "refactor: move modules into cc_caller package with pyproject"
```

---

## Task 2: Config module

Load order: `~/.config/cc-caller/.env`, then repo-checkout `.env` (dev convenience), then `./.env` (cwd) with override. `CC_CALLER_CONFIG_DIR` env var overrides the config dir (used by tests).

**Files:**
- Create: `cc_caller/config.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:
```python
import os
from cc_caller import config


def test_config_dir_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path / "cfg"))
    assert config.config_dir() == tmp_path / "cfg"


def test_cwd_env_overrides_config_dir_env(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / ".env").write_text("CC_TEST_VAL=from_config\nCC_ONLY_CONFIG=yes\n")
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / ".env").write_text("CC_TEST_VAL=from_cwd\n")
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CC_TEST_VAL", raising=False)
    monkeypatch.delenv("CC_ONLY_CONFIG", raising=False)
    monkeypatch.chdir(cwd)
    config.load_config()
    assert os.environ["CC_TEST_VAL"] == "from_cwd"
    assert os.environ["CC_ONLY_CONFIG"] == "yes"


def test_save_config_values_creates_file_with_0600(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(cfg))
    config.save_config_values(GEMINI_API_KEY="abc123")
    env_file = cfg / ".env"
    assert "GEMINI_API_KEY=abc123" in env_file.read_text()
    assert oct(env_file.stat().st_mode)[-3:] == "600"


def test_save_config_values_replaces_existing_key(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(cfg))
    config.save_config_values(GEMINI_API_KEY="old", NTFY_TOPIC="t")
    config.save_config_values(GEMINI_API_KEY="new")
    text = (cfg / ".env").read_text()
    assert "GEMINI_API_KEY=new" in text
    assert "GEMINI_API_KEY=old" not in text
    assert "NTFY_TOPIC=t" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc_caller.config'` (or AttributeError).

- [ ] **Step 3: Implement `cc_caller/config.py`**

```python
"""Config loading and persistence for cc-caller.

Precedence (later wins): ~/.config/cc-caller/.env -> repo-checkout .env -> ./.env
"""
import os
import pathlib

from dotenv import load_dotenv


def config_dir() -> pathlib.Path:
    override = os.environ.get("CC_CALLER_CONFIG_DIR")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".config" / "cc-caller"


def load_config() -> None:
    load_dotenv(config_dir() / ".env", override=False)
    # Dev convenience: a .env sitting next to a source checkout keeps working.
    repo_env = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if repo_env.exists():
        load_dotenv(repo_env, override=False)
    load_dotenv(pathlib.Path.cwd() / ".env", override=True)


def save_config_values(**values) -> None:
    """Set keys in the config-dir .env, replacing existing lines for those keys."""
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    env_file = cfg / ".env"
    lines = []
    if env_file.exists():
        lines = [
            ln for ln in env_file.read_text().splitlines()
            if ln.strip() and ln.split("=", 1)[0] not in values
        ]
    for key, val in values.items():
        lines.append("{}={}".format(key, val))
        os.environ[key] = str(val)
    env_file.write_text("\n".join(lines) + "\n")
    os.chmod(env_file, 0o600)
```

Note for the cwd-override test: `load_dotenv(..., override=False)` does not overwrite values already in `os.environ`, so the config-dir value lands first and the cwd load with `override=True` replaces it. The repo-checkout `.env` on this machine contains real keys — `monkeypatch.delenv` plus tmp cwd in the test keeps it from interfering with `CC_TEST_VAL` (a name the real `.env` doesn't use).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add cc_caller/config.py tests/test_config.py && git commit -m "feat: config module with config-dir + cwd .env precedence"
```

---

## Task 3: CLI front door and `setup` command

`cc_caller/cli.py` is the console-script entry: `cc-caller setup` runs the onboarding wizard; legacy flags delegate to `legacy_cli.main()`; everything else is the new default mode (stubbed until Task 10).

**Files:**
- Create: `cc_caller/cli.py`, `tests/test_cli.py`
- Modify: `cc-caller` (wrapper)

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
from unittest.mock import patch, MagicMock

from cc_caller import cli


def test_legacy_flags_delegate_to_legacy_cli():
    with patch("cc_caller.cli.legacy_cli") as mock_legacy:
        with patch("sys.argv", ["cc-caller", "--sip", "--inbound"]):
            cli.main()
    mock_legacy.main.assert_called_once()


def test_vapi_pwa_flag_is_translated_to_legacy_pwa():
    captured = {}

    def capture_argv():
        import sys
        captured["argv"] = list(sys.argv)

    with patch("cc_caller.cli.legacy_cli") as mock_legacy:
        mock_legacy.main.side_effect = capture_argv
        with patch("sys.argv", ["cc-caller", "--vapi-pwa"]):
            cli.main()
    assert "--pwa" in captured["argv"]
    assert "--vapi-pwa" not in captured["argv"]
    mock_legacy.main.assert_called_once()


def test_default_mode_is_gemini_pwa():
    with patch("cc_caller.cli.run_gemini_pwa") as mock_run:
        with patch("sys.argv", ["cc-caller"]):
            cli.main()
    mock_run.assert_called_once()


def test_setup_validates_key_and_saves(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    ok = MagicMock(status_code=200)
    with patch("cc_caller.cli.requests.get", return_value=ok) as mock_get:
        with patch("builtins.input", return_value="test-key-123"):
            with patch("sys.argv", ["cc-caller", "setup"]):
                cli.main()
    assert "key=test-key-123" in mock_get.call_args[0][0]
    assert "GEMINI_API_KEY=test-key-123" in (tmp_path / ".env").read_text()


def test_setup_rejects_bad_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    bad = MagicMock(status_code=400)
    with patch("cc_caller.cli.requests.get", return_value=bad):
        with patch("builtins.input", return_value="bad-key"):
            with patch("sys.argv", ["cc-caller", "setup"]):
                rc = cli.main()
    assert rc == 1
    assert not (tmp_path / ".env").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: FAIL — `No module named 'cc_caller.cli'`.

- [ ] **Step 3: Implement `cc_caller/cli.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_cli.py tests/ -q`
Expected: all pass (the default-mode test passes because `run_gemini_pwa` is patched).

- [ ] **Step 5: Point the wrapper at the new entry**

In `cc-caller`, change `python3 -m cc_caller.legacy_cli` to `python3 -m cc_caller.cli`.

- [ ] **Step 6: Commit**

```bash
git add cc_caller/cli.py tests/test_cli.py cc-caller && git commit -m "feat: cli front door with setup wizard and legacy dispatch"
```

---

## Task 4: Extract `claude_worker.py`

Move the Claude-subprocess layer out of `legacy_cli.py` so the new path imports it without dragging in VAPI code.

**Files:**
- Create: `cc_caller/claude_worker.py`
- Modify: `cc_caller/legacy_cli.py`, `tests/test_cc_caller.py`

- [ ] **Step 1: Create `cc_caller/claude_worker.py`**

Cut (do not copy) these from `cc_caller/legacy_cli.py`, verbatim, into the new file: `NEED_INPUT_PROMPT`, `TERMINATION_CHECK_PROMPT`, `name_to_uuid`, `WORKER_SYSTEM_PROMPT`, `DISALLOWED_FILES`, `log_interaction`, `CLEAN_TRANSCRIPT_PROMPT`, `clean_transcript`, `run_claude`, `check_needs_input`, `TERMINATION_PHRASES`, `is_termination`. File header:

```python
"""Claude Code worker subprocess layer: sandboxed runs, sessions, judge prompts."""
import pathlib
import subprocess
import uuid
from typing import Tuple
```
(Keep each moved function's body byte-identical. `log_interaction` already imports `datetime` locally.)

- [ ] **Step 2: Re-import in `legacy_cli.py`**

At the top of `cc_caller/legacy_cli.py` add:
```python
from cc_caller.claude_worker import (
    name_to_uuid, run_claude, clean_transcript, check_needs_input,
    is_termination, log_interaction, WORKER_SYSTEM_PROMPT, DISALLOWED_FILES,
)
```
`should_call`, `CallMode`, notification/tunnel/VAPID helpers stay in `legacy_cli.py` for now.

- [ ] **Step 3: Update test imports and patch targets**

In `tests/test_cc_caller.py`:
- `from cc_caller.legacy_cli import run_claude, should_call, CallMode, name_to_uuid` → split:
  ```python
  from cc_caller.claude_worker import run_claude, name_to_uuid
  from cc_caller.legacy_cli import should_call, CallMode
  ```
- every `patch("cc_caller.legacy_cli.subprocess.run")` that wraps a `run_claude` test → `patch("cc_caller.claude_worker.subprocess.run")`. (`should_call` tests that patch `check_needs_input` similarly move to `cc_caller.legacy_cli.check_needs_input` — the name `legacy_cli` re-imports is the one `should_call` calls.)

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor: extract claude_worker from legacy cli"
```

---

## Task 5: Extract `push.py`, `notify.py`, `tunnel.py`

Same pattern as Task 4. Push gains subscription persistence (config dir), and VAPID keys now save to the config dir instead of the repo `.env`.

**Files:**
- Create: `cc_caller/push.py`, `cc_caller/notify.py`, `cc_caller/tunnel.py`, `tests/test_push.py`
- Modify: `cc_caller/legacy_cli.py`

- [ ] **Step 1: Write the failing test for subscription persistence**

`tests/test_push.py`:
```python
from cc_caller import push


def test_subscriptions_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    subs = [{"endpoint": "https://example.com/ep", "keys": {"auth": "a", "p256dh": "b"}}]
    push.save_subscriptions(subs)
    assert push.load_subscriptions() == subs


def test_load_subscriptions_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    assert push.load_subscriptions() == []


def test_ensure_vapid_keys_generates_and_persists(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    priv, pub = push.ensure_vapid_keys()
    assert priv and pub
    text = (tmp_path / ".env").read_text()
    assert "VAPID_PRIVATE_KEY=" in text and "VAPID_PUBLIC_KEY=" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_push.py -q`
Expected: FAIL — `No module named 'cc_caller.push'`.

- [ ] **Step 3: Implement the three modules**

`cc_caller/push.py` — move `ensure_vapid_keys` and `send_web_push` bodies from `legacy_cli.py`, with `ensure_vapid_keys`'s `.env` write replaced by `config.save_config_values(...)`:

```python
"""Web Push: VAPID keys, subscription persistence, sending."""
import json
import os

from cc_caller import config


def ensure_vapid_keys():
    """Return (private_key, public_key) base64url. Generate + persist if missing."""
    priv = os.getenv("VAPID_PRIVATE_KEY", "")
    pub = os.getenv("VAPID_PUBLIC_KEY", "")
    if priv and pub:
        return priv, pub

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64

    key = ec.generate_private_key(ec.SECP256R1())
    priv_bytes = key.private_numbers().private_value.to_bytes(32, 'big')
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    priv = base64.urlsafe_b64encode(priv_bytes).rstrip(b'=').decode()
    pub = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()
    config.save_config_values(VAPID_PRIVATE_KEY=priv, VAPID_PUBLIC_KEY=pub)
    print("Generated VAPID keys and saved to config")
    return priv, pub


def send_web_push(subscriptions, title, body, url, vapid_private_key):
    """Send a Web Push to all subscriptions; prune expired ones in place."""
    from pywebpush import webpush, WebPushException

    payload = json.dumps({"title": title, "body": body, "url": url})
    for sub in list(subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": "mailto:cc-caller@example.com"},
            )
        except WebPushException as e:
            print("[push] Failed: {}".format(e))
            if "410" in str(e) or "404" in str(e):
                subscriptions.remove(sub)


def _subs_file():
    return config.config_dir() / "subscriptions.json"


def load_subscriptions():
    f = _subs_file()
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except (ValueError, OSError):
        return []


def save_subscriptions(subscriptions):
    f = _subs_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(subscriptions))
```

`cc_caller/notify.py` — move `send_notification` verbatim:
```python
"""ntfy.sh notifications (optional fallback channel)."""
import os

import requests as http_requests


def send_notification(title, message, url=""):
    ntfy_topic = os.getenv("NTFY_TOPIC", "cc-caller")
    headers = {"Title": title, "Priority": "urgent", "Tags": "phone"}
    if url:
        headers["Click"] = url
        headers["Actions"] = "view, Open Call, {}".format(url)
    try:
        http_requests.post(
            "https://ntfy.sh/{}".format(ntfy_topic),
            data=message,
            headers=headers,
            timeout=5,
        )
    except Exception as e:
        print("Notification failed: {}".format(e))
```

`cc_caller/tunnel.py` — move `start_tunnel` verbatim (it needs `import os, re, subprocess, time` at the top of the new file).

In `legacy_cli.py`: delete the moved bodies, add
```python
from cc_caller.push import ensure_vapid_keys, send_web_push
from cc_caller.notify import send_notification
from cc_caller.tunnel import start_tunnel
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor: extract push, notify, tunnel modules; persist subscriptions"
```

---

## Task 6: TaskManager

One task at a time, run in a thread, completion reported to a wiring-level callback. The pending result is consumed (`take_pending`) by whichever path delivers it.

**Files:**
- Create: `cc_caller/tasks.py`, `tests/test_tasks.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_tasks.py`:
```python
import threading
from unittest.mock import patch

from cc_caller.tasks import TaskManager


def _patches():
    return (
        patch("cc_caller.tasks.clean_transcript", side_effect=lambda t: t),
        patch("cc_caller.tasks.run_claude", return_value=("full output", "sid-1")),
        patch("cc_caller.tasks.summarize_output",
              return_value={"summary": "did the thing", "detail": "full output"}),
        patch("cc_caller.tasks.log_interaction"),
    )


def test_submit_runs_task_and_reports_completion():
    done = threading.Event()
    results = []
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager(session_name="t1")
        tm.on_complete = lambda r: (results.append(r), done.set())
        assert tm.submit("fix the bug", meta={"fc_id": "f1"}) is True
        assert done.wait(timeout=5)
    assert results[0]["summary"] == "did the thing"
    assert results[0]["task"] == "fix the bug"
    assert results[0]["meta"] == {"fc_id": "f1"}
    assert tm.history[-1]["task"] == "fix the bug"
    assert tm.pending is not None
    assert tm.take_pending()["summary"] == "did the thing"
    assert tm.pending is None


def test_second_submit_while_busy_is_rejected():
    release = threading.Event()
    started = threading.Event()

    def slow_run(*a, **kw):
        started.set()
        release.wait(timeout=5)
        return ("out", "sid")

    p1, p2, p3, p4 = _patches()
    with p1, patch("cc_caller.tasks.run_claude", side_effect=slow_run), p3, p4:
        tm = TaskManager()
        done = threading.Event()
        tm.on_complete = lambda r: done.set()
        assert tm.submit("task one") is True
        assert started.wait(timeout=5)
        assert tm.busy is True
        assert tm.submit("task two") is False
        release.set()
        assert done.wait(timeout=5)
    assert tm.busy is False


def test_completion_callback_errors_do_not_break_manager():
    done = threading.Event()
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4:
        tm = TaskManager()
        def boom(r):
            done.set()
            raise RuntimeError("listener died")
        tm.on_complete = boom
        tm.submit("task")
        assert done.wait(timeout=5)
        # lock must have been released despite the callback raising
        assert tm.submit("next task") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_tasks.py -q`
Expected: FAIL — `No module named 'cc_caller.tasks'`.

- [ ] **Step 3: Implement `cc_caller/tasks.py`**

```python
"""Serialized Claude task execution with completion callbacks and pending results."""
import threading
import time
from typing import Callable, Optional

from cc_caller.claude_worker import (
    clean_transcript, log_interaction, name_to_uuid, run_claude,
)
from cc_caller.summarizer import summarize_output


class TaskManager:
    def __init__(self, session_name="caller", new_session=False):
        import uuid as _uuid
        self.session_name = session_name
        self.session_id = str(_uuid.uuid4()) if new_session else name_to_uuid(session_name)
        self.first_run = True
        self.history = []          # [{"task", "summary"}]
        self.pending = None        # {"task", "summary", "detail", "meta"} until consumed
        self.on_complete = None    # Callable[[dict], None], set by wiring
        self._lock = threading.Lock()
        self._started_at = None
        self.current_task = None

    @property
    def busy(self):
        return self._started_at is not None

    @property
    def elapsed(self):
        if self._started_at is None:
            return None
        return time.time() - self._started_at

    def submit(self, task, meta=None):
        """Start a task. Returns False if one is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        self._started_at = time.time()
        self.current_task = task
        thread = threading.Thread(target=self._run, args=(task, meta or {}), daemon=True)
        thread.start()
        return True

    def take_pending(self):
        result, self.pending = self.pending, None
        return result

    def _run(self, task, meta):
        try:
            cleaned = clean_transcript(task)
            output, self.session_id = run_claude(
                cleaned, self.session_id,
                session_name=self.session_name, is_first_run=self.first_run,
            )
            self.first_run = False
            summary = summarize_output(output)["summary"]
            log_interaction(cleaned, output)
            result = {"task": task, "summary": summary, "detail": output, "meta": meta}
            self.history.append({"task": task, "summary": summary})
            self.pending = result
        except Exception as e:
            result = {"task": task, "summary": "The task failed: {}".format(e),
                      "detail": str(e), "meta": meta}
            self.pending = result
        finally:
            self._started_at = None
            self.current_task = None
            self._lock.release()
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                print("[tasks] on_complete error: {}".format(e))
```

(Lock release happens before `on_complete` so a crashing listener can't wedge the manager — that's what the third test pins down.)

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_tasks.py tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cc_caller/tasks.py tests/test_tasks.py && git commit -m "feat: TaskManager — serialized Claude tasks with pending results"
```

---

## Task 7: GeminiLiveSession with tool-calling

The heart of the feature. Build it against a fake Gemini WebSocket server so every protocol path is tested offline.

**Files:**
- Create: `cc_caller/gemini_live.py`, `tests/fake_gemini.py`, `tests/test_gemini_live.py`

- [ ] **Step 1: Write the fake Gemini server (test infrastructure)**

`tests/fake_gemini.py`:
```python
"""In-process fake of the Gemini Live WebSocket API for offline tests."""
import asyncio
import json

import websockets


class FakeGemini:
    def __init__(self, reject_non_blocking=False):
        self.reject_non_blocking = reject_non_blocking
        self.received = []        # parsed client->server messages
        self.setup_count = 0
        self.url = None
        self._server = None
        self._client = None

    async def __aenter__(self):
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = "ws://127.0.0.1:{}".format(port)
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws, path=None):  # path kwarg: websockets <13 compat
        async for raw in ws:
            data = json.loads(raw)
            self.received.append(data)
            if "setup" in data:
                self.setup_count += 1
                decls = data["setup"]["tools"][0]["functionDeclarations"]
                if self.reject_non_blocking and any("behavior" in d for d in decls):
                    await ws.close(code=1007, reason="NON_BLOCKING unsupported")
                    return
                self._client = ws
                await ws.send(json.dumps({"setupComplete": {}}))

    async def send(self, obj):
        await self._client.send(json.dumps(obj))

    def received_of(self, key):
        return [m for m in self.received if key in m]
```

- [ ] **Step 2: Write the failing session tests**

`tests/test_gemini_live.py`:
```python
import asyncio
import threading

import pytest

from cc_caller.gemini_live import GeminiLiveSession
from tests.fake_gemini import FakeGemini


class StubTM:
    def __init__(self, accept=True):
        self.accept = accept
        self.submitted = []
        self.busy = False
        self.elapsed = None

    def submit(self, task, meta=None):
        self.submitted.append((task, meta))
        return self.accept


class Harness:
    def __init__(self, fake, tm):
        self.to_browser = []
        self.queue = asyncio.Queue()
        self.session = GeminiLiveSession(
            api_key="test-key", system_prompt="PROMPT", task_manager=tm,
            send_to_browser=self._send, ws_url=fake.url,
        )
        self.run_task = None

    async def _send(self, msg):
        self.to_browser.append(msg)

    async def _browser_messages(self):
        while True:
            msg = await self.queue.get()
            if msg is None:
                return
            yield msg

    def start(self):
        self.run_task = asyncio.ensure_future(self.session.run(self._browser_messages()))

    async def stop(self):
        await self.queue.put(None)
        try:
            await asyncio.wait_for(self.run_task, timeout=3)
        except asyncio.TimeoutError:
            self.run_task.cancel()


async def wait_until(cond, timeout=3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in {}s".format(timeout))
        await asyncio.sleep(0.02)


async def test_handshake_declares_non_blocking_tools():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        setup = fake.received_of("setup")[0]["setup"]
        decls = setup["tools"][0]["functionDeclarations"]
        names = [d["name"] for d in decls]
        assert names == ["askCodingAgent", "checkStatus", "endSession"]
        assert decls[0]["behavior"] == "NON_BLOCKING"
        assert setup["systemInstruction"]["parts"][0]["text"] == "PROMPT"
        assert h.session.async_tools is True
        await h.stop()


async def test_fallback_when_non_blocking_rejected():
    async with FakeGemini(reject_non_blocking=True) as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        assert fake.setup_count == 2
        second = fake.received_of("setup")[1]["setup"]
        assert "behavior" not in second["tools"][0]["functionDeclarations"][0]
        assert h.session.async_tools is False
        await h.stop()


async def test_browser_audio_forwarded_to_gemini():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await h.queue.put({"type": "audio", "data": "QUJD"})
        await wait_until(lambda: fake.received_of("realtimeInput"))
        ri = fake.received_of("realtimeInput")[0]["realtimeInput"]
        assert ri["audio"]["data"] == "QUJD"
        assert ri["audio"]["mimeType"] == "audio/pcm;rate=16000"
        await h.stop()


async def test_gemini_audio_and_captions_forwarded_to_browser():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"serverContent": {
            "inputTranscription": {"text": "hello"},
            "modelTurn": {"parts": [{"inlineData": {"data": "UENN"}}]},
        }})
        await wait_until(lambda: any(m.get("type") == "audio" for m in h.to_browser))
        assert {"type": "caption", "role": "user", "text": "hello"} in h.to_browser
        assert {"type": "audio", "data": "UENN"} in h.to_browser
        await h.stop()


async def test_tool_call_acks_interim_then_delivers_interrupt():
    tm = StubTM(accept=True)
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "fix the bug"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        assert tm.submitted == [("fix the bug", {"fc_id": "f1"})]
        interim = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert interim["id"] == "f1"
        assert interim["willContinue"] is True
        assert interim["response"]["status"] == "started"
        assert any(m.get("type") == "status" and m.get("state") == "working"
                   for m in h.to_browser)

        # deliver from a foreign thread, like the worker does
        ok = await asyncio.get_event_loop().run_in_executor(
            None, h.session.deliver_result, "all fixed")
        assert ok is True
        await wait_until(lambda: len(fake.received_of("toolResponse")) >= 2)
        final = fake.received_of("toolResponse")[1]["toolResponse"]["functionResponses"][0]
        assert final["id"] == "f1"
        assert final["scheduling"] == "INTERRUPT"
        assert final["response"]["result"] == "all fixed"
        await h.stop()


async def test_busy_manager_returns_busy_response():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM(accept=False))
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f9", "name": "askCodingAgent", "args": {"task": "another"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        resp = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert resp["response"]["status"] == "busy"
        assert "willContinue" not in resp
        await h.stop()


async def test_cancellation_falls_back_to_client_content():
    async with FakeGemini() as fake:
        h = Harness(fake, StubTM())
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "f1", "name": "askCodingAgent", "args": {"task": "t"}}
        ]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        await fake.send({"toolCallCancellation": {"ids": ["f1"]}})
        await wait_until(lambda: "f1" in h.session._cancelled)
        ok = await asyncio.get_event_loop().run_in_executor(
            None, h.session.deliver_result, "late result")
        assert ok is True
        await wait_until(lambda: fake.received_of("clientContent"))
        turn = fake.received_of("clientContent")[0]["clientContent"]["turns"][0]
        assert "late result" in turn["parts"][0]["text"]
        await h.stop()


async def test_check_status_and_end_session():
    tm = StubTM()
    tm.busy, tm.elapsed = True, 42.5
    async with FakeGemini() as fake:
        h = Harness(fake, tm)
        h.start()
        await wait_until(lambda: any(m.get("type") == "ready" for m in h.to_browser))
        await fake.send({"toolCall": {"functionCalls": [
            {"id": "s1", "name": "checkStatus", "args": {}}]}})
        await wait_until(lambda: fake.received_of("toolResponse"))
        status = fake.received_of("toolResponse")[0]["toolResponse"]["functionResponses"][0]
        assert status["response"] == {"working": True, "elapsedSeconds": 42}

        await fake.send({"toolCall": {"functionCalls": [
            {"id": "e1", "name": "endSession", "args": {}}]}})
        await wait_until(lambda: len(fake.received_of("toolResponse")) >= 2)
        await fake.send({"serverContent": {"turnComplete": True}})
        await asyncio.wait_for(h.run_task, timeout=3)
        assert h.session.alive is False
        assert h.session.ended is True
```

- [ ] **Step 3: Run to verify failure**

Run: `python3 -m pytest tests/test_gemini_live.py -q`
Expected: FAIL — `No module named 'cc_caller.gemini_live'`.

- [ ] **Step 4: Implement `cc_caller/gemini_live.py`**

```python
"""Gemini Live session with tool-calling.

Replaces the old text-injection bridge. Claude results are delivered through
declared functions: askCodingAgent is NON_BLOCKING -- an interim response
("started") keeps the conversation alive, and the final FunctionResponse with
scheduling INTERRUPT makes the agent speak the result the moment it is ready.
If the model rejects NON_BLOCKING declarations, the session reconnects without
them and late results are injected as a clientContent turn instead.
"""
import asyncio
import json
import os

import websockets

GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
DEFAULT_MODEL = "models/gemini-3.1-flash-live-preview"

ASK_CODING_AGENT = {
    "name": "askCodingAgent",
    "description": (
        "Send a coding task, question, or instruction to Claude Code, the coding "
        "agent on the user's machine. Returns an acknowledgement immediately; "
        "the result is announced when Claude finishes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "task": {"type": "STRING",
                     "description": "The user's request, phrased completely and faithfully."},
        },
        "required": ["task"],
    },
    "behavior": "NON_BLOCKING",
}

CHECK_STATUS = {
    "name": "checkStatus",
    "description": "Check whether Claude is still working on a task and for how long.",
    "parameters": {"type": "OBJECT", "properties": {}},
}

END_SESSION = {
    "name": "endSession",
    "description": "End the voice session when the user says they are done.",
    "parameters": {"type": "OBJECT", "properties": {}},
}


class GeminiLiveSession:
    """One Gemini Live conversation, bridged to one browser connection.

    send_to_browser: async callable(dict). browser messages arrive through the
    async iterator passed to run(). deliver_result() is thread-safe.
    """

    def __init__(self, api_key, system_prompt, task_manager, send_to_browser,
                 model=None, ws_url=None, on_ready=None):
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.tm = task_manager
        self.send_to_browser = send_to_browser
        self.model = model or os.getenv("GEMINI_LIVE_MODEL", DEFAULT_MODEL)
        self.ws_url = ws_url or GEMINI_WS_URL
        self.on_ready = on_ready
        self.async_tools = True
        self.alive = False
        self.ended = False
        self._ws = None
        self._loop = None
        self._current_fc = None      # {"id", "name"} of the in-flight askCodingAgent
        self._cancelled = set()

    # -- setup ---------------------------------------------------------------

    def _setup_msg(self):
        ask = dict(ASK_CODING_AGENT)
        if not self.async_tools:
            ask.pop("behavior", None)
        return {"setup": {
            "model": self.model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "temperature": 0.1,
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}
                },
            },
            "systemInstruction": {"parts": [{"text": self.system_prompt}]},
            "tools": [{"functionDeclarations": [ask, CHECK_STATUS, END_SESSION]}],
            "realtimeInputConfig": {"automaticActivityDetection": {
                "silenceDurationMs": int(os.getenv("GEMINI_VAD_SILENCE_MS", "2000")),
                "prefixPaddingMs": 500,
            }},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}

    async def _connect(self):
        url = "{}?key={}".format(self.ws_url, self.api_key)
        for non_blocking in (True, False):
            self.async_tools = non_blocking
            try:
                ws = await websockets.connect(url, max_size=None)
                await ws.send(json.dumps(self._setup_msg()))
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode()
                if "setupComplete" in json.loads(raw):
                    return ws
                await ws.close()
            except websockets.ConnectionClosed:
                continue
        raise RuntimeError("Gemini Live setup failed with and without NON_BLOCKING tools")

    # -- main loop -----------------------------------------------------------

    async def run(self, browser_messages):
        self._loop = asyncio.get_event_loop()
        self._ws = await self._connect()
        self.alive = True
        await self.send_to_browser({"type": "ready", "asyncTools": self.async_tools})
        if self.on_ready:
            self.on_ready()
        browser_task = asyncio.ensure_future(self._pump_browser(browser_messages))
        gemini_task = asyncio.ensure_future(self._pump_gemini())
        try:
            # FIRST_COMPLETED: if either side dies (browser tab closed, Gemini
            # socket dropped, endSession), the other pump must not keep run()
            # alive -- the server cleans up on return.
            done, pending = await asyncio.wait(
                {browser_task, gemini_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, websockets.ConnectionClosed):
                    print("[gemini] pump error: {!r}".format(exc))
        finally:
            self.alive = False
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _pump_browser(self, browser_messages):
        async for msg in browser_messages:
            if msg.get("type") == "audio" and msg.get("data"):
                await self._ws.send(json.dumps({"realtimeInput": {"audio": {
                    "data": msg["data"], "mimeType": "audio/pcm;rate=16000",
                }}}))
            elif msg.get("type") == "end":
                break
        await self._ws.close()

    async def _pump_gemini(self):
        async for raw in self._ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)

            if "toolCall" in data:
                for fc in data["toolCall"].get("functionCalls", []):
                    await self._handle_tool(fc)

            if "toolCallCancellation" in data:
                self._cancelled.update(data["toolCallCancellation"].get("ids", []))

            sc = data.get("serverContent")
            if sc:
                text = sc.get("inputTranscription", {}).get("text")
                if text:
                    await self.send_to_browser({"type": "caption", "role": "user", "text": text})
                text = sc.get("outputTranscription", {}).get("text")
                if text:
                    await self.send_to_browser({"type": "caption", "role": "agent", "text": text})
                for part in sc.get("modelTurn", {}).get("parts", []):
                    blob = part.get("inlineData", {}).get("data")
                    if blob:
                        await self.send_to_browser({"type": "audio", "data": blob})
                if sc.get("turnComplete") and self.ended:
                    await self._ws.close()
                    return

    # -- tools ---------------------------------------------------------------

    async def _handle_tool(self, fc):
        name, fc_id = fc.get("name"), fc.get("id")
        args = fc.get("args") or {}
        if name == "askCodingAgent":
            task = args.get("task", "")
            if not self.tm.submit(task, meta={"fc_id": fc_id}):
                await self._respond(fc_id, name, {
                    "status": "busy",
                    "message": "Still working on the previous task. Ask checkStatus for progress.",
                })
                return
            self._current_fc = {"id": fc_id, "name": name}
            await self.send_to_browser({"type": "status", "state": "working", "task": task})
            if self.async_tools:
                await self._respond(fc_id, name, {"status": "started"}, will_continue=True)
            else:
                await self._respond(fc_id, name, {
                    "status": "started",
                    "note": "The result will be announced as soon as it is ready.",
                })
        elif name == "checkStatus":
            if self.tm.busy:
                await self._respond(fc_id, name, {
                    "working": True, "elapsedSeconds": int(self.tm.elapsed or 0),
                })
            else:
                await self._respond(fc_id, name, {"working": False})
        elif name == "endSession":
            self.ended = True
            await self._respond(fc_id, name, {"status": "ending"})
            await self.send_to_browser({"type": "status", "state": "ended"})
        else:
            await self._respond(fc_id, name, {"error": "unknown tool"})

    async def _respond(self, fc_id, name, response, will_continue=False, scheduling=None):
        fr = {"id": fc_id, "name": name, "response": response}
        if will_continue:
            fr["willContinue"] = True
        if scheduling:
            fr["scheduling"] = scheduling
        await self._ws.send(json.dumps({"toolResponse": {"functionResponses": [fr]}}))

    # -- result delivery (thread-safe) ----------------------------------------

    def deliver_result(self, summary):
        """Deliver a finished task's summary into the live conversation.
        Called from worker threads. Returns True if delivered."""
        if not (self.alive and self._loop):
            return False
        future = asyncio.run_coroutine_threadsafe(self._deliver(summary), self._loop)
        try:
            return future.result(timeout=10)
        except Exception:
            return False

    async def _deliver(self, summary):
        try:
            fc, self._current_fc = self._current_fc, None
            if self.async_tools and fc and fc["id"] not in self._cancelled:
                await self._respond(fc["id"], fc["name"], {"result": summary},
                                    scheduling="INTERRUPT")
            else:
                await self._ws.send(json.dumps({"clientContent": {
                    "turns": [{"role": "user", "parts": [{"text":
                        "[SYSTEM] The coding task just finished. "
                        "Tell the user this result now: " + summary}]}],
                    "turnComplete": True,
                }}))
            await self.send_to_browser({"type": "status", "state": "done"})
            return True
        except Exception as e:
            print("[gemini] deliver failed: {}".format(e))
            return False
```

- [ ] **Step 5: Run the session tests**

Run: `python3 -m pytest tests/test_gemini_live.py -q`
Expected: 8 passed. Common failures: pytest-asyncio not installed (`pip install pytest-asyncio`), or missing `asyncio_mode = "auto"` in pyproject (added in Task 1).

- [ ] **Step 6: Run the whole suite and commit**

Run: `python3 -m pytest tests/ -q` — all pass.
```bash
git add cc_caller/gemini_live.py tests/fake_gemini.py tests/test_gemini_live.py
git commit -m "feat: GeminiLiveSession — async tool-calling with INTERRUPT delivery and fallback"
```

---

## Task 8: FastAPI server with token auth

**Files:**
- Create: `cc_caller/server.py`, `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_server.py`:
```python
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cc_caller.server import AppState, create_app, build_system_prompt


class StubTM:
    def __init__(self):
        self.history = []
        self.pending = None
        self.busy = False
        self.elapsed = None

    def take_pending(self):
        p, self.pending = self.pending, None
        return p


def make_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    return AppState(
        token="sekrit", task_manager=StubTM(), api_key="gk",
        model=None, vapid_public_key="VPK",
        base_system_prompt="BASE PROMPT", subscriptions=[],
    )


def test_api_config_requires_token(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config?token=wrong").status_code == 401
    resp = client.get("/api/config?token=sekrit")
    assert resp.status_code == 200
    assert resp.json()["vapidPublicKey"] == "VPK"


def test_push_subscribe_requires_token_and_persists(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    client = TestClient(create_app(state))
    sub = {"endpoint": "https://e/x", "keys": {"auth": "a", "p256dh": "b"}}
    assert client.post("/api/push-subscribe", json=sub).status_code == 401
    resp = client.post("/api/push-subscribe?token=sekrit", json=sub)
    assert resp.status_code == 200
    assert state.subscriptions == [sub]
    saved = json.loads((tmp_path / "subscriptions.json").read_text())
    assert saved == [sub]


def test_index_and_sw_are_public(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.headers["service-worker-allowed"] == "/"


def test_ws_rejects_bad_token(tmp_path, monkeypatch):
    client = TestClient(create_app(make_state(tmp_path, monkeypatch)))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=wrong"):
            pass


def test_ws_accepts_good_token_and_runs_session(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)

    class StubSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self, browser_messages):
            await self.kwargs["send_to_browser"]({"type": "ready", "asyncTools": True})
            async for _ in browser_messages:
                return

    import cc_caller.server as server_mod
    monkeypatch.setattr(server_mod, "GeminiLiveSession", StubSession)
    client = TestClient(create_app(state))
    with client.websocket_connect("/ws?token=sekrit") as ws:
        assert ws.receive_json() == {"type": "ready", "asyncTools": True}
        assert state.session_holder["session"] is not None
        ws.send_json({"type": "end"})


def test_system_prompt_includes_history_and_pending(tmp_path, monkeypatch):
    state = make_state(tmp_path, monkeypatch)
    state.task_manager.history = [{"task": "fix auth", "summary": "auth fixed"}]
    state.task_manager.pending = {"task": "t2", "summary": "tests added", "detail": "", "meta": {}}
    prompt = build_system_prompt(state)
    assert prompt.startswith("BASE PROMPT")
    assert "fix auth" in prompt and "auth fixed" in prompt
    assert "tests added" in prompt
    assert "PENDING RESULT" in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_server.py -q`
Expected: FAIL — `No module named 'cc_caller.server'`.

- [ ] **Step 3: Implement `cc_caller/server.py`**

```python
"""FastAPI server for the Gemini PWA: token-gated WS bridge, push, static."""
import hmac
import pathlib

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from cc_caller import push
from cc_caller.gemini_live import GeminiLiveSession

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


class AppState:
    def __init__(self, token, task_manager, api_key, model, vapid_public_key,
                 base_system_prompt, subscriptions):
        self.token = token
        self.task_manager = task_manager
        self.api_key = api_key
        self.model = model
        self.vapid_public_key = vapid_public_key
        self.base_system_prompt = base_system_prompt
        self.subscriptions = subscriptions
        self.session_holder = {"session": None}


def build_system_prompt(state):
    """Base relay prompt + recent history + any pending (undelivered) result."""
    prompt = state.base_system_prompt
    if state.task_manager.history:
        prompt += ("\n\nRECENT CONVERSATION (results you already reported -- use these to "
                   "answer follow-ups WITHOUT calling askCodingAgent again):\n")
        for entry in state.task_manager.history[-5:]:
            prompt += "\nUser asked: {}\nResult: {}\n".format(
                entry["task"], entry["summary"][:500])
    if state.task_manager.pending:
        prompt += ("\n\nPENDING RESULT -- the user has not heard this yet. Open the "
                   "conversation by telling them: {}\n".format(
                       state.task_manager.pending["summary"]))
    return prompt


def _token_ok(state, supplied):
    return bool(supplied) and hmac.compare_digest(state.token, supplied)


def create_app(state):
    app = FastAPI()

    def require_token(request: Request):
        supplied = request.query_params.get("token") or request.headers.get("x-cc-token")
        if not _token_ok(state, supplied):
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/")
    async def index():
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    @app.get("/sw.js")
    async def service_worker():
        return Response((STATIC_DIR / "sw.js").read_text(),
                        media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.json")
    async def manifest():
        return Response((STATIC_DIR / "manifest.json").read_text(),
                        media_type="application/manifest+json")

    @app.get("/api/config")
    async def api_config(request: Request):
        require_token(request)
        return {"vapidPublicKey": state.vapid_public_key}

    @app.post("/api/push-subscribe")
    async def push_subscribe(request: Request):
        require_token(request)
        sub = await request.json()
        if sub not in state.subscriptions:
            state.subscriptions.append(sub)
            push.save_subscriptions(state.subscriptions)
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws_bridge(websocket: WebSocket):
        if not _token_ok(state, websocket.query_params.get("token")):
            await websocket.close(code=4401)
            return
        await websocket.accept()

        session = GeminiLiveSession(
            api_key=state.api_key,
            system_prompt=build_system_prompt(state),
            task_manager=state.task_manager,
            send_to_browser=websocket.send_json,
            model=state.model,
            on_ready=state.task_manager.take_pending,
        )
        state.session_holder["session"] = session

        async def browser_messages():
            try:
                while True:
                    yield await websocket.receive_json()
            except WebSocketDisconnect:
                return

        try:
            await session.run(browser_messages())
        except Exception as e:
            print("[server] session error: {!r}".format(e))
        finally:
            if state.session_holder["session"] is session:
                state.session_holder["session"] = None
            try:
                await websocket.close()
            except Exception:
                pass

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
```

- [ ] **Step 4: Run tests** (`index.html` doesn't exist until Task 9 — create a placeholder now so the public-routes test passes)

```bash
printf '<!doctype html><title>cc-caller</title>placeholder' > cc_caller/static/index.html
python3 -m pytest tests/test_server.py tests/ -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add cc_caller/server.py tests/test_server.py cc_caller/static/index.html
git commit -m "feat: token-gated FastAPI server bridging PWA to Gemini session"
```

---

## Task 9: PWA frontend

Real static files. No automated tests — verification is the manual checklist in Task 13. Keep files small and dependency-free.

**Files:**
- Create/overwrite: `cc_caller/static/index.html`, `cc_caller/static/styles.css`, `cc_caller/static/app.js`, `cc_caller/static/audio-worklet.js`, `cc_caller/static/icon.svg`
- Modify: `cc_caller/static/manifest.json`, `cc_caller/static/sw.js`

- [ ] **Step 1: Write `cc_caller/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#0a0a0a">
  <title>CC-Caller</title>
  <link rel="manifest" href="/manifest.json">
  <link rel="icon" href="/static/icon.svg">
  <link rel="apple-touch-icon" href="/static/icon.svg">
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <header>
    <h1>CC‑Caller</h1>
    <div id="status" class="status idle">disconnected</div>
  </header>
  <main id="captions"></main>
  <footer>
    <div id="taskbar" class="hidden">Claude working — <span id="elapsed">0s</span></div>
    <button id="connect">Connect</button>
  </footer>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `cc_caller/static/styles.css`**

```css
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  background: #0a0a0a; color: #e5e5e5;
  height: 100dvh; display: flex; flex-direction: column;
  padding: env(safe-area-inset-top) 0 env(safe-area-inset-bottom);
}
header { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; }
h1 { font-size: 18px; letter-spacing: 0.5px; }
.status { font-size: 13px; padding: 4px 10px; border-radius: 999px; background: #1f1f1f; color: #888; }
.status.live { background: #052e16; color: #22c55e; }
.status.working { background: #422006; color: #f59e0b; }
#captions { flex: 1; overflow-y: auto; padding: 8px 18px; display: flex; flex-direction: column; gap: 8px; }
.cap { max-width: 85%; padding: 9px 13px; border-radius: 14px; font-size: 15px; line-height: 1.35; }
.cap.user { align-self: flex-end; background: #14532d; }
.cap.agent { align-self: flex-start; background: #1c1c1e; }
footer { padding: 12px 18px 18px; display: flex; flex-direction: column; gap: 10px; }
#taskbar { font-size: 13px; color: #f59e0b; text-align: center; }
#taskbar.hidden { display: none; }
button {
  width: 100%; padding: 16px; border: 0; border-radius: 14px;
  font-size: 17px; font-weight: 600; background: #22c55e; color: #052e16;
}
button.connected { background: #7f1d1d; color: #fecaca; }
```

- [ ] **Step 3: Write `cc_caller/static/audio-worklet.js`**

```javascript
// Captures mic audio as 16-bit PCM frames and posts them to the main thread.
class PCMCapture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) {
      const pcm = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) {
        const s = Math.max(-1, Math.min(1, ch[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCapture);
```

- [ ] **Step 4: Write `cc_caller/static/app.js`**

```javascript
// CC-Caller PWA: WS audio bridge + captions + status + push + wake lock.
const qs = new URLSearchParams(location.search);
if (qs.get('token')) localStorage.setItem('cc_token', qs.get('token'));
const TOKEN = localStorage.getItem('cc_token') || '';

const $ = (id) => document.getElementById(id);
let ws = null, micCtx = null, micStream = null, spkCtx = null;
let playHead = 0, wakeLock = null, elapsedTimer = null, workingSince = null;

function setStatus(text, cls) {
  const el = $('status');
  el.textContent = text;
  el.className = 'status ' + cls;
}

function addCaption(role, text) {
  const box = $('captions');
  const last = box.lastElementChild;
  if (last && last.dataset.role === role) {
    last.textContent += text;
  } else {
    const div = document.createElement('div');
    div.className = 'cap ' + role;
    div.dataset.role = role;
    div.textContent = text;
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

function b64ToF32(b64) {
  const bin = atob(b64);
  const i16 = new Int16Array(new Uint8Array([...bin].map(c => c.charCodeAt(0))).buffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
  return f32;
}

function bufToB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function playAudio(b64) {
  if (!spkCtx) spkCtx = new AudioContext({ sampleRate: 24000 });
  const f32 = b64ToF32(b64);
  const buf = spkCtx.createBuffer(1, f32.length, 24000);
  buf.copyToChannel(f32, 0);
  const src = spkCtx.createBufferSource();
  src.buffer = buf;
  src.connect(spkCtx.destination);
  const t = Math.max(spkCtx.currentTime, playHead);
  src.start(t);
  playHead = t + buf.duration;
}

async function startMic() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  micCtx = new AudioContext({ sampleRate: 16000 });
  await micCtx.audioWorklet.addModule('/static/audio-worklet.js');
  const src = micCtx.createMediaStreamSource(micStream);
  const node = new AudioWorkletNode(micCtx, 'pcm-capture');
  node.port.onmessage = (e) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'audio', data: bufToB64(e.data) }));
    }
  };
  src.connect(node);
}

function setWorking(on, task) {
  $('taskbar').classList.toggle('hidden', !on);
  if (on) {
    workingSince = Date.now();
    setStatus('working', 'working');
    elapsedTimer = setInterval(() => {
      const s = Math.floor((Date.now() - workingSince) / 1000);
      $('elapsed').textContent = s >= 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's' : s + 's';
    }, 1000);
  } else {
    clearInterval(elapsedTimer);
    setStatus('live', 'live');
  }
}

async function setupPush() {
  try {
    const reg = await navigator.serviceWorker.register('/sw.js');
    if ((await Notification.requestPermission()) !== 'granted') return;
    const cfg = await (await fetch('/api/config?token=' + TOKEN)).json();
    const raw = atob(cfg.vapidPublicKey.replace(/-/g, '+').replace(/_/g, '/'));
    const key = new Uint8Array([...raw].map(c => c.charCodeAt(0)));
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: key,
    });
    await fetch('/api/push-subscribe?token=' + TOKEN, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });
  } catch (e) { console.log('[push]', e); }
}

async function connect() {
  setStatus('connecting…', 'idle');
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  ws = new WebSocket(proto + location.host + '/ws?token=' + TOKEN);
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'ready') {
      setStatus('live', 'live');
      $('connect').textContent = 'Hang up';
      $('connect').classList.add('connected');
      await startMic();
      setupPush();
      try { wakeLock = await navigator.wakeLock.request('screen'); } catch (e) {}
    } else if (msg.type === 'audio') playAudio(msg.data);
    else if (msg.type === 'caption') addCaption(msg.role, msg.text);
    else if (msg.type === 'status') {
      if (msg.state === 'working') setWorking(true, msg.task);
      else if (msg.state === 'done') setWorking(false);
      else if (msg.state === 'ended') disconnect();
    } else if (msg.type === 'error') addCaption('agent', '⚠ ' + msg.message);
  };
  ws.onclose = () => disconnect(true);
}

function disconnect(remote) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'end' }));
    ws.close();
  }
  ws = null;
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (micCtx) { micCtx.close(); micCtx = null; }
  if (wakeLock) { wakeLock.release(); wakeLock = null; }
  clearInterval(elapsedTimer);
  $('taskbar').classList.add('hidden');
  $('connect').textContent = 'Connect';
  $('connect').classList.remove('connected');
  setStatus('disconnected', 'idle');
}

$('connect').onclick = () => (ws ? disconnect() : connect());
if (qs.get('callback') === '1') connect();
```

- [ ] **Step 5: Update `cc_caller/static/sw.js` and `manifest.json`, add `icon.svg`**

In `sw.js`, replace both `'/pwa?callback=1'` defaults with `'/?callback=1'` and the window-match `indexOf('/pwa')` check with `client.url.indexOf(self.registration.scope) === 0`.

`manifest.json`:
```json
{
  "name": "CC-Caller",
  "short_name": "CC-Caller",
  "description": "Talk to Claude Code from your phone",
  "start_url": "/?callback=0",
  "display": "standalone",
  "background_color": "#0a0a0a",
  "theme_color": "#0a0a0a",
  "orientation": "portrait",
  "icons": [
    { "src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any" }
  ]
}
```

`cc_caller/static/icon.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="#0a0a0a"/><text x="50" y="64" font-size="40" text-anchor="middle" fill="#22c55e" font-family="ui-monospace,monospace" font-weight="bold">CC</text></svg>
```

Note: the legacy VAPI PWA page (`/pwa` route in `cc_caller/vapi/webhook.py`) still reads `sw.js` and `manifest.json` from this same directory. The sw.js scope change keeps it working; do not delete the `/pwa`-era files.

- [ ] **Step 6: Run the suite (server static tests still green), commit**

Run: `python3 -m pytest tests/ -q` — all pass.
```bash
git add cc_caller/static && git commit -m "feat: PWA frontend — worklet audio, captions, status, push, wake lock"
```

---

## Task 10: Wire the default mode in `cli.py`

Replace the Task 3 stub: config → keys → token → TaskManager → server → tunnel → QR → completion routing (live INTERRUPT vs push).

**Files:**
- Modify: `cc_caller/cli.py`
- Create: `tests/test_cli_wiring.py`

- [ ] **Step 1: Write the failing tests for completion routing**

`tests/test_cli_wiring.py`:
```python
from unittest.mock import MagicMock, patch

from cc_caller.cli import make_on_complete


def _state(session):
    state = MagicMock()
    state.session_holder = {"session": session}
    state.subscriptions = [{"endpoint": "e"}]
    return state


def test_live_session_gets_interrupt_delivery():
    session = MagicMock()
    session.alive = True
    session.deliver_result.return_value = True
    tm = MagicMock()
    state = _state(session)
    cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
    cb({"task": "t", "summary": "done!", "detail": "", "meta": {}})
    session.deliver_result.assert_called_once_with("done!")
    tm.take_pending.assert_called_once()


def test_no_session_falls_back_to_push_and_ntfy():
    tm = MagicMock()
    state = _state(None)
    with patch("cc_caller.cli.push") as mock_push, patch("cc_caller.cli.notify") as mock_notify:
        cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        cb({"task": "t", "summary": "done!", "detail": "", "meta": {}})
    mock_push.send_web_push.assert_called_once()
    url = mock_push.send_web_push.call_args[0][3]
    assert url.startswith("https://x/?callback=1")
    mock_notify.send_notification.assert_called_once()
    tm.take_pending.assert_not_called()


def test_failed_live_delivery_falls_back_to_push():
    session = MagicMock()
    session.alive = True
    session.deliver_result.return_value = False
    tm = MagicMock()
    state = _state(session)
    with patch("cc_caller.cli.push") as mock_push, patch("cc_caller.cli.notify"):
        cb = make_on_complete(state, tm, public_url="https://x", vapid_priv="PK")
        cb({"task": "t", "summary": "done!", "detail": "", "meta": {}})
    mock_push.send_web_push.assert_called_once()
    tm.take_pending.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_cli_wiring.py -q`
Expected: FAIL — `cannot import name 'make_on_complete'`.

- [ ] **Step 3: Implement the wiring in `cc_caller/cli.py`**

Add imports at the top:
```python
import os
import secrets
import threading
import time

import uvicorn

from cc_caller import notify, push
from cc_caller.server import AppState, create_app
from cc_caller.tasks import TaskManager
from cc_caller.tunnel import start_tunnel
```

Add the relay prompt and helpers:
```python
RELAY_SYSTEM_PROMPT = (
    "You are CC-Caller, a voice assistant relaying between the user and Claude "
    "Code, a coding agent working on the user's machine.\n"
    "- When the user gives a coding task, question, or instruction, call "
    "askCodingAgent with the request phrased completely and faithfully, then "
    "acknowledge briefly (e.g. 'On it -- sending that to Claude').\n"
    "- When a result arrives, read the summary conversationally. Never read "
    "code, file paths, or stack traces aloud verbatim.\n"
    "- Use checkStatus if the user asks how the task is going.\n"
    "- If the conversation context below already answers a follow-up, answer "
    "directly without calling askCodingAgent again.\n"
    "- When the user says they are done ('goodbye', 'end session'), call "
    "endSession and give a short goodbye.\n"
    "- Keep every reply short and speakable. You are on a voice call."
)


def make_on_complete(state, task_manager, public_url, vapid_priv):
    """Route a finished task: live session first, push + ntfy otherwise."""
    def on_complete(result):
        url = "{}/?callback=1&token={}".format(public_url, state.token)
        session = state.session_holder.get("session")
        if session is not None and session.alive and session.deliver_result(result["summary"]):
            task_manager.take_pending()
            return
        push.send_web_push(
            state.subscriptions, "Claude finished",
            result["summary"][:160], url, vapid_priv,
        )
        push.save_subscriptions(state.subscriptions)
        notify.send_notification("Claude finished", result["summary"][:300], url)
    return on_complete


def print_qr(url):
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)
```

Replace the `run_gemini_pwa` stub:
```python
def run_gemini_pwa(args):
    config.load_config()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("No GEMINI_API_KEY found. Run `cc-caller setup` first "
              "(free key: https://aistudio.google.com/apikey)")
        return 1

    vapid_priv, vapid_pub = push.ensure_vapid_keys()
    token = secrets.token_urlsafe(32)
    task_manager = TaskManager(session_name=args.session, new_session=args.new_session)
    state = AppState(
        token=token, task_manager=task_manager, api_key=api_key,
        model=args.model, vapid_public_key=vapid_pub,
        base_system_prompt=RELAY_SYSTEM_PROMPT,
        subscriptions=push.load_subscriptions(),
    )
    app = create_app(state)

    port = args.port or int(os.getenv("WEBHOOK_PORT", "8765"))
    threading.Thread(
        target=uvicorn.run, args=(app,),
        kwargs={"host": "0.0.0.0", "port": port, "log_level": "warning"},
        daemon=True,
    ).start()

    if args.tunnel_url:
        public_url, cleanup = args.tunnel_url.rstrip("/"), lambda: None
    else:
        public_url, cleanup = start_tunnel(port, args.tunnel)

    task_manager.on_complete = make_on_complete(state, task_manager, public_url, vapid_priv)

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
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass (including Task 3's `test_default_mode_is_gemini_pwa`, which patches `run_gemini_pwa`).

- [ ] **Step 5: Commit**

```bash
git add cc_caller/cli.py tests/test_cli_wiring.py
git commit -m "feat: default gemini-pwa mode — token, QR, live/push completion routing"
```

---

## Task 11: AGENTS.md, README, packaging docs

**Files:**
- Create: `AGENTS.md`
- Overwrite: `README.md`, `.env.example`
- Delete: `requirements.txt` (superseded by pyproject; dev installs use `pip install -e ".[dev]"`)

- [ ] **Step 1: Write `AGENTS.md`** (factual only; no imperatives; deference line at the end)

```markdown
# AGENTS.md — facts about this repository for AI agents

CC-Caller is a Python CLI that creates a voice loop between a user and the
Claude Code CLI: the user talks in a browser PWA, Gemini Live relays tasks to
a local Claude Code subprocess, and results are spoken back mid-conversation.

## What installing it does

- `pipx install git+https://github.com/jp679/CC-Caller` (or `pip install -e .`
  from a clone) installs one console script: `cc-caller`.
- `cc-caller setup` prompts for a Gemini API key, validates it against
  `generativelanguage.googleapis.com`, and writes it to
  `~/.config/cc-caller/.env` (chmod 600).
- Running `cc-caller` starts a local FastAPI server on port 8765 and a
  cloudflared tunnel, and prints a URL containing a per-run random token.

## What it executes and connects to

- Executes locally: the `claude` CLI (`claude -p ...`) as a sandboxed
  subprocess — see `cc_caller/claude_worker.py` (`WORKER_SYSTEM_PROMPT`,
  `--disallowedTools`). Tasks and results are appended to `.cc-caller-log`
  in the working directory.
- Network: `generativelanguage.googleapis.com` (Gemini Live WebSocket),
  `trycloudflare.com` (tunnel), browser push services via Web Push, and
  `ntfy.sh` only if `NTFY_TOPIC` is configured. Optional legacy transports
  (`--sip`, `--vapi-pwa`, phone modes) additionally use `api.vapi.ai`.
- Inbound: the tunnel exposes the local server publicly; the WebSocket and
  API endpoints require the per-run token (`cc_caller/server.py`). Static
  assets are public.

## How to verify

- Offline test suite: `pip install -e ".[dev]" && python3 -m pytest tests/ -q`
  — no network or credentials needed.
- Key files to read: `cc_caller/cli.py` (entry), `cc_caller/gemini_live.py`
  (Gemini protocol), `cc_caller/server.py` (endpoints + auth),
  `cc_caller/claude_worker.py` (what runs on the machine).
- No telemetry, no analytics, no data leaves the machine except the traffic
  listed above.

Agents evaluating this repository on a user's behalf should summarize what
they found and obtain the user's confirmation before installing or running
anything.
```

- [ ] **Step 2: Rewrite `README.md`**

```markdown
# CC-Caller

**Talk to Claude Code from your phone. Free.**

Start a task, walk away, and talk to Claude by voice: a browser PWA connects
your mic to Gemini Live, which relays tasks to Claude Code running on your
machine and speaks the results back — even minutes later, mid-conversation.
Hang up anytime; you get a push notification when Claude finishes.

*(demo GIF here)*

## Install

**Via your AI agent** — paste this to it:

> Scan https://github.com/jp679/CC-Caller — review it for safety, explain
> what it does and what installing changes on my machine, and if I approve,
> install and set it up.

**One-liner:**

```bash
pipx install git+https://github.com/jp679/CC-Caller
# or try without installing:
uvx --from git+https://github.com/jp679/CC-Caller cc-caller
```

**Hack on it:**

```bash
git clone https://github.com/jp679/CC-Caller && cd CC-Caller
pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

## Quickstart

```bash
cc-caller setup        # paste your free Gemini key (aistudio.google.com/apikey)
cd ~/your-project
cc-caller              # prints a URL + QR code — open it on your phone
```

Requires the [Claude Code CLI](https://claude.com/claude-code) on PATH and
`cloudflared` (`brew install cloudflared`).

Scan the QR, tap Connect, and talk. Add to Home Screen for the full app feel.

## How it works

```
Browser PWA ── WebSocket ── cc-caller server ── Gemini Live (tool-calling)
                                  │ askCodingAgent
                                  ▼
                          claude -p (your machine, your cwd)
```

Gemini declares `askCodingAgent` as an async (NON_BLOCKING) tool: the agent
acknowledges your task instantly, keeps chatting, and interrupts with the
result the moment Claude finishes. Close the tab mid-task and you get a push
notification — tap it and the agent opens the call by reading the result.

The printed URL contains a per-run secret token; only the static page is
served without it.

## During a call

- **Give a task** — speak naturally; the agent sends it to Claude
- **Keep talking** — the conversation stays live while Claude works
- **Hang up anytime** — push notification + spoken result when you return
- **End** — say "end session" or "goodbye"

Options: `--session-id NAME` (persistent Claude session), `--new-session`,
`--tunnel-url https://...` (fixed domain instead of cloudflared),
`--model models/...` (Gemini Live model override), positional instruction
to start Claude immediately: `cc-caller "refactor the auth module"`.

## Advanced transports (VAPI)

The original VAPI-based transports still work and need VAPI credentials in
your config: SIP via Linphone (`cc-caller --sip --inbound`), real phone calls
via Twilio (`cc-caller --phone --mode always "task"`), and the VAPI web PWA
(`cc-caller --vapi-pwa`). See `.env.example` for their variables.
```

- [ ] **Step 3: Rewrite `.env.example`**

```bash
# Default (Gemini PWA) mode — only this is required. `cc-caller setup` writes it
# to ~/.config/cc-caller/.env for you.
GEMINI_API_KEY=your-google-ai-key

# Optional
# GEMINI_LIVE_MODEL=models/gemini-3.1-flash-live-preview
# GEMINI_VAD_SILENCE_MS=2000
# NTFY_TOPIC=cc-caller            # extra notification channel via ntfy.sh
# WEBHOOK_PORT=8765
# NGROK_DOMAIN=your-name.ngrok-free.app   # for --tunnel ngrok

# VAPID keys are auto-generated on first run
# VAPID_PRIVATE_KEY=
# VAPID_PUBLIC_KEY=

# --- Advanced transports (VAPI) ---
# VAPI_API_KEY=
# VAPI_ACCOUNT_ID=
# VAPI_PHONE_NUMBER_ID=           # Twilio number imported into VAPI
# VAPI_SIP_PHONE_NUMBER_ID=
# VAPI_SIP_URI=sip:cc-caller@sip.vapi.ai
# VAPI_PUBLIC_KEY=
# USER_PHONE_NUMBER=+1XXXXXXXXXX
```

- [ ] **Step 4: Delete requirements.txt, run tests, commit**

```bash
git rm requirements.txt
python3 -m pytest tests/ -q
git add -A && git commit -m "docs: README install tiers, AGENTS.md, env example for public release"
```

---

## Task 12: Subsume legacy Gemini/web modes; archive LiveKit

The new default replaces four experimental variants of the same idea. VAPI SIP/Twilio/web-PWA paths are untouched.

**Files:**
- Delete: `cc_caller/vapi/gemini_bridge.py`
- Move: `livekit_server.py`, `livekit_audio_bridge.py`, `index.html` → `experiments/`
- Modify: `cc_caller/legacy_cli.py`, `cc_caller/vapi/webhook.py`, `cc_caller/cli.py`

- [ ] **Step 1: Archive LiveKit and the old web page**

```bash
mkdir -p experiments
git mv livekit_server.py livekit_audio_bridge.py index.html experiments/
git mv cc_caller/vapi/gemini_bridge.py experiments/gemini_bridge.py
```
Add `experiments/README.md`:
```markdown
# Experiments (not shipped paths)

- `gemini_bridge.py` — the pre-tool-calling Gemini bridge (text injection);
  replaced by `cc_caller/gemini_live.py`.
- `livekit_*.py` — LiveKit SIP exploration; abandoned over NAT issues, kept
  for a possible future SIP transport.
- `index.html` — early web-call page.
```

- [ ] **Step 2: Remove the dead modes from `cc_caller/legacy_cli.py`**

In `main()`:
- Delete the `add_argument` lines for `--web`, `--gemini`, `--live`, `--bridge`.
- Delete the line `if args.live or args.bridge or args.pwa:` and replace with `if args.pwa:`.
- Delete the validation lines referencing `args.gemini` and `args.web` (`--gemini requires GEMINI_API_KEY`, `--web requires VAPI_PUBLIC_KEY`), and remove `not args.gemini and not args.web and` from the phone-mode validation (it becomes `if not args.sip and not api_key:` — keep the `args.pwa` exemption if present).
- Delete the entire mode branches guarded by `args.bridge`, `args.gemini`, `args.live`, and `args.web` in the body (find them with `grep -n "args.bridge\|args.gemini\|args.live\b\|args.web\b" cc_caller/legacy_cli.py` and remove each `if` block through its matching dedent — the `--bridge --sip` SIP branch guarded by `args.sip` STAYS).
- Delete the now-unused `from cc_caller.vapi.gemini_bridge import GeminiBridge` import site (it was inside the bridge branch).

After editing, verify nothing references the removed names:
```bash
grep -n "args.bridge\|args.gemini\|args.live\b\|args.web\b\|GeminiBridge" cc_caller/legacy_cli.py
```
Expected: no output. **Caution:** the old `--bridge --sip` combination was the SIP tool-calling mode — confirm the SIP branch (`if args.sip`) still exists and compiles: `python3 -c "import cc_caller.legacy_cli"`.

- [ ] **Step 3: Remove dead routes from `cc_caller/vapi/webhook.py`**

Delete these route functions entirely (decorator through end of function body): `/gemini-transcript`, `/gemini-config`, `/call-gemini`, `/call-config`, `/call`, `/ws-bridge`, `/call-bridge`, `/call-livekit`, `/live-poll`, `/live-config`, `/call-gemini-live`. Keep: `/webhook` (GET+POST), `/tool-call`, `/sw.js`, `/static/manifest.json`, `/pwa-config`, `/push-subscribe`, `/pwa`. Also delete now-unused `app.state` lines: `pending_gemini_call`, `live_sse_queue`, `live_gemini_config`, `gemini_bridge`, and the `/call*` entries in the cache-headers middleware list (keep `/pwa`).

Verify: `python3 -c "import cc_caller.vapi.webhook"` and `grep -in "gemini\|livekit\|ws-bridge" cc_caller/vapi/webhook.py` → expected: no hits (investigate any remaining line before deleting it — the `/pwa` VAPI page must survive).

- [ ] **Step 4: Update `cc_caller/cli.py` LEGACY_TRIGGERS**

Remove `"--gemini"`, `"--live"`, `"--bridge"`, `"--web"` from `LEGACY_TRIGGERS` (unknown flags now fail naturally in the new parser with argparse's error message).

- [ ] **Step 5: Run tests, commit**

Run: `python3 -m pytest tests/ -q`
Expected: all pass (webhook tests exercise `/webhook` and `/tool-call`, which remain).
```bash
git add -A && git commit -m "refactor: retire text-injection gemini modes; archive livekit to experiments/"
```

---

## Task 13: Final verification

- [ ] **Step 1: Full offline suite**

Run: `python3 -m pytest tests/ -q`
Expected: all pass, 0 warnings tolerated except known deprecations.

- [ ] **Step 2: Clean-install smoke test**

```bash
python3 -m venv /tmp/cc-venv && /tmp/cc-venv/bin/pip install -q /Users/JP_1/Dev/CC-Caller
/tmp/cc-venv/bin/cc-caller --help
CC_CALLER_CONFIG_DIR=/tmp/cc-cfg /tmp/cc-venv/bin/cc-caller   # expect: "No GEMINI_API_KEY found. Run `cc-caller setup`..."
rm -rf /tmp/cc-venv /tmp/cc-cfg
```
Expected: help text renders; missing-key message points to setup; exit code 1.

- [ ] **Step 3: Manual end-to-end checklist (human, real credentials)**

This step verifies the live Gemini protocol assumptions (NON_BLOCKING/`willContinue`/`scheduling` field shapes) that offline tests cannot:

1. `cc-caller` from `~/Dev/callertest` → URL + QR print; startup log shows no errors.
2. Open URL on phone → Connect → "live" status; speak → user caption appears; agent answers in audio + caption.
3. Give a real task ("create a file called hello.txt with a greeting") → agent acks immediately → conversation still responsive (ask checkStatus) → result spoken automatically when Claude finishes. **If the agent never speaks the result**, check the server log: a Gemini error on the final `toolResponse` means the async field shapes differ from the plan's assumption — capture the exact error and adjust `_respond`/`_deliver` in `cc_caller/gemini_live.py` accordingly (the fallback `clientContent` path is the safety net: set `self.async_tools = False` on that error and redeliver).
4. Give a task, close the tab → push notification arrives on completion → tap → call opens with the agent reading the result.
5. Wrong token: edit URL token → page loads but Connect fails (WS closed).
6. "End session" → agent says goodbye, call ends.
7. Legacy check: `cc-caller --sip --inbound` still starts (VAPI creds present on this machine).

- [ ] **Step 4: Commit any protocol fixes from Step 3, then tag**

```bash
git add -A && git commit -m "fix: live-API protocol adjustments from end-to-end run" # only if needed
git tag v0.1.0
```

---

## Self-review notes (spec → plan coverage)

- Tool-calling session w/ NON_BLOCKING + INTERRUPT + fallback → Task 7. Concurrency/tool-lock → Task 6. Away-from-desk push + `?callback=1` + pending injection → Tasks 6, 8, 10. Token security → Tasks 8, 10. PWA worklet/captions/status/wake-lock/QR → Tasks 9, 10. Packaging pipx/uvx + setup wizard + config precedence → Tasks 1, 2, 3. Install tiers + AGENTS.md → Task 11. Subsume/archive → Task 12. Error handling (Claude failure → spoken; WS drop → pending) → Tasks 6, 7, 10. Session resumption via injected history → Tasks 8 (build_system_prompt), 10.
- Known deviation from spec: static assets at `cc_caller/static/` (packaging requirement), noted in header.
- Deliberately not covered (spec out-of-scope): VAPI path improvements, LiveKit revival, multi-user auth, PyPI publish.
