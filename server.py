import asyncio
import json
import os
import secrets
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

load_dotenv()

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "Iv1.b507a08c87ecfe98")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "change-this-to-a-long-random-secret")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8787"))

MODEL = "gpt-5.4-nano"

GITHUB_DEVICE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_RESPONSES_URL = "https://api.githubcopilot.com/responses"

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
TOKEN_FILE = DATA_DIR / "tokens.json"

CONNECT_TIMEOUT = 15
REQUEST_TIMEOUT = 120

app = FastAPI(title="Copilot Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Token storage
# ---------------------------------------------------------

def load_tokens():
    if not TOKEN_FILE.exists():
        return {}

    try:
        return json.loads(TOKEN_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_tokens(tokens):
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tokens))

    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_FILE)

    os.chmod(TOKEN_FILE, 0o600)


# ---------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------

async def device_login():
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            GITHUB_DEVICE_URL,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "scope": "read:user",
            },
            headers={"Accept": "application/json"},
        )

        r.raise_for_status()
        device = r.json()

        print()
        print("Open:", device["verification_uri"])
        print("Code:", device["user_code"])
        print()

        interval = int(device.get("interval", 5))

        while True:
            await asyncio.sleep(interval)

            r = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "device_code": device["device_code"],
                    "grant_type": (
                        "urn:ietf:params:oauth:grant-type:device_code"
                    ),
                },
                headers={"Accept": "application/json"},
            )

            data = r.json()
            error = data.get("error")

            if "access_token" in data:
                save_tokens({
                    "github_access_token": data["access_token"],
                    "github_refresh_token": data.get("refresh_token"),
                })

                print("GitHub login OK.")
                return

            if error == "authorization_pending":
                continue

            if error == "slow_down":
                interval += 5
                continue

            raise RuntimeError(f"GitHub OAuth failed: {data}")


# ---------------------------------------------------------
# Copilot token
# ---------------------------------------------------------

async def get_copilot_token():
    tokens = load_tokens()

    github_token = tokens.get("github_access_token")

    if not github_token:
        raise RuntimeError(
            "Not authenticated. Run: python server.py login"
        )

    async with httpx.AsyncClient(
        timeout=CONNECT_TIMEOUT
    ) as client:

        r = await client.get(
            COPILOT_TOKEN_URL,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
                "User-Agent": "Copilot-Gateway",
            },
        )

        if r.status_code == 401:
            raise RuntimeError(
                "GitHub authentication expired. "
                "Run: python server.py login"
            )

        r.raise_for_status()

        data = r.json()

        token = data.get("token")

        if not token:
            raise RuntimeError(
                "GitHub did not return a Copilot token."
            )

        return token


# ---------------------------------------------------------
# Authentication
# ---------------------------------------------------------

def authenticate(authorization):
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization scheme",
        )

    key = authorization[7:]

    if not secrets.compare_digest(key, GATEWAY_API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )


# ---------------------------------------------------------
# Copilot headers
# ---------------------------------------------------------

def copilot_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",

        "copilot-integration-id": "copilot-developer-cli",
        "editor-version": "copilot/1.0.88",
        "user-agent": "copilot/1.0.88",
        "openai-intent": "conversation-agent",
        "x-interaction-type": "conversation-user",
        "copilot-harness-id": "copilot-sdk",
        "x-github-api-version": "2026-08-01",
        "X-Initiator": "user",
    }


# ---------------------------------------------------------
# Payload Transformation & Output Normalization
# ---------------------------------------------------------

