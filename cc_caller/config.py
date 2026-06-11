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
