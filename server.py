import asyncio
import json
import os
import secrets
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

    body = await request.json()

    # Single-purpose gateway for gpt-5.4-nano via GitHub OAuth / Copilot.
    body["model"] = MODEL

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

    async def upstream_stream():
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    COPILOT_RESPONSES_URL,
                    headers=headers,
                    json=body,
                ) as upstream:

                    # One controlled retry on expired Copilot token.
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
                                yield detail
                                return

                            async for chunk in retry.aiter_bytes():
                                yield chunk

                        return

                    if upstream.status_code >= 400:
                        detail = await upstream.aread()
                        yield detail
                        return

                    async for chunk in upstream.aiter_bytes():
                        yield chunk

            except httpx.TimeoutException:
                yield b'{"error":{"message":"Upstream timeout"}}'

            except httpx.HTTPError:
                yield (
                    b'{"error":{"message":"Upstream connection error"}}'
                )

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

            try:
                content = r.json()
            except ValueError:
                content = {
                    "error": {
                        "message": "Invalid upstream response"
                    }
                }

            return JSONResponse(
                content=content,
                status_code=r.status_code,
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
