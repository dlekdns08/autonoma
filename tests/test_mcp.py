"""Coverage for the MCP HTTP transport (feature #8).

We don't mount the router on the production FastAPI app because that
mount lives in ``api.py`` and the tests here run in isolation. Instead
we build a stand-alone FastAPI app, include the router, and exercise
the JSON-RPC endpoint via httpx ASGI.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


_TOKEN = "test-mcp-token"


@pytest.fixture
async def client(fresh_db, monkeypatch) -> AsyncIterator[AsyncClient]:
    from autonoma.config import settings as live_settings
    from autonoma.mcp.server import router as mcp_router

    monkeypatch.setattr(live_settings, "coordinator_token", _TOKEN)

    app = FastAPI()
    app.include_router(mcp_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _envelope(method: str, params: dict | None = None, *, req_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }


async def test_tools_list_returns_at_least_five_tools(client: AsyncClient) -> None:
    r = await client.post(
        "/mcp/jsonrpc",
        json=_envelope("tools/list"),
        headers={"X-MCP-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    tools = body["result"]["tools"]
    assert len(tools) >= 5
    names = {t["name"] for t in tools}
    assert {
        "list_sessions",
        "start_swarm_headless",
        "fetch_run_summary",
        "fetch_diary",
        "fetch_world_events",
    }.issubset(names)
    # Each tool must carry a JSONSchema-ish inputSchema dict.
    for t in tools:
        assert isinstance(t["inputSchema"], dict)
        assert t["inputSchema"].get("type") == "object"


async def test_tools_call_fetch_run_summary_no_rows_returns_empty_list(
    client: AsyncClient,
) -> None:
    r = await client.post(
        "/mcp/jsonrpc",
        json=_envelope(
            "tools/call",
            {"name": "fetch_run_summary", "arguments": {"session_id": 999_999}},
        ),
        headers={"X-MCP-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "error" not in body
    content = body["result"]["content"]
    assert content and content[0]["type"] == "text"
    decoded = json.loads(content[0]["text"])
    assert decoded == []


async def test_missing_token_returns_401(client: AsyncClient) -> None:
    r = await client.post(
        "/mcp/jsonrpc",
        json=_envelope("tools/list"),
    )
    assert r.status_code == 401
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert "error" in body
