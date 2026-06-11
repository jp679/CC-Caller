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


def prompt_extra() -> str:
    """User calibration text appended to the relay prompt (prompt.md in the config dir)."""
    f = config_dir() / "prompt.md"
    if not f.exists():
        return ""
    try:
        return f.read_text().strip()
    except OSError as e:
        print("[config] Could not read prompt.md: {}".format(e))
        return ""


def save_config_values(**values) -> None:
    """Set keys in the config-dir .env, replacing existing lines for those keys."""
    cleaned = {}
    for key, val in values.items():
        sval = str(val)
        if "\n" in sval or "\r" in sval:
            raise ValueError("config values must not contain newlines")
        cleaned[key] = sval.strip()
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    env_file = cfg / ".env"
    lines = []
    if env_file.exists():
        lines = [
            ln for ln in env_file.read_text().splitlines()
            if ln.strip() and ln.split("=", 1)[0] not in cleaned
        ]
    for key, val in cleaned.items():
        # Double-quote so dotenv preserves inline '#' etc. on read.
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        lines.append('{}="{}"'.format(key, escaped))
        os.environ[key] = val
    # Atomic write, created 0600 from the start (no world-readable window).
    tmp = env_file.parent / (env_file.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(str(tmp), str(env_file))
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
