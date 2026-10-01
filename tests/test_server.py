import os
import json
import pytest
from fastapi.testclient import TestClient

import server

client = TestClient(server.app)

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

def test_invalid_bearer_token():
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong-key"},
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

def test_responses_endpoint_unauthenticated_github(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TOKEN_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(server, "GATEWAY_API_KEY", "test-secret")

    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer test-secret"},
        json={"input": "hello"}
    )
    assert response.status_code == 502
    assert "Not authenticated" in response.json()["detail"]
