"""ntfy.sh notifications (optional fallback channel)."""
import os

import requests as http_requests


def send_notification(title: str, message: str, url: str = "") -> None:
    ntfy_topic = os.getenv("NTFY_TOPIC", "cc-caller")
    headers = {"Title": title, "Priority": "urgent", "Tags": "phone"}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open Call, {url}"
    try:
        http_requests.post(
            f"https://ntfy.sh/{ntfy_topic}",
            data=message,
            headers=headers,
            timeout=5,
        )
    except Exception as e:
        print(f"Notification failed: {e}")
