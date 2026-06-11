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
    assert 'GEMINI_API_KEY="test-key-123"' in (tmp_path / ".env").read_text()


def test_setup_handles_network_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    import requests as requests_lib
    with patch("cc_caller.cli.requests.get",
               side_effect=requests_lib.exceptions.ConnectionError("boom")):
        with patch("builtins.input", return_value="some-key"):
            with patch("sys.argv", ["cc-caller", "setup"]):
                rc = cli.main()
    assert rc == 1
    assert not (tmp_path / ".env").exists()


def test_setup_rejects_bad_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CC_CALLER_CONFIG_DIR", str(tmp_path))
    bad = MagicMock(status_code=400)
    with patch("cc_caller.cli.requests.get", return_value=bad):
        with patch("builtins.input", return_value="bad-key"):
            with patch("sys.argv", ["cc-caller", "setup"]):
                rc = cli.main()
    assert rc == 1
    assert not (tmp_path / ".env").exists()
