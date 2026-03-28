import queue
from fastapi.testclient import TestClient
from webhook import create_app


def test_end_of_call_report_extracts_user_messages():
    q = queue.Queue()
    app = create_app(q)
    client = TestClient(app)

    payload = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "hangup",
            "artifact": {
                "messages": [
                    {"role": "assistant", "message": "I finished refactoring the auth module. All tests pass. What should I work on next?"},
                    {"role": "user", "message": "Great, now add integration tests."},
                    {"role": "assistant", "message": "Got it. Anything else?"},
                    {"role": "user", "message": "No, go ahead."},
                ]
            }
        }
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200

    transcript = q.get(timeout=1)
    assert transcript == "Great, now add integration tests. No, go ahead."


def test_webhook_ignores_non_end_of_call_report():
    q = queue.Queue()
    app = create_app(q)
    client = TestClient(app)

    payload = {
        "message": {
            "type": "status-update",
            "status": "in-progress"
        }
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert q.empty()


def test_webhook_handles_no_user_messages():
    q = queue.Queue()
    app = create_app(q)
    client = TestClient(app)

    payload = {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "hangup",
            "artifact": {
                "messages": [
                    {"role": "assistant", "message": "Hello?"},
                ]
            }
        }
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert q.empty()
