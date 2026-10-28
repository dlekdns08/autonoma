"""Coverage for the MCP HTTP transport (feature #8).

We don't mount the router on the production FastAPI app because that
mount lives in ``api.py`` and the tests here run in isolation. Instead
we build a stand-alone FastAPI app, include the router, and exercise
the JSON-RPC endpoint via httpx ASGI.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp.types import TextContent

_TOKEN = "test-mcp-token"


def _text(block: object) -> str:
    assert isinstance(block, TextContent), block
    return block.text


@pytest.fixture
async def client(fresh_db, monkeypatch) -> AsyncIterator[AsyncClient]:
    from autonoma.config import settings as live_settings
    from autonoma.db.engine import init_db
    from autonoma.mcp.server import router as mcp_router

    monkeypatch.setattr(live_settings, "coordinator_token", _TOKEN)
    # ``fresh_db`` only points the engine at a scratch dir; tools need
    # the schema actually created or queries trip
    # ``OperationalError: no such table: run_summary``.
    await init_db()

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


# ── Streamable HTTP transport (``/mcp``) ─────────────────────────────
#
# The SDK's own ``Client`` drives the server end-to-end through httpx2's
# in-process ASGI transport: initialize/discover handshake → tools/list
# → tools/call. ``mcp_server.lifespan()`` must be entered explicitly
# because httpx's ASGI transport does not run app lifespan events — and
# it must be entered *inside the test body*: the session manager owns
# an anyio task group, which cannot be exited from a different task
# than it was entered in (pytest-asyncio tears async fixtures down in
# a separate task).


@pytest.fixture
async def mcp_app(fresh_db, monkeypatch) -> AsyncIterator[FastAPI]:
    from autonoma.config import settings as live_settings
    from autonoma.db.engine import dispose_engine, init_db
    from autonoma.mcp import server as mcp_server

    monkeypatch.setattr(live_settings, "coordinator_token", _TOKEN)
    await init_db()

    app = FastAPI()
    mcp_server.mount(app)
    yield app
    # Close pooled aiosqlite connections while the loop is still alive;
    # ``fresh_db`` only drops the engine reference, and a connection
    # finalised after loop shutdown trips a worker-thread exception.
    await dispose_engine()


@asynccontextmanager
async def _connected(app: FastAPI):
    """Server lifespan + SDK ``Client`` over in-process ASGI, as one scope."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    from autonoma.mcp import server as mcp_server

    async with mcp_server.lifespan():
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-MCP-Token": _TOKEN},
        )
        async with http:
            transport = streamable_http_client("http://test/mcp", http_client=http)
            async with Client(transport) as client:
                yield client


async def test_streamable_http_handshake_and_tools_list(mcp_app: FastAPI) -> None:
    async with _connected(mcp_app) as client:
        assert client.server_info is not None
        assert client.server_info.name == "autonoma-mcp"
        listing = await client.list_tools()
    names = {t.name for t in listing.tools}
    assert {
        "list_sessions",
        "start_swarm_headless",
        "fetch_run_summary",
        "fetch_diary",
        "fetch_world_events",
    } <= names
    by_name = {t.name: t for t in listing.tools}
    # Typed arguments surface as a JSON schema with required fields.
    schema = by_name["fetch_run_summary"].input_schema
    assert schema["type"] == "object"
    assert schema["required"] == ["session_id"]
    assert schema["properties"]["session_id"]["type"] == "integer"
    assert by_name["fetch_diary"].input_schema["properties"]["limit"]["maximum"] == 500


async def test_streamable_http_tools_call_round_trip(mcp_app: FastAPI) -> None:
    async with _connected(mcp_app) as client:
        result = await client.call_tool("fetch_run_summary", {"session_id": 999_999})
        assert result.is_error is False
        # Single text block carrying a JSON document — same convention as
        # the legacy endpoint, so clients can ``json.loads`` block 0.
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert json.loads(_text(result.content[0])) == []

        result = await client.call_tool("list_sessions", {})
        assert result.is_error is False
        assert isinstance(json.loads(_text(result.content[0])), list)


async def test_streamable_http_invalid_arguments_is_error(mcp_app: FastAPI) -> None:
    async with _connected(mcp_app) as client:
        # Pydantic validation (wrong type) → isError result, not a crash.
        result = await client.call_tool("fetch_run_summary", {"session_id": "nope"})
        assert result.is_error is True
        assert "session_id" in _text(result.content[0])
        # Handler-level ValueError keeps its message.
        result = await client.call_tool("start_swarm_headless", {"goal": "   "})
        assert result.is_error is True
        assert "goal is required" in _text(result.content[0])


async def test_streamable_http_missing_token_returns_401(mcp_app: FastAPI) -> None:
    import httpx2

    from autonoma.mcp import server as mcp_server

    async with mcp_server.lifespan():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=mcp_app), base_url="http://test"
        ) as http:
            r = await http.post("/mcp", json=_envelope("tools/list"))
    assert r.status_code == 401
    assert "error" in r.json()


async def test_streamable_http_without_lifespan_returns_503(mcp_app: FastAPI) -> None:
    """``/mcp`` is routable but answers 503 until the session manager runs."""
    import httpx2

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=mcp_app), base_url="http://test"
    ) as http:
        r = await http.post("/mcp", json=_envelope("tools/list"), headers={"X-MCP-Token": _TOKEN})
    assert r.status_code == 503


async def test_legacy_jsonrpc_unknown_tool_and_bad_args(client: AsyncClient) -> None:
    r = await client.post(
        "/mcp/jsonrpc",
        json=_envelope("tools/call", {"name": "nope", "arguments": {}}),
        headers={"X-MCP-Token": _TOKEN},
    )
    assert r.json()["error"]["code"] == -32601
    r = await client.post(
        "/mcp/jsonrpc",
        json=_envelope("tools/call", {"name": "start_swarm_headless", "arguments": {"goal": ""}}),
        headers={"X-MCP-Token": _TOKEN},
    )
    body = r.json()
    assert body["error"]["code"] == -32602
    assert body["error"]["message"] == "goal is required"