def transform_request_body(body: dict) -> dict:
    """Transform OpenAI format body to Copilot /responses API format."""
    new_body = {}

    # 1. Model & Parameters
    new_body["model"] = MODEL
    if "stream" in body:
        new_body["stream"] = bool(body["stream"])
    if "temperature" in body and isinstance(body["temperature"], (int, float)):
        new_body["temperature"] = float(body["temperature"])
    if "top_p" in body and isinstance(body["top_p"], (int, float)):
        new_body["top_p"] = float(body["top_p"])

    # 1b. Pass tool-calling through. Harnesses send OpenAI chat-completions
    # style tools ({"type":"function","function":{...}}) but the upstream
    # /responses API requires the flat Responses format
    # ({"type":"function","name":...}) and rejects the nested one with 400.
    if isinstance(body.get("tools"), list) and body["tools"]:
        converted = []
        for tool in body["tools"]:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                flat = dict(tool["function"])
                flat["type"] = "function"
                converted.append(flat)
            else:
                converted.append(tool)
        if converted:
            new_body["tools"] = converted
    if body.get("tool_choice") is not None:
        new_body["tool_choice"] = body["tool_choice"]

    # 2. Extract string prompt input
    if "input" in body and isinstance(body["input"], str):
        new_body["input"] = body["input"]
    else:
        messages = body.get("messages", [])
        if isinstance(messages, list) and len(messages) > 0:
            prompt_parts = []
            input_items = []  # Responses API items (function_call(+_output))
            for m in messages:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role", "user"))

                if role == "tool":
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": m.get("tool_call_id", ""),
                        "output": m.get("content") if isinstance(m.get("content"), str) else str(m.get("content", "")),
                    })
                    continue

                content = m.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(str(c.get("text", "")))
                        elif isinstance(c, str):
                            parts.append(c)
                    content = "\n".join(parts)
                elif not isinstance(content, str):
                    content = str(content)

                if role == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                    # Re-create the function_call items so upstream can bind
                    # each following tool result to its call by id.
                    for tc in m["tool_calls"]:
                        if isinstance(tc, dict):
                            fn = tc.get("function") or {}
                            args = fn.get("arguments")
                            input_items.append({
                                "type": "function_call",
                                "call_id": tc.get("id") or f"call_{secrets.token_hex(8)}",
                                "name": fn.get("name", ""),
                                "arguments": args if isinstance(args, str) else json.dumps(args or {}),
                            })
                    continue

                prompt_parts.append(f"{role.capitalize()}: {content}")

            if input_items:
                # Multi-turn tool conversation: rebuild a Responses API input
                # list so results bind back to their function calls by id.
                # Preceding context (system/user turns) goes as a plain text
                # item first so the model keeps the original instruction.
                prior = "\n".join(prompt_parts).strip()
                if prior:
                    input_items.insert(0, {"role": "user", "content": prior})
                new_body["input"] = input_items
            elif len(messages) == 1 and messages[0].get("role") == "user":
                single_content = messages[0].get("content", "")
                if isinstance(single_content, str):
                    new_body["input"] = single_content
                else:
                    new_body["input"] = "\n".join(prompt_parts)
            else:
                new_body["input"] = "\n".join(prompt_parts)
        else:
            new_body["input"] = ""

    return new_body


