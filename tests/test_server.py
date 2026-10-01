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

def test_transform_passes_tools_through():
    tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]
    out = server.transform_request_body({
        "model": "x", "messages": [{"role": "user", "content": "hi"}],
        "tools": tools, "tool_choice": "auto",
    })
    assert out["tools"] == [{"type": "function", "name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}]
    assert out["tool_choice"] == "auto"

def test_transform_without_tools_omits_keys():
    out = server.transform_request_body({"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    assert "tools" not in out
    assert "tool_choice" not in out

def test_transform_converts_nested_tools_to_flat():
    out = server.transform_request_body({
        "model": "x",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "get_weather", "description": "d", "parameters": {"type": "object"}}}],
        "tool_choice": "auto",
    })
    assert out["tools"] == [{"type": "function", "name": "get_weather", "description": "d", "parameters": {"type": "object"}}]
    assert out["tool_choice"] == "auto"

def test_transform_tool_result_binds_function_call_output():
    out = server.transform_request_body({
        "model": "x",
        "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Weather in Jakarta?"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"Jakarta\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "30C sunny"},
        ],
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
    })
    inp = out["input"]
    assert isinstance(inp, list)
    assert inp[0] == {"role": "user", "content": "System: Be terse.\nUser: Weather in Jakarta?"}
    assert inp[1] == {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": "{\"city\":\"Jakarta\"}"}
    assert inp[2] == {"type": "function_call_output", "call_id": "call_1", "output": "30C sunny"}

def test_function_call_items_and_conversion():
    raw = {
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "call_id": "call_abc", "name": "get_weather", "arguments": "{\"city\": \"Jakarta\"}"},
            {"type": "message", "content": [{"type": "output_text", "text": "Checking."}]},
        ]
    }
    items = server._function_call_items(raw)
    assert len(items) == 1
    calls = server._to_openai_tool_calls(items)
    assert calls[0]["id"] == "call_abc"
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "get_weather"
    assert calls[0]["function"]["arguments"] == "{\"city\": \"Jakarta\"}"

def test_completion_response_with_tool_calls():
    resp = server.format_openai_completion_response(None, [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}])
    assert resp["choices"][0]["finish_reason"] == "tool_calls"
    assert resp["choices"][0]["message"]["tool_calls"][0]["id"] == "c1"

def test_completion_response_plain_stop():
    resp = server.format_openai_completion_response("hello")
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert "tool_calls" not in resp["choices"][0]["message"]

def test_chunks_from_sse_tool_events():
    added = server.chunks_from_sse_payload({
        "type": "response.output_item.added",
        "output_item": {"type": "function_call", "call_id": "call_x", "name": "get_weather"},
    })
    assert added == [{"tool_delta": {"index": 0, "type": "function", "id": "call_x", "function": {"name": "get_weather", "arguments": ""}}}]
    args = server.chunks_from_sse_payload({"type": "response.function_call_arguments.delta", "delta": "{\"city\":"})
    assert args == [{"tool_delta": {"index": 0, "function": {"arguments": "{\"city\":"}}}]
    # text delta still works through the same translator
    txt = server.chunks_from_sse_payload({"type": "response.output_text.delta", "delta": "Hi"})
    assert txt == [{"text": "Hi"}]
    # ignorable events produce nothing
    assert server.chunks_from_sse_payload({"type": "response.created"}) == []

def test_chunk_sse_tool_call_format():
    raw = server.format_openai_chunk_sse("", "req1", extra_delta={"tool_calls": [{"index": 0, "type": "function", "id": "c1", "function": {"name": "f", "arguments": "{}"}}]})
    data = json.loads(raw.decode().removeprefix("data: ").strip())
    assert data["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "f"
    fin = json.loads(server.format_openai_chunk_sse("", "req1", finish_reason="stop").decode().removeprefix("data: ").strip())
    assert fin["choices"][0]["finish_reason"] == "stop"
