import os
import json
import pytest
from fastapi.testclient import TestClient

import server

client = TestClient(server.app)

def test_transform_request_body_single_message():
    openai_body = {
        "messages": [{"role": "user", "content": "Halo, tes gpt-5.4-nano!"}],
        "stream": False,
        "model": "gpt-4o"
    }
    copilot_body = server.transform_request_body(openai_body)
    assert copilot_body["model"] == "gpt-5.4-nano"
    assert copilot_body["input"] == "Halo, tes gpt-5.4-nano!"
    assert "messages" not in copilot_body

def test_transform_request_body_multiple_messages():
    openai_body = {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Tell me a joke."}
        ],
        "stream": True
    }
    copilot_body = server.transform_request_body(openai_body)
    assert copilot_body["model"] == "gpt-5.4-nano"
    assert "System: You are a helpful assistant." in copilot_body["input"]
    assert "User: Tell me a joke." in copilot_body["input"]

def test_extract_text_and_format_openai_completion():
    raw_data = {"choices": [{"message": {"role": "assistant", "content": "Hello world!"}}]}
    text = server.extract_text_from_copilot_json(raw_data)
    assert text == "Hello world!"

    formatted = server.format_openai_completion_response(text)
    assert formatted["object"] == "chat.completion"
    assert formatted["model"] == "gpt-5.4-nano"
    assert formatted["choices"][0]["message"]["content"] == "Hello world!"

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["provider"] == "github-copilot"
    assert data["model"] == "gpt-5.4-nano"
    assert "authenticated" in data

def test_models_endpoint():
    response = client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {server.GATEWAY_API_KEY}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    model_ids = [m["id"] for m in data["data"]]
    assert model_ids == ["gpt-5.4-nano"]

def test_unauthorized_request():
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]}
    )
    assert response.status_code == 401

def test_authorized_unauthenticated_github(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TOKEN_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(server, "GATEWAY_API_KEY", "test-secret")

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-secret"},
        json={"messages": [{"role": "user", "content": "hello"}]}
    )
    assert response.status_code == 502
    assert "Not authenticated" in response.json()["detail"]

def test_extract_text_responses_api_shape():
    # Actual upstream /responses shape: output[].content[] is a list of parts
    raw = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "annotations": [],
                        "logprobs": [],
                        "text": "PING-TEST-OK",
                        "type": "output_text",
                    }
                ],
            }
        ],
        "output_text": None,
    }
    assert server.extract_text_from_copilot_json(raw) == "PING-TEST-OK"

def test_extract_text_output_item_content_string():
    raw = {"output": [{"type": "message", "content": "hello"}]}
    assert server.extract_text_from_copilot_json(raw) == "hello"

def test_extract_text_skips_reasoning_items():
    raw = {
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
        ]
    }
    assert server.extract_text_from_copilot_json(raw) == "answer"

def test_extract_delta_responses_api_events():
    assert server.extract_delta_from_copilot_json(
        {"type": "response.output_text.delta", "delta": "Hi"}
    ) == "Hi"
    # Non-delta Responses events must yield nothing (esp. response.completed,
    # which carries the FULL text again — yielding it would duplicate output)
    assert server.extract_delta_from_copilot_json({"type": "response.created"}) == ""
    full = {
        "type": "response.completed",
        "response": {"output": [{"content": [{"type": "output_text", "text": "full"}]}]},
    }
    assert server.extract_delta_from_copilot_json(full) == ""
    assert server.extract_delta_from_copilot_json(
        {"type": "response.failed", "response": {"error": {"message": "boom"}}}
    ) == "Error: boom"

def test_extract_delta_chat_chunk():
    chunk = {"choices": [{"index": 0, "delta": {"content": "abc"}, "finish_reason": None}]}
    assert server.extract_delta_from_copilot_json(chunk) == "abc"