def _content_parts_to_text(content) -> str:
    """Flatten Responses API content (string or list of parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif "content" in part:
                    texts.append(_content_parts_to_text(part["content"]))
        return "".join(texts)
    if isinstance(content, dict):
        inner = content.get("text")
        if isinstance(inner, str):
            return inner
        if "content" in content:
            return _content_parts_to_text(content["content"])
        return ""
    if content is None:
        return ""
    return str(content)


def extract_text_from_copilot_json(data: dict) -> str:
    """Extract assistant response text from Copilot upstream JSON response.

    The upstream /responses endpoint returns OpenAI Responses API format:
    ``output`` is a list of items (message / reasoning / tool calls) whose
    ``content`` is itself a list of parts (e.g. ``output_text``). It may
    also return plain chat-completions format, so both are handled.
    """
    if not isinstance(data, dict):
        return ""

    # Chat-completions style.
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            message = choice.get("message")
            if isinstance(message, dict):
                return _content_parts_to_text(message.get("content"))
            delta = choice.get("delta")
            if isinstance(delta, dict):
                return _content_parts_to_text(delta.get("content"))
            if "text" in choice:
                return _content_parts_to_text(choice.get("text"))

    # Responses API style: concatenate text across output items,
    # skipping reasoning items (their summaries are not the answer).
    output = data.get("output")
    if isinstance(output, list):
        texts = []
        for item in output:
            if isinstance(item, str):
                texts.append(item)
                continue
            if isinstance(item, dict):
                if item.get("type") == "reasoning":
                    continue
                texts.append(_content_parts_to_text(item.get("content")))
        return "".join(texts)
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        return _content_parts_to_text(output)

    if "text" in data:
        return _content_parts_to_text(data["text"])

    return ""


def extract_delta_from_copilot_json(data: dict) -> str:
    """Extract incremental text from one upstream SSE JSON payload.

    Handles both Responses API events (response.output_text.delta etc.)
    and chat-completions chunks. Events that carry the complete response
    (response.completed / response.done) return "" so the full text is
    not emitted a second time after the deltas.
    """
    if not isinstance(data, dict):
        return ""

    event_type = data.get("type")

    if event_type:
        if event_type == "response.output_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str):
                return delta
            return _content_parts_to_text(delta)

        if event_type in ("response.output_text.done", "response.completed", "response.done"):
            # Full text already streamed via deltas — do not re-emit.
            return ""

        if event_type == "response.failed":
            response = data.get("response") or {}
            error = response.get("error") or {}
            msg = error.get("message") if isinstance(error, dict) else str(error)
            return f"Error: {msg or 'unknown upstream error'}"

        if event_type == "error":
            err = data.get("error")
            msg = data.get("message") or (
                err.get("message") if isinstance(err, dict) else None
            )
            return f"Error: {msg or 'unknown upstream error'}"

        # Unknown/ignorable event types (response.created,
        # response.in_progress, response.content_part.added, ...).
        return ""

    # Chat-completions style chunk.
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            delta = choice.get("delta")
            if isinstance(delta, dict):
                return _content_parts_to_text(delta.get("content"))
            if "text" in choice:
                return _content_parts_to_text(choice.get("text"))

    return ""


def _function_call_items(data: dict) -> list:
    """Return Responses API function_call items from an upstream response."""
    calls = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "function_call":
                calls.append(item)
    return calls


def _to_openai_tool_calls(items: list) -> list:
    """Convert Responses API function_call items to OpenAI tool_calls."""
    calls = []
    for item in items:
        args = item.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args if args is not None else {})
        calls.append({
            "id": item.get("call_id") or item.get("id") or f"call_{secrets.token_hex(8)}",
            "type": "function",
            "function": {"name": item.get("name", ""), "arguments": args},
        })
    return calls


def format_openai_completion_response(text: str, tool_calls: list | None = None) -> dict:
    """Construct standard OpenAI chat completion JSON response."""
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{secrets.token_hex(12)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def chunks_from_sse_payload(payload: dict) -> list:
    """Translate one upstream SSE JSON payload into OpenAI chunk deltas.

    Returns a list of ``{"text": ...}`` / ``{"tool_delta": ...}`` dicts.
    Events with no incremental output (response.created, .completed,
    .done, .output_text.done, ...) yield nothing.
    """
    if not isinstance(payload, dict):
        return []

    t = payload.get("type")

    if t == "response.output_text.delta":
        delta = payload.get("delta")
        text = delta if isinstance(delta, str) else _content_parts_to_text(delta)
        return [{"text": text}] if text else []

    if t == "response.output_item.added":
        item = payload.get("output_item") or payload.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "function_call":
            call_id = (
                item.get("call_id")
                or item.get("id")
                or f"call_{secrets.token_hex(8)}"
            )
            return [{
                "tool_delta": {
                    "index": 0,
                    "type": "function",
                    "id": call_id,
                    "function": {"name": item.get("name", ""), "arguments": ""},
                }
            }]
        return []

    if t == "response.function_call_arguments.delta":
        d = payload.get("delta")
        if isinstance(d, str) and d:
            return [{"tool_delta": {"index": 0, "function": {"arguments": d}}}]
        return []

    if t == "response.failed":
        response = payload.get("response") or {}
        error = response.get("error") or {}
        msg = error.get("message") if isinstance(error, dict) else str(error)
        return [{"text": f"Error: {msg or 'unknown upstream error'}"}]

    if t == "error":
        err = payload.get("error")
        msg = payload.get("message") or (
            err.get("message") if isinstance(err, dict) else None
        )
        return [{"text": f"Error: {msg or 'unknown upstream error'}"}]

    return []


def format_openai_chunk_sse(
    text: str,
    req_id: str,
    finish_reason: str | None = None,
    extra_delta: dict | None = None,
) -> bytes:
    """Format an SSE data line in standard OpenAI chat completion chunk format."""
    delta = {}
    if text:
        delta["content"] = text
    if extra_delta:
        delta.update(extra_delta)
    chunk = {
        "id": req_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")


# ---------------------------------------------------------
# Health & Models
# ---------------------------------------------------------

@app.get("/health")
async def health():
    tokens = load_tokens()
    authenticated = bool(tokens.get("github_access_token"))
    return {
        "ok": True,
        "provider": "github-copilot",
        "authenticated": authenticated,
        "model": MODEL,
    }


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(default=None),
):
    authenticate(authorization)

    return {
        "object": "list",
        "data": [
            {
                "id": MODEL,
                "object": "model",
                "created": 1700000000,
                "owned_by": "github-copilot",
            }
        ],
    }


# ---------------------------------------------------------
# Responses / Chat Completions API
# ---------------------------------------------------------

@app.post("/v1/chat/completions")
@app.post("/v1/responses")
async def responses(
    request: Request,
    authorization: str | None = Header(default=None),
):
    authenticate(authorization)

    raw_body = await request.json()
    body = transform_request_body(raw_body)

    stream_mode = bool(body.get("stream", False))

    try:
        copilot_token = await get_copilot_token()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    headers = copilot_headers(copilot_token)

    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT,
        read=None if stream_mode else REQUEST_TIMEOUT,
        write=30,
        pool=15,
    )

    req_id = f"chatcmpl-{secrets.token_hex(12)}"

    async def upstream_stream():
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    COPILOT_RESPONSES_URL,
                    headers=headers,
                    json=body,
                ) as upstream:

                    if upstream.status_code == 401:
                        await upstream.aclose()

                        fresh_token = await get_copilot_token()

                        async with client.stream(
                            "POST",
                            COPILOT_RESPONSES_URL,
                            headers=copilot_headers(fresh_token),
                            json=body,
                        ) as retry:

                            if retry.status_code >= 400:
                                detail = await retry.aread()
                                try:
                                    err_json = json.loads(detail)
                                    msg = err_json.get("error", {}).get("message") or str(err_json)
                                except Exception:
                                    msg = detail.decode("utf-8", errors="ignore")
                                yield format_openai_chunk_sse(f"Error: {msg}", req_id)
                                yield b"data: [DONE]\n\n"
                                return

                            async for line in retry.aiter_lines():
                                if not line:
                                    continue
                                if line.startswith("data: "):
                                    payload_str = line[6:].strip()
                                    if payload_str == "[DONE]":
                                        break
                                    try:
                                        payload = json.loads(payload_str)
                                        for piece in chunks_from_sse_payload(payload):
                                            if "tool_delta" in piece:
                                                yield format_openai_chunk_sse(
                                                    "", req_id, extra_delta={"tool_calls": [piece["tool_delta"]]}
                                                )
                                            else:
                                                yield format_openai_chunk_sse(piece.get("text", ""), req_id)
                                    except json.JSONDecodeError:
                                        if payload_str:
                                            yield format_openai_chunk_sse(payload_str, req_id)

                            yield format_openai_chunk_sse("", req_id, finish_reason="stop")
                            yield b"data: [DONE]\n\n"
                            return

                    if upstream.status_code >= 400:
                        detail = await upstream.aread()
                        try:
                            err_json = json.loads(detail)
                            msg = err_json.get("error", {}).get("message") or str(err_json)
                        except Exception:
                            msg = detail.decode("utf-8", errors="ignore")
                        yield format_openai_chunk_sse(f"Error: {msg}", req_id)
                        yield b"data: [DONE]\n\n"
                        return

                    async for line in upstream.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            payload_str = line[6:].strip()
                            if payload_str == "[DONE]":
                                break
                            try:
                                payload = json.loads(payload_str)
                                for piece in chunks_from_sse_payload(payload):
                                    if "tool_delta" in piece:
                                        yield format_openai_chunk_sse(
                                            "", req_id, extra_delta={"tool_calls": [piece["tool_delta"]]}
                                        )
                                    else:
                                        yield format_openai_chunk_sse(piece.get("text", ""), req_id)
                            except json.JSONDecodeError:
                                if payload_str:
                                    yield format_openai_chunk_sse(payload_str, req_id)

                    yield format_openai_chunk_sse("", req_id, finish_reason="stop")
                    yield b"data: [DONE]\n\n"

            except httpx.TimeoutException:
                yield format_openai_chunk_sse("[Upstream timeout]", req_id)
                yield b"data: [DONE]\n\n"

            except httpx.HTTPError:
                yield format_openai_chunk_sse("[Upstream connection error]", req_id)
                yield b"data: [DONE]\n\n"

    if stream_mode:
        return StreamingResponse(
            upstream_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming request.
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT
    ) as client:

        try:
            r = await client.post(
                COPILOT_RESPONSES_URL,
                headers=headers,
                json=body,
            )

            if r.status_code == 401:
                fresh_token = await get_copilot_token()

                r = await client.post(
                    COPILOT_RESPONSES_URL,
                    headers=copilot_headers(fresh_token),
                    json=body,
                )

            if r.status_code >= 400:
                try:
                    err_content = r.json()
                except ValueError:
                    err_content = {"error": {"message": r.text}}
                return JSONResponse(
                    content=err_content,
                    status_code=r.status_code,
                )

            try:
                raw_json = r.json()
                fc_items = _function_call_items(raw_json)
                tool_calls = _to_openai_tool_calls(fc_items)
                text = extract_text_from_copilot_json(raw_json)
                if tool_calls:
                    text = text or None
                response_json = format_openai_completion_response(text, tool_calls or None)
            except ValueError:
                response_json = format_openai_completion_response(r.text)

            return JSONResponse(
                content=response_json,
                status_code=200,
            )

        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail="Copilot request timed out",
            )

        except httpx.HTTPError:
            raise HTTPException(
                status_code=502,
                detail="Copilot connection failed",
            )


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

if __name__ == "__main__":
    import sys
    import uvicorn

    if len(sys.argv) > 1 and sys.argv[1] == "login":
        asyncio.run(device_login())
    else:
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
        )
