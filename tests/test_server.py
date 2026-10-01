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
