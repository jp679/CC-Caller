import sys
import types
from unittest.mock import MagicMock

from cc_caller.tunnel import start_tunnel


def _stub_pyngrok(monkeypatch):
    fake_ngrok = MagicMock()
    fake_ngrok.connect.return_value = types.SimpleNamespace(public_url="https://stable.ngrok-free.app")
    fake_module = types.ModuleType("pyngrok")
    fake_module.ngrok = fake_ngrok
    monkeypatch.setitem(sys.modules, "pyngrok", fake_module)
    return fake_ngrok


def test_ngrok_sets_authtoken_when_env_present(monkeypatch):
    fake = _stub_pyngrok(monkeypatch)
    monkeypatch.setenv("NGROK_AUTHTOKEN", "tok-123")
    monkeypatch.delenv("NGROK_DOMAIN", raising=False)
    url, cleanup = start_tunnel(8765, "ngrok")
    fake.set_auth_token.assert_called_once_with("tok-123")
    assert url == "https://stable.ngrok-free.app"


def test_ngrok_skips_authtoken_when_absent(monkeypatch):
    fake = _stub_pyngrok(monkeypatch)
    monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
    monkeypatch.setenv("NGROK_DOMAIN", "my.ngrok-free.app")
    url, cleanup = start_tunnel(8765, "ngrok")
    fake.set_auth_token.assert_not_called()
    # tunnel.py calls ngrok.connect(addr=port, proto="http", domain=domain)
    call_kwargs = fake.connect.call_args[1]
    assert call_kwargs.get("domain") == "my.ngrok-free.app"
