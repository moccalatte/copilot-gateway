#!/usr/bin/env python3
"""Live smoke test for copilot-gateway: verifies tool-calling round trip
and streaming against the RUNNING container.

Run from the morph host:
    python3 /home/morph/apps/copilot-gateway/tests/smoke_live.py

Exit code 0 = all pass, 1 = something failed (prints details).
Finds the gateway API key in ../.env and the container IP via docker.
"""
import json
import subprocess
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent


def gateway_api_key() -> str:
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith("GATEWAY_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("GATEWAY_API_KEY not found in repo .env")


def container_ip() -> str:
    out = subprocess.run(
        ["docker", "inspect", "copilot-gateway",
         "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        sys.exit("copilot-gateway container not running?")
    return out


def main() -> int:
    base = f"http://{container_ip()}:8787/v1/chat/completions"
    headers = {"Authorization": f"Bearer {gateway_api_key()}",
               "Content-Type": "application/json"}
    tools = [{"type": "function", "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}}]

    failures = []

    # 1. Plain completion
    r = httpx.post(base, headers=headers, timeout=120, json={
        "model": "gpt-5.4-nano",
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    })
    ok = (r.status_code == 200
          and r.json()["choices"][0]["message"]["content"].strip() == "OK")
    print(f"[{'PASS' if ok else 'FAIL'}] plain completion (HTTP {r.status_code})")
    if not ok:
        failures.append(f"plain completion: HTTP {r.status_code} {r.text[:200]}")

    # 2. Tool call round trip (turn 1: expect tool_calls)
    r1 = httpx.post(base, headers=headers, timeout=120, json={
        "model": "gpt-5.4-nano",
        "messages": [{"role": "user", "content": "Weather in Jakarta?"}],
        "tools": tools, "tool_choice": "auto",
    })
    try:
        m1 = r1.json()["choices"][0]["message"]
        tc = m1["tool_calls"][0]
        ok1 = (r1.status_code == 200
               and tc["function"]["name"] == "get_weather"
               and r1.json()["choices"][0]["finish_reason"] == "tool_calls")
    except Exception:
        tc, ok1 = None, False
    print(f"[{'PASS' if ok1 else 'FAIL'}] tool_call emitted (HTTP {r1.status_code})")
    if not ok1:
        failures.append(f"tool_call: HTTP {r1.status_code} {r1.text[:200]}")

    # 3. Tool result bound back (turn 2: expect final answer)
    if ok1:
        r2 = httpx.post(base, headers=headers, timeout=120, json={
            "model": "gpt-5.4-nano",
            "messages": [
                {"role": "user", "content": "Weather in Jakarta?"},
                {"role": "assistant", "tool_calls": [tc]},
                {"role": "tool", "tool_call_id": tc["id"],
                 "content": "31C sunny"},
            ],
            "tools": tools, "tool_choice": "auto",
        })
        try:
            ok2 = (r2.status_code == 200
                   and bool(r2.json()["choices"][0]["message"]["content"])
                   and r2.json()["choices"][0]["finish_reason"] == "stop")
        except Exception:
            ok2 = False
        print(f"[{'PASS' if ok2 else 'FAIL'}] tool result -> final answer (HTTP {r2.status_code})")
        if not ok2:
            failures.append(f"tool result: HTTP {r2.status_code} {r2.text[:200]}")

    # 4. Streaming with tools present (regression: empty stream 200)
    chunks = []
    with httpx.stream("POST", base, headers=headers, timeout=120, json={
        "model": "gpt-5.4-nano", "stream": True,
        "messages": [{"role": "user", "content": "Count 1 to 3, digits only."}],
        "tools": tools,
    }) as r3:
        for line in r3.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                d = json.loads(line[6:])
                chunks.append(d["choices"][0]["delta"].get("content") or "")
    ok3 = bool("".join(chunks).strip())
    print(f"[{'PASS' if ok3 else 'FAIL'}] streaming non-empty ({len(chunks)} chunks)")
    if not ok3:
        failures.append("streaming: empty stream (200 with no content)")

    if failures:
        print("\nSMOKE FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL SMOKE TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
