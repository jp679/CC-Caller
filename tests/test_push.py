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
