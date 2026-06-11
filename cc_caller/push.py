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


def send_web_push(subscriptions: list, title: str, body: str, url: str, vapid_private_key: str) -> None:
    """Send Web Push notification to all subscribed browsers."""
    from pywebpush import webpush, WebPushException
    import json as json_lib

    payload = json_lib.dumps({"title": title, "body": body, "url": url})
    for sub in list(subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": "mailto:cc-caller@example.com"},
            )
        except WebPushException as e:
            print(f"[push] Failed: {e}")
            # Remove expired subscriptions
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
