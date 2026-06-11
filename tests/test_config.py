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
