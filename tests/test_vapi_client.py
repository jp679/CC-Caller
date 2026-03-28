import json
from unittest.mock import patch, MagicMock
from vapi_client import build_assistant_config, create_call


def test_build_assistant_config_includes_summary_and_detail():
    config = build_assistant_config(
        summary="I fixed the auth bug. What next?",
        detail="Changed login.py lines 42-58, updated token validation. All 12 tests pass.",
        webhook_url="https://abc123.ngrok.io/webhook",
    )

    assert config["firstMessage"] == "Hey, got an update for you."
    assert config["serverUrl"] == "https://abc123.ngrok.io/webhook"
    assert config["endCallPhrases"] == ["go ahead", "that's all", "stop", "we're done"]

    system_content = config["model"]["messages"][0]["content"]
    assert "I fixed the auth bug. What next?" in system_content
    assert "Changed login.py lines 42-58" in system_content
    assert config["model"]["provider"] == "anthropic"
    assert config["voice"]["provider"] == "11labs"
    assert {"type": "endCall"} in config["model"]["tools"]
    assert config["backgroundSound"] == "off"


def test_build_assistant_config_truncates_long_detail():
    long_detail = "x" * 15000
    config = build_assistant_config(
        summary="Summary here.",
        detail=long_detail,
        webhook_url="https://abc123.ngrok.io/webhook",
    )

    system_content = config["model"]["messages"][0]["content"]
    # Detail should be truncated to avoid exceeding VAPI limits
    assert len(system_content) < 12000


def test_create_call_posts_to_vapi(monkeypatch):
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"id": "call-123", "status": "queued"}

    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr("vapi_client.requests.post", mock_post)

    result = create_call(
        api_key="test-key",
        phone_number_id="phone-123",
        customer_number="+16505551234",
        assistant_config={"firstMessage": "Hello"},
    )

    assert result == {"id": "call-123", "status": "queued"}

    call_args = mock_post.call_args
    assert call_args[0][0] == "https://api.vapi.ai/call"
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-key"

    body = call_args[1]["json"]
    assert body["phoneNumberId"] == "phone-123"
    assert body["customer"]["number"] == "+16505551234"
    assert body["assistant"]["firstMessage"] == "Hello"


def test_create_call_raises_on_failure(monkeypatch):
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad request"
    mock_response.raise_for_status.side_effect = Exception("400 Bad Request")

    mock_post = MagicMock(return_value=mock_response)
    monkeypatch.setattr("vapi_client.requests.post", mock_post)

    try:
        create_call(
            api_key="test-key",
            phone_number_id="phone-123",
            customer_number="+16505551234",
            assistant_config={"firstMessage": "Hello"},
        )
        assert False, "Should have raised"
    except Exception as e:
        assert "400" in str(e)
